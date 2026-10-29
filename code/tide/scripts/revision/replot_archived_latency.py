#!/usr/bin/env python3
"""Plot archived platform-summary latency without reconstructing raw requests."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import LogLocator, NullFormatter


SERIES = (
    ("Tide", "23042301tide-noconf-noconf", "#be3d35", "o"),
    ("Bare metal", "23041801metal-cloud_edge-noconf", "#4d5965", "s"),
    ("KubeEdge", "23041801kube-cloud_edge-all", "#237bb5", "^"),
    ("KubeEdge 2C4C", "23041801kube-edge_perfer-2c4c-init_1", "#78b7d2", "v"),
    ("Ray Task", "23042002ray-cloud_edge-task", "#55936b", "D"),
    ("Ray Actor-40", "23042102ray-cloud_edge-actor", "#8873a9", "P"),
)
RATES = tuple(range(5, 51, 5))


def read_summary(source: Path):
    wanted = {item[1] for item in SERIES}
    values = {}
    with source.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["plat"] not in wanted:
                continue
            rate = int(row["fps"])
            if rate not in RATES:
                continue
            key = (row["plat"], rate)
            if key in values:
                raise ValueError(f"duplicate configuration/rate: {key}")
            mean = float(row["mean"])
            p95 = float(row["95%"])
            if mean <= 0 or p95 < mean:
                raise ValueError(f"invalid summary latency: {key}")
            values[key] = {"mean_ms": mean, "p95_ms": p95, "mean_case_count": float(row["count"])}
    expected = {(item[1], rate) for item in SERIES for rate in RATES}
    if set(values) != expected:
        raise ValueError(f"missing={sorted(expected - set(values))}; unexpected={sorted(set(values) - expected)}")
    return values


def plot(values, output: Path):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "pdf.fonttype": 42})
    fig, axes = plt.subplots(1, 2, figsize=(7.05, 3.3), sharex=True, sharey=True)
    for ax, (field, title) in zip(axes, (("mean_ms", "Mean"), ("p95_ms", "95th percentile"))):
        for name, key, color, marker in SERIES:
            ys = [values[(key, rate)][field] for rate in RATES]
            ax.plot(RATES, ys, label=name, color=color, marker=marker, linewidth=1.5, markersize=3.7)
        ax.set_yscale("log")
        ax.set_ylim(70, 90000)
        ax.set_xticks((5, 10, 20, 30, 40, 50))
        ax.set_xlabel("Offered requests/s")
        ax.set_title(title, fontsize=9)
        ax.grid(axis="y", which="major", color="#d5d9dd", linewidth=0.6)
        ax.yaxis.set_minor_locator(LogLocator(base=10, subs=(2, 5)))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("Latency (ms, log scale)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, -0.015), fontsize=7.6)
    fig.tight_layout(rect=(0, 0.17, 1, 1), w_pad=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    fig.savefig(output.with_suffix(".png"), dpi=220, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = read_summary(args.input)
    plot(values, args.output)
    audit = {
        "source": str(args.input),
        "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "scope": "archived per-configuration summaries; not request-level distributions or conference-run configuration",
        "series": [
            {"name": name, "source_key": key, "points": [
                {"rate": rate, **values[(key, rate)]} for rate in RATES
            ]} for name, key, _, _ in SERIES
        ],
    }
    args.output.with_suffix(".json").write_text(json.dumps(audit, indent=2) + "\n")


if __name__ == "__main__":
    main()
