"""Bounded local Docling sidecar worker.

The worker is deliberately separate from the public extraction process.  It
accepts only an explicitly hashed local model manifest, disables Docling's
remote/plugin switches, and returns the same schema-2 shape as the other
extractors.  It is an offline guard, not an operating-system sandbox.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .artifacts import add_asset, table_html
else:
    from artifacts import add_asset, table_html


PROFILE = "docling-2.126.0:local-v1"
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
DOCUMENT_TIMEOUT_SECONDS = 120.0
SUBPROCESS_TIMEOUT_SECONDS = 180.0
DEFAULT_OCR_SCALE = 3.0
MAX_OCR_SCALE = 8.0
MODEL_MANIFEST_SCHEMA = 1
LAYOUT_PRESET = "layout_heron_default"
LAYOUT_MODEL_ID = "layout_heron"
LAYOUT_REPO_ID = "docling-project/docling-layout-heron"
LAYOUT_REVISION = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
TABLE_MODEL_ID = "tableformer_accurate_v1"
TABLE_REPO_ID = "docling-project/docling-models"
TABLE_VERSION = "v2.3.0"
TABLE_REVISION = "fc0f2d45e2218ea24bce5045f58a389aed16dc23"
MODEL_SUBTREES = {
    "layout": PurePosixPath(LAYOUT_REPO_ID.replace("/", "--")),
    "table": PurePosixPath(TABLE_REPO_ID.replace("/", "--")),
}
MODEL_CACHE_EXCLUDED_PARTS = {".cache"}
TESSERACT_COMMAND = "tesseract"
TESSERACT_PROBE_TIMEOUT_SECONDS = 5.0

# These are the components for which this public adapter is version-bound.
# Torch is checked at the 2.14 line because CPU and CUDA wheels have different
# local version suffixes; the selected value is recorded in extraction.json.
PINNED_DISTRIBUTIONS = {
    "docling-slim": "2.126.0",
    "docling-core": "2.95.0",
    "docling-ibm-models": "4.0.2",
    "docling-parse": "7.17.0",
    "transformers": "5.16.1",
    "opencv-python-headless": "5.0.0.93",
}

OFFLINE_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "TOKENIZERS_PARALLELISM": "false",
}

_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_EXPORT_PAGE = re.compile(r"page_(\d{6})_[a-f0-9]+\.png\Z")
_EXPORT_PICTURE = re.compile(r"image_(\d{6})_[a-f0-9]+\.png\Z")


class DoclingError(Exception):
    """An outward-safe worker failure code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _safe_path(value: Path | str, *, strict: bool = False) -> Path:
    """Reject symlinks/reparse points before resolving a local path."""
    path = Path(os.path.abspath(os.fspath(value)))
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    try:
        return path.resolve(strict=strict)
    except OSError as exc:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST") from exc


def _relative_model_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    return relative.as_posix()


def _actual_model_files(root: Path, role: str) -> dict[str, Path]:
    subtree = root / MODEL_SUBTREES[role]
    if not subtree.exists():
        raise DoclingError("DOCLING_MODEL_PATH_MISSING")
    try:
        subtree = _safe_path(subtree, strict=True)
    except DoclingError as exc:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST") from exc
    if not subtree.is_dir() or not subtree.is_relative_to(root):
        raise DoclingError("DOCLING_MODEL_PATH_MISSING")
    actual = {}
    try:
        candidates = subtree.rglob("*")
        for candidate in candidates:
            relative_subtree = candidate.relative_to(subtree)
            if MODEL_CACHE_EXCLUDED_PARTS.intersection(relative_subtree.parts):
                continue
            info = candidate.lstat()
            if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
            if candidate.is_file():
                actual[candidate.relative_to(root).as_posix()] = candidate
            elif not candidate.is_dir():
                raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    except DoclingError:
        raise
    except (OSError, ValueError) as exc:
        raise DoclingError("DOCLING_MODEL_PATH_MISSING") from exc
    if not actual:
        raise DoclingError("DOCLING_MODEL_PATH_MISSING")
    return actual


