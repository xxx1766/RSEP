#!/usr/bin/env python3
"""Validate and archive the prepared release without staging or pushing Git."""
import argparse
import hashlib
from pathlib import Path
import zipfile

from make_manifest import ROOT, release_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    hashes = {}
    for line in (ROOT / "MANIFEST.sha256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        hashes[name] = digest
    names = release_files()
    if set(names) != set(hashes):
        raise SystemExit("Release list and checksum manifest differ")
    for name in names:
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != hashes[name]:
            raise SystemExit("Checksum mismatch: " + name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names + ["MANIFEST.sha256"]:
            archive.write(ROOT / name, "RSEP/" + name)
    print("Prepared archive:", args.output)
    print("Files:", len(names) + 1)


if __name__ == "__main__":
    main()
