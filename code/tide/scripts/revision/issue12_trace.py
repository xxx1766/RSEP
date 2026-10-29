#!/usr/bin/env python3
"""Generate the conference arrival shapes as frozen, identifiable JSONL traces."""

import argparse
import hashlib
import json
from pathlib import Path


def generate(pattern: str, producers: int, repeat: int) -> tuple[list[dict], int]:
    if pattern not in {"gradual", "burst"} or producers < 1 or repeat < 1:
        raise ValueError("pattern, producers and repeat must be valid")
    if pattern == "gradual":
        intervals = [(300, 500), (300, 300), (300, 100), (100, 0)]
    else:
        intervals = [(400, 300), (200, 0), (400, 300)]
    offsets = []
    current_ms = 0
    for count, interval_ms in intervals:
        for _ in range(count):
            offsets.append(current_ms * 1_000_000)
            current_ms += interval_ms
    window_ms = current_ms
    rows = [
        {"id": f"issue12-{pattern}-p{producer:02d}-r{repeat:02d}-i{index:04d}",
         "scheduled_offset_ns": offset}
        for index, offset in enumerate(offsets)
        for producer in range(producers)
    ]
    return rows, window_ms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", choices=["gradual", "burst"], required=True)
    parser.add_argument("--producers", type=int, required=True)
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows, window_ms = generate(args.pattern, args.producers, args.repeat)
    raw = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows).encode()
    with args.out.open("xb") as target:
        target.write(raw)
    print(json.dumps({"trace": str(args.out), "sha256": hashlib.sha256(raw).hexdigest(),
                      "offered": len(rows), "window_ms": window_ms,
                      "pattern": args.pattern, "producers": args.producers, "repeat": args.repeat}))


if __name__ == "__main__":
    main()