def _validate_model_entry(role: str, entry: Any, root: Path) -> dict:
    if not isinstance(entry, dict) or not isinstance(entry.get("files"), list) or not entry["files"]:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    expected = {
        "layout": {
            "id": LAYOUT_PRESET,
            "repo_id": LAYOUT_REPO_ID,
            "revision": LAYOUT_REVISION,
        },
        "table": {
            "id": TABLE_MODEL_ID,
            "repo_id": TABLE_REPO_ID,
            "version": TABLE_VERSION,
            "revision": TABLE_REVISION,
        },
    }[role]
    if set(entry) != set(expected) | {"files"}:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    for key, value in expected.items():
        if entry.get(key) != value:
            raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")

    actual_files = _actual_model_files(root, role)
    subtree = MODEL_SUBTREES[role].as_posix()
    files = []
    seen = set()
    for item in entry["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
        relative = _relative_model_path(item["path"])
        if relative in seen or not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"]):
            raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
        if type(item["bytes"]) is not int or item["bytes"] < 0:
            raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
        if not relative.startswith(subtree + "/"):
            raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
        if relative not in actual_files:
            raise DoclingError("DOCLING_MODEL_PATH_MISSING")
        seen.add(relative)
        path = actual_files[relative]
        try:
            actual_size, actual_hash = _hash_file(path)
        except OSError as exc:
            raise DoclingError("DOCLING_MODEL_PATH_MISSING") from exc
        if actual_size != item["bytes"]:
            raise DoclingError("DOCLING_MODEL_HASH_MISMATCH")
        if actual_hash != item["sha256"]:
            raise DoclingError("DOCLING_MODEL_HASH_MISMATCH")
        files.append({"path": relative, "sha256": item["sha256"], "bytes": item["bytes"]})
    if seen != set(actual_files):
        raise DoclingError("DOCLING_MODEL_MANIFEST_INCOMPLETE")
    return {
        **{key: entry[key] for key in expected},
        "subtree": subtree,
        "files": files,
    }


def validate_model_manifest(path_value: Path | str) -> dict:
    """Validate model paths and bytes before any optional dependency import."""
    if path_value is None:
        raise DoclingError("DOCLING_MODEL_MANIFEST_REQUIRED")
    try:
        path = _safe_path(path_value)
    except (OSError, TypeError, ValueError) as exc:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST") from exc
    if not path.is_file():
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except DoclingError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST") from exc
    if (not isinstance(document, dict) or document.get("schema_version") != MODEL_MANIFEST_SCHEMA or
            set(document) != {"schema_version", "artifacts_path", "models"}):
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    artifacts_value = document.get("artifacts_path")
    if not isinstance(artifacts_value, str) or not Path(artifacts_value).is_absolute():
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    try:
        root = _safe_path(artifacts_value, strict=True)
    except DoclingError:
        raise DoclingError("DOCLING_MODEL_PATH_MISSING")
    if not root.is_dir():
        raise DoclingError("DOCLING_MODEL_PATH_MISSING")
    models = document.get("models")
    if not isinstance(models, dict) or set(models) != {"layout", "table"}:
        raise DoclingError("DOCLING_INVALID_MODEL_MANIFEST")
    validated = {role: _validate_model_entry(role, models[role], root) for role in ("layout", "table")}
    return {
        "schema_version": MODEL_MANIFEST_SCHEMA,
        "artifacts_path": root,
        "models": validated,
        "manifest_sha256": _sha256(raw),
    }


def model_manifest_summary(manifest: dict) -> dict:
    """Return model identity and hashes without the private root path."""
    return {
        "schema_version": manifest["schema_version"],
        "manifest_sha256": manifest["manifest_sha256"],
        "models": {
            role: {
                key: value
                for key, value in entry.items()
                if key != "files"
            } | {"files": list(entry["files"])}
            for role, entry in manifest["models"].items()
        },
    }


def validate_ocr_engine_metadata(value: Any, languages: list[str]) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "kind", "executable", "version", "binary_sha256", "traineddata"
    }:
        return False
    if value["kind"] != "tesseract-cli":
        return False
    if (not isinstance(value["executable"], str) or
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value["executable"])):
        return False
    if (not isinstance(value["version"], str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}", value["version"])):
        return False
    if not isinstance(value["binary_sha256"], str) or not _SHA256.fullmatch(value["binary_sha256"]):
        return False
    expected_languages = list(dict.fromkeys(languages))
    traineddata = value["traineddata"]
    if not isinstance(traineddata, dict) or set(traineddata) != set(expected_languages):
        return False
    for language in expected_languages:
        item = traineddata.get(language)
        if (not isinstance(item, dict) or set(item) != {"sha256", "bytes"} or
                not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"]) or
                type(item["bytes"]) is not int or item["bytes"] < 0):
            return False
    return True


def request_settings(*, ocr: bool, ocr_language: str, device: str, ocr_scale: float,
                     manifest_summary: dict | None = None,
                     ocr_engine: dict | None = None) -> dict:
    settings = {
        "adapter_revision": 1,
        "profile": PROFILE,
        "ocr": ocr,
        "ocr_language": ocr_language if ocr else None,
        "ocr_scale": ocr_scale if ocr else None,
        "ocr_engine": ocr_engine if ocr else None,
        "device": device,
        "batch_size": 1,
        "num_threads": 2,
        "layout": {
            "preset": LAYOUT_PRESET,
            "model_id": LAYOUT_MODEL_ID,
            "repo_id": LAYOUT_REPO_ID,
            "revision": LAYOUT_REVISION,
        },
        "table": {
            "mode": "accurate",
            "profile_version": "v1",
            "model_id": TABLE_MODEL_ID,
            "repo_id": TABLE_REPO_ID,
            "version": TABLE_VERSION,
            "revision": TABLE_REVISION,
        },
        "offline": {
            "environment": dict(OFFLINE_ENVIRONMENT),
            "python_socket_guard": "enabled",
            "os_sandbox": "not_provided",
        },
    }
    if manifest_summary is not None:
        settings["model_manifest"] = manifest_summary
    return settings


def result_base(*, ocr: bool, ocr_language: str, device: str, ocr_scale: float,
                manifest_summary: dict | None = None) -> dict:
    return {
        "schema_version": 2,
        "method": PROFILE,
        "status": "failed",
        "pages": [],
        "warnings": [],
        "assets": {},
        "capabilities": {
            "text": "layout_aware_unverified",
            "visuals": "page_previews_and_pictures",
            "tables": "native_cells_spans_html_unverified",
            "ocr": "requested_unverified" if ocr else "disabled",
            "page_basis": "printed_pages",
            "raw": "docling_export_json",
        },
        "settings": request_settings(ocr=ocr, ocr_language=ocr_language, device=device,
                                      ocr_scale=ocr_scale, manifest_summary=manifest_summary),
    }


def failure_result(code: str, *, ocr: bool, ocr_language: str, device: str, ocr_scale: float,
                   manifest_summary: dict | None = None) -> dict:
    result = result_base(ocr=ocr, ocr_language=ocr_language, device=device, ocr_scale=ocr_scale,
                         manifest_summary=manifest_summary)
    result["status"] = "unsupported" if code in {
        "DOCLING_DEPENDENCY_MISSING", "DOCLING_DEPENDENCY_VERSION_UNVERIFIED",
        "DOCLING_MODEL_MANIFEST_REQUIRED", "DOCLING_INVALID_MODEL_MANIFEST",
        "DOCLING_MODEL_PATH_MISSING", "DOCLING_MODEL_HASH_MISMATCH",
        "DOCLING_MODEL_MANIFEST_INCOMPLETE", "DOCLING_PROFILE_UNSUPPORTED",
        "DOCLING_TESSERACT_MISSING", "DOCLING_TESSERACT_UNAVAILABLE",
        "DOCLING_TRAINEDDATA_MISSING",
    } else "failed"
    result["warnings"] = [code]
    return result


def validate_request(*, suffix: str, ocr_language: str, device: str, ocr_scale: float) -> str | None:
    if suffix not in SUPPORTED_SUFFIXES:
        return "DOCLING_PROFILE_UNSUPPORTED"
    if not isinstance(ocr_language, str) or not re.fullmatch(r"[a-z0-9_]+(?:\+[a-z0-9_]+)*", ocr_language):
        return "INVALID_OCR_LANGUAGE"
    if device not in {"cpu", "cuda"}:
        return "INVALID_DOCLING_DEVICE"
    if (type(ocr_scale) not in {int, float} or not math.isfinite(float(ocr_scale)) or
            not 0.1 <= float(ocr_scale) <= MAX_OCR_SCALE):
        return "INVALID_DOCLING_OCR_SCALE"
    return None


def _set_offline_environment() -> None:
    for key, value in OFFLINE_ENVIRONMENT.items():
        os.environ[key] = value


def install_socket_audit() -> dict:
    """Block common Python network paths; this is not an OS-level sandbox."""
    original_socket = socket.socket

    class OfflineSocket(original_socket):
        def connect(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("network disabled")

        def connect_ex(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("network disabled")

        def sendto(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("network disabled")

        def send(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("network disabled")

        def sendall(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("network disabled")

    def blocked(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("network disabled")

    socket.socket = OfflineSocket
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    socket.gethostbyname = blocked
    socket.gethostbyname_ex = blocked
    socket.getnameinfo = blocked
    return {
        "environment": dict(OFFLINE_ENVIRONMENT),
        "python_socket_guard": "enabled",
        "os_sandbox": "not_provided",
    }


def _load_runtime() -> dict:
    try:
        import cv2  # noqa: F401  # required by the selected slim profile
        import torch
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            LayoutObjectDetectionOptions,
            PdfPipelineOptions,
            TableFormerMode,
            TableStructureOptions,
            TesseractCliOcrOptions,
        )
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
        from docling_core.types.doc import ImageRefMode
    except (ImportError, ModuleNotFoundError) as exc:
        raise DoclingError("DOCLING_DEPENDENCY_MISSING") from exc

    versions = {}
    for name, expected in PINNED_DISTRIBUTIONS.items():
        try:
            actual = package_version(name)
        except PackageNotFoundError as exc:
            raise DoclingError("DOCLING_DEPENDENCY_MISSING") from exc
        if actual != expected:
            raise DoclingError("DOCLING_DEPENDENCY_VERSION_UNVERIFIED")
        versions[name] = actual
    try:
        torch_version = package_version("torch")
    except PackageNotFoundError as exc:
        raise DoclingError("DOCLING_DEPENDENCY_MISSING") from exc
    if not torch_version.startswith("2.14."):
        raise DoclingError("DOCLING_DEPENDENCY_VERSION_UNVERIFIED")
    versions["torch"] = torch_version
    versions["cv2"] = getattr(cv2, "__version__", "unknown")
    return {
        "versions": versions,
        "AcceleratorOptions": AcceleratorOptions,
        "InputFormat": InputFormat,
        "LayoutObjectDetectionOptions": LayoutObjectDetectionOptions,
        "PdfPipelineOptions": PdfPipelineOptions,
        "TableFormerMode": TableFormerMode,
        "TableStructureOptions": TableStructureOptions,
        "TesseractCliOcrOptions": TesseractCliOcrOptions,
        "DocumentConverter": DocumentConverter,
        "ImageFormatOption": ImageFormatOption,
        "PdfFormatOption": PdfFormatOption,
        "ImageRefMode": ImageRefMode,
    }


def _tesseract_runtime(languages: list[str]) -> dict:
    command_value = shutil.which(TESSERACT_COMMAND)
    if not command_value:
        raise DoclingError("DOCLING_TESSERACT_MISSING")
    try:
        command = Path(command_value).resolve(strict=True)
    except OSError as exc:
        raise DoclingError("DOCLING_TESSERACT_MISSING") from exc
    if not command.is_file():
        raise DoclingError("DOCLING_TESSERACT_MISSING")
    try:
        _binary_bytes, binary_sha256 = _hash_file(command)
    except OSError as exc:
        raise DoclingError("DOCLING_TESSERACT_MISSING") from exc

    environment = {**os.environ, **OFFLINE_ENVIRONMENT}
    try:
        version_run = subprocess.run(
            [str(command), "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TESSERACT_PROBE_TIMEOUT_SECONDS,
            check=False, env=environment,
        )
        languages_run = subprocess.run(
            [str(command), "--list-langs"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TESSERACT_PROBE_TIMEOUT_SECONDS,
            check=False, env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DoclingError("DOCLING_TESSERACT_UNAVAILABLE") from exc
    if version_run.returncode != 0 or languages_run.returncode != 0:
        raise DoclingError("DOCLING_TESSERACT_UNAVAILABLE")
    version_match = re.search(r"^\s*tesseract\s+([^\s]+)", version_run.stdout, re.I | re.M)
    if not version_match:
        raise DoclingError("DOCLING_TESSERACT_UNAVAILABLE")
    data_dirs = []
    data_match = re.search(r'available languages in "([^"]+)"', languages_run.stdout, re.I)
    if data_match:
        data_dirs.append(Path(data_match[1]))
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix:
        data_dirs.extend([Path(prefix), Path(prefix) / "tessdata"])
    tessdata = None
    for candidate in data_dirs:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and all((resolved / f"{language}.traineddata").is_file()
                                     for language in dict.fromkeys(languages)):
            tessdata = resolved
            break
    if tessdata is None:
        raise DoclingError("DOCLING_TRAINEDDATA_MISSING")
    traineddata = {}
    for language in dict.fromkeys(languages):
        path = tessdata / f"{language}.traineddata"
        try:
            size, sha256 = _hash_file(path)
        except OSError as exc:
            raise DoclingError("DOCLING_TRAINEDDATA_MISSING") from exc
        traineddata[language] = {"sha256": sha256, "bytes": size}
    metadata = {
        "kind": "tesseract-cli",
        "executable": command.name,
        "version": version_match[1],
        "binary_sha256": binary_sha256,
        "traineddata": traineddata,
    }
    if not validate_ocr_engine_metadata(metadata, languages):
        raise DoclingError("DOCLING_TESSERACT_UNAVAILABLE")
    return {"metadata": metadata, "command": str(command), "path": str(tessdata)}


def _converter(runtime: dict, manifest: dict, *, ocr: bool, ocr_language: str,
               device: str, ocr_scale: float, tesseract: dict | None = None):
    layout = runtime["LayoutObjectDetectionOptions"].from_preset(LAYOUT_PRESET)
    layout = layout.model_copy(update={
        "model_spec": layout.model_spec.model_copy(update={"revision": LAYOUT_REVISION})
    })
    table = runtime["TableStructureOptions"](
        do_cell_matching=True,
        mode=runtime["TableFormerMode"].ACCURATE,
    )
    ocr_kwargs = {
        "lang": ocr_language.split("+") if ocr else [],
        "scale": float(ocr_scale),
        "tesseract_cmd": tesseract["command"] if tesseract else TESSERACT_COMMAND,
        "path": tesseract["path"] if tesseract else None,
    }
    ocr_options = runtime["TesseractCliOcrOptions"](**ocr_kwargs)
    options = runtime["PdfPipelineOptions"](
        document_timeout=DOCUMENT_TIMEOUT_SECONDS,
        accelerator_options=runtime["AcceleratorOptions"](num_threads=2, device=device),
        enable_remote_services=False,
        allow_external_plugins=False,
        artifacts_path=manifest["artifacts_path"],
        do_picture_classification=False,
        do_picture_description=False,
        do_chart_extraction=False,
        images_scale=1.0,
        generate_page_images=True,
        generate_picture_images=True,
        do_table_structure=True,
        do_ocr=ocr,
        ocr_options=ocr_options,
        layout_options=layout,
        table_structure_options=table,
        do_code_enrichment=False,
        do_formula_enrichment=False,
        generate_table_images=False,
        generate_parsed_pages=False,
        ocr_batch_size=1,
        layout_batch_size=1,
        table_batch_size=1,
        queue_max_size=1,
    )
    format_options = {
        runtime["InputFormat"].PDF: runtime["PdfFormatOption"](pipeline_options=options),
        runtime["InputFormat"].IMAGE: runtime["ImageFormatOption"](pipeline_options=options),
    }
    converter = runtime["DocumentConverter"](
        allowed_formats=[runtime["InputFormat"].PDF, runtime["InputFormat"].IMAGE],
        format_options=format_options,
    )
    return converter, options


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _model_json(item: Any) -> Any:
    if item is None:
        return None
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=True)
    if hasattr(item, "dict"):
        return item.dict()
    if isinstance(item, dict):
        return item
    return item


def _bbox_json(bbox: Any) -> dict | None:
    if bbox is None:
        return None
    dumped = _model_json(bbox)
    if not isinstance(dumped, dict):
        raise DoclingError("DOCLING_INVALID_PROVENANCE")
    required = {"l", "t", "r", "b"}
    if not required <= set(dumped):
        raise DoclingError("DOCLING_INVALID_PROVENANCE")
    return {key: dumped[key] for key in ("l", "t", "r", "b", "coord_origin") if key in dumped}


def _provenance(items: Any, expected_pages: set[int] | None = None) -> list[dict]:
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        raise DoclingError("DOCLING_INVALID_PROVENANCE")
    output = []
    for item in items:
        page_no = _value(item, "page_no")
        bbox = _bbox_json(_value(item, "bbox"))
        charspan = _value(item, "charspan")
        if (type(page_no) is not int or page_no < 1 or
                (expected_pages is not None and page_no not in expected_pages) or
                not isinstance(charspan, (list, tuple)) or len(charspan) != 2 or
                any(type(value) is not int or value < 0 for value in charspan) or bbox is None):
            raise DoclingError("DOCLING_INVALID_PROVENANCE")
        output.append({"page": page_no, "bbox": bbox, "charspan": list(charspan)})
    return output


def _page_count(conversion: Any, suffix: str) -> int:
    if suffix in {".png", ".jpg", ".jpeg"}:
        return 1
    input_document = _value(conversion, "input")
    count = _value(input_document, "page_count")
    if type(count) is not int or count < 1:
        raise DoclingError("DOCLING_PAGE_COUNT_UNAVAILABLE")
    return count


def _page_objects(conversion: Any, expected: int) -> dict[int, Any]:
    pages = _value(conversion, "pages")
    if not isinstance(pages, (list, tuple)):
        raise DoclingError("DOCLING_PARTIAL_PAGE_COVERAGE")
    output = {}
    for page in pages:
        number = _value(page, "page_no")
        if type(number) is not int or number in output:
            raise DoclingError("DOCLING_PARTIAL_PAGE_COVERAGE")
        output[number] = page
    expected_pages = set(range(1, expected + 1))
    if set(output) != expected_pages:
        raise DoclingError("DOCLING_PARTIAL_PAGE_COVERAGE")
    return output


def _page_size(page: Any) -> tuple[Any, Any]:
    size = _value(page, "size")
    return _value(size, "width"), _value(size, "height")


def _item_texts(document: Any, page_no: int, expected_pages: set[int]) -> list[dict]:
    iterator = getattr(document, "iterate_items", None)
    if iterator is None:
        return []
    try:
        items = iterator(page_no=page_no, with_groups=False, traverse_pictures=False)
    except TypeError:
        items = iterator(page_no=page_no)
    output = []
    for item, _level in items:
        text = _value(item, "text")
        if not isinstance(text, str):
            continue
        provenance = _provenance(_value(item, "prov", []), expected_pages)
        if provenance and not any(item["page"] == page_no for item in provenance):
            continue
        label = _value(item, "label")
        label = _value(label, "value", label)
        output.append({
            "text": text,
            "label": label,
            "provenance": provenance,
        })
    return output


def _table_info(table: Any, index: int, expected_pages: set[int]) -> tuple[dict, str, set[int]]:
    provenance = _provenance(_value(table, "prov", []), expected_pages)
    if not provenance:
        raise DoclingError("DOCLING_TABLE_PROVENANCE_MISSING")
    data = _value(table, "data")
    rows, cols = _value(data, "num_rows"), _value(data, "num_cols")
    cells_source = _value(data, "table_cells")
    if (type(rows) is not int or type(cols) is not int or rows < 0 or cols < 0 or
            not isinstance(cells_source, (list, tuple))):
        raise DoclingError("DOCLING_INVALID_TABLE_SPAN")
    cells = []
    for cell in cells_source:
        row = _value(cell, "start_row_offset_idx")
        col = _value(cell, "start_col_offset_idx")
        rowspan = _value(cell, "row_span")
        colspan = _value(cell, "col_span")
        text = _value(cell, "text")
        if (type(row) is not int or type(col) is not int or type(rowspan) is not int or
                type(colspan) is not int or not isinstance(text, str)):
            raise DoclingError("DOCLING_INVALID_TABLE_SPAN")
        item = {
            "row": row,
            "col": col,
            "rowspan": rowspan,
            "colspan": colspan,
            "text": text,
            "header": bool(_value(cell, "column_header", False) or
                            _value(cell, "row_header", False) or
                            _value(cell, "row_section", False)),
        }
        bbox = _bbox_json(_value(cell, "bbox"))
        if bbox is not None:
            item["bbox"] = bbox
        cells.append(item)
    try:
        markup = table_html(cells, rows, cols)
    except (ValueError, TypeError, KeyError) as exc:
        raise DoclingError("DOCLING_INVALID_TABLE_SPAN") from exc
    page_numbers = {item["page"] for item in provenance}
    info = {
        "id": f"table-{index}",
        "locator": {"table_index": index, "pages": sorted(page_numbers)},
        "rows": rows,
        "cols": cols,
        "cells": cells,
        "provenance": provenance,
    }
    return info, markup, page_numbers


def _table_content_missing(info: dict) -> bool:
    return info.get("rows") == 0 and info.get("cols") == 0 and info.get("cells") == []


def _image_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if value is None or not hasattr(value, "save"):
        raise DoclingError("DOCLING_PAGE_PREVIEW_MISSING")
    stream = io.BytesIO()
    try:
        value.save(stream, format="PNG")
    except (OSError, ValueError) as exc:
        raise DoclingError("DOCLING_PAGE_PREVIEW_MISSING") from exc
    return stream.getvalue()


def _valid_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def _replace_export_refs(value: Any, mapping: dict[str, str], source: Path, output: Path) -> Any:
    if isinstance(value, list):
        return [_replace_export_refs(item, mapping, source, output) for item in value]
    if isinstance(value, dict):
        return {key: _replace_export_refs(item, mapping, source, output) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    normalized = value.replace("\\", "/")
    normalized = normalized[2:] if normalized.startswith("./") else normalized
    if normalized in mapping:
        return mapping[normalized]
    source_strings = {
        str(source).replace("\\", "/"),
        source.as_posix(),
        source.as_uri().replace("\\", "/"),
    }
    output_prefixes = {str(output).replace("\\", "/").rstrip("/") + "/"}
    if normalized in source_strings:
        return source.name
    if any(normalized.startswith(prefix) for prefix in output_prefixes):
        return Path(normalized).name
    return value


def _pipeline_markers(conversion: Any, logs: str) -> list[str]:
    messages = []
    for error in _value(conversion, "errors", []) or []:
        for key in ("error_message", "module_name", "category"):
            item = _value(error, key)
            if item is not None:
                messages.append(str(_value(item, "value", item)))
    text = "\n".join(messages + [logs])
    markers = []
    if re.search(r"\bosd\b|orientation\s+and\s+script|script\s+detection", text, re.I):
        markers.append("DOCLING_PIPELINE_OSD")
    if re.search(
            r"table[\s_-]*fallback|fallback[\s_-]*table|"
            r"orphan[\s_-]*cells?[^\n]*(?:fallback|nearest[\s-]*row)|"
            r"(?:fallback|nearest[\s-]*row)[^\n]*orphan[\s_-]*cells?",
            text, re.I):
        markers.append("DOCLING_PIPELINE_TABLEFALLBACK")
    if _value(conversion, "errors", []) or (text and re.search(r"\b(error|warning|failed)\b", text, re.I)):
        markers.append("DOCLING_PIPELINE_WARNING")
    return sorted(set(markers))


@contextmanager
def _capture_pipeline_output():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    if previous_level > logging.INFO:
        root.setLevel(logging.INFO)
    try:
        with redirect_stdout(stream), redirect_stderr(stream):
            yield stream
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous_level)


def _status_value(status: Any) -> str:
    value = _value(status, "value", status)
    return str(value).rsplit(".", 1)[-1].lower()


def _export_markdown(document: Any, page_no: int, image_mode: Any) -> str:
    try:
        text = document.export_to_markdown(page_no=page_no, image_mode=image_mode,
                                           traverse_pictures=False, include_annotations=False)
    except TypeError:
        text = document.export_to_markdown(page_no=page_no)
    return text if isinstance(text, str) else ""


def _export_text(document: Any, page_no: int) -> str:
    try:
        text = document.export_to_text(page_no=page_no, traverse_pictures=False)
    except TypeError:
        text = document.export_to_text(page_no=page_no)
    return text if isinstance(text, str) else ""


def _cleanup_export(output: Path) -> None:
    try:
        (output / "docling-export.json").unlink(missing_ok=True)
        shutil.rmtree(output / "assets", ignore_errors=True)
    except OSError:
        pass


def convert(source_value: Path | str, output_value: Path | str, manifest_value: Path | str,
            *, ocr: bool = False, ocr_language: str = "eng", device: str = "cpu",
            ocr_scale: float = DEFAULT_OCR_SCALE) -> dict:
    """Run the bounded conversion and return an in-memory schema-2 result."""
    source = _safe_path(source_value, strict=True)
    output = _safe_path(output_value)
    output.mkdir(parents=True, exist_ok=True)
    output = _safe_path(output, strict=True)
    error = validate_request(suffix=source.suffix.lower(), ocr_language=ocr_language,
                             device=device, ocr_scale=ocr_scale)
    if error:
        raise DoclingError(error)
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise DoclingError("DOCLING_INPUT_TOO_LARGE")
    manifest = validate_model_manifest(manifest_value)
    summary = model_manifest_summary(manifest)
    result = result_base(ocr=ocr, ocr_language=ocr_language, device=device, ocr_scale=ocr_scale,
                         manifest_summary=summary)
    _set_offline_environment()
    network_audit = install_socket_audit()
    runtime = _load_runtime()
    result["settings"]["offline"] = network_audit
    result["dependencies"] = runtime["versions"]
    tesseract = _tesseract_runtime(ocr_language.split("+")) if ocr else None
    if tesseract is not None:
        result["settings"]["ocr_engine"] = tesseract["metadata"]

    logs = None
    try:
        with _capture_pipeline_output() as logs:
            converter, _options = _converter(
                runtime, manifest, ocr=ocr, ocr_language=ocr_language,
                device=device, ocr_scale=ocr_scale, tesseract=tesseract,
            )
            conversion = converter.convert(source, raises_on_error=False,
                                           max_num_pages=sys.maxsize, max_file_size=MAX_INPUT_BYTES)
        result["warnings"].extend(_pipeline_markers(conversion, logs.getvalue()))
        if _status_value(_value(conversion, "status", "success")) != "success":
            raise DoclingError("DOCLING_CONVERSION_FAILED")
        if _value(conversion, "errors", []):
            raise DoclingError("DOCLING_CONVERSION_PARTIAL")
        expected = _page_count(conversion, source.suffix.lower())
        page_objects = _page_objects(conversion, expected)
        document = _value(conversion, "document")
        if document is None:
            raise DoclingError("DOCLING_CONVERSION_FAILED")
        document_pages = _value(document, "pages", {})
        if isinstance(document_pages, dict) and document_pages:
            try:
                document_numbers = {int(number) for number in document_pages}
            except (TypeError, ValueError) as exc:
                raise DoclingError("DOCLING_PARTIAL_PAGE_COVERAGE") from exc
            if document_numbers != set(range(1, expected + 1)):
                raise DoclingError("DOCLING_PARTIAL_PAGE_COVERAGE")
        expected_pages = set(range(1, expected + 1))
        pages = {
            number: {
                "number": number,
                "text": _export_text(document, number),
                "text_items": _item_texts(document, number, expected_pages),
                "images": [],
                "tables": [],
                "locator": {"page": number},
                "coordinate_system": "docling_native",
            }
            for number in expected_pages
        }
        for number, page in page_objects.items():
            width, height = _page_size(page)
            pages[number]["width"] = width
            pages[number]["height"] = height

        tables = []
        missing_table_content = False
        for index, table in enumerate(_value(document, "tables", []) or [], 1):
            info, markup, page_numbers = _table_info(table, index, expected_pages)
            missing_table_content = missing_table_content or _table_content_missing(info)
            table_asset = add_asset(result, markup.encode("utf-8"), "html", "text/html", "structured_table")
            info["asset"] = table_asset
            tables.append((info, markup, page_numbers))
            for number in page_numbers:
                pages[number]["tables"].append(info.copy())

        export_json = output / "docling-export.json"
        # Path('assets') is intentional: Docling then emits relative refs rather
        # than absolute worker paths.  The refs are rewritten to our hashes below.
        document.save_as_json(export_json, artifacts_dir=Path("assets"),
                              image_mode=runtime["ImageRefMode"].REFERENCED,
                              ensure_ascii=False, sort_keys=True)
        export_assets = output / "assets"
        generated = {item.name: item for item in export_assets.iterdir() if item.is_file()}
        page_files, picture_files = {}, []
        for name, path in generated.items():
            page_match, picture_match = _EXPORT_PAGE.fullmatch(name), _EXPORT_PICTURE.fullmatch(name)
            if page_match:
                page_files[int(page_match[1])] = path
            elif picture_match:
                picture_files.append((int(picture_match[1]), path))
            else:
                raise DoclingError("DOCLING_UNEXPECTED_EXPORT_ASSET")
        page_ref_map = {}
        if set(page_files) != expected_pages:
            if source.suffix.lower() in {".png", ".jpg", ".jpeg"} and not page_files:
                original = source.read_bytes()
                if source.suffix.lower() == ".png" and _valid_png(original):
                    page_asset = add_asset(result, original, "png", "image/png", "original_image")
                elif source.suffix.lower() in {".jpg", ".jpeg"} and original.startswith(b"\xff\xd8\xff"):
                    page_asset = add_asset(result, original, "jpg", "image/jpeg", "original_image")
                else:
                    raise DoclingError("DOCLING_PAGE_PREVIEW_MISSING")
                pages[1]["preview"] = page_asset
            else:
                raise DoclingError("DOCLING_PAGE_PREVIEW_MISSING")
        else:
            for number in sorted(page_files):
                data = page_files[number].read_bytes()
                if not _valid_png(data):
                    raise DoclingError("DOCLING_PAGE_PREVIEW_MISSING")
                page_asset = add_asset(result, data, "png", "image/png", "page_preview")
                pages[number]["preview"] = page_asset
                page_ref_map[f"assets/{page_files[number].name}"] = page_asset

        pictures = list(_value(document, "pictures", []) or [])
        picture_files.sort(key=lambda item: item[0])
        if len(picture_files) != len(pictures):
            raise DoclingError("DOCLING_PICTURE_ASSET_MISSING")
        picture_ref_map = {}
        for picture, (_ordinal, path) in zip(pictures, picture_files):
            data = path.read_bytes()
            if not _valid_png(data):
                raise DoclingError("DOCLING_PICTURE_ASSET_MISSING")
            picture_asset = add_asset(result, data, "png", "image/png", "extracted_picture")
            picture_ref_map[f"assets/{path.name}"] = picture_asset
            provenance = _provenance(_value(picture, "prov", []), expected_pages)
            if not provenance:
                raise DoclingError("DOCLING_PICTURE_PROVENANCE_MISSING")
            for item in provenance:
                if picture_asset not in pages[item["page"]]["images"]:
                    pages[item["page"]]["images"].append(picture_asset)

        raw = json.loads(export_json.read_text(encoding="utf-8"))
        references = {**page_ref_map, **picture_ref_map}
        raw = _replace_export_refs(raw, references, source, output)
        result["raw_asset"] = add_asset(
            result,
            json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8"),
            "json", "application/json", "raw_docling_output",
        )

        for number in sorted(pages):
            page = pages[number]
            markdown = _export_markdown(document, number, runtime["ImageRefMode"].PLACEHOLDER).strip()
            if not markdown:
                markdown = page["text"]
            if page["images"]:
                markdown += "\n\n" + "\n".join(
                    f"![Extracted picture {index}]({asset})"
                    for index, asset in enumerate(page["images"], 1)
                )
            for info, markup, page_numbers in tables:
                if number in page_numbers:
                    markdown += "\n\n" + markup
            page["markdown"] = markdown
        result["pages"] = [pages[number] for number in sorted(pages)]
        result["warnings"].extend(["DOCLING_OUTPUT_UNVERIFIED", "DOCLING_TABLE_STRUCTURE_UNVERIFIED"])
        if ocr:
            result["warnings"].append("DOCLING_OCR_UNVERIFIED")
        nonempty = sum(bool(page["text"].strip()) for page in result["pages"])
        if not nonempty:
            result["status"] = "needs_ocr" if not ocr else "partial"
            result["warnings"].append("NO_EXTRACTED_TEXT")
        elif nonempty != len(result["pages"]):
            result["status"] = "partial"
            result["warnings"].append("PAGES_WITHOUT_TEXT")
        else:
            # Docling conversion success does not qualify table/OCR fidelity
            # for publication. Keep the complete candidate explicitly partial.
            result["status"] = "partial"
            result["warnings"].append("DOCLING_FIDELITY_REVIEW_REQUIRED")
        if missing_table_content:
            result["status"] = "partial"
            result["warnings"].append("DOCLING_TABLE_CONTENT_MISSING")
        result["warnings"] = sorted(set(result["warnings"]))
        return result
    finally:
        _cleanup_export(output)


def _write_result(output: Path, result: dict) -> None:
    blobs = result.pop("_asset_bytes", {})
    for name, data in blobs.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Offline local Docling worker")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ocr-language", default="eng")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--ocr-scale", type=float, default=DEFAULT_OCR_SCALE)
    args = parser.parse_args(argv)
    output = Path(os.path.abspath(args.output))
    output.mkdir(parents=True, exist_ok=True)
    summary = None
    try:
        summary = model_manifest_summary(validate_model_manifest(args.model_manifest))
        result = convert(args.source, output, args.model_manifest, ocr=args.ocr,
                         ocr_language=args.ocr_language, device=args.device, ocr_scale=args.ocr_scale)
    except DoclingError as exc:
        result = failure_result(exc.code, ocr=args.ocr, ocr_language=args.ocr_language,
                                device=args.device, ocr_scale=args.ocr_scale,
                                manifest_summary=summary)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        result = failure_result("DOCLING_WORKER_FAILED", ocr=args.ocr, ocr_language=args.ocr_language,
                                device=args.device, ocr_scale=args.ocr_scale,
                                manifest_summary=summary)
    _write_result(output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
