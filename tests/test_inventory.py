import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_library.core import LibraryError
from agent_library.inventory import audit_inventory, inspect_sidecar


def entry(fid, name, parent="root", kind="file", size=100, **extra):
    return {"file_id": fid, "name": name, "parent_id": parent, "kind": kind, "size": size, **extra}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"schema_version": "1.0.0", "meta": {"complete": True}, "entries": [
            entry("root", "Library", None, "folder", None),
            entry("archive", "Renamed historic records", kind="folder", size=None),
            entry("source", "manual.pdf", integrity={"sha256": "a"*64}),
            entry("brief", "manual.pdf.md", answerability="FULL"),
            entry("notes", "_NOTES.md"),
            entry("map", "_AI_MAP.md"),
            entry("old", "manual.pdf", "archive"),
            entry("orphan", "missing.docx.md"),
            entry("no-brief", "form.docx"),
        ]}

    def audit(self, snapshot=None):
        return audit_inventory(snapshot or self.snapshot, root_ids=["root"], archive_ids=["archive"])

    def test_counts_separate_sources_derivatives_notes_and_archive(self):
        result = self.audit()
        self.assertEqual(result["counts"]["source_documents"], 2)
        self.assertEqual(result["counts"]["archived_files"], 1)
        self.assertEqual(result["counts"]["sidecars"], 2)
        self.assertEqual(result["missing_sidecar_ids"], ["no-brief"])
        self.assertEqual(result["orphan_sidecar_ids"], ["orphan"])
        self.assertIn("brief", result["unverified_sidecar_ids"])
        self.assertEqual(result["automatic_actions"], [])

    def test_partial_or_string_boolean_cannot_be_a_complete_inventory(self):
        for value in (False, "true", 1, None):
            d = copy.deepcopy(self.snapshot); d["meta"]["complete"] = value
            with self.assertRaises(LibraryError): self.audit(d)

    def test_same_name_different_bytes_remains_conflict_and_same_bytes_only_candidate(self):
        self.snapshot["entries"].extend([
            entry("conflict", "manual.pdf", integrity={"sha256": "b"*64}),
            entry("copy", "another-use.pdf", integrity={"sha256": "a"*64}),
        ])
        result = self.audit()
        self.assertEqual(result["same_name_conflicts"], [["conflict", "source"]])
        self.assertEqual(result["identical_bytes_candidates"], [["copy", "source"]])
        self.assertEqual(result["automatic_actions"], [])

    def test_folder_named_like_sidecar_is_not_content(self):
        self.snapshot["entries"].append(entry("fake", "form.docx.md", kind="folder", size=None))
        self.assertIn("no-brief", self.audit()["missing_sidecar_ids"])

    def test_duplicate_id_cycle_and_invalid_size_fail_loudly(self):
        invalid = [entry("source", "other.pdf"), entry("cycle", "Loop", "cycle", "folder"),
                   entry("bad-size", "broken.pdf", size=True)]
        for row in invalid:
            d = copy.deepcopy(self.snapshot); d["entries"].append(row)
            with self.assertRaises(LibraryError): self.audit(d)

    def test_nonempty_and_claimed_full_text_never_prove_completeness(self):
        cases = [("ocr_status: needs_ocr", "OCR_REQUIRED"),
                 ("Tier 2 lite sidecar", "METADATA_ONLY"),
                 ("## 摘要\nA short extract\n## 原始檔", "BRIEF_ONLY"),
                 ("status: FULL\nThe complete document, allegedly.", "TEXT_UNVERIFIED")]
        for text, expected in cases:
            self.assertEqual(inspect_sidecar(text), {"status": expected, "fulltext_verified": False})


if __name__ == "__main__": unittest.main()
