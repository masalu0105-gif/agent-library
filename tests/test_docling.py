import copy
import io
import json
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_library.artifacts import add_asset, asset_manifest, table_html
from agent_library.cli import parser
from agent_library.core import Library, LibraryError
from agent_library.docling_worker import (
    LAYOUT_PRESET,
    LAYOUT_REPO_ID,
    LAYOUT_REVISION,
    PROFILE,
    _capture_pipeline_output,
    _converter,
    TABLE_MODEL_ID,
    TABLE_REPO_ID,
    TABLE_REVISION,
    TABLE_VERSION,
    DoclingError,
    _page_objects,
    _pipeline_markers,
    _table_info,
    convert,
    failure_result,
    model_manifest_summary,
    request_settings,
    validate_ocr_engine_metadata,
    validate_model_manifest,
)
from agent_library.extractors import extract


PNG = b"\x89PNG\r\n\x1a\nsynthetic"
BBOX = {"l": 0.0, "t": 0.0, "r": 100.0, "b": 100.0, "coord_origin": "TOPLEFT"}


class DoclingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-library-docling-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.model_root = self.base / "models"
        self.model_root.mkdir()
        self.layout_folder = self.model_root / "docling-project--docling-layout-heron"
        self.table_folder = self.model_root / "docling-project--docling-models"
        self.layout_folder.mkdir()
        self.table_folder.mkdir()
        self.layout_config = self.layout_folder / "config.json"
        self.layout_file = self.layout_folder / "model.safetensors"
        self.layout_license = self.layout_folder / "LICENSE.txt"
        self.table_config = self.table_folder / "config.json"
        self.table_file = self.table_folder / "model.safetensors"
        self.table_license = self.table_folder / "LICENSE.txt"
        self.layout_config.write_bytes(b"synthetic layout config")
        self.layout_file.write_bytes(b"synthetic layout model")
        self.layout_license.write_bytes(b"synthetic layout license")
        self.table_config.write_bytes(b"synthetic table config")
        self.table_file.write_bytes(b"synthetic table model")
        self.table_license.write_bytes(b"synthetic table license")
        self.manifest_path = self.base / "models.json"
        self._write_manifest()

    def _file_entry(self, path):
        data = path.read_bytes()
        import hashlib
        return {"path": path.relative_to(self.model_root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def _model_entries(self, folder):
        return [self._file_entry(path) for path in sorted(folder.rglob("*")) if path.is_file()]

    def _write_manifest(self, mutate=None):
        document = {
            "schema_version": 1,
            "artifacts_path": str(self.model_root),
            "models": {
                "layout": {"id": LAYOUT_PRESET, "repo_id": LAYOUT_REPO_ID,
                            "revision": LAYOUT_REVISION, "files": self._model_entries(self.layout_folder)},
                "table": {"id": TABLE_MODEL_ID, "repo_id": TABLE_REPO_ID,
                           "version": TABLE_VERSION, "revision": TABLE_REVISION,
                           "files": self._model_entries(self.table_folder)},
            },
        }
        if mutate:
            mutate(document)
        self.manifest_path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")

    def _synthetic_ocr_engine(self, languages):
        return {
            "kind": "tesseract-cli",
            "executable": "tesseract",
            "version": "5.3.4",
            "binary_sha256": "a" * 64,
            "traineddata": {
                language: {"sha256": "b" * 64, "bytes": 1}
                for language in dict.fromkeys(languages)
            },
        }

    def _synthetic_result(self, *, warning="DOCLING_FIDELITY_REVIEW_REQUIRED", status="partial",
                          ocr=False, ocr_language="eng", device="cpu", ocr_scale=3.0):
        summary = model_manifest_summary(validate_model_manifest(self.manifest_path))
        engine = self._synthetic_ocr_engine(ocr_language.split("+")) if ocr else None
        result = {
            "schema_version": 2,
            "method": PROFILE,
            "status": status,
            "pages": [
                {"number": 1, "text": "Synthetic page one", "preview": "", "images": [], "tables": [],
                 "markdown": "Synthetic page one", "text_items": []},
                {"number": 2, "text": "Synthetic page two", "preview": "", "images": [], "tables": [],
                 "markdown": "Synthetic page two", "text_items": []},
            ],
            "warnings": [warning],
            "assets": {},
            "capabilities": {"text": "layout_aware_unverified", "visuals": "page_previews_and_pictures",
                             "tables": "native_cells_spans_html_unverified", "ocr": "disabled"},
            "settings": request_settings(ocr=ocr, ocr_language=ocr_language, device=device,
                                          ocr_scale=ocr_scale, manifest_summary=summary,
                                          ocr_engine=engine),
        }
        previews = []
        for page in result["pages"]:
            ref = add_asset(result, PNG + bytes([page["number"]]), "png", "image/png", "page_preview")
            page["preview"] = ref
            previews.append(ref)
        picture = add_asset(result, PNG + b"picture", "png", "image/png", "extracted_picture")
        result["pages"][1]["images"].append(picture)
        cells = [{"row": 0, "col": 0, "rowspan": 1, "colspan": 2, "text": "<header>", "header": True},
                 {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "000123", "header": False},
                 {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "0.25", "header": False}]
        markup = table_html(cells, 2, 2)
        table_asset = add_asset(result, markup.encode(), "html", "text/html", "structured_table")
        table = {"id": "table-1", "locator": {"table_index": 1, "pages": [1]}, "rows": 2, "cols": 2,
                 "cells": cells, "provenance": [{"page": 1, "bbox": BBOX, "charspan": [0, 10]}],
                 "asset": table_asset}
        result["pages"][0]["tables"].append(table)
        raw = add_asset(result, b'{"schema_name":"DoclingDocument","synthetic":true}', "json",
                        "application/json", "raw_docling_output")
        result["raw_asset"] = raw
        result["pages"][0]["markdown"] += "\n\n" + markup
        result["warnings"].append("DOCLING_OUTPUT_UNVERIFIED")
        return result

    def _worker(self, factory, *, returncode=0):
        def run(args, **kwargs):
            output = Path(args[3])
            result = copy.deepcopy(factory())
            blobs = result.pop("_asset_bytes", {})
            for name, data in blobs.items():
                target = output / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
            return subprocess.CompletedProcess(args, returncode, stdout=b"", stderr=b"")
        return run

    def test_manifest_hashes_and_paths_are_verified_before_inference(self):
        manifest = validate_model_manifest(self.manifest_path)
        summary = model_manifest_summary(manifest)
        self.assertNotIn("artifacts_path", json.dumps(summary))
        self.layout_file.write_bytes(b"tampered")
        with self.assertRaises(DoclingError) as error:
            validate_model_manifest(self.manifest_path)
        self.assertEqual(error.exception.code, "DOCLING_MODEL_HASH_MISMATCH")
        with patch("agent_library.extractors.subprocess.run") as run:
            result = extract(b"synthetic pdf", ".pdf", parser="docling",
                             docling_model_manifest=self.manifest_path)
        run.assert_not_called()
        self.assertEqual(result["warnings"], ["DOCLING_MODEL_HASH_MISMATCH"])
        self.layout_file.write_bytes(b"synthetic layout model")
        self._write_manifest(lambda doc: doc["models"]["layout"]["files"][0].update({"path": "../outside.bin"}))
        with self.assertRaises(DoclingError) as error:
            validate_model_manifest(self.manifest_path)
        self.assertEqual(error.exception.code, "DOCLING_INVALID_MODEL_MANIFEST")

    def test_manifest_requires_complete_selected_model_subtrees(self):
        def license_only(document):
            document["models"]["layout"]["files"] = [
                item for item in document["models"]["layout"]["files"]
                if item["path"].endswith("LICENSE.txt")
            ]

        self._write_manifest(license_only)
        with self.assertRaises(DoclingError) as error:
            validate_model_manifest(self.manifest_path)
        self.assertEqual(error.exception.code, "DOCLING_MODEL_MANIFEST_INCOMPLETE")

    def test_extract_actual_entrypoint_preserves_pages_pictures_tables_and_raw_json(self):
        with patch("agent_library.extractors.subprocess.run", side_effect=self._worker(self._synthetic_result)) as run:
            result = extract(b"synthetic pdf", ".pdf", parser="docling",
                             docling_model_manifest=self.manifest_path)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([page["number"] for page in result["pages"]], [1, 2])
        self.assertEqual(len(result["pages"][0]["tables"]), 1)
        self.assertEqual(len(result["pages"][1]["images"]), 1)
        self.assertEqual(result["assets"][result["raw_asset"]]["role"], "raw_docling_output")
        table_ref = result["pages"][0]["tables"][0]["asset"]
        self.assertNotIn(b"<header>", result["_asset_bytes"][table_ref])
        self.assertIn(b"&lt;header&gt;", result["_asset_bytes"][table_ref])
        settings_text = json.dumps(result["settings"])
        self.assertNotIn(str(self.model_root), settings_text)
        command = run.call_args.args[0]
        self.assertEqual(Path(command[1]).name, "docling_worker.py")
        self.assertIn("--model-manifest", command)

    def test_ocr_options_reach_docling_entrypoint_and_remain_unverified(self):
        with patch("agent_library.extractors.subprocess.run", side_effect=self._worker(
                lambda: self._synthetic_result(ocr=True, ocr_language="chi_tra+eng", ocr_scale=4.0))) as run:
            result = extract(b"synthetic pdf", ".pdf", parser="docling", ocr=True,
                             ocr_language="chi_tra+eng", docling_ocr_scale=4.0,
                             docling_model_manifest=self.manifest_path)
        command = run.call_args.args[0]
        self.assertIn("--ocr", command)
        self.assertEqual(command[command.index("--ocr-language") + 1], "chi_tra+eng")
        self.assertEqual(command[command.index("--ocr-scale") + 1], "4.0")
        self.assertIn("DOCLING_OUTPUT_UNVERIFIED", result["warnings"])

    def test_converter_uses_explicit_tesseract_cli_and_scale(self):
        layout_cls, layout = Mock(), Mock()
        layout_cls.from_preset.return_value = layout
        layout.model_copy.return_value = layout
        layout.model_spec.model_copy.return_value = layout.model_spec
        tesseract_cls = Mock()
        runtime = {
            "LayoutObjectDetectionOptions": layout_cls,
            "TableStructureOptions": Mock(),
            "TableFormerMode": SimpleNamespace(ACCURATE="accurate"),
            "TesseractCliOcrOptions": tesseract_cls,
            "PdfPipelineOptions": Mock(),
            "AcceleratorOptions": Mock(),
            "InputFormat": SimpleNamespace(PDF="pdf", IMAGE="image"),
            "PdfFormatOption": Mock(),
            "ImageFormatOption": Mock(),
            "DocumentConverter": Mock(),
        }
        _converter(runtime, {"artifacts_path": self.model_root}, ocr=True,
                   ocr_language="chi_tra+eng", device="cpu", ocr_scale=4.0,
                   tesseract={"command": "/local/tesseract", "path": "/local/tessdata"})
        tesseract_cls.assert_called_once_with(
            lang=["chi_tra", "eng"], scale=4.0,
            tesseract_cmd="/local/tesseract", path="/local/tessdata",
        )
        self.assertIs(runtime["PdfPipelineOptions"].call_args.kwargs["ocr_options"],
                      tesseract_cls.return_value)

    def test_missing_dependency_and_timeout_are_fail_closed(self):
        def missing():
            summary = model_manifest_summary(validate_model_manifest(self.manifest_path))
            return failure_result("DOCLING_DEPENDENCY_MISSING", ocr=False, ocr_language="eng",
                                  device="cpu", ocr_scale=3.0, manifest_summary=summary)
        with patch("agent_library.extractors.subprocess.run", side_effect=self._worker(missing)):
            result = extract(b"synthetic pdf", ".pdf", parser="docling",
                             docling_model_manifest=self.manifest_path)
        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["warnings"], ["DOCLING_DEPENDENCY_MISSING"])
        with patch("agent_library.extractors.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(["python"], 1)):
            result = extract(b"synthetic pdf", ".pdf", parser="docling",
                             docling_model_manifest=self.manifest_path)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["warnings"], ["DOCLING_TIMEOUT"])

    def test_nonzero_worker_exit_and_settings_mismatch_fail_closed(self):
        for status in ("partial", "needs_ocr", "empty"):
            with patch("agent_library.extractors.subprocess.run", side_effect=self._worker(
                    lambda status=status: self._synthetic_result(status=status), returncode=7)):
                result = extract(b"synthetic pdf", ".pdf", parser="docling",
                                 docling_model_manifest=self.manifest_path)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["warnings"], ["DOCLING_WORKER_FAILED"])
            self.assertEqual(result["assets"], {})
            self.assertNotIn("_asset_bytes", result)

        def mismatched_result():
            result = self._synthetic_result()
            result["settings"]["model_manifest"]["manifest_sha256"] = "0" * 64
            return result

        with patch("agent_library.extractors.subprocess.run",
                   side_effect=self._worker(mismatched_result)):
            result = extract(b"synthetic pdf", ".pdf", parser="docling",
                             docling_model_manifest=self.manifest_path)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["warnings"], ["DOCLING_SETTINGS_MISMATCH"])
        self.assertEqual(result["assets"], {})
        self.assertNotIn("_asset_bytes", result)

    def test_ocr_candidate_requires_tesseract_metadata(self):
        def missing_metadata():
            result = self._synthetic_result(ocr=True, ocr_language="chi_tra+eng", ocr_scale=4.0)
            result["settings"]["ocr_engine"].pop("binary_sha256")
            return result

        with patch("agent_library.extractors.subprocess.run",
                   side_effect=self._worker(missing_metadata)):
            result = extract(b"synthetic pdf", ".pdf", parser="docling", ocr=True,
                             ocr_language="chi_tra+eng", docling_ocr_scale=4.0,
                             docling_model_manifest=self.manifest_path)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["warnings"], ["DOCLING_SETTINGS_MISMATCH"])
        self.assertEqual(result["assets"], {})

    def test_pipeline_capture_includes_logging_and_orphan_cell_fallback(self):
        root = logging.getLogger()
        existing_stream = io.StringIO()
        existing_handler = logging.StreamHandler(existing_stream)
        root.addHandler(existing_handler)
        try:
            with _capture_pipeline_output() as captured:
                logging.getLogger("docling.pipeline").warning(
                    "Orphan cell nearest-row fallback occurred")
            markers = _pipeline_markers(SimpleNamespace(errors=[]), captured.getvalue())
        finally:
            root.removeHandler(existing_handler)
            existing_handler.close()
        self.assertIn("Orphan cell nearest-row fallback occurred", captured.getvalue())
        self.assertIn("Orphan cell nearest-row fallback occurred", existing_stream.getvalue())
        self.assertIn("DOCLING_PIPELINE_TABLEFALLBACK", markers)

    def test_partial_page_coverage_and_invalid_spans_fail_closed(self):
        conversion = SimpleNamespace(pages=[SimpleNamespace(page_no=1)],
                                      input=SimpleNamespace(page_count=2))
        with self.assertRaises(DoclingError) as error:
            _page_objects(conversion, 2)
        self.assertEqual(error.exception.code, "DOCLING_PARTIAL_PAGE_COVERAGE")
        provenance = [SimpleNamespace(page_no=1, bbox=BBOX, charspan=(0, 5))]
        bad_cell = SimpleNamespace(start_row_offset_idx=0, start_col_offset_idx=0,
                                    row_span=2, col_span=2, text="bad", bbox=BBOX,
                                    column_header=False, row_header=False, row_section=False)
        table = SimpleNamespace(prov=provenance, data=SimpleNamespace(num_rows=1, num_cols=1,
                                                                         table_cells=[bad_cell]))
        with self.assertRaises(DoclingError) as error:
            _table_info(table, 1, {1})
        self.assertEqual(error.exception.code, "DOCLING_INVALID_TABLE_SPAN")

    def test_empty_detected_table_warns_and_keeps_page_candidate_partial(self):
        provenance = [SimpleNamespace(page_no=1, bbox=BBOX, charspan=(0, 0))]
        table = SimpleNamespace(prov=provenance, data=SimpleNamespace(num_rows=0, num_cols=0,
                                                                         table_cells=[]))

        class FakeDocument:
            pages = {1: SimpleNamespace()}
            tables = [table]
            pictures = []

            def export_to_text(self, **kwargs):
                return "Visible title"

            def iterate_items(self, **kwargs):
                return []

            def save_as_json(self, path, artifacts_dir, **kwargs):
                assets = path.parent / artifacts_dir
                assets.mkdir(parents=True, exist_ok=True)
                (assets / "page_000001_deadbeef.png").write_bytes(PNG)
                path.write_text("{}", encoding="utf-8")

            def export_to_markdown(self, **kwargs):
                return "Visible title"

        class FakeConverter:
            def convert(self, *args, **kwargs):
                return SimpleNamespace(
                    status="success", errors=[], input=SimpleNamespace(page_count=1),
                    pages=[SimpleNamespace(page_no=1,
                                           size=SimpleNamespace(width=100, height=100))],
                    document=FakeDocument(),
                )

        runtime = {
            "versions": {},
            "ImageRefMode": SimpleNamespace(REFERENCED="referenced", PLACEHOLDER="placeholder"),
        }
        with patch("agent_library.docling_worker._load_runtime", return_value=runtime), \
             patch("agent_library.docling_worker._converter", return_value=(FakeConverter(), None)), \
             patch("agent_library.docling_worker.install_socket_audit", return_value={
                 "environment": {}, "python_socket_guard": "enabled", "os_sandbox": "not_provided",
             }):
            source = self.base / "synthetic.pdf"
            source.write_bytes(b"synthetic pdf")
            result = convert(source, self.base / "output", self.manifest_path)

        self.assertEqual(result["status"], "partial")
        self.assertIn("DOCLING_TABLE_CONTENT_MISSING", result["warnings"])
        self.assertTrue(result["pages"][0]["preview"])
        self.assertEqual(result["pages"][0]["tables"][0]["cells"], [])

    def test_pipeline_markers_are_specific_and_do_not_echo_logs(self):
        conversion = SimpleNamespace(errors=[SimpleNamespace(error_message="OSD warning at /secret/file.pdf",
                                                              module_name="table fallback")])
        markers = _pipeline_markers(conversion, "tablefallback and OSD /private/document text")
        self.assertIn("DOCLING_PIPELINE_OSD", markers)
        self.assertIn("DOCLING_PIPELINE_TABLEFALLBACK", markers)
        self.assertNotIn("/secret/file.pdf", markers)
        self.assertNotIn("/private/document text", markers)

    def test_core_pipeline_keeps_docling_candidate_out_of_publication(self):
        source = self.base / "source"
        source.mkdir()
        document = source / "guide.pdf"
        document.write_bytes(b"synthetic pdf")
        library = Library.initialize(self.base / "runtime", [source])
        library.scan()
        with patch("agent_library.extractors.subprocess.run", side_effect=self._worker(self._synthetic_result)):
            staged = library.ingest(document, parser="docling", docling_model_manifest=self.manifest_path)
        self.assertEqual(staged["quality"], "partial")
        read = library.read(staged["version_id"], historical=True)
        self.assertEqual(len(read["pages"]), 2)
        self.assertIn("full.md#page-1", library.read(staged["version_id"], brief="brief.md", historical=True)["markdown"])
        with self.assertRaises(LibraryError) as error:
            library.plan(staged["version_id"], reason="Synthetic review")
        self.assertEqual(error.exception.code, "QUALITY_BLOCKED")

    def test_cli_exposes_docling_profile_options(self):
        args = parser().parse_args([
            "--home", "/local/runtime", "ingest", "/local/source.pdf", "--parser", "docling",
            "--docling-model-manifest", "/local/models.json", "--docling-device", "cuda",
            "--ocr", "--ocr-language", "chi_tra+eng", "--docling-ocr-scale", "4",
        ])
        self.assertEqual(args.parser, "docling")
        self.assertEqual(args.docling_device, "cuda")
        self.assertEqual(args.docling_ocr_scale, 4.0)

    def test_missing_manifest_and_unsupported_format_do_not_start_worker(self):
        with patch("agent_library.extractors.subprocess.run") as run:
            missing = extract(b"synthetic pdf", ".pdf", parser="docling")
            unsupported = extract(b"synthetic text", ".txt", parser="docling",
                                  docling_model_manifest=self.manifest_path)
        run.assert_not_called()
        self.assertEqual(missing["status"], "unsupported")
        self.assertEqual(missing["warnings"], ["DOCLING_MODEL_MANIFEST_REQUIRED"])
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertEqual(unsupported["warnings"], ["DOCLING_PROFILE_UNSUPPORTED"])


if __name__ == "__main__":
    unittest.main()
