# Full text and briefs are different artifacts

## Implemented parser boundary

The adapter accepts original bytes, an allowed suffix, an explicit OCR flag and
optional local OCR language(s). It returns:

```json
{
  "schema_version": 1,
  "method": "liteparse-2.0.0:no-ocr",
  "status": "extracted",
  "pages": [{"number": 1, "text": "Synthetic source text."}],
  "warnings": []
}
```

UTF-8 `.txt` and `.md` use a strict local decoder. Binary/NUL input and invalid encoding fail. Other allowed formats use [LiteParse](https://github.com/run-llama/liteparse), pinned to the tested CLI version 2.0.0. Other versions fail with `UNVERIFIED_PARSER_VERSION`; upgrading requires compatibility tests and a deliberate adapter change. The local executable and installed dependencies are part of the trusted host environment.

For Traditional Chinese scans use `ingest document.pdf --ocr --ocr-language chi_tra+eng`.
Install the corresponding Tesseract language data locally and set `TESSDATA_PREFIX`
for the invoking process if required by the host. An OCR-language failure can
occur even when the parser exits zero: recognized failures remain partial or
unreadable and cannot be published. OCR quality still needs visual review.

The adapter runs with an argument array, fixed temporary filenames, a timeout and captured output. Parser failure is explicit; stderr is not echoed into public logs because it may contain document text. A hung or compromised parser still needs an OS sandbox/resource limits in a production hostile-upload service; this reference CLI is not such a service.

## Fidelity contract

| Artifact | Preserved | Limits |
| --- | --- | --- |
| Original | Exact captured bytes and SHA-256 | Snapshot may differ from a subsequently changed NAS file |
| Extraction JSON | All returned page text and page numbers | Adapter currently omits parser bounding boxes and image assets |
| Full Markdown | All returned text, in page order, with `Page N` headings | Tables may flatten; diagrams and reading order need visual review |
| Brief Markdown | Short page excerpts and links to the same version | Navigation only; no semantic importance ranking |

A populated text layer can still have wrong numbers, missing footnotes, columns in the wrong order or unrecognized images. Conversely, an intentionally blank page makes the conservative mechanical check `partial`; review or a future explicit blank-page adjudication is needed. Do not silently relabel a placeholder Markdown as complete.

LiteParse handles Word through a conversion dependency such as LibreOffice. `examples/verify_liteparse.py --office` generates a synthetic DOCX and verifies actual extraction, both Markdown roles and publication. Legacy `.doc`, spreadsheet/presentation formats, image-only OCR, complex tables and multilingual visual fidelity are **not** covered by this fixture and are not production-qualified by it.

OCR is disabled unless `ingest --ocr` is used. Local OCR dependencies may need model/language downloads at initial setup. Provision and test those separately under the deployment's network policy. The reference adapter makes no cloud model calls; installing dependencies is a separate network operation.

## Where LangExtract fits

[LangExtract](https://github.com/google/langextract) extracts structured information from text using LLMs and returns source grounding. In this design it fits **after document parsing**, for candidate fields and semantic brief generation. It is not used by v0.1, and no model/API key is required by the core.

Before a semantic summarizer can be integrated, it must return a candidate artifact with:

- Source hash, full-text hash, parser/normalization version and summarizer/model/prompt version.
- Every claim's page and character span in the exact canonical source text, including units and applicability scope.
- An explicit unsupported/ambiguous result when no grounded span exists; failed grounding cannot be repaired by invented text.
- A separate review outcome and schema version; a new summary must create a newly reviewed version, never overwrite the existing brief silently.

Grounded extraction still needs evaluation: a cited fragment can omit a nearby exception or refer to a different model. Benchmark real document layouts in a private evaluation set before selecting a production parser or summarizer. No vendor-specific integration is promised until its full route passes those checks.
