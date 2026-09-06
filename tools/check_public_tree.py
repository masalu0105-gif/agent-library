"""Small disclosure guard for tracked and non-ignored files; not a secret scanner replacement."""
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "google-token": re.compile(rb"ya29\.[A-Za-z0-9_-]{25,}"),
    "cloud-document-url": re.compile(rb"(?:drive|docs)\.google\.com/(?:file/d/|drive/folders/|document/d/|spreadsheets/d/)[A-Za-z0-9_-]{15,}"),
    "private-windows-path": re.compile(rb"[A-Za-z]:[\\/](?:Users|projects)[\\/][^\s\"']+", re.I),
    "private-unix-path": re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
}
BLOCKED = {".pdf", ".doc", ".docx", ".xlsx", ".pptx", ".db", ".sqlite", ".sqlite3", ".pem", ".key", ".zip"}


def main():
    result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                            cwd=ROOT, capture_output=True, check=True)
    names = sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})
    failures = []
    for name in names:
        path = ROOT / name
        if path.suffix.lower() in BLOCKED or path.is_symlink():
            failures.append((name, "private-artifact-or-link"))
            continue
        data = path.read_bytes()
        if len(data) > 1_000_000 or b"\0" in data:
            failures.append((name, "binary-or-large-file"))
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                failures.append((name, label))
    if failures:
        for name, label in failures:
            print(f"FAIL {name}: {label}")  # Never echo the matched secret.
        return 1
    print(f"PASS: {len(names)} public source files checked; no blocked artifacts or matched disclosure patterns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
