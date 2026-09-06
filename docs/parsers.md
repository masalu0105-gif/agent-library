# Document parsing, visual evidence and LangExtract

## Implemented in v0.2

Schema 2 retains original bytes, full Markdown, separate navigation briefs and
content-addressed visual assets. Schema 1 snapshots stay readable without rewrites.
Re-ingestion creates a candidate when parser output, settings or assets change;
current publication still requires the existing review workflow.

Some Office PDF generators include timestamps in rendered bytes. An explicit new
ingestion can therefore create another extraction revision of the same source hash.
This is not a new upstream document revision. Reconciliation skips unchanged sources
that already have a successful extraction instead of repeatedly rendering them.

| Profile | Preserved | Limits |
| --- | --- | --- |
| UTF-8 text / Markdown | Decoded text | External image links are not downloaded |
| LiteParse 2.0.0 | Original parser JSON, text boxes, dimensions, page text and a PNG preview of every rendered page | PDF tables are `visual_only`, not reconstructed cells |
| MarkItDown 0.1.7 Office: DOCX | Mammoth reading + MarkItDown HTML rendering; embedded images, table cells, row/column spans, native main-document XML | Logical section, not printed pages; unsupported parts/conversion warnings stay partial |
| MarkItDown Office: XLSX | openpyxl cells, leading-zero strings, formula text, cached values, merged cells and packaged images | Charts/drawings/print layout not rendered; candidates remain partial and cannot publish |

This narrow composed Office profile uses MarkItDown's HtmlConverter, not generic
URL conversion, plugins, an LLM client or Azure. Its output differs from the stock
MarkItDown CLI. PDF/OCR requests to this profile are rejected. Docling and PaddleOCR
remain candidates for PDF cell reconstruction; neither is invoked by these profiles.

## Run the real verifiers

```sh
npm install -g @llamaindex/liteparse@2.0.0
python -m pip install ".[office]"
python examples/verify_liteparse.py
# With LibreOffice:
python examples/verify_liteparse.py --office
python examples/verify_multimodal.py

agent-library --home /private/runtime ingest /private/sources/guide.pdf
agent-library --home /private/runtime ingest /private/sources/guide.docx --parser markitdown
agent-library --home /private/runtime ingest /private/sources/scan.pdf --ocr --ocr-language chi_tra+eng
```

On Windows use npm.cmd; the adapter invokes lit.cmd. PDF previews render locally at
120 DPI. OCR is off unless requested; language data must be provisioned separately.
For Office inputs on the LiteParse route, LibreOffice converts once using an
isolated temporary profile. Text and PNGs use that same retained PDF; it is included
as a version-bound rendered_document asset. The screenshot CLI cannot consume Word
directly. LibreOffice/soffice must be on PATH for this route.
Missing previews, language errors (even with a zero process exit code), empty pages
and failed conversions are not complete extraction. OCR still needs visual review.

PNG/JPEG/TIFF may use LiteParse with its image conversion dependency. This extension
allowance is not OCR accuracy qualification. Existing policies retain their old
extension allowlist until deliberately updated.

## Artifact and fidelity contract

Each extraction.json binds assets/<sha256>.<extension> to SHA-256, size, media type
and role. Pages may include text_items, preview, images, tables, markdown and a
source locator. Table cells have zero-based row/column, positive rowspan/colspan
and extracted text. Generated HTML escapes cell content and keeps merged cells.
Raw embedded images are preserved byte-for-byte; unsupported formats are attachments.

PDF coordinates remain in the parser's native coordinate system and original JSON,
not preview pixels. Office locations identify package parts or worksheets. Page N
anchors for logical sections do not claim printed page numbers. Unsupported Office
parts, unlocated media and model interpretations must not be called faithful text.

```text
documents/<version>/
  original.docx
  full.md
  extraction.json
  brief.md
  sections/...
  assets/<sha256>.png
  assets/<sha256>.html
  assets/<sha256>.xml
```

Assets are immutable runtime objects and relative files in exports. Generated
image/table links do not contain temporary paths. Approvals bind extraction hashes,
which bind asset hashes. Read, apply, restore and export verify referenced bytes.
Missing/corrupt assets or removed manifest entries fail verification. Read assets
through the same historical/current and freshness boundary as text:

```sh
agent-library --home /private/runtime read VERSION_ID --historical --page 1
agent-library --home /private/runtime read VERSION_ID --historical --asset assets/ASSET_SHA256.png
```

Use the exact asset address returned by read. Runtime snapshot paths are hash
addresses; use the media type when serving them. Export names include extensions.
Pin a trusted bundle digest to authenticate the selected release; content hashes
alone do not identify a publisher.

## Existing local LangExtract workflows

[LangExtract](https://github.com/google/langextract) extracts candidate information
from text. It sits after document parsing and is not a required core dependency.

```sh
agent-library --home /private/runtime langextract-input VERSION_ID --historical --page 1
```

The JSON response supplies result.text, source/extraction hashes, quality warnings
and source spans in Unicode code-point offsets. Pass result.text to the installed
LangExtract workflow with explicit examples and a configured local model. This
command does not invoke a model or transmit data. Field extraction and semantic
brief generation remain outside the publication engine.

Compare returned intervals with this exact input. If a unique literal span repairs
an absent interval, mark it as a fallback; ambiguous matches are not precise
provenance. Matching OCR text does not prove accuracy against the original image.

## Qualification and limits

Synthetic checks exercise actual DOCX image byte identity, both merge directions,
numeric text, export, archive/restore and deliberate corruption. XLSX checks preserve
strings/formulas while blocking incomplete visuals. PDF checks use actual PNGs and
text boxes. These are not clinical OCR accuracy or cloud/NAS deployment validation.

Office containers have entry/expanded-size limits. Assets have an aggregate size
ceiling. Parser subprocesses use fixed temporary filenames, timeouts and captured
errors. This trusted single-host CLI is not a hostile-upload sandbox. Large corpora
still require measured resource budgets and streaming export.

See [commercial dependencies](commercial-dependencies.md) before selecting models,
OCR plugins or hosted services.
