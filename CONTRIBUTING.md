# Contributing

Open a focused issue with the observed behavior, expected behavior and a **synthetic** reproduction. Do not upload company documents, customer details, private folder IDs, credentials or real extracted text. Replace private infrastructure paths in logs before sharing them.

Use Python 3.11+ and keep the core standard-library-only unless a measured need justifies a dependency. Preserve explicit errors and trust boundaries. Add a focused test for behavior changes; tests should exercise the same API/schema used by the CLI and actual adapters.

```sh
python -m pip install .
python -m unittest discover -s tests -v
python examples/demo.py
python tools/check_public_tree.py
```

Parser changes additionally require the pinned real parser and `python examples/verify_liteparse.py --office` with LibreOffice. Include Windows and Linux results. Pin CI actions to verified commit SHAs. Do not equate parser text presence with semantic accuracy.

Before proposing a new adapter, document its credentials/permissions, idempotency, complete-listing rules, version comparison, rollback and independent readback. Separate currently implemented behavior from planned capabilities. Never rewrite an existing user's source folders or installed skills as a side effect of installing this project.
