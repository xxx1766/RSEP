#!/usr/bin/env python3
"""Summarize low-rate timestamped ICMP RTT monitors by formal cell window."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics


PATTERN = re.compile(r"^\[(\d+(?:\.\d+)?)\].*icmp_seq=(\d+).*time=(\d+(?:\.\d+)?) ms$")


def parse_ping(body: str, source: int = 0) -> list[dict]:
    result = []
    for line in body.splitlines():
        match = PATTERN.match(line)
        if match:
            result.append({"unix_seconds": float(match[1]), "sequence": int(match[2]), "source": source,
                           "rtt_ms": float(match[3])})
    return result


def summarize_window(rows: list[dict], start: float, end: float) -> dict:
    selected = sorted((row for row in rows if start <= row["unix_seconds"] <= end),
                      key=lambda row: row["unix_seconds"])
    if not selected:
        return {"received": 0, "median_ms": None, "p95_ms": None,
                "sequence_gap_count": None, "first_unix_seconds": None, "last_unix_seconds": None}
    values = sorted(row["rtt_ms"] for row in selected)
    by_source = {}
    for row in selected:
        by_source.setdefault(row.get("source", 0), []).append(row["sequence"])
    sequence_gaps = sum(max(0, current - previous - 1)
                        for sequences in by_source.values()
                        for previous, current in zip(sequences, sequences[1:]))
    return {"received": len(selected), "median_ms": statistics.median(values),
            "p95_ms": values[math.ceil(.95 * len(values)) - 1],
            "sequence_gap_count": sequence_gaps,
            "first_unix_seconds": selected[0]["unix_seconds"],
            "last_unix_seconds": selected[-1]["unix_seconds"]}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    args = p.parse_args()
    plan = json.loads((args.base / "plan.json").read_text())
    monitors = {}
    for node in ("nj2", "nj3"):
        paths = [args.base / f"continuous-rtt-{node}.log",
                 args.base / f"continuous-rtt-{node}-part2.log"]
        monitors[node] = [row for source, path in enumerate(paths) if path.exists()
                          for row in parse_ping(path.read_text(), source=source)]
    output = {}
    for cell in plan["cells"]:
        folder = args.base / "raw" / cell["id"]
        for system in cell["order"]:
            start = datetime.fromisoformat((folder / f"{system}-start-utc.txt").read_text().strip()).timestamp()
            end = datetime.fromisoformat((folder / f"{system}-end-utc.txt").read_text().strip()).timestamp()
            output[f"{cell['id']}/{system}"] = {
                node: summarize_window(rows, start, end) for node, rows in monitors.items()}
    with (args.base / "continuous-rtt-summary.json").open("x") as target:
        json.dump(output, target, indent=2)
        target.write("\n")
    print(json.dumps({"windows": len(output), "monitored": sum(v["nj2"]["received"] > 0 for v in output.values())}))


if __name__ == "__main__":
    main()
