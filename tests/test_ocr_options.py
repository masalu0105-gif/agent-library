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


if __name__ == '__main__': unittest.main()
