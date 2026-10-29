#!/usr/bin/env python3
"""Write MANIFEST.sha256 for every tracked file (or every file in a zip)."""
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"MANIFEST.sha256"}


def tracked_files():
    if (ROOT / ".git").exists():
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True
        ).splitlines()
    else:
        out = [
            str(p.relative_to(ROOT))
            for p in ROOT.rglob("*")
            if p.is_file()
        ]
    return [
        f for f in out
        if f not in SKIP
        and not f.endswith((".pyc", ".DS_Store"))
        and "__pycache__/" not in f
    ]


def main():
    lines = []
    for name in sorted(tracked_files()):
        digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    (ROOT / "MANIFEST.sha256").write_text("".join(lines))
    print(f"MANIFEST.sha256: {len(lines)} files")


if __name__ == "__main__":
    main()
