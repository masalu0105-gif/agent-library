import json
import os
import posixpath
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_library.bundles import export_bundle, verify_bundle
from agent_library.core import Library, LibraryError, canonical, digest
from agent_library.extractors import extract
from agent_library.navigation import build_briefs, build_catalog


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="library-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "來源 文件"
        self.source.mkdir()
        self.file = self.source / "manual 000123.txt"
        self.file.write_text("Synthetic document.\nKeep reference 000123.\n原文保留，不補寫日期。", encoding="utf-8")
        self.lib = Library.initialize(self.base / "runtime", [self.source])
        self.lib.scan()

    def stage(self, **kwargs):
        return self.lib.ingest(self.file, **kwargs)

    def publish(self, staged=None):
        staged = staged or self.stage()
        plan = self.lib.plan(staged["version_id"], reason="Reviewed synthetic document")
        self.lib.approve(plan["plan_id"], plan["digest"], "test-reviewer")
        self.lib.apply(plan["plan_id"])
        return staged

    def assert_code(self, code, call):
        with self.assertRaises(LibraryError) as cm:
            call()
        self.assertEqual(cm.exception.code, code)

    def edit_policy(self, **changes):
        path = self.lib.home / "policy.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy.update(changes)
        path.write_bytes(canonical(policy))

    def test_real_lifecycle_preserves_source_and_fulltext(self):
        original = self.file.read_bytes()
        staged = self.stage(metadata={"aliases": ["AX-100"], "external_reference": "000123"})
        self.assertEqual(self.lib.search("000123")["total"], 0)
        self.publish(staged)
        result = self.lib.search("ax100")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["metadata"]["external_reference"], "000123")
        read = self.lib.read(staged["version_id"], page=1)
        self.assertEqual(read["pages"][0]["text"], original.decode())
        self.assertEqual(read["evidence_status"], "unverified")
        self.assertEqual(self.file.read_bytes(), original)

    def test_duplicate_event_is_idempotent(self):
        first, second = self.stage(), self.stage()
        self.assertEqual(first["version_id"], second["version_id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(self.lib.status()["versions"], 1)

    def test_same_bytes_in_two_contexts_are_distinct_documents(self):
        first = self.stage()
        other = self.source / "second-context.txt"
        other.write_bytes(self.file.read_bytes())
        second = self.lib.ingest(other)
        self.assertNotEqual(first["document_id"], second["document_id"])
        self.assertEqual(self.lib.history(first["document_id"])["versions"][0]["source_hash"],
                         self.lib.history(second["document_id"])["versions"][0]["source_hash"])

    def test_reused_identity_for_other_path_requires_migration(self):
        first = self.stage()
        other = self.source / "renamed.txt"
        other.write_bytes(self.file.read_bytes())
        self.assert_code("IDENTITY_CONFLICT", lambda: self.lib.ingest(other, document_id=first["document_id"]))

    def test_approval_required_and_digest_bound(self):
        plan = self.lib.plan(self.stage()["version_id"], reason="Review")
        self.assert_code("APPROVAL_REQUIRED", lambda: self.lib.apply(plan["plan_id"]))
        self.assert_code("PLAN_CHANGED", lambda: self.lib.approve(plan["plan_id"], "wrong", "reviewer"))
        self.assertEqual(self.lib.status()["current_documents"], 0)

    def test_stored_plan_tampering_is_rejected(self):
        plan = self.lib.plan(self.stage()["version_id"], reason="Review")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        changed = {**plan["payload"], "action": "restore"}
        with self.lib._db(write=True) as db:
            db.execute("UPDATE plans SET payload=? WHERE id=?", (canonical(changed).decode(), plan["plan_id"]))
        self.assert_code("PLAN_CHANGED", lambda: self.lib.apply(plan["plan_id"]))

    def test_policy_change_invalidates_approval(self):
        plan = self.lib.plan(self.stage()["version_id"], reason="Review")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        self.edit_policy(settle_seconds=60)
        self.assert_code("POLICY_CHANGED", lambda: self.lib.apply(plan["plan_id"]))

    def test_source_change_invalidates_publication(self):
        plan = self.lib.plan(self.stage()["version_id"], reason="Review")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        self.file.write_text("New bytes", encoding="utf-8")
        self.assert_code("SOURCE_CHANGED", lambda: self.lib.apply(plan["plan_id"]))

    def test_missing_source_is_not_not_found(self):
        plan = self.lib.plan(self.stage()["version_id"], reason="Review")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        self.file.unlink()
        self.assert_code("SOURCE_UNAVAILABLE", lambda: self.lib.apply(plan["plan_id"]))

    def test_concurrent_plans_cannot_overwrite_current(self):
        first = self.stage()
        p1 = self.lib.plan(first["version_id"], reason="First")
        self.file.write_text("Replacement", encoding="utf-8")
        second = self.stage()
        p2 = self.lib.plan(second["version_id"], reason="Second")
        for p in [p1, p2]:
            self.lib.approve(p["plan_id"], p["digest"], "reviewer")
        self.lib.apply(p2["plan_id"])
        self.assert_code("CURRENT_CHANGED", lambda: self.lib.apply(p1["plan_id"]))

    def test_two_threads_only_one_publication_wins(self):
        stage = self.stage()
        plans = [self.lib.plan(stage["version_id"], reason="Same source") for _ in range(2)]
        for p in plans:
            self.lib.approve(p["plan_id"], p["digest"], "reviewer")
        def apply(plan):
            try:
                return self.lib.apply(plan["plan_id"])["applied"]
            except LibraryError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, plans))
        self.assertCountEqual(results, [True, "CURRENT_CHANGED"])

    def test_archive_and_restore_keep_all_snapshots(self):
        first = self.publish()
        original = self.lib.read(first["version_id"])["pages"]
        self.file.write_text("Version two", encoding="utf-8")
        second = self.publish(self.stage())
        self.assert_code("NOT_CURRENT", lambda: self.lib.read(first["version_id"]))
        self.assertEqual(self.lib.read(first["version_id"], historical=True)["pages"], original)
        archive = self.lib.plan(second["version_id"], action="archive", reason="Retire")
        self.lib.approve(archive["plan_id"], archive["digest"], "reviewer")
        self.lib.apply(archive["plan_id"])
        self.assertEqual(self.lib.search()["total"], 0)
        restore = self.lib.plan(first["version_id"], action="restore", reason="Explicit rollback to reviewed historical bytes")
        self.lib.approve(restore["plan_id"], restore["digest"], "reviewer")
        self.lib.apply(restore["plan_id"])
        self.assertEqual(self.lib.read(first["version_id"])["pages"], original)
        self.assertEqual(self.lib.status()["versions"], 2)

    def test_restore_cannot_bypass_initial_publication(self):
        self.assert_code("NOT_PUBLISHED", lambda: self.lib.plan(self.stage()["version_id"], action="restore", reason="Bypass attempt"))

    def test_replayed_plan_reports_historical_receipt(self):
        p = self.lib.plan(self.stage()["version_id"], reason="Reviewed")
        self.lib.approve(p["plan_id"], p["digest"], "reviewer")
        self.lib.apply(p["plan_id"])
        self.assertTrue(self.lib.apply(p["plan_id"])["replayed"])
        self.assertEqual(sum(e["kind"] == "APPLIED" for e in self.lib.audit()), 1)

    def test_all_four_artifacts_are_verified(self):
        stage = self.publish()
        with self.lib._db() as db:
            row = db.execute("SELECT * FROM versions WHERE id=?", (stage["version_id"],)).fetchone()
        for key in ["source_hash", "markdown_hash", "extraction_hash", "brief_hash"]:
            with self.subTest(key=key):
                path = self.lib.home / "objects" / row[key]
                original = path.read_bytes()
                path.write_bytes(b"tampered")
                self.assert_code("INTEGRITY", lambda: self.lib.read(stage["version_id"]))
                path.write_bytes(original)

    def test_partial_and_placeholder_extraction_never_publish(self):
        for status in ["needs_ocr", "partial", "failed", "unsupported", "empty"]:
            with self.subTest(status=status), patch("agent_library.core.extract", return_value={
                "schema_version": 1, "method": "test", "status": status,
                "pages": [{"number": 1, "text": "Needs OCR; this is not usable content"}], "warnings": ["NO_TEXT"]}):
                staged = self.stage()
                self.assert_code("QUALITY_BLOCKED", lambda: self.lib.plan(staged["version_id"], reason="Must fail"))

    def test_scan_waits_for_stability_and_recovers_after_restart(self):
        self.assertEqual(self.lib.scan()["staged"], [])
        future = time.time() + 40
        with patch("agent_library.core.time.time", return_value=future):
            first = Library(self.lib.home).scan()
            second = Library(self.lib.home).scan()
        self.assertEqual(len(first["staged"]), 1)
        self.assertEqual(second["staged"], [])
        self.assertEqual(self.lib.status()["current_documents"], 0)

    def test_file_change_resets_stability_window(self):
        self.file.write_text("Still being copied", encoding="utf-8")
        with patch("agent_library.core.time.time", return_value=time.time() + 100):
            result = self.lib.scan()
        self.assertEqual(result["staged"], [])
        self.assertEqual(result["waiting"], 1)

    def test_source_disappearance_does_not_delete_current(self):
        stage = self.publish()
        self.file.unlink()
        report = self.lib.scan()
        self.assertEqual(report["missing"], [str(self.file.resolve())])
        self.assertEqual(self.lib.status()["current_documents"], 1)
        self.assertEqual(self.lib.read(stage["version_id"])["source_sha256"], self.lib.history(stage["document_id"])["versions"][0]["source_hash"])

    def test_nas_unavailable_keeps_last_publication_but_blocks_fresh_claim(self):
        stage = self.publish()
        self.source.rename(self.base / "offline")
        self.assertFalse(self.lib.scan()["complete"])
        self.assert_code("STALE_INDEX", lambda: self.lib.search())
        self.assertEqual(self.lib.search(allow_stale=True)["total"], 1)
        self.assertEqual(self.lib.history(stage["document_id"])["current_version"], stage["version_id"])

    def test_scan_permission_error_is_explicit(self):
        with patch("agent_library.core.os.walk", side_effect=PermissionError("denied")):
            result = self.lib.scan()
        self.assertFalse(result["complete"])
        self.assertEqual(result["errors"][0]["code"], "SOURCE_IO")

    def test_stale_inventory_requires_explicit_override(self):
        self.publish()
        with patch("agent_library.core.time.time", return_value=time.time() + 200000):
            self.assert_code("STALE_INDEX", lambda: self.lib.search())
            result = self.lib.search(allow_stale=True)
            self.assertFalse(result["health"]["fresh"])

    def test_source_scope_and_runtime_overlap_are_rejected(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside")
        self.assert_code("SOURCE_SCOPE", lambda: self.lib.ingest(outside))
        self.assert_code("SOURCE_SCOPE", lambda: Library.initialize(self.source / "runtime", [self.source]))

    def test_symbolic_link_cannot_escape_scope(self):
        other = self.base / "outside.txt"
        other.write_text("private")
        link = self.source / "link.txt"
        try:
            link.symlink_to(other)
        except OSError:
            self.skipTest("OS does not permit creating test symlinks")
        self.assert_code("UNSAFE_PATH", lambda: self.lib.ingest(link))

    @unittest.skipUnless(os.name == "nt", "Windows short-name API")
    def test_windows_short_path_resolves_to_same_document(self):
        import ctypes
        from ctypes import wintypes
        short_path = ctypes.windll.kernel32.GetShortPathNameW
        short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        short_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        size = short_path(str(self.file), buffer, len(buffer))
        if not size or buffer.value == str(self.file):
            self.skipTest("Filesystem has no short-name alias")
        first = self.stage()
        second = self.lib.ingest(Path(buffer.value))
        self.assertEqual(first["document_id"], second["document_id"])
        self.assertTrue(second["deduplicated"])

    def test_policy_missing_and_unknown_schema_fail_closed(self):
        path = self.lib.home / "policy.json"
        old = path.read_bytes()
        path.unlink()
        self.assert_code("POLICY_UNAVAILABLE", lambda: self.lib.status())
        path.write_bytes(old)
        self.edit_policy(schema_version=99)
        self.assert_code("POLICY_SCHEMA", lambda: Library(self.lib.home))

    def test_metadata_does_not_invent_or_accept_validity(self):
        self.assert_code("METADATA_SCHEMA", lambda: self.stage(metadata={"expiry_date": "2099-01-01"}))
        stage = self.stage()
        with self.lib._db() as db:
            metadata = json.loads(db.execute("SELECT metadata FROM versions WHERE id=?", (stage["version_id"],)).fetchone()[0])
        self.assertNotIn("expiry_date", metadata)

    def test_file_size_limit_and_invalid_utf8(self):
        self.edit_policy(max_file_bytes=5)
        self.assert_code("FILE_TOO_LARGE", lambda: self.stage())
        self.edit_policy(max_file_bytes=1000)
        self.file.write_bytes(b"\xff\xfe\x00")
        self.assertEqual(self.stage()["quality"], "failed")

    def test_human_notes_are_untouched(self):
        notes = self.lib.home / "human-notes.md"
        notes.write_text("Operator annotation", encoding="utf-8")
        before = notes.read_bytes()
        self.publish()
        self.lib.scan()
        self.assertEqual(notes.read_bytes(), before)

    def test_brief_is_separate_bounded_and_points_to_fulltext(self):
        stage = self.publish()
        result = self.lib.read(stage["version_id"], brief="brief.md")
        self.assertEqual(result["layer"], "L2.5")
        self.assertIn("full.md#page-1", result["markdown"])
        self.assertIn(result["source_sha256"], result["markdown"])
        self.assertIn(result["markdown_sha256"], result["markdown"])
        self.assertNotEqual(result["brief_sha256"], result["markdown_sha256"])
        self.assert_code("SECTION_NOT_FOUND", lambda: self.lib.read(stage["version_id"], brief="../../policy.json"))

    def test_long_document_briefs_progressively_reveal(self):
        pages = [{"number": n, "text": "Document section " + str(n)} for n in range(1, 1001)]
        cards = build_briefs(pages, {"source_sha256": "a" * 64}, "b" * 64)
        self.assertGreater(len(cards), 2)
        for content in cards.values():
            self.assertLessEqual(sum(line.startswith("- [") for line in content.splitlines()), 8)
        self.assertNotIn("#page-1000", cards["brief.md"])
        self.assertTrue(any("#page-1000" in c for c in cards.values()))

    def test_large_catalogs_are_bounded(self):
        cards = build_catalog([(f"Document {n}", f"documents/{n}/brief.md") for n in range(2000)], "groups/test.md", "Test")
        self.assertTrue(all(sum(line.startswith("- ") for line in card.splitlines()) <= 32 for card in cards.values()))
        self.assertEqual(sum(card.count("/brief.md)") for card in cards.values()), 2000)

    def test_every_nested_brief_link_resolves_to_same_version(self):
        cards = build_briefs([{"number": n, "text": f"Synthetic page {n}"} for n in range(1, 1001)], {}, "a" * 64)
        for address, content in cards.items():
            for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", content):
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(address), target))
                self.assertTrue(resolved == "full.md" or resolved in cards, (address, target))

    def test_brief_generator_upgrade_creates_candidate_without_replacing_current(self):
        old = self.publish()
        with patch("agent_library.core.BRIEF_METHOD", "synthetic-v2"), patch("agent_library.navigation.BRIEF_METHOD", "synthetic-v2"):
            new = self.stage()
        self.assertNotEqual(old["version_id"], new["version_id"])
        self.assertEqual(self.lib.history(old["document_id"])["current_version"], old["version_id"])
        self.assertIn("synthetic-v2", self.lib.read(new["version_id"], brief="brief.md", historical=True)["markdown"])

    def test_new_candidate_is_visible_without_replacing_current(self):
        current = self.publish()
        self.file.write_text("Unreviewed newer version", encoding="utf-8")
        self.stage()
        result = self.lib.search()["results"][0]
        self.assertEqual(result["version_id"], current["version_id"])
        self.assertTrue(result["newer_candidate_exists"])

    def test_export_contains_real_layers_and_no_private_source_paths(self):
        self.publish()
        result = export_bundle(self.lib, self.base / "bundle")
        folder = Path(result["output"])
        self.assertTrue(verify_bundle(folder, expected_sha256=result["manifest_sha256"])["verified"])
        manifest = json.loads((folder / "bundle.json").read_bytes())
        self.assertEqual(result["documents"], 1)
        self.assertNotIn(str(self.source), (folder / "bundle.json").read_text(encoding="utf-8"))
        self.assertIn("groups/", (folder / "_AI_MAP.md").read_text(encoding="utf-8"))
        self.assertTrue((folder / manifest["documents"][0]["brief"]).is_file())
        self.assertTrue((folder / manifest["documents"][0]["fulltext"]).is_file())

    def test_export_refuses_existing_or_source_destination(self):
        self.publish()
        self.assert_code("UNSAFE_PATH", lambda: export_bundle(self.lib, self.source / "generated"))
        self.assert_code("OUTPUT_EXISTS", lambda: export_bundle(self.lib, self.source))

    def test_bundle_detects_modified_brief_and_manifest(self):
        self.publish()
        result = export_bundle(self.lib, self.base / "bundle")
        folder = Path(result["output"])
        raw = (folder / "bundle.json").read_bytes()
        manifest = json.loads(raw)
        brief = folder / manifest["documents"][0]["brief"]
        brief.write_text("Wrong highlights", encoding="utf-8")
        self.assert_code("INTEGRITY", lambda: verify_bundle(folder))
        (folder / "bundle.json").write_bytes(raw + b" ")
        self.assert_code("INTEGRITY", lambda: verify_bundle(folder, expected_sha256=result["manifest_sha256"]))

    def test_bundle_path_traversal_is_rejected(self):
        self.publish()
        result = export_bundle(self.lib, self.base / "bundle")
        folder = Path(result["output"])
        manifest = json.loads((folder / "bundle.json").read_bytes())
        manifest["files"]["../outside.txt"] = "a" * 64
        (folder / "bundle.json").write_bytes(canonical(manifest))
        self.assert_code("UNSAFE_PATH", lambda: verify_bundle(folder))

    def test_database_schema_drift_is_explicit(self):
        with self.lib._db(write=True) as db:
            db.execute("PRAGMA user_version=999")
        self.assert_code("SCHEMA_VERSION", lambda: Library(self.lib.home))

    def test_incomplete_scan_blocks_publication(self):
        version = self.stage()
        plan = self.lib.plan(version["version_id"], reason="Synthetic review")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        with patch("agent_library.core.os.walk", side_effect=PermissionError):
            self.assertFalse(self.lib.scan()["complete"])
        self.assert_code("STALE_INDEX", lambda: self.lib.apply(plan["plan_id"]))
        self.assertIsNone(self.lib.history(version["document_id"])["current_version"])

    def test_unchanged_successful_source_is_not_reparsed_by_scan(self):
        self.stage()
        with patch("agent_library.core.time.time", return_value=time.time() + 60), patch("agent_library.core.extract") as extractor:
            self.assertTrue(self.lib.scan()["complete"])
        extractor.assert_not_called()

    def test_failed_transaction_retains_previous_current(self):
        old = self.publish()
        self.file.write_text("Synthetic replacement", encoding="utf-8")
        new = self.stage()
        plan = self.lib.plan(new["version_id"], reason="Review replacement")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        with patch.object(self.lib, "_event", side_effect=RuntimeError("Simulated interruption")):
            with self.assertRaises(RuntimeError):
                self.lib.apply(plan["plan_id"])
        self.assertEqual(Library(self.lib.home).history(old["document_id"])["current_version"], old["version_id"])
        self.assertTrue(self.lib.apply(plan["plan_id"])["verified"])

    def test_cli_error_is_json_and_nonzero(self):
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        result = subprocess.run([sys.executable, "-m", "agent_library", "--home", str(self.lib.home), "read", "missing"],
                                capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "NOT_FOUND")

    def test_empty_and_binary_text_are_not_publishable(self):
        self.assertEqual(extract(b"", ".txt")["status"], "empty")
        self.assertEqual(extract(b"abc\0def", ".txt")["status"], "failed")

    def test_missing_parser_is_not_silent_success(self):
        with patch("agent_library.extractors.shutil.which", return_value=None):
            result = extract(b"not a pdf", ".pdf")
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["warnings"], ["LITEPARSE_NOT_INSTALLED"])


if __name__ == "__main__":
    unittest.main()
