#!/usr/bin/env python3
"""Regenerate calibration table/figure from verified measured request traces."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    names = {"cpu": "CPU (SHA-256)", "memory": "Memory access", "io": "Synchronous file I/O"}
    data, rows, sources = [], [], {}
    for kind, label in names.items():
        run = args.root / kind
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["status"] == "completed" and manifest["exit_code"] == 0
        for relative, expected in manifest["files_sha256"].items():
            assert hashlib.sha256((run / relative).read_bytes()).hexdigest() == expected, relative
        raw = run / "run/raw.csv"
        with raw.open() as f:
            records = list(csv.DictReader(f))
        expected_count = json.loads((run / "run/run.json").read_text())["requests"]
        assert len(records) == expected_count and all(int(r["status"]) == 200 for r in records)
        values = sorted(int(r["worker_service_ns"]) / 1e6 for r in records if int(r["req_id"]) != 0)
        assert len(values) >= 19 and min(values) > 0
        p95 = values[math.ceil(.95 * len(values)) - 1]
        rows.append([kind, len(values), sum(values) / len(values), p95, math.ceil(p95)])
        data.append(values)
        sources[str(raw.relative_to(args.root))] = hashlib.sha256(raw.read_bytes()).hexdigest()
    with (args.root / "calibration_table.csv").open("w") as f:
        writer = csv.writer(f)
        writer.writerow(["workload", "warm_requests", "mean_service_ms", "p95_service_ms", "frozen_estimate_ms"])
        writer.writerows(rows)
    with (args.root / "calibration_table.tex").open("w") as f:
        f.write("\\begin{tabular}{lrrrr}\n\\hline\nWorkload & $n$ & Mean (ms) & P95 (ms) & Estimate (ms) \\\\\n\\hline\n")
        for kind, n, mean, p95, estimate in rows:
            f.write(f"{names[kind]} & {n} & {mean:.2f} & {p95:.2f} & {estimate} \\\\\n")
        f.write("\\hline\n\\end{tabular}\n")
    fig, ax = plt.subplots(figsize=(6.6, 3.8), constrained_layout=True)
    ax.boxplot(data, tick_labels=list(names.values()), showmeans=True)
    ax.set_ylabel("Measured worker service time (ms)")
    ax.set_title("Workload calibration on NJ-k1")
    ax.grid(axis="y", alpha=.25)
    ax.text(.5, -.2, "29 warm requests per workload; 2 allocated logical CPUs; 5 requests/s",
            ha="center", transform=ax.transAxes, fontsize=8)
    fig.savefig(args.root / "calibration_service.pdf", bbox_inches="tight")
    fig.savefig(args.root / "calibration_service.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    (args.root / "calibration_artifact_manifest.json").write_text(json.dumps(dict(
        purpose="Calibration evidence only; not a baseline comparison or replacement for A–F evaluation figures",
        sources_sha256=sources, estimate_rule="ceil nearest-rank p95 of warm service milliseconds",
        regenerate="python scripts/revision/summarize_calibration.py results-revision-20260928/calibration-01"), indent=2) + "\n")
    print(json.dumps(rows))


if __name__ == "__main__":
    main()
