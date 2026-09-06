"""Real parser acceptance: generated PDF/Word -> full text -> brief -> publication."""
import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_library.bundles import export_bundle
from agent_library.core import Library, LibraryError
from synthetic_pdf import write_pdf


def write_docx(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic Word guide. Reference 000456.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')


def verify(base, office=False):
    source = base / "source"
    source.mkdir()
    write_pdf(source / "guide.pdf", ["Synthetic PDF guide. Reference 000123.", "Second page. Keep every page."])
    write_pdf(source / "partial.pdf", ["Synthetic readable page.", ""])
    if office:
        write_docx(source / "guide.docx")
    library = Library.initialize(base / "runtime", [source])
    assert library.scan()["complete"]
    types = ["pdf", "docx"] if office else ["pdf"]
    for suffix in types:
        staged = library.ingest(source / ("guide." + suffix))
        assert staged["quality"] == "extracted", staged
        review = library.read(staged["version_id"], historical=True)
        texts = [page["text"] for page in review["pages"]]
        assert "000123" in texts[0] if suffix == "pdf" else "000456" in texts[0]
        if suffix == "pdf":
            assert len(texts) == 2 and "Keep every page" in texts[1]
        assert "full.md#page-1" in library.read(staged["version_id"], brief="brief.md", historical=True)["markdown"]
        plan = library.plan(staged["version_id"], reason="Reviewed synthetic parser fixture")
        library.approve(plan["plan_id"], plan["digest"], "parser-verifier")
        assert library.apply(plan["plan_id"])["verified"]
    partial = library.ingest(source / "partial.pdf")
    assert partial["quality"] == "partial", partial
    try:
        library.plan(partial["version_id"], reason="Must reject incomplete extraction")
    except LibraryError as exc:
        assert exc.code == "QUALITY_BLOCKED"
    else:
        raise AssertionError("Partially extracted PDF was publishable")
    result = export_bundle(library, base / "bundle")
    assert result["documents"] == len(types) and result["verified"]
    return {"ok": True, "parser": "LiteParse 2.0.0", "tested": types, "partial_pdf_blocked": True,
            "fulltext_and_brief_verified": True, "bundle_verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--office", action="store_true", help="Also test Word; requires LibreOffice")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agent-library-parser-") as folder:
        print(json.dumps(verify(Path(folder), args.office)))
