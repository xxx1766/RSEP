#!/usr/bin/env python3
import ast
import hashlib
import gzip
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".tex", ".bib", ".aux", ".log", ".ipynb", ".pem", ".key"}
PRIVATE_NAMES = {".env", "kubeconfig", "admin.conf", "WORKER_REPORT.md"}
PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
]


def main():
    listing = ROOT / "RELEASE_FILES.txt"
    names = listing.read_text().splitlines()
    errors = []
    if len(names) != len(set(names)):
        errors.append("Duplicate release path")
    hashes = {}
    for line in (ROOT / "MANIFEST.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        hashes[name] = digest
    if set(names) != set(hashes):
        errors.append("Release list and checksum manifest disagree")
    size = 0
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("Invalid path: " + name)
            continue
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            errors.append("Missing or linked file: " + name)
            continue
        if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append("Excluded extension: " + name)
        if relative.name in PRIVATE_NAMES or any(
            part.startswith("results-") or part in {"raw", "_supervision", ".git"}
            for part in relative.parts
        ):
            errors.append("Excluded path: " + name)
        data = path.read_bytes()
        size += len(data)
        if hashlib.sha256(data).hexdigest() != hashes.get(name):
            errors.append("Checksum mismatch: " + name)
        try:
            if relative.suffix == ".gz":
                data = gzip.decompress(data)
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        decoded = str(json.loads(content)) if relative.suffix == ".json" else content
        if re.search(r"[\u4e00-\u9fff]", decoded):
            errors.append("Non-English release text: " + name)
        if ("/" + "Users" + "/") in content:
            errors.append("Machine-specific path: " + name)
        if any(pattern.search(content) for pattern in PATTERNS):
            errors.append("Credential pattern: " + name)
        if relative.suffix == ".py":
            try:
                ast.parse(content, filename=name)
            except SyntaxError as exc:
                errors.append("Python syntax: " + name + ": " + str(exc))
    report = {"files": len(names), "bytes": size, "errors": errors}
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
