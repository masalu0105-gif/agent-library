import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_library.artifacts import add_asset, asset_manifest, table_html
from agent_library.core import Library, LibraryError, canonical, digest
from agent_library.bundles import export_bundle, verify_bundle
from agent_library.extractors import extract


def rich_result(content=b"\x89PNG\r\n\x1a\nsynthetic"):
    result = {"schema_version": 2, "method": "synthetic", "status": "extracted", "warnings": [],
              "pages": [{"number": 1, "text": "第一頁🙂 000123"}, {"number": 2, "text": "Second page"}]}
    image = add_asset(result, content, "png", "image/png", "page_preview")
    result["pages"][0]["preview"] = image
    return result


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.file = self.source / "example.txt"
        self.file.write_text("Synthetic original", encoding="utf-8")
        self.lib = Library.initialize(self.base / "runtime", [self.source])
        self.lib.scan()

    def stage(self, result=None):
        with patch("agent_library.core.extract", return_value=copy.deepcopy(result or rich_result())):
            return self.lib.ingest(self.file)

    def publish(self, stage):
        plan = self.lib.plan(stage["version_id"], reason="Synthetic asset test")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        self.lib.apply(plan["plan_id"])

    def assert_error(self, code, function):
        with self.assertRaises(LibraryError) as exc:
            function()
        self.assertEqual(exc.exception.code, code)

    def test_assets_survive_restart_and_bundle_with_same_hash(self):
        stage = self.stage()
        self.publish(stage)
        self.lib = Library(self.lib.home)
        result = self.lib.read(stage["version_id"], page=1)
        name = result["pages"][0]["preview"]
        asset = self.lib.read(stage["version_id"], asset=name)
        self.assertEqual(digest(Path(asset["snapshot_path"]).read_bytes()), asset["sha256"])
        folder = self.base / "export"
        export_bundle(self.lib, folder)
        self.assertTrue((folder / "documents" / stage["version_id"] / name).is_file())
        self.assertTrue(verify_bundle(folder)["verified"])

    def test_missing_or_changed_asset_blocks_read_and_apply(self):
        stage = self.stage()
        plan = self.lib.plan(stage["version_id"], reason="Review")
        self.lib.approve(plan["plan_id"], plan["digest"], "reviewer")
        result = self.lib.read(stage["version_id"], historical=True)
        item = next(iter(result["assets"].values()))
        path = self.lib.home / "objects" / item["sha256"]
        path.write_bytes(b"corrupt")
        self.assert_error("INTEGRITY", lambda: self.lib.apply(plan["plan_id"]))
        path.unlink()
        self.assert_error("INTEGRITY", lambda: self.lib.read(stage["version_id"], historical=True))

    def test_changed_image_creates_candidate_and_preserves_current(self):
        first = self.stage()
        self.publish(first)
        second = self.stage(rich_result(b"another synthetic asset"))
        self.assertNotEqual(first["version_id"], second["version_id"])
        self.assertEqual(self.lib.history(first["document_id"])["current_version"], first["version_id"])
        self.assertTrue(self.lib.read(first["version_id"])["is_current"])

    def test_asset_read_does_not_bypass_history_or_path_scope(self):
        stage = self.stage()
        name = next(iter(rich_result()["assets"]))
        self.assert_error("NOT_CURRENT", lambda: self.lib.read(stage["version_id"], asset=name))
        self.assert_error("ASSET_NOT_FOUND", lambda: self.lib.read(stage["version_id"], asset="../../policy.json", historical=True))

    def test_parser_cannot_reference_missing_bytes(self):
        result = rich_result()
        result["_asset_bytes"].clear()
        self.assert_error("INTEGRITY", lambda: self.stage(result))
        result = rich_result()
        result["pages"][0]["preview"] = "assets/missing.png"
        self.assert_error("INTEGRITY", lambda: self.stage(result))

    def test_assets_reject_traversal_and_digest_mismatch(self):
        result = rich_result()
        item = next(iter(result["assets"].values()))
        result["assets"] = {"../outside.png": item}
        with self.assertRaises(ValueError):
            asset_manifest(result)
        result = rich_result()
        next(iter(result["assets"].values()))["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            asset_manifest(result)

    def test_bundle_rejects_deleted_asset_even_with_rebuilt_file_manifest(self):
        stage = self.stage()
        self.publish(stage)
        folder = self.base / "export"
        export_bundle(self.lib, folder)
        manifest = json.loads((folder / "bundle.json").read_text())
        name = next(k for k in manifest["files"] if k.endswith(".png"))
        del manifest["files"][name]
        (folder / name).unlink()
        (folder / "bundle.json").write_bytes(canonical(manifest))
        self.assert_error("INTEGRITY", lambda: verify_bundle(folder))

    def test_legacy_v1_extraction_remains_readable(self):
        result = {"schema_version": 1, "method": "utf8", "status": "extracted", "warnings": [],
                  "pages": [{"number": 1, "text": "Old snapshot"}]}
        stage = self.stage(result)
        read = self.lib.read(stage["version_id"], historical=True)
        self.assertEqual(read["pages"][0]["text"], "Old snapshot")
        self.assertEqual(read["assets"], {})

    def test_langextract_offsets_match_unicode_and_selected_page(self):
        stage = self.stage()
        result = self.lib.langextract_input(stage["version_id"], historical=True)
        pages = rich_result()["pages"]
        for span, page in zip(result["source_spans"], pages):
            self.assertEqual(result["text"][span["start"]:span["end"]], page["text"])
        selected = self.lib.langextract_input(stage["version_id"], historical=True, page=2)
        self.assertEqual(selected["source_spans"][0]["start"], 0)
        self.assertEqual(selected["source_spans"][0]["page"], 2)
        self.assertEqual(selected["extraction_sha256"], result["extraction_sha256"])

    def test_table_spans_retain_values_and_escape_markup(self):
        cells = [{"row": 0, "col": 0, "rowspan": 2, "colspan": 1, "text": "<script>x</script>"},
                 {"row": 0, "col": 1, "rowspan": 1, "colspan": 2, "text": "000123"}]
        rendered = table_html(cells, 2, 3)
        self.assertIn('rowspan="2"', rendered)
        self.assertIn('colspan="2"', rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("000123", rendered)
        with self.assertRaises(ValueError):
            table_html(cells + [cells[0]], 2, 3)

    def test_screenshot_failure_keeps_text_but_blocks_quality(self):
        def run(args, **kwargs):
            if "--version" in args:
                return CompletedProcess(args, 0, stdout=b"2.0.0", stderr=b"")
            if "screenshot" in args:
                raise OSError("render unavailable")
            Path(args[args.index("-o") + 1]).write_text(json.dumps({"pages": [{"page": 1, "text": "Visible", "textItems": [{"text": "Visible", "x": 1}]}]}))
            return CompletedProcess(args, 0, stdout=b"", stderr=b"")
        with patch("agent_library.extractors.shutil.which", return_value="lit"), patch("agent_library.extractors.subprocess.run", side_effect=run):
            result = extract(b"synthetic", ".pdf")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][0]["text"], "Visible")
        self.assertEqual(result["pages"][0]["text_items"][0]["x"], 1)
        self.assertIn("PAGE_PREVIEW_FAILED", result["warnings"])

    def test_markitdown_never_silently_treats_scan_as_native_document(self):
        with patch("agent_library.extractors.subprocess.run") as run:
            result = extract(b"synthetic", ".pdf", parser="markitdown")
        run.assert_not_called()
        self.assertEqual(result["status"], "unsupported")

    def test_existing_markdown_image_reference_does_not_prove_asset_capture(self):
        result = extract(b"Some text. ![diagram](missing.png)", ".md")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["assets"], {})
        self.assertIn("MARKDOWN_ASSETS_NOT_IMPORTED", result["warnings"])


if __name__ == "__main__":
    unittest.main()
