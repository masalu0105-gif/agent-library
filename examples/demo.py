"""Run the real engine with synthetic documents. No network or credentials."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_library.bundles import export_bundle, verify_bundle
from agent_library.core import Library, LibraryError


def demo(base: Path) -> dict:
    source = base / "source"
    source.mkdir(parents=True)
    document = source / "example.txt"
    original = "Synthetic instrument guide.\nReference 000123.\n原始文字保留。\n"
    document.write_bytes(original.encode("utf-8"))
    library = Library.initialize(base / "runtime", [source])
    assert library.scan()["complete"]
    first = library.ingest(document, {"title": "Synthetic guide", "kind": "guide", "aliases": ["DEMO-100"]})

    def transition(version, action):
        plan = library.plan(version, action=action, reason="Synthetic demo review")
        library.approve(plan["plan_id"], plan["digest"], "demo-operator")
        return library.apply(plan["plan_id"])

    transition(first["version_id"], "publish")
    assert library.search("DEMO-100")["total"] == 1
    brief = library.read(first["version_id"], brief="brief.md")
    assert "full.md#page-1" in brief["markdown"]
    assert library.read(first["version_id"], page=1)["pages"][0]["text"] == original
    document.write_text("Updated synthetic guide.\nReference 000123.\n", encoding="utf-8")
    second = library.ingest(document)
    transition(second["version_id"], "publish")
    try:
        library.read(first["version_id"])
    except LibraryError as exc:
        assert exc.code == "NOT_CURRENT"
    else:
        raise AssertionError("Old version was offered as current")
    transition(second["version_id"], "archive")
    assert library.search()["total"] == 0
    transition(first["version_id"], "restore")
    assert library.read(first["version_id"], page=1)["pages"][0]["text"] == original
    bundle = export_bundle(library, base / "bundle")
    assert verify_bundle(base / "bundle", expected_sha256=bundle["manifest_sha256"])["verified"]
    return {"ok": True, "documents": library.status()["documents"], "versions": library.status()["versions"],
            "verified": ["fulltext", "separate_brief", "alias_search", "supersede", "archive", "restore", "bundle"],
            "bundle": bundle}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="New private demo directory outside this checkout; retained for inspection")
    args = parser.parse_args()
    if args.output:
        output = args.output.resolve()
        if output.exists() or output.is_relative_to(Path(__file__).resolve().parents[1]):
            parser.error("Choose a new directory outside this checkout.")
        print(json.dumps(demo(output), ensure_ascii=False))
    else:
        with tempfile.TemporaryDirectory(prefix="agent-library-demo-") as folder:
            result = demo(Path(folder))
            result["temporary_demo_removed_after_exit"] = True
            print(json.dumps(result, ensure_ascii=False))
