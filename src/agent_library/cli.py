"""One JSON response per invocation; explicit errors never masquerade as no matches."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .bundles import export_bundle, verify_bundle
from .core import Library, LibraryError
from .inventory import audit_inventory


def parser():
    p = argparse.ArgumentParser(description="A governed document library for AI agents.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--home", type=Path, help="Private runtime directory, separate from source documents")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--source", type=Path, action="append", required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--metadata", type=Path)
    ingest.add_argument("--document-id")
    ingest.add_argument("--parser", choices=["liteparse", "markitdown"], default="liteparse")
    ingest.add_argument("--ocr", action="store_true", help="Explicitly enable local LiteParse OCR")
    ingest.add_argument("--ocr-language", default="eng", help="Local OCR language(s), e.g. chi_tra+eng; used with --ocr")
    for name in ["scan", "status", "map"]:
        sub.add_parser(name)
    plan = sub.add_parser("plan")
    plan.add_argument("version_id")
    plan.add_argument("--action", choices=["publish", "archive", "restore"], default="publish")
    plan.add_argument("--reason", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("plan_id")
    approve.add_argument("--digest", required=True)
    approve.add_argument("--reviewer", required=True)
    sub.add_parser("apply").add_argument("plan_id")
    search = sub.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--kind")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--allow-stale", action="store_true")
    for name in ["read", "brief"]:
        read = sub.add_parser(name)
        read.add_argument("version_id")
        read.add_argument("--historical", action="store_true")
        read.add_argument("--allow-stale", action="store_true")
        if name == "read":
            read.add_argument("--page", type=int)
            read.add_argument("--source", action="store_true")
            read.add_argument("--asset", help="Version-relative asset address returned by read")
        else:
            read.add_argument("--section", default="brief.md")
    sub.add_parser("history").add_argument("document_id")
    fields = sub.add_parser("langextract-input", help="Exact version-bound text and source offsets; no model calls")
    fields.add_argument("version_id")
    fields.add_argument("--page", type=int)
    fields.add_argument("--historical", action="store_true")
    fields.add_argument("--allow-stale", action="store_true")
    sub.add_parser("audit").add_argument("--limit", type=int, default=100)
    sub.add_parser("export").add_argument("output", type=Path)
    verify = sub.add_parser("verify-bundle")
    verify.add_argument("folder", type=Path)
    verify.add_argument("--expected-sha256")
    inventory = sub.add_parser("audit-inventory", help="Read-only audit of a complete private metadata snapshot")
    inventory.add_argument("snapshot", type=Path)
    inventory.add_argument("--root-id", action="append", required=True)
    inventory.add_argument("--archive-id", action="append", default=[])
    return p


def dispatch(args):
    if args.command == "audit-inventory":
        return audit_inventory(json.loads(args.snapshot.read_text(encoding="utf-8")),
                               root_ids=args.root_id, archive_ids=args.archive_id)
    if args.command == "verify-bundle":
        return verify_bundle(args.folder, expected_sha256=args.expected_sha256)
    if args.home is None:
        raise LibraryError("HOME_REQUIRED", "Specify --home before the command.")
    if args.command == "init":
        return Library.initialize(args.home, args.source).status()
    library = Library(args.home)
    if args.command == "ingest":
        metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else {}
        return library.ingest(args.file, metadata, document_id=args.document_id, ocr=args.ocr,
                              ocr_language=args.ocr_language, parser=args.parser)
    if args.command in {"scan", "status", "map"}:
        return getattr(library, args.command)()
    if args.command == "plan":
        return library.plan(args.version_id, action=args.action, reason=args.reason)
    if args.command == "approve":
        return library.approve(args.plan_id, args.digest, args.reviewer)
    if args.command == "apply":
        return library.apply(args.plan_id)
    if args.command == "search":
        return library.search(args.query, kind=args.kind, limit=args.limit, allow_stale=args.allow_stale)
    if args.command in {"read", "brief"}:
        return library.read(args.version_id, historical=args.historical, allow_stale=args.allow_stale,
                            brief=args.section if args.command == "brief" else None,
                            page=getattr(args, "page", None), source=getattr(args, "source", False),
                            asset=getattr(args, "asset", None))
    if args.command == "history":
        return library.history(args.document_id)
    if args.command == "langextract-input":
        return library.langextract_input(args.version_id, page=args.page, historical=args.historical,
                                         allow_stale=args.allow_stale)
    if args.command == "audit":
        return library.audit(args.limit)
    if args.command == "export":
        return export_bundle(library, args.output)
    raise LibraryError("INVALID_COMMAND", "Unknown command.")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        ok = not (args.command == "scan" and not result["complete"])
        print(json.dumps({"ok": ok, "result": result}, ensure_ascii=False))
        return 0 if ok else 2
    except LibraryError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        # Avoid echoing document text, credential values or OS paths in public logs.
        print(json.dumps({"ok": False, "error": {"code": "IO_OR_SCHEMA_ERROR", "message": type(exc).__name__}}))
    return 2
