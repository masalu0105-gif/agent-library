# Private inventory audit

`agent-library audit-inventory inventory.json --root-id library-root --archive-id archive-root`

This read-only command does not require a library runtime. Store the input and
output outside the public checkout: filenames, folder IDs and hashes can be
private information. The provider must finish pagination, check the actual root,
and set `meta.complete` to the JSON boolean `true`; partial scans are rejected.

Minimal provider-neutral input (identifiers below are synthetic):

```json
{
  "schema_version": "1.0.0",
  "meta": {"complete": true, "generated_at": "2026-01-01T00:00:00Z"},
  "entries": [
    {"file_id": "library-root", "parent_id": null, "name": "Library", "kind": "folder", "size": null},
    {"file_id": "archive-root", "parent_id": "library-root", "name": "History", "kind": "folder", "size": null},
    {"file_id": "manual", "parent_id": "library-root", "name": "manual.pdf", "kind": "file", "size": 1024},
    {"file_id": "brief", "parent_id": "library-root", "name": "manual.pdf.md", "kind": "file", "size": 512}
  ]
}
```

Optional `integrity.sha256` permits identical-byte candidate grouping. A matching
hash does not establish that two uses have the same owner, permissions, approval,
or retention requirements. The audit never deletes, moves, or selects a winner.

The output separates supported source documents, sidecars, generated files, human
notes, other assets, and archived files. Archive membership follows folder IDs,
not a name containing `ARCHIVE`. Same-parent/name conflicts, missing and orphaned
sidecars, empty files and unverified sidecars are explicit queues.

No metadata-only sidecar is certified as full text, even if an upstream system
labels it `FULL`. `inspect_sidecar(text)` adds a conservative content probe for
empty text, OCR placeholders, metadata stubs and legacy summaries. Other text
remains unverified: passing a text probe is not parser qualification.

For an actual migration, retain the initial snapshot and content backups, review
the applicable policy, make a concrete change plan, enforce source name/parent/hash
preconditions, and read back each mutation. Use the same production reader for
acceptance. A clean structural audit is not evidence that every document has been
parsed completely or reviewed for current clinical/regulatory use.
