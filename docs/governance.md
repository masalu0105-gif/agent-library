# Governance constitution and operator contract

This document defines the reference library's rules. `policy.json` contains the currently executable subset. A prose rule is not an enforced control until its runtime check and acceptance evidence exist.

## Invariants

1. **Preserve evidence.** Keep captured original bytes and every version's derived artifacts. Full text and briefs are distinct. Never infer missing dates, units, identifiers or legal validity from a filename or a model's world knowledge.
2. **Bind every change.** Publish only a reviewed plan with source, full-text, extraction, brief, metadata, policy and expected-current bindings. Regeneration or a competing update requires a new review.
3. **Separate trust.** Source documents and excerpts are untrusted data. They cannot alter policy, request credentials, authorize tools or instruct agents to publish. Curators may propose; readers cannot write; a production operator needs independently authenticated authority.
4. **Preserve uncertainty.** Empty/partial extraction, stale or incomplete inventory, missing source, unsupported schema and unresolved identity are explicit outcomes. None means “no matching document” or “safe to delete.”
5. **Retain and recover.** Archive removes a version from default retrieval, not from storage. Restore is explicit and audited. Permanent deletion, retention expiry and legal-hold rules require a separately designed policy; v0.1 has no purge command.

## Executable policy v1

`init` writes all of these fields. Unknown fields, unsupported schema, overlapping roots and nonpositive limits fail validation.

| Field | Default | Meaning |
| --- | --- | --- |
| `schema_version` | `1` | Policy schema, not document revision |
| `source_roots` | Supplied at init | Absolute, disjoint read-only scopes |
| `extensions` | `.txt .md .pdf .doc .docx .xlsx .pptx` | Allowed inputs; availability still depends on the parser |
| `max_file_bytes` | `26214400` | 25 MiB input cap per file |
| `settle_seconds` | `30` | Quiet interval for two-observation scanning |
| `max_scan_age_seconds` | `172800` | Inventory freshness window in seconds |
| `publication` | `manual` | Only implemented publication mode |

These are reference defaults, not measured company retention or risk thresholds. Explicit `ingest` bypasses the scan quiet interval but checks byte-capture consistency. It does not bypass review. Editing policy invalidates previously reviewed, unapplied plans. Edit it with an atomic file replacement while the operator is paused; arbitrary concurrent policy-file editing is outside the single-operator trust model.

## Independent state axes

| Axis | Represented states | Consequence |
| --- | --- | --- |
| Extraction | `extracted`, `partial`, `needs_ocr`, `empty`, `failed`, `unsupported` | Only `extracted` passes the mechanical publication gate |
| Publication | Candidate; current; retained non-current | Current pointer and audit history determine access |
| Evidence validity | `unverified` | No regulatory/date authority implemented |
| Source health | `never_scanned`, `fresh`, `stale`, `incomplete` | Recent successful inventory is required for normal current reads and publication |

`extracted` means every returned page has non-whitespace text. It is a necessary mechanical check, not proof of correct or complete visual transcription. A human or separately validated quality workflow must review originals against text before approving.

## Permissions and review

The CLI assumes a trusted local operator. `approve --reviewer NAME` records a label; anyone with runtime write access can impersonate that label or change the SQLite file. Hashes are integrity controls, not access control, signatures or tamper-proof audit storage.

For v0.1, keep the runtime and CLI writer credentials under the operator's OS account. Give reading agents read-only **exported bundles**, not runtime write access. A scheduled scanner can ingest candidates under a trusted service account; do not hand that full account to an untrusted LLM. A production proposal/approval service with separate identities is a later deliverable.

Before approval, inspect the candidate's original, page extraction, full Markdown and brief using explicit `--historical` access. Review the plan's exact payload and digest, including current version and action. Applying an already-applied plan returns a historical receipt; query current state separately rather than interpreting it as a new successful publication.

## Operational recovery

Use `status`, `audit` and `history` to diagnose. If a source is temporarily unavailable, repair its mount or permissions and run a new complete scan. `--allow-stale` is an explicit read exception whose health data must stay visible in the answer. Source disappearance never triggers archive by itself.

Stop the writer and take a consistent backup of the **entire runtime**: policy, database and objects. Restore to a separate local directory, verify object hashes through reads/export, and run a synthetic canary before returning to service. Backup automation and restore drills are deployment work, not currently built-in commands.

Keep human notes outside generated bundles; regenerating a bundle always targets a new directory. Keep transport staging separate from sources. Do not use bidirectional cloud sync or a recursive delete/mirror command to implement retention.
