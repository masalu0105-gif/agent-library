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
| Optional local Docling 2.126.0 | PDF/PNG/JPEG pages, page previews, extracted pictures, native table cells/spans/provenance, escaped table HTML and structured JSON | Requires a validated local model manifest; table/OCR fidelity remains unverified and pipeline exceptions keep candidates fail-closed |

This narrow composed Office profile uses MarkItDown's HtmlConverter, not generic
URL conversion, plugins, an LLM client or Azure. Its output differs from the stock
MarkItDown CLI. PDF/OCR requests to this profile are rejected. The optional
Docling profile below is the only local cell-reconstruction path; PaddleOCR is not
invoked by any profile.

## Optional local Docling profile

Install the profile only in an environment that has the pinned local stack and a
separately provisioned model artifact directory:

```sh
python -m pip install ".[docling]"
agent-library --home /local/runtime ingest /local/sources/guide.pdf \
  --parser docling --docling-model-manifest /local/docling-models.json
```

The public adapter is version-bound to Docling Slim 2.126.0, Docling Core 2.95.0,
Docling IBM Models 4.0.2, Docling Parse 7.17.0, Transformers 5.16.1 and
opencv-python-headless 5.0.0.93. The selected runtime must also provide Torch on
the 2.14 line; CPU is the default and `--docling-device cuda` selects CUDA without
silently falling back. The tested CUDA canary used Torch 2.14.0+cu126. CPU/CUDA
wheel selection is host-specific, so the optional dependency leaves that choice
to the installation environment.

The manifest is required so model paths and bytes are checked before inference.
It is local configuration, not a public artifact; use an absolute local root and
relative file entries. The two selected model identities are fixed to the Heron
layout preset and the accurate TableFormer v1 snapshot:

The `files` arrays below are abbreviated for readability; a runnable manifest
must enumerate every regular file in both selected subtrees.

```json
{
  "schema_version": 1,
  "artifacts_path": "/absolute/local/docling-models",
  "models": {
    "layout": {
      "id": "layout_heron_default",
      "repo_id": "docling-project/docling-layout-heron",
      "revision": "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
      "files": [
        {"path": "docling-project--docling-layout-heron/config.json", "sha256": "...", "bytes": 0},
        {"path": "docling-project--docling-layout-heron/model.safetensors", "sha256": "...", "bytes": 0}
      ]
    },
    "table": {
      "id": "tableformer_accurate_v1",
      "repo_id": "docling-project/docling-models",
      "version": "v2.3.0",
      "revision": "fc0f2d45e2218ea24bce5045f58a389aed16dc23",
      "files": [
        {"path": "docling-project--docling-models/config.json", "sha256": "...", "bytes": 0},
        {"path": "docling-project--docling-models/model.safetensors", "sha256": "...", "bytes": 0}
      ]
    }
  }
}
```

Every `files` entry must name an existing regular file under the selected model
subtree, where the subtree is the repo ID with `/` replaced by `--`; the list must
be the complete recursive regular-file set (excluding only the snapshot `.cache`
sidecar). It therefore includes the selected model's config and weights as well
as license/notice files when present. A license-only or otherwise incomplete list
is rejected. Each declared file is checked for its exact byte count and SHA-256.
Missing roots/files, traversal, symlinks, size/hash changes and malformed
manifests return an unsupported or failed result; they never become a successful
extraction. The validated model identities and file hashes are recorded in
`settings.model_manifest` without recording the private root path, so a model
change creates a new extraction fingerprint.

The worker enables only the PDF and image formats, uses
`LayoutObjectDetectionOptions.from_preset("layout_heron_default")`, pins the
Heron revision above, sets `TableStructureOptions` to `ACCURATE` v1, runs with
batch size 1 and two threads, and disables remote services, plugins, VLM picture
descriptions and automatic downloads. It sets offline environment variables and
audits common Python socket paths. That socket guard is defense-in-depth and is
not an OS sandbox.

OCR is opt-in and uses Docling's `TesseractCliOcrOptions` explicitly; the automatic
OCR engine is not selected. The worker resolves the local `tesseract` executable,
passes its resolved command and tessdata directory to Docling, and records the
engine version, executable SHA-256 and requested `.traineddata` SHA-256 values in
`settings.ocr_engine`. Missing or uninspectable engine/data files fail closed, so
an engine or language-data change is visible in the extraction settings. For the
Docling profile the actual Tesseract render scale defaults to 3.0 and can be
adjusted with `--docling-ocr-scale`; this is a rendering setting, not an OCR
accuracy claim:

```sh
agent-library --home /local/runtime ingest /local/sources/scan.pdf \
  --parser docling --docling-model-manifest /local/docling-models.json \
  --ocr --ocr-language chi_tra+eng --docling-ocr-scale 3.0
```

The worker retains every reported page and requires complete page coverage. It
uses the relative `Path("assets")` export directory so Docling's structured JSON
does not acquire absolute worker links, then rewrites those references to the
existing content-addressed asset names. PNG page previews, extracted picture
assets, table cells/spans/provenance, escaped HTML from `table_html`, and the
structured JSON all flow through the existing `add_asset` and bundle verification
path. The existing 25 MiB input and 128 MiB aggregate asset limits still apply.

`DOCLING_PIPELINE_OSD`, `DOCLING_PIPELINE_TABLEFALLBACK` and the generic pipeline
warning marker are emitted when those signals occur in the actual conversion
status/errors or captured worker logs. A detected table with zero rows, columns
and cells adds `DOCLING_TABLE_CONTENT_MISSING` and keeps the candidate partial;
the page preview remains available. Missing models/dependencies, invalid
manifests, timeouts, conversion failures, invalid table spans, missing pictures
or previews, and partial page coverage are outward-sanitized and fail closed.
Conversion success never becomes a `HIGH` or correctness assertion. Table and OCR
outputs remain partial/unverified candidates for review. The public tests use
synthetic conversion results and do not claim the private canary or exception
integration is complete.

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
Single-page PNG/JPEG inputs retain their original image bytes as the page preview;
they do not use the PDF-only screenshot command. Missing OCR remains visible even
when the original image is preserved successfully.

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
