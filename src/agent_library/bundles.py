"""Immutable, verifiable export for a future transport adapter. No cloud writes."""
import json
import re
from pathlib import Path

from .core import RULES, Library, canonical, digest, no_links, require
from .navigation import build_catalog


def export_bundle(library: Library, output: Path) -> dict:
    output = no_links(output)
    policy = library._policy()
    require(not output.exists(), "OUTPUT_EXISTS", "Export into a new directory; existing files are never overwritten.")
    roots = [library.home, *(Path(p) for p in policy["source_roots"]), Path(__file__).resolve().parents[2]]
    require(not any(output.is_relative_to(p) or p.is_relative_to(output) for p in roots),
            "UNSAFE_PATH", "Export must be separate from sources, runtime and installed code.")
    with library._db() as db:
        require(library._health(db, policy)["fresh"], "STALE_INDEX", "Reconcile sources before export.")
        rows = db.execute("SELECT v.* FROM versions v JOIN documents d ON d.current_version=v.id ORDER BY v.document_id").fetchall()
        files, documents, groups = {}, [], {}
        files["00_CONSTITUTION.md"] = ("# Library reading rules\n\n" + "\n\n".join(RULES) + "\n").encode()
        for row in rows:
            library._verify_version(row)
            folder = "documents/" + row["id"]
            metadata = json.loads(row["metadata"])
            files[folder + "/full.md"] = library._object(row["markdown_hash"])
            files[folder + "/extraction.json"] = library._object(row["extraction_hash"])
            suffix = Path(row["filename"]).suffix.lower()
            require(suffix in policy["extensions"], "UNSUPPORTED_TYPE", "Published source type is outside current policy.")
            original = folder + "/original" + suffix
            files[original] = library._object(row["source_hash"])
            for name, content in json.loads(library._object(row["brief_hash"])).items():
                require(name == "brief.md" or re.fullmatch(r"sections/[0-9-]+\.md", name), "INTEGRITY", "Invalid brief address.")
                files[folder + "/" + name] = content.encode()
            item = {"document_id": row["document_id"], "version_id": row["id"], "metadata": metadata,
                    "original_filename": row["filename"], "source_sha256": row["source_hash"], "original": original,
                    "brief": folder + "/brief.md", "fulltext": folder + "/full.md", "evidence_status": "unverified"}
            documents.append(item)
            groups.setdefault(metadata["kind"], []).append(item)
        catalog_entries = []
        for kind, items in sorted(groups.items()):
            name = "groups/" + digest(kind.encode())[:16] + ".md"
            catalog_entries.append((f"{kind}: {len(items)} documents", name))
            entries = [(item["metadata"]["title"] + " / " + item["version_id"], item["brief"]) for item in items]
            files.update({path: value.encode() for path, value in build_catalog(entries, name, kind).items()})
        files.update({path: value.encode() for path, value in build_catalog(catalog_entries, "_AI_MAP.md", "Library map").items()})
        files["_AI_MAP.md"] += b"\nRead [governance](00_CONSTITUTION.md) before following document links.\n"
        manifest = {"schema_version": 1, "policy_sha256": digest(canonical(policy)), "documents": documents,
                    "files": {name: digest(data) for name, data in sorted(files.items())}}
        # Nothing is discoverable as a complete bundle until bundle.json is written last.
        output.mkdir(parents=True)
        for name, data in files.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (output / "bundle.json").write_bytes(canonical(manifest))
    sha = digest(canonical(manifest))
    result = verify_bundle(output, expected_sha256=sha)
    return {**result, "output": str(output), "entrypoint": str(output / "_AI_MAP.md")}


def verify_bundle(folder: Path, *, expected_sha256=None) -> dict:
    folder = no_links(folder).resolve(strict=True)
    raw = no_links(folder / "bundle.json").read_bytes()
    if expected_sha256 is not None:
        require(digest(raw) == expected_sha256, "INTEGRITY", "Manifest differs from the trusted expected hash.")
    doc = json.loads(raw)
    require(isinstance(doc, dict) and doc.get("schema_version") == 1 and
            isinstance(doc.get("files"), dict) and isinstance(doc.get("documents"), list), "BUNDLE_SCHEMA", "Invalid bundle schema.")
    require("_AI_MAP.md" in doc["files"] and "00_CONSTITUTION.md" in doc["files"], "BUNDLE_SCHEMA", "Missing bundle entrypoints.")
    for name, sha in doc["files"].items():
        require(isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_./-]+", name) and
                not name.startswith("/") and ".." not in name.split("/"), "UNSAFE_PATH", "Invalid bundle path.")
        path = no_links(folder / name)
        require(path.is_relative_to(folder) and path.is_file() and digest(path.read_bytes()) == sha,
                "INTEGRITY", "Bundle asset is missing or changed.")
    for item in doc["documents"]:
        require(all(item.get(key) in doc["files"] for key in ["brief", "fulltext", "original"]), "BUNDLE_SCHEMA", "Unresolved document reference.")
        require(doc["files"][item["original"]] == item.get("source_sha256"), "INTEGRITY", "Source reference hash mismatch.")
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    require(actual == set(doc["files"]) | {"bundle.json"}, "BUNDLE_SCHEMA", "Unexpected or missing files in publication bundle.")
    return {"verified": True, "manifest_sha256": digest(raw), "documents": len(doc["documents"]),
            "files": len(doc["files"]), "publisher_authenticated": False}
