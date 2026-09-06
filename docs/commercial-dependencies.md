# Commercial use and parser choices

Reviewed 2026-09-07. Distinguish package code, model weights and optional services.
Review exact versions and preserve required notices when distributing a product.

| Component | Primary license source | Use in agent-library |
| --- | --- | --- |
| agent-library | [MIT](../LICENSE) | Commercial products permitted; preserve required notices |
| Microsoft MarkItDown | [MIT](https://github.com/microsoft/markitdown/blob/main/LICENSE) | Optional Office profile, pinned 0.1.7; commercial use permitted under MIT |
| Google LangExtract | [Apache-2.0](https://github.com/google/langextract/blob/main/LICENSE) | Existing local installations consume version-bound text; models are separately selected |
| Docling | [MIT code](https://github.com/docling-project/docling/blob/main/LICENSE), [model licenses separate](https://github.com/docling-project/docling#license) | Candidate for layout/table extraction, not a dependency or tested parser in v0.2 |
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

Docling supports layout, reading order, tables, images and multiple export formats.
Its MIT code license does not grant rights to every optional model. A future
integration must record the selected package/model revision, original license,
preprocessing configuration and private benchmark result.

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
