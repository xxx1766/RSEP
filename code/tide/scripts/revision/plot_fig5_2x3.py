#!/usr/bin/env python3
"""Render six measured ablation rates as a 2x3 Fig. 5 preview.

Reads an audited ``summary/per_cell.csv`` made by ``scripts/analyze.py``.
Never interpolates rates or fills missing cells. A one-run preview is visibly
labelled as exploratory and is not a substitute for a repeated experiment.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


CELLS = ["C1", "C2", "C3", "C4", "C5", "C6"]
RATES = [1, 2, 4, 8, 16, 32]
COLORS = ["#b5b5b5", "#e3a839", "#75a868", "#4d88bf", "#df7063", "#8d69b5"]
METRICS = [
    ("avg_ms_mean", "Mean latency (ms)"),
    ("p95_ms_mean", "P95 latency (ms)"),
    ("std_ms_mean", "Latency SD (ms)"),
    ("throughput_mean", "Completion throughput (req/s)"),
]


def load_summary(path: Path) -> tuple[dict[tuple[int, str], dict], int]:
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    values = {(int(row["rps"]), row["cell"]): row for row in rows}
    expected = {(rate, cell) for rate in RATES for cell in CELLS}
    if set(values) != expected or len(rows) != len(expected):
        missing = sorted(expected - set(values))
        extra = sorted(set(values) - expected)
        raise ValueError(f"expected 36 distinct measured cells; missing={missing}, extra={extra}")
    runs = {int(row["runs"]) for row in rows}
    if len(runs) != 1 or next(iter(runs)) < 1:
        raise ValueError(f"inconsistent run count: {runs}")
    for row in rows:
        for key, _ in METRICS:
            if not row[key]:
                raise ValueError(f"missing {key} for {row['cell']} at {row['rps']} RPS")
    return values, next(iter(runs))


def draw(values: dict[tuple[int, str], dict], runs: int, out: Path) -> None:
    fig = plt.figure(figsize=(17.5, 9.6), dpi=150)
    outer = fig.add_gridspec(2, 3, left=.045, right=.99, top=.91, bottom=.105,
                             wspace=.24, hspace=.31)
    for idx, rate in enumerate(RATES):
        inner = outer[idx // 3, idx % 3].subgridspec(
            3, 2, height_ratios=[.15, 1, 1], wspace=.36, hspace=.48)
        title_ax = fig.add_subplot(inner[0, :])
        title_ax.axis("off")
        title_ax.text(.5, .5, f"Offered rate: {rate} req/s", ha="center",
                      va="center", fontsize=11, fontweight="bold")
        for metric_idx, (key, title) in enumerate(METRICS):
            ax = fig.add_subplot(inner[1 + metric_idx // 2, metric_idx % 2])
            y = [float(values[rate, cell][key]) for cell in CELLS]
            ci_key = key.removesuffix("_mean") + "_ci95_half"
            ci = [float(values[rate, cell].get(ci_key, 0)) for cell in CELLS]
            ax.bar(range(len(CELLS)), y, color=COLORS, width=.72,
                   yerr=ci if runs > 1 else None,
                   error_kw={"elinewidth": .8, "capsize": 2, "capthick": .8})
            ax.set_title(title, fontsize=8.5, pad=3)
            ax.set_xticks(range(len(CELLS)), CELLS, fontsize=7)
            ax.tick_params(axis="y", labelsize=7)
            ax.set_ylim(bottom=0)
            ax.grid(axis="y", alpha=.25)
            ax.set_axisbelow(True)
            ax.ticklabel_format(axis="y", style="sci", scilimits=(-2, 4))
            ax.yaxis.get_offset_text().set_fontsize(7)
    qualifier = ("Exploratory preview: one run per cell; no run-level uncertainty" if runs == 1
                 else f"{runs} independent runs per cell; error bars: run-level 95% CI")
    fig.suptitle(f"NJ-k1 RSEP ablation: six rates × six isolation settings\n{qualifier}",
                 fontsize=13, y=.985)
    fig.legend(handles=[Patch(facecolor=COLORS[i], label=f"{CELLS[i]}: {label}")
                        for i, label in enumerate([
                            "CC off / PI off / EI off", "CC on / PI off / EI off",
                            "CC off / PI off / EI on", "CC on / PI off / EI on",
                            "CC off / PI on / EI on", "CC on / PI on / EI on",
                        ])], ncol=3, loc="lower center", bbox_to_anchor=(.5, .005),
               fontsize=8, frameon=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    values, runs = load_summary(args.summary)
    draw(values, runs, args.out)
    print(f"Rendered 36 measured cells, {runs} run(s) each: {args.out}")


if __name__ == "__main__":
    main()
