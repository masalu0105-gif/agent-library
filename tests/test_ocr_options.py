import json
import sys
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_library.extractors import extract


class OcrOptionsTests(unittest.TestCase):
    def run_parser(self, stderr=b""):
        observed = []
        def run(args, **kwargs):
            if "--version" in args:
                return CompletedProcess(args, 0, stdout=b"2.0.0\n", stderr=b"")
            if "screenshot" in args:
                folder = Path(args[args.index("-o")+1])
                folder.mkdir()
                (folder / "page_1.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
                return CompletedProcess(args, 0, stdout=b"", stderr=b"")
            observed.extend(args)
            Path(args[args.index("-o")+1]).write_text(json.dumps({"pages":[{"page":1,"text":"Visible text layer"}]}))
            return CompletedProcess(args, 0, stdout=b"", stderr=stderr)
        with patch("agent_library.extractors.shutil.which", return_value="lit"), patch("agent_library.extractors.subprocess.run", side_effect=run):
            result=extract(b"synthetic input", ".pdf", ocr=True, ocr_language="chi_tra+eng")
        return result, observed

    def test_language_reaches_parser_and_is_recorded(self):
        result,args=self.run_parser()
        self.assertEqual(args[args.index("--ocr-language")+1], "chi_tra+eng")
        self.assertEqual(result["ocr_language"], "chi_tra+eng")
        self.assertEqual(result["status"], "extracted")

    def test_zero_exit_ocr_failure_is_not_full_extraction(self):
        result,_=self.run_parser(b"Failed loading language 'chi_tra'\n[ocr] failed for page 1")
        self.assertEqual(result["status"], "partial")
        self.assertIn("OCR_FAILED", result["warnings"])

    def test_invalid_language_is_rejected_before_invoking_parser(self):
        with patch("agent_library.extractors.subprocess.run") as run:
            result=extract(b"x", ".pdf", ocr=True, ocr_language="../models")
        run.assert_not_called()
        self.assertIn("INVALID_OCR_LANGUAGE", result["warnings"])

    def test_image_preview_preserves_original_without_pdf_screenshot(self):
        def run(args, **kwargs):
            self.assertNotIn("screenshot", args)
            if "--version" not in args:
                Path(args[args.index("-o")+1]).write_text(json.dumps({"pages":[{"page":1,"text":""}]}))
            return CompletedProcess(args, 0, stdout=b"2.0.0\n", stderr=b"")
        for suffix, data in [(".jpg", b"\xff\xd8\xffsynthetic"), (".png", b"\x89PNG\r\n\x1a\nsynthetic")]:
            with self.subTest(suffix=suffix), patch("agent_library.extractors.shutil.which", return_value="lit"), patch("agent_library.extractors.subprocess.run", side_effect=run):
                result=extract(data, suffix)
                self.assertEqual(result["_asset_bytes"][result["pages"][0]["preview"]], data)
                self.assertNotIn("PAGE_PREVIEW_FAILED", result["warnings"])
                self.assertEqual(result["status"], "needs_ocr")


if __name__ == '__main__': unittest.main()
