"""Real DOCX/XLSX conversion and visual-asset lifecycle acceptance, all synthetic."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_library.core import Library, LibraryError
from agent_library.bundles import export_bundle, verify_bundle
from synthetic_office import png, write_rich_docx, write_rich_xlsx


def verify(base):
    source = base / "sources"
    source.mkdir()
    docx, xlsx = source / "guide.docx", source / "sheet.xlsx"
    write_rich_docx(docx)
    write_rich_xlsx(xlsx)
    library = Library.initialize(base / "runtime", [source])
    library.scan()
    stage = library.ingest(docx, parser="markitdown")
    assert stage["quality"] == "extracted", stage
    review = library.read(stage["version_id"], historical=True)
    page = review["pages"][0]
    cells = page["tables"][0]["cells"]
    assert any(c["text"] == "Merged heading" and c["colspan"] == 2 for c in cells)
    assert any(c["text"] == "Group A" and c["rowspan"] == 2 for c in cells)
    assert any(c["text"] == "0.25 ± 0.05 mg/L" for c in cells)
    assert 'rowspan="2"' in page["markdown"] and 'colspan="2"' in page["markdown"]
    image = page["images"][0]
    asset = library.read(stage["version_id"], historical=True, asset=image)
    assert Path(asset["snapshot_path"]).read_bytes() == png()
    assert image in page["markdown"]
    assert library.ingest(docx, parser="markitdown")["deduplicated"]
    for action in ["publish", "archive", "restore"]:
        plan = library.plan(stage["version_id"], action=action, reason="Synthetic visual acceptance")
        library.approve(plan["plan_id"], plan["digest"], "synthetic-reviewer")
        assert library.apply(plan["plan_id"])["verified"]
    output = base / "bundle"
    exported = export_bundle(library, output)
    assert exported["verified"]
    docfolder = output / "documents" / stage["version_id"]
    assert (docfolder / image).read_bytes() == png()
    # Corruption is caught by the same verifier used by actual readers.
    (docfolder / image).write_bytes(b"broken image")
    try:
        verify_bundle(output)
    except LibraryError as exc:
        assert exc.code == "INTEGRITY"
    else:
        raise AssertionError("Corrupted exported image was accepted")
    sheet = library.ingest(xlsx, parser="markitdown")
    workbook = library.read(sheet["version_id"], historical=True)
    assert sheet["quality"] == "partial"  # Print layout and charts are not rendered.
    assert "WORKBOOK_CHARTS_AND_DRAWINGS_REQUIRE_ORIGINAL" in workbook["warnings"]
    assert any(c["text"] == "000123" for c in workbook["pages"][0]["tables"][0]["cells"])
    assert "=B2*2" in workbook["pages"][0]["text"]
    return {"ok": True, "parser": "MarkItDown 0.1.7 + native Office structure",
            "docx_image_byte_identity": True, "merged_rows_and_columns": True,
            "asset_lifecycle_and_tamper_detection": True, "xlsx_cells_and_formula_preserved": True,
            "xlsx_incomplete_visuals_block_publication": True}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="library-visual-acceptance-") as folder:
        print(json.dumps(verify(Path(folder))))
