"""Local extraction. Empty pages remain visible; metadata is not evidence."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def extract(data: bytes, suffix: str, *, ocr: bool = False) -> dict:
    result = {"schema_version": 1, "method": "utf8", "status": "extracted", "pages": [], "warnings": []}
    if suffix in {".txt", ".md"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return {**result, "status": "failed", "warnings": ["INVALID_UTF8"]}
        if "\x00" in text:
            return {**result, "status": "failed", "warnings": ["BINARY_TEXT"]}
        result["pages"] = [{"number": 1, "text": text}]
    else:
        executable = shutil.which("lit.cmd" if os.name == "nt" else "lit")
        result["method"] = "liteparse-2.0.0:ocr" if ocr else "liteparse-2.0.0:no-ocr"
        if not executable:
            return {**result, "status": "unsupported", "warnings": ["LITEPARSE_NOT_INSTALLED"]}
        try:
            version = subprocess.run([executable, "--version"], capture_output=True, timeout=15, check=True)
            if version.stdout.decode().strip() != "2.0.0":
                return {**result, "status": "failed", "warnings": ["UNVERIFIED_PARSER_VERSION"]}
            with tempfile.TemporaryDirectory(prefix="agent-library-parse-") as folder:
                source, output = Path(folder) / ("document" + suffix), Path(folder) / "parsed.json"
                source.write_bytes(data)
                args = [executable, "parse", str(source), "--format", "json", "-o", str(output)]
                if not ocr:
                    args.append("--no-ocr")
                subprocess.run(args, capture_output=True, timeout=120, check=True)
                parsed = json.loads(output.read_text(encoding="utf-8"))
                pages = parsed["pages"]
                if not isinstance(pages, list) or not pages:
                    raise ValueError("Missing pages")
                for number, page in enumerate(pages, 1):
                    if page.get("page") != number or not isinstance(page.get("text"), str):
                        raise ValueError("Unsupported page schema")
                    result["pages"].append({"number": number, "text": page["text"]})
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
            # Parser stderr may contain document text or local paths. Keep it private.
            return {**result, "status": "failed", "warnings": ["PARSER_FAILED"]}
    nonempty = sum(bool(p["text"].strip()) for p in result["pages"])
    if not nonempty:
        result["status"] = "needs_ocr" if suffix not in {".txt", ".md"} else "empty"
        result["warnings"].append("NO_EXTRACTED_TEXT")
    elif nonempty != len(result["pages"]):
        result["status"] = "partial"
        result["warnings"].append("PAGES_WITHOUT_TEXT")
    return result
