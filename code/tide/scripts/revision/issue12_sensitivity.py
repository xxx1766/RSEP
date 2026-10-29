#!/usr/bin/env python3
"""Check paired direction after excluding cells with prolonged treatment gaps."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads((args.base / "plan.json").read_text())
    grouped = defaultdict(list)
    excluded = []
    for cell in plan["cells"]:
        folder = args.base / "raw" / cell["id"]
        audit = json.loads((folder / "audit.json").read_text())
        recovery_path = folder / "recovery.json"
        recovery = json.loads(recovery_path.read_text()) if recovery_path.exists() else {}
        long_gap = "pair_gap_before_tide_seconds" in recovery
        if long_gap:
            excluded.append({"cell": cell["id"],
                             "gap_seconds": recovery["pair_gap_before_tide_seconds"]})
        key = f"{cell['pattern']}-p{cell['producers']}"
        grouped[key].append({"cell": cell["id"], "excluded_for_gap": long_gap,
                             "k8s_minus_tide_on_time": audit["systems"]["k8s"]["on_time"] -
                                                       audit["systems"]["tide"]["on_time"],
                             "k8s_minus_tide_yield": audit["systems"]["k8s"]["yield_fraction"] -
                                                   audit["systems"]["tide"]["yield_fraction"]})
    result = {"excluded_long_gap_pairs": excluded, "conditions": {}}
    for key, rows in sorted(grouped.items()):
        retained = [row for row in rows if not row["excluded_for_gap"]]
        if len(rows) != 3 or not retained:
            raise ValueError(f"missing repeat or retained cell for {key}")
        result["conditions"][key] = {
            "all_repeats": len(rows), "retained_repeats": len(retained),
            "all_k8s_minus_tide_on_time": [row["k8s_minus_tide_on_time"] for row in rows],
            "retained_k8s_minus_tide_on_time": [row["k8s_minus_tide_on_time"] for row in retained],
            "all_positive": all(row["k8s_minus_tide_on_time"] > 0 for row in rows),
            "retained_positive": all(row["k8s_minus_tide_on_time"] > 0 for row in retained),
            "retained_mean_yield_difference": statistics.mean(
                row["k8s_minus_tide_yield"] for row in retained),
        }
    with (args.base / "sensitivity.json").open("x") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
