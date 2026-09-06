# Working on agent-library

This repository contains public code, specifications and synthetic fixtures only.
Never add customer documents, private incident transcripts, credentials, private
file IDs or deployment paths. Keep runtime libraries outside the source checkout.

Use the standard library before adding a dependency. Use the same production
entry points in demos and regression tests. Preserve the explicit boundaries in
docs/architecture.md. In particular, extraction is not approval, and approval is
not regulatory verification. Never infer dates or validity from filenames.

Run `python -m unittest discover -s tests -v`, `python examples/demo.py`, and
`python tools/check_public_tree.py` before publishing. For parser changes also run
`python examples/verify_liteparse.py` with LiteParse 2.0.0 installed. Report skipped
checks explicitly. Never call a NAS/cloud deployment verified from a local demo.
