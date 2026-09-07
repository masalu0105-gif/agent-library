# Commercial use and parser choices

Reviewed 2026-09-07. Distinguish package code, model weights and optional services.
Review exact versions and preserve required notices when distributing a product.

| Component | Primary license source | Use in agent-library |
| --- | --- | --- |
| agent-library | [MIT](../LICENSE) | Commercial products permitted; preserve required notices |
| Microsoft MarkItDown | [MIT](https://github.com/microsoft/markitdown/blob/main/LICENSE) | Optional Office profile, pinned 0.1.7; commercial use permitted under MIT |
| Google LangExtract | [Apache-2.0](https://github.com/google/langextract/blob/main/LICENSE) | Existing local installations consume version-bound text; models are separately selected |
| Docling | [MIT code](https://github.com/docling-project/docling/blob/main/LICENSE), [model licenses separate](https://github.com/docling-project/docling#license) | Optional local PDF/image profile, version-bound 2.126.0 stack; no default dependency or cloud service |
| Docling layout Heron | [Apache-2.0 model notice](https://huggingface.co/docling-project/docling-layout-heron) | Explicit local artifact manifest; pinned revision and file hashes are recorded per extraction |
| Docling TableFormer snapshot | [CDLA-Permissive-2.0](https://github.com/docling-project/docling-models) | Accurate v1 table profile, pinned to the selected v2.3.0 snapshot; preserve its notice separately |
| PaddleOCR | [Apache-2.0 code](https://github.com/PaddlePaddle/PaddleOCR) | Candidate for OCR/structure; check selected models and dependencies |

Our Office profile uses MarkItDown's HTML renderer, Mammoth, openpyxl and
markdownify. It does not select a generative model, activate OCR plugins or send
content to an LLM/Azure endpoint. Dependencies are installed from their own
distributions, not vendored here. Optional dependency pins and extraction metadata
identify the tested components; they are not a complete transitive dependency lock.

Installed metadata for the selected reader/rendering components was also checked:
Mammoth 1.11.0 is BSD-2-Clause, openpyxl 3.1.5 is MIT and markdownify 1.2.3 declares
MIT. These permit commercial use subject to their notices and terms. Transitive
dependencies are not all MIT: for example certifi declares MPL-2.0, whose obligations
must be retained in a distribution inventory rather than relabeled as MIT.

MarkItDown's [official README](https://github.com/microsoft/markitdown) describes a
lightweight Markdown conversion tool and warns about high-fidelity conversion.
Broader format support and optional cloud/vision plugins do not automatically
provide complete table structure or our local profile's data flow.

The optional Docling profile supports the bounded layout, reading-order, table and
image path described in `parsers.md`. Its MIT code license does not grant rights to
every optional model. The selected Heron layout model is Apache-2.0 and the pinned
TableFormer snapshot is CDLA-Permissive-2.0; retain those model notices and any
transitive redistribution notices in the product inventory. Extraction metadata
records package/model revisions and preprocessing settings, while private canary
results remain outside this public repository.

[MinerU's current license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)
adds commercial-scale thresholds and online-service attribution to Apache terms.
[Marker](https://github.com/datalab-to/marker#commercial-usage) distinguishes
Apache-2.0 code from model weights with a conditional free commercial range.
They are not default dependencies. Do not rely on a remembered license from an
earlier version when distributing a new product.

LangExtract's Apache-2.0 license covers the library; local model weights and hosted
providers remain separate. Source-grounded output does not grant permission to
republish customer documents. Keep private material and runtime outputs outside
this public repository.

Before distributing a concrete product, record the full dependency versions and
notices/licenses, selected weights and enabled providers/plugins. This package
inventory is not certification of an entire future deployment.
