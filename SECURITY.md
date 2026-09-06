# Security

This is a trusted-operator, single-host reference implementation. It is not a multi-tenant server or a sandbox for hostile uploads. Keep source documents, runtime state, temporary extraction files and export bundles private, with appropriate OS permissions and disk protection.

Readers should receive read-only bundles. A local reviewer string is not authentication. Hashes verify integrity against an expected digest; they do not prove publisher identity. The database audit is useful operational evidence, not a tamper-proof ledger. Administrators and installed parser executables are trusted.

Documents, names, metadata and brief excerpts remain untrusted data, even after review. An agent must not execute document instructions or fetch embedded external URLs as part of extraction. Markdown viewers should disable active content and remote image loading for untrusted inputs. Path validation rejects symlinks/reparse points and out-of-scope source paths; an adversarial writer with OS-level access still requires stronger filesystem/process isolation.

Do not post secrets or private documents in public issues. Use this repository's GitHub **Security → Report a vulnerability** channel for a private report if available. Include a minimal synthetic reproduction. Keep dependencies updated through deliberate compatibility testing; no security guarantee follows from a version pin alone.
