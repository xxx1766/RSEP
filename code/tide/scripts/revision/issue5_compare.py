#!/usr/bin/env python3
"""Report five-repeat paired native Tide weight contrasts from audited runs."""

import argparse
import collections
import csv
import json
import statistics
from pathlib import Path


def load(path):
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for key in ("rate", "repeat", "submitted", "success", "on_time"):
            row[key] = int(row[key])
        for key in ("goodput_rps", "cohort_on_time_pct"):
            row[key] = float(row[key])
        for key in ("failure_statuses", "placements"):
            row[key] = json.loads(row[key])
    return rows


def compare(layouts):
    output = []
    for layout, path in layouts.items():
        rows = load(path)
        groups = collections.defaultdict(dict)
        for row in rows:
            groups[(row["rate"], row["pattern"], row["repeat"])][row["weight"]] = row
        assert len(groups) == 30
        for rate in (50, 700, 1000):
            for pattern in ("steady", "burst"):
                blocks = [groups[(rate, pattern, repeat)] for repeat in range(1, 6)]
                for weight in ("equal", "latency", "neighbor"):
                    selected = [block[weight] for block in blocks]
                    deltas = [block[weight]["cohort_on_time_pct"] - block["equal"]["cohort_on_time_pct"]
                              for block in blocks]
                    counts = collections.Counter()
                    failures = collections.Counter()
                    for row in selected:
                        counts.update(row["placements"])
                        failures.update(row["failure_statuses"])
                    output.append({
                        "layout": layout, "rate": rate, "pattern": pattern, "weight": weight,
                        "on_time_pct_mean": statistics.mean(row["cohort_on_time_pct"] for row in selected),
                        "on_time_pct_min": min(row["cohort_on_time_pct"] for row in selected),
                        "on_time_pct_max": max(row["cohort_on_time_pct"] for row in selected),
                        "goodput_rps_mean": statistics.mean(row["goodput_rps"] for row in selected),
                        "delta_vs_equal_pp_mean": statistics.mean(deltas),
                        "delta_vs_equal_pp_min": min(deltas),
                        "delta_vs_equal_pp_max": max(deltas),
                        "positive_repeats": sum(value > 0 for value in deltas),
                        "negative_repeats": sum(value < 0 for value in deltas),
                        "placements": dict(counts), "failures": dict(failures),
                    })
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("nj", type=Path)
    parser.add_argument("bj", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = compare({"NJ": args.nj, "NJ-BJ": args.bj})
    args.output.write_text(json.dumps(rows, indent=2) + "\n")
    for row in rows:
        print(f"{row['layout']:5} {row['rate']:4} {row['pattern']:6} {row['weight']:8} "
              f"on-time={row['on_time_pct_mean']:6.2f}% "
              f"delta={row['delta_vs_equal_pp_mean']:+6.2f} pp "
              f"range=[{row['delta_vs_equal_pp_min']:+.2f},{row['delta_vs_equal_pp_max']:+.2f}] "
              f"sign=+{row['positive_repeats']}/-{row['negative_repeats']}")


if __name__ == "__main__":
    main()
