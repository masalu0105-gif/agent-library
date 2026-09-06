# Deployment path and acceptance criteria

The [product specification](product-spec.md) defines the full quality-management
scope, two authority modes, and future on-premises de-identification with governed
cloud analysis. The stages below develop its document foundation.

v0.2 includes the local core, versioned visual assets, a native Office profile and
LangExtract text/offset input. PDF table reconstruction and semantic extraction are
still qualification work. The phases below are not enabled by installing the package.

## 1. Private inventory and parser qualification

Select exact NAS roots and the intended private cloud destination. Establish export authorization, network restrictions, owner/reviewer roles, document-family keys, active regulatory authority, backup policy and retention/legal holds. Inventory originals separately from existing sidecars, indexes and archives. Preserve original locations and old IDs while comparing mappings.

Create a private representative evaluation set: digital PDF, scanned PDF, multilingual text, two-column tables, model-specific restrictions, stamps, Word and corrupt files. Acceptance: source byte hashes, page counts, missing-page checks and human-reviewed key fields; no invented validity dates. The public repository retains only synthetic examples.

## 2. NAS scanner and private Drive transport

Deploy the writer/runtime on one supported host's local disk with read-only NAS mounts. Schedule the existing `scan` command through the site's approved scheduler; use file events only as hints, perform complete reconciliation regularly, and record actual run outcomes. Select cadence from file size/copy behavior and business needs rather than assuming a universal interval.

Implement a private Drive adapter over exported bundles:

- Explicit destination folder IDs and credential scope, separate from public repository settings; no OAuth tokens in source control.
- Stage immutable version files, verify remote size/hash/content by readback, then advance a current manifest only if its expected previous revision still matches.
- Resolve bundle-relative links for agents; keep prior release manifests so rollback restores a pointer without deleting data.
- Retry/resume by an idempotency key; incomplete listings/uploads cannot switch current. Return transport state separately from source health and notification state.

Acceptance: a small approved synthetic or de-identified canary runs through the actual NAS scheduler → parse → review → private Drive → actual assistant retrieval route. Network interruption, retries, duplicate events, stale cloud state, permission loss, concurrent publisher and rollback must be exercised. Only then perform an approved recoverable migration batch. Do not mirror-delete the existing cloud library.

## 3. Semantic briefs and domain evidence

Evaluate LangExtract or another grounded extractor behind the [parser/summary contract](parsers.md). Add validated span references, section coverage, failure status and independent quality review. Semantic briefs remain navigation aids; a user-facing factual answer checks the original applicable section.

Add domain authority adapters separately: explicit fields, validity source and date, jurisdiction, language, model/unit scope, evidence grade and review status. Acceptance: ambiguous or missing sources remain unknown; expired or superseded scope cannot be offered as current merely because a document was newly uploaded.

## 4. Authenticated governance and operations

Separate reader, proposer, scanner, approver and publisher identities at an OS/service boundary. Add authenticated approvals, signed manifests if required, a durable exception queue, explicit source rename/family migration, backup/restore drills and measurable corpus-scale performance. Any retention/purge implementation requires legal holds, review, a recoverable plan and deletion evidence.

Add notifications only for actionable changes/failures. A provider response of `skipped` must not count as delivered; test the user's actual route. Deploy reader skills and runtime as one versioned artifact manifest and run canaries from every supported agent platform. Publishing new code alone is not activation proof.

The first production decision is a private allowlist of NAS input roots and cloud output scope. No production document movement should be inferred from the public software release.
