"""Local extraction. Empty pages remain visible; metadata is not evidence."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path
from .artifacts import add_asset


def extract(data: bytes, suffix: str, *, ocr: bool = False, ocr_language: str = "eng", parser="liteparse") -> dict:
    result = {"schema_version": 2, "method": "utf8", "status": "extracted", "pages": [], "warnings": [],
              "assets": {}, "capabilities": {"text": "decoded", "visuals": "not_applicable", "tables": "not_analyzed"}}
    if parser not in {"liteparse", "markitdown"}:
        return {**result, "status": "unsupported", "warnings": ["UNKNOWN_PARSER"]}
    if parser == "markitdown":
        if suffix not in {".docx", ".xlsx"} or ocr:
            return {**result, "status": "unsupported", "warnings": ["MARKITDOWN_PROFILE_UNSUPPORTED"]}
        try:
            with tempfile.TemporaryDirectory(prefix="agent-library-office-") as folder:
                base = Path(folder)
                source = base / ("document" + suffix)
                source.write_bytes(data)
                # No generic URL conversion, plugins, LLM clients or cloud credentials.
                subprocess.run([sys.executable, str(Path(__file__).with_name("office.py")), str(source), str(base)],
                               capture_output=True, timeout=180, check=True)
                parsed = json.loads((base / "result.json").read_text(encoding="utf-8"))
                from .artifacts import asset_manifest
                assets = asset_manifest(parsed)
                parsed["_asset_bytes"] = {name: (base / name).read_bytes() for name in assets}
                return parsed
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return {**result, "method": "markitdown", "status": "failed", "warnings": ["OFFICE_PARSER_FAILED"]}
    if suffix in {".txt", ".md"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return {**result, "status": "failed", "warnings": ["INVALID_UTF8"]}
        if "\x00" in text:
            return {**result, "status": "failed", "warnings": ["BINARY_TEXT"]}
        result["pages"] = [{"number": 1, "text": text}]
        if suffix == ".md" and re.search(r"!\[|<img\b", text, re.I):
            result["status"] = "partial"
            result["warnings"].append("MARKDOWN_ASSETS_NOT_IMPORTED")
            result["capabilities"]["visuals"] = "unpackaged_references"
    else:
        if not isinstance(ocr_language, str) or not re.fullmatch(r"[a-z0-9_]+(?:\+[a-z0-9_]+)*", ocr_language):
            return {**result, "status": "failed", "warnings": ["INVALID_OCR_LANGUAGE"]}
        executable = shutil.which("lit.cmd" if os.name == "nt" else "lit")
        result["method"] = "liteparse-2.0.0:ocr" if ocr else "liteparse-2.0.0:no-ocr"
        if ocr:
            result["ocr_language"] = ocr_language
        if not executable:
            return {**result, "status": "unsupported", "warnings": ["LITEPARSE_NOT_INSTALLED"]}
        try:
            version = subprocess.run([executable, "--version"], capture_output=True, timeout=15, check=True)
            if version.stdout.decode().strip() != "2.0.0":
                return {**result, "status": "failed", "warnings": ["UNVERIFIED_PARSER_VERSION"]}
            with tempfile.TemporaryDirectory(prefix="agent-library-parse-") as folder:
                source, output = Path(folder) / ("document" + suffix), Path(folder) / "parsed.json"
                source.write_bytes(data)
                if suffix in {".doc", ".docx", ".xlsx", ".pptx"}:
                    office = shutil.which("libreoffice") or shutil.which("soffice")
                    if not office:
                        return {**result, "status": "unsupported", "warnings": ["LIBREOFFICE_NOT_INSTALLED"]}
                    # Parse and preview the SAME conversion; screenshot does not convert Office inputs.
                    profile = (Path(folder) / "office-profile").as_uri()
                    subprocess.run([office, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
                                    "--outdir", folder, str(source)], capture_output=True, timeout=120, check=True)
                    source = Path(folder) / "document.pdf"
                    rendered = source.read_bytes()
                    if not rendered.startswith(b"%PDF-"):
                        raise ValueError("Office conversion did not produce a PDF")
                    result["rendered_asset"] = add_asset(result, rendered, "pdf", "application/pdf", "rendered_document")
                args = [executable, "parse", str(source), "--format", "json", "-o", str(output)]
                if not ocr:
                    args.append("--no-ocr")
                else:
                    args.extend(["--ocr-language", ocr_language])
                execution = subprocess.run(args, capture_output=True, timeout=120, check=True)
                parsed = json.loads(output.read_text(encoding="utf-8"))
                result["raw_asset"] = add_asset(result, json.dumps(parsed, ensure_ascii=False, sort_keys=True,
                    allow_nan=False).encode(), "json", "application/json", "raw_parser_output")
                result["capabilities"] = {"text": "ocr" if ocr else "text_layer", "visuals": "page_previews",
                                           "tables": "visual_only", "layout": "text_boxes", "page_basis": "rendered"}
                result["settings"] = {"adapter_revision": 2, "preview_dpi": 120, "ocr": ocr,
                                      "ocr_language": ocr_language if ocr else None}
                result["warnings"].append("TABLE_STRUCTURE_NOT_RECONSTRUCTED")
                pages = parsed["pages"]
                if not isinstance(pages, list) or not pages:
                    raise ValueError("Missing pages")
                for number, page in enumerate(pages, 1):
                    if page.get("page") != number or not isinstance(page.get("text"), str):
                        raise ValueError("Unsupported page schema")
                    result["pages"].append({"number": number, "text": page["text"],
                        "width": page.get("width"), "height": page.get("height"),
                        "text_items": page.get("textItems", []), "coordinate_system": "liteparse_native"})
                try:
                    previews = Path(folder) / "previews"
                    subprocess.run([executable, "screenshot", str(source), "-o", str(previews), "--dpi", "120"],
                                   capture_output=True, timeout=120, check=True)
                    for page in result["pages"]:
                        raw = (previews / f"page_{page['number']}.png").read_bytes()
                        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise ValueError("Invalid page preview")
                        page["preview"] = add_asset(result, raw, "png", "image/png", "page_preview")
                except (OSError, subprocess.SubprocessError, ValueError):
                    result["status"] = "partial"
                    result["warnings"].append("PAGE_PREVIEW_FAILED")
                    result["capabilities"]["visuals"] = "incomplete"
                if ocr and re.search(r"\[ocr\].*failed|Failed loading language|Tesseract couldn't load",
                                     execution.stderr.decode("utf-8", "replace"), re.I):
                    result["status"] = "partial"
                    result["warnings"].append("OCR_FAILED")
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
