#!/usr/bin/env python3
"""Hash the explicit prepared release list, including unstaged curated files."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def release_files():
    listing = ROOT / "RELEASE_FILES.txt"
    if not listing.is_file():
        raise SystemExit("Missing RELEASE_FILES.txt; define the prepared release first.")
    names = [line.strip() for line in listing.read_text().splitlines() if line.strip()]
    if len(names) != len(set(names)):
        raise SystemExit("Duplicate release path")
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("Unsafe release path: " + name)
        if not (ROOT / name).is_file():
            raise SystemExit("Missing release file: " + name)
    return sorted(name for name in names if name != "MANIFEST.sha256")


def main():
    lines = [hashlib.sha256((ROOT / name).read_bytes()).hexdigest() + "  " + name
             for name in release_files()]
    (ROOT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    print("Manifest files:", len(lines))


if __name__ == "__main__":
    main()
