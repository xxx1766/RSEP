#!/usr/bin/env python3
"""Aggregate three complete paired repeats per frozen issue-12 condition."""

import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


T95_DF2 = 4.302652729911275


def describe(values: list[float]) -> dict:
    mean = statistics.mean(values)
    radius = T95_DF2 * statistics.stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0
    return {"mean": mean, "ci95": [mean - radius, mean + radius],
            "min": min(values), "max": max(values), "repeats": values}


def paired_summary(audits: list[dict]) -> dict:
    grouped = defaultdict(list)
    for audit in audits:
        cell = audit["cell"]
        grouped[f"{cell['pattern']}-p{cell['producers']}"].append(audit)
    result = {}
    for key, entries in grouped.items():
        entries.sort(key=lambda audit: audit["cell"]["repeat"])
        if [audit["cell"]["repeat"] for audit in entries] != [1, 2, 3]:
            raise ValueError(f"incomplete or duplicate repeat set for {key}")
        row = {"cells": [audit["cell"]["id"] for audit in entries if "id" in audit["cell"]]}
        for system in ("tide", "k8s"):
            row[f"{system}_goodput_rps"] = describe([
                audit["systems"][system]["on_time_goodput_rps"] for audit in entries])
            row[f"{system}_yield"] = describe([
                audit["systems"][system]["yield_fraction"] for audit in entries])
        row["goodput_difference_k8s_minus_tide_rps"] = describe([
            audit["systems"]["k8s"]["on_time_goodput_rps"] -
            audit["systems"]["tide"]["on_time_goodput_rps"] for audit in entries])
        row["yield_difference_k8s_minus_tide"] = describe([
            audit["systems"]["k8s"]["yield_fraction"] -
            audit["systems"]["tide"]["yield_fraction"] for audit in entries])
        result[key] = row
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    args = p.parse_args()
    plan = json.loads((args.base / "plan.json").read_text())
    audits = [json.loads((args.base / "raw" / cell["id"] / "audit.json").read_text())
              for cell in plan["cells"]]
    result = paired_summary(audits)
    if set(result) != {"gradual-p1", "gradual-p2", "burst-p1", "burst-p2"}:
        raise ValueError("not all four planned conditions were audited")
    with (args.base / "summary.json").open("x") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
