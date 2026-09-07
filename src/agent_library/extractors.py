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
from .artifacts import asset_manifest
from .docling_worker import (
    DEFAULT_OCR_SCALE,
    OFFLINE_ENVIRONMENT,
    PROFILE,
    SUBPROCESS_TIMEOUT_SECONDS,
    DoclingError,
    failure_result,
    model_manifest_summary,
    request_settings,
    validate_ocr_engine_metadata,
    validate_model_manifest,
    validate_request,
)


def _extract_docling(data: bytes, suffix: str, *, ocr: bool, ocr_language: str,
                     model_manifest: Path | str | None, device: str,
                     ocr_scale: float) -> dict:
    error = validate_request(suffix=suffix, ocr_language=ocr_language,
                             device=device, ocr_scale=ocr_scale)
    if error:
        return failure_result(error, ocr=ocr, ocr_language=ocr_language, device=device,
                              ocr_scale=ocr_scale)
    if model_manifest is None:
        return failure_result("DOCLING_MODEL_MANIFEST_REQUIRED", ocr=ocr,
                              ocr_language=ocr_language, device=device, ocr_scale=ocr_scale)
    try:
        manifest = validate_model_manifest(model_manifest)
        summary = model_manifest_summary(manifest)
    except (DoclingError, OSError, ValueError, TypeError) as exc:
        if not isinstance(exc, DoclingError):
            return failure_result("DOCLING_INVALID_MODEL_MANIFEST", ocr=ocr,
                                  ocr_language=ocr_language, device=device, ocr_scale=ocr_scale)
        return failure_result(exc.code, ocr=ocr, ocr_language=ocr_language, device=device,
                              ocr_scale=ocr_scale)
    try:
        with tempfile.TemporaryDirectory(prefix="agent-library-docling-") as folder:
            base = Path(folder)
            source = base / ("document" + suffix)
            source.write_bytes(data)
            command = [sys.executable, str(Path(__file__).with_name("docling_worker.py")),
                       str(source), str(base), "--model-manifest", str(Path(model_manifest).resolve()),
                       "--ocr-language", ocr_language, "--device", device,
                       "--ocr-scale", str(ocr_scale)]
            if ocr:
                command.append("--ocr")
            environment = {**os.environ, **OFFLINE_ENVIRONMENT}
            try:
                execution = subprocess.run(command, capture_output=True,
                                           timeout=SUBPROCESS_TIMEOUT_SECONDS, check=False,
                                           env=environment)
            except subprocess.TimeoutExpired:
                return failure_result("DOCLING_TIMEOUT", ocr=ocr, ocr_language=ocr_language,
                                      device=device, ocr_scale=ocr_scale, manifest_summary=summary)
            except (OSError, subprocess.SubprocessError):
                return failure_result("DOCLING_WORKER_FAILED", ocr=ocr,
                                      ocr_language=ocr_language, device=device,
                                      ocr_scale=ocr_scale, manifest_summary=summary)
            result_path = base / "result.json"
            try:
                parsed = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, TypeError):
                return failure_result("DOCLING_WORKER_FAILED", ocr=ocr,
                                      ocr_language=ocr_language, device=device,
                                      ocr_scale=ocr_scale, manifest_summary=summary)
            if (not isinstance(parsed, dict) or parsed.get("schema_version") != 2 or
                    parsed.get("method") != PROFILE or
                    parsed.get("status") not in {"extracted", "partial", "needs_ocr", "empty", "failed", "unsupported"}):
                return failure_result("DOCLING_WORKER_FAILED", ocr=ocr,
                                      ocr_language=ocr_language, device=device,
                                      ocr_scale=ocr_scale, manifest_summary=summary)
            if getattr(execution, "returncode", 0) != 0:
                return failure_result("DOCLING_WORKER_FAILED", ocr=ocr,
                                      ocr_language=ocr_language, device=device,
                                      ocr_scale=ocr_scale, manifest_summary=summary)
            returned_settings = parsed.get("settings")
            returned_engine = None
            if (ocr and parsed.get("status") in {"extracted", "partial", "needs_ocr", "empty"}):
                if not isinstance(returned_settings, dict):
                    return failure_result("DOCLING_SETTINGS_MISMATCH", ocr=ocr,
                                          ocr_language=ocr_language, device=device,
                                          ocr_scale=ocr_scale, manifest_summary=summary)
                returned_engine = returned_settings.get("ocr_engine")
                if not validate_ocr_engine_metadata(returned_engine, ocr_language.split("+")):
                    return failure_result("DOCLING_SETTINGS_MISMATCH", ocr=ocr,
                                          ocr_language=ocr_language, device=device,
                                          ocr_scale=ocr_scale, manifest_summary=summary)
            expected_settings = request_settings(
                ocr=ocr, ocr_language=ocr_language, device=device, ocr_scale=ocr_scale,
                manifest_summary=summary, ocr_engine=returned_engine)
            if returned_settings != expected_settings:
                return failure_result("DOCLING_SETTINGS_MISMATCH", ocr=ocr,
                                      ocr_language=ocr_language, device=device,
                                      ocr_scale=ocr_scale, manifest_summary=summary)
            try:
                assets = asset_manifest(parsed)
                blobs = {}
                for name, item in assets.items():
                    blob = (base / name).read_bytes()
                    if len(blob) != item["bytes"] or _sha256(blob) != item["sha256"]:
                        raise ValueError("asset hash mismatch")
                    blobs[name] = blob
                parsed["_asset_bytes"] = blobs
                return parsed
            except (OSError, ValueError, TypeError, KeyError):
                return failure_result("DOCLING_WORKER_FAILED", ocr=ocr,
                                      ocr_language=ocr_language, device=device,
                                      ocr_scale=ocr_scale, manifest_summary=summary)
    except (OSError, ValueError, TypeError):
        return failure_result("DOCLING_WORKER_FAILED", ocr=ocr, ocr_language=ocr_language,
                              device=device, ocr_scale=ocr_scale, manifest_summary=summary)


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def extract(data: bytes, suffix: str, *, ocr: bool = False, ocr_language: str = "eng", parser="liteparse",
            docling_model_manifest: Path | str | None = None, docling_device: str = "cpu",
            docling_ocr_scale: float = DEFAULT_OCR_SCALE) -> dict:
    result = {"schema_version": 2, "method": "utf8", "status": "extracted", "pages": [], "warnings": [],
              "assets": {}, "capabilities": {"text": "decoded", "visuals": "not_applicable", "tables": "not_analyzed"}}
    if parser not in {"liteparse", "markitdown", "docling"}:
        return {**result, "status": "unsupported", "warnings": ["UNKNOWN_PARSER"]}
    if parser == "docling":
        return _extract_docling(data, suffix, ocr=ocr, ocr_language=ocr_language,
                                model_manifest=docling_model_manifest, device=docling_device,
                                ocr_scale=docling_ocr_scale)
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
                    if suffix in {".png", ".jpg", ".jpeg"}:
                        # The source already is the visual evidence; screenshot accepts PDFs only.
                        extension, media, signature = ("png", "image/png", b"\x89PNG\r\n\x1a\n") if suffix == ".png" else ("jpg", "image/jpeg", b"\xff\xd8\xff")
                        if len(result["pages"]) != 1 or not data.startswith(signature):
                            raise ValueError("Invalid single-frame image")
                        result["pages"][0]["preview"] = add_asset(result, data, extension, media, "original_image")
                        result["capabilities"]["visuals"] = "original_image"
                        result["settings"]["adapter_revision"] = 3
                        result["settings"]["preview_dpi"] = None
                    else:
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
