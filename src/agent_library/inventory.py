"""Read-only migration audit. Metadata locates evidence; it never proves full text."""
from collections import Counter, defaultdict
import re

from .core import LibraryError


SOURCE_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "xlsm", "ppt", "pptx",
                     "rtf", "jpg", "jpeg", "png", "tif", "tiff", "bmp"}
GENERATED_NAMES = {"_AI_MAP.md", "_AI_NAV.md", "FILE_INDEX.json", "FILE_INDEX_SUMMARY.md",
                   "index_build_status.json", "00_ARCHIVE_LEDGER.md", "archive_ledger.json"}


def inspect_sidecar(text):
    """Conservative content probe, not a parser-completeness or clinical review."""
    if not isinstance(text, str):
        raise LibraryError("INVENTORY_SCHEMA", "Sidecar must be text.")
    if not text.strip():
        status = "EMPTY"
    elif re.search(r"ocr_status:\s*needs_ocr|需 OCR 後補摘要", text):
        status = "OCR_REQUIRED"
    elif "未做完整摘要與章節抽取" in text or "lite sidecar" in text:
        status = "METADATA_ONLY"
    elif "## 摘要" in text and "## 原始檔" in text:
        status = "BRIEF_ONLY"
    else:
        status = "TEXT_UNVERIFIED"
    return {"status": status, "fulltext_verified": False}


def audit_inventory(snapshot, *, root_ids, archive_ids=()):
    """Audit a complete metadata snapshot; never propose deleting matching bytes.

    Contract: schema_version 1.0.0, meta.complete, meta.generated_at, and entries
    with file_id, parent_id, name, kind, size, and optional integrity hashes.
    Providers must complete pagination and independently establish the allowed root.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "1.0.0":
        raise LibraryError("INVENTORY_SCHEMA", "Unsupported inventory schema.")
    if not isinstance(snapshot.get("meta"), dict) or snapshot["meta"].get("complete") is not True:
        raise LibraryError("INCOMPLETE_INVENTORY", "A complete snapshot is required.")
    entries = snapshot.get("entries")
    if not isinstance(entries, list) or not root_ids:
        raise LibraryError("INVENTORY_SCHEMA", "Entries and explicit roots are required.")
    by_id = {}
    for row in entries:
        if not isinstance(row, dict) or row.get("kind") not in {"file", "folder"}:
            raise LibraryError("INVENTORY_SCHEMA", "Invalid inventory entry.")
        for key in ("file_id", "name"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise LibraryError("INVENTORY_SCHEMA", "An identifier or name is missing.")
        if row["file_id"] in by_id:
            raise LibraryError("INVENTORY_SCHEMA", "Duplicate inventory identifier.")
        if row.get("parent_id") is not None and not isinstance(row["parent_id"], str):
            raise LibraryError("INVENTORY_SCHEMA", "Invalid parent identifier.")
        size = row.get("size")
        if size is not None and (type(size) is not int or size < 0):
            raise LibraryError("INVENTORY_SCHEMA", "Invalid byte count.")
        if not isinstance(row.get("integrity", {}), dict):
            raise LibraryError("INVENTORY_SCHEMA", "Invalid integrity metadata.")
        by_id[row["file_id"]] = row
    roots, archives = set(root_ids), set(archive_ids)
    for fid in roots | archives:
        if fid not in by_id or by_id[fid]["kind"] != "folder":
            raise LibraryError("INVENTORY_SCHEMA", "A configured folder is missing.")

    def chain(fid):
        seen = set()
        while fid in by_id:
            if fid in seen:
                raise LibraryError("INVENTORY_SCHEMA", "Cycle in folder ancestry.")
            seen.add(fid)
            if fid in roots:
                return seen
            parent = by_id[fid].get("parent_id")
            if parent in by_id and by_id[parent]["kind"] != "folder":
                raise LibraryError("INVENTORY_SCHEMA", "A parent is not a folder.")
            fid = parent
        return None

    for fid in archives:
        if chain(fid) is None:
            raise LibraryError("INVENTORY_SCHEMA", "Archive is outside the selected scope.")
    scoped = {}
    for fid, row in by_id.items():
        ancestry = chain(fid)
        if ancestry:
            scoped[fid] = (row, bool(ancestry & archives))
    siblings = defaultdict(list)
    for row, _ in scoped.values():
        siblings[(row.get("parent_id"), row["name"])].append(row)
    counts, missing, orphan, unverified, empty, hashes = Counter(), [], [], [], [], defaultdict(list)
    for fid, (row, archived) in scoped.items():
        counts["folders" if row["kind"] == "folder" else "files"] += 1
        if row["kind"] != "file":
            continue
        if archived:
            counts["archived_files"] += 1
            continue
        name = row["name"]
        if name in GENERATED_NAMES:
            counts["generated_files"] += 1
            continue
        ext = name.rsplit(".", 1)[-1].lower()
        if name.endswith(".md") and name[:-3].rsplit(".", 1)[-1].lower() in SOURCE_EXTENSIONS:
            counts["sidecars"] += 1
            originals = siblings.get((row.get("parent_id"), name[:-3]), [])
            if not any(x["kind"] == "file" for x in originals):
                orphan.append(fid)
            unverified.append(fid)
        elif ext in SOURCE_EXTENSIONS:
            counts["source_documents"] += 1
            peers = siblings.get((row.get("parent_id"), name + ".md"), [])
            if not any(x["kind"] == "file" for x in peers):
                missing.append(fid)
            checksum = row.get("integrity", {}).get("sha256")
            if isinstance(checksum, str) and re.fullmatch(r"[a-fA-F0-9]{64}", checksum) and row.get("size", 0):
                hashes[(checksum.lower(), row["size"])].append(fid)
        elif name == "_NOTES.md":
            counts["human_note_files"] += 1
        else:
            counts["other_files"] += 1
        if row.get("size") == 0:
            empty.append(fid)
    conflicts = [sorted(x["file_id"] for x in group) for group in siblings.values() if len(group) > 1]
    return {"schema_version": "1.0.0", "captured_at": snapshot["meta"].get("generated_at"),
            "root_ids": sorted(roots), "complete": True, "counts": dict(sorted(counts.items())),
            "same_name_conflicts": sorted(conflicts),
            "identical_bytes_candidates": sorted(sorted(v) for v in hashes.values() if len(v) > 1),
            "missing_sidecar_ids": sorted(missing), "orphan_sidecar_ids": sorted(orphan),
            "unverified_sidecar_ids": sorted(unverified), "empty_file_ids": sorted(empty),
            "automatic_actions": [],
            "limitations": ["METADATA_IS_NOT_FULLTEXT_EVIDENCE", "SAME_BYTES_DO_NOT_PROVE_SAME_PURPOSE",
                            "SOURCE_APPROVAL_AND_VALIDITY_NOT_INFERRED"]}
