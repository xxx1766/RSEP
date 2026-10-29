#!/usr/bin/env python3
"""Aggregate Tide RSEP ablation results and render paper-style plots.

Input layout:
    results/<cell>/rps<N>/run<k>/raw.csv
where <cell> ∈ {C1..C6}, <N> ∈ {1,2,4,8,16,32,64}, <k> ∈ {1..5}
and raw.csv has columns: req_id,t_submit_ns,t_reply_ns,latency_ms,status.

Outputs (under results/):
    summary/per_run.csv   — one row per (cell, rps, run) with agg metrics
    summary/per_cell.csv  — one row per (cell, rps), mean±spread over runs
    plots/fig5.png        — RSEP paper Fig. 5 style: 6 cells × N RPS small-multiples
    plots/fig6_cc.png     — single-factor CC relative change (paper Fig. 6)
    plots/fig7_pi.png     — single-factor PI relative change (paper Fig. 7)
    plots/fig8_ei.png     — single-factor EI relative change (paper Fig. 8)

Usage:
    python3 scripts/analyze.py [--root results] [--warmup-frac 0.10]

Notes:
  - Paper Fig 5 (RSEP §VI.B) does NOT explicitly warmup-trim. We output both
    with-trim and without-trim views to let the reader see the cold-start
    contribution of per-request container creation (which is the signal
    RSEP's isolation is supposed to amortize — trimming may hide it).
  - Cohort/window and warmup are selected on ALL requests before outcomes.
    Latencies use status==200. See revision/metrics.py for exact rate semantics.
    With manifests, rates count completions inside the stated window; yields
    also include drained outcomes. Without manifests, the window spans first
    retained submission to last reply of any outcome (including drain).
    QoS goodput is unavailable unless --qos-ms or per-row qos_ms is supplied.
"""
from __future__ import annotations

import argparse
import json
import importlib.util
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

# Cell → (CC, PI, EI) tuple, in the paper's (Concurrency, Physical, Env) order.
CELL_SPEC = {
    "C1": ("off", "off", "off"),
    "C2": ("on",  "off", "off"),
    "C3": ("off", "off", "on"),
    "C4": ("on",  "off", "on"),
    "C5": ("off", "on",  "on"),
    "C6": ("on",  "on",  "on"),
}
CELL_ORDER = list(CELL_SPEC.keys())
RPS_RE = re.compile(r"rps(\d+)$")
RUN_RE = re.compile(r"run(\d+)$")


# Load by path so legacy imports and direct script execution both work.
_metrics_spec = importlib.util.spec_from_file_location(
    "revision_metrics", Path(__file__).parent / "revision" / "metrics.py")
_metrics = importlib.util.module_from_spec(_metrics_spec)
_metrics_spec.loader.exec_module(_metrics)


def _agg_one_csv(path: Path, warmup_frac: float, qos_ms: float | None = None) -> dict | None:
    try:
        records = _metrics.read_records(path)
        manifest_path = path.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        if manifest.get("status") not in (None, "completed"):
            raise ValueError("run manifest is not completed")
        if manifest.get("schema_version") == 1 or "raw_sha256" in manifest or "expected_count" in manifest:
            records = _metrics.validate_completed(path, manifest_path)
        result = _metrics.aggregate_records(records, warmup_frac, qos_ms, manifest)
        return {k: np.nan if v is None else v for k, v in result.items()}
    except (ValueError, OSError) as e:
        print(f"[warn] cannot aggregate {path}: {e}", file=sys.stderr)
        return None


def collect_per_run(root: Path, warmup_frac: float, qos_ms: float | None = None) -> pd.DataFrame:
    rows = []
    for cell_dir in sorted(root.iterdir()):
        if not cell_dir.is_dir() or cell_dir.name not in CELL_SPEC:
            continue
        cell = cell_dir.name
        for rps_dir in sorted(cell_dir.iterdir()):
            m = RPS_RE.match(rps_dir.name)
            if not m:
                continue
            rps = int(m.group(1))
            for run_dir in sorted(rps_dir.iterdir()):
                m2 = RUN_RE.match(run_dir.name)
                if not m2:
                    continue
                run = int(m2.group(1))
                csv = run_dir / "raw.csv"
                if not csv.exists():
                    continue
                agg = _agg_one_csv(csv, warmup_frac, qos_ms)
                if agg is None:
                    continue
                cc, pi, ei = CELL_SPEC[cell]
                rows.append({
                    "cell": cell, "cc": cc, "pi": pi, "ei": ei,
                    "rps": rps, "run": run, **agg,
                })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["cell", "rps", "run"]).reset_index(drop=True)


def collect_per_cell(per_run: pd.DataFrame) -> pd.DataFrame:
    if per_run.empty:
        return per_run
    g = per_run.groupby(["cell", "cc", "pi", "ei", "rps"], as_index=False)
    agg = g.agg(
        runs=("run", "count"),
        avg_ms_mean=("avg_ms", "mean"),
        avg_ms_min=("avg_ms", "min"),
        avg_ms_max=("avg_ms", "max"),
        p95_ms_mean=("p95_ms", "mean"),
        std_ms_mean=("std_ms", "mean"),
        throughput_mean=("throughput", "mean"),
        drop_pct_mean=("drop_pct", "mean"),
        ontime_goodput_mean=("ontime_goodput", "mean"),
        success_yield_mean=("success_yield", "mean"),
        on_time_yield_mean=("on_time_yield", "mean"),
    )
    return agg.sort_values(["cell", "rps"]).reset_index(drop=True)


def plot_fig5(per_cell: pd.DataFrame, outpath: Path) -> None:
    if per_cell.empty:
        return
    metrics = [
        ("avg_ms_mean", "Avg Latency (ms)"),
        ("p95_ms_mean", "95th Latency (ms)"),
        ("std_ms_mean", "Std Latency (ms)"),
        ("throughput_mean", "Throughput (req/s)"),
    ]
    rps_points = sorted(per_cell["rps"].unique())
    fig, axes = plt.subplots(len(metrics), 1, figsize=(10, 3.2 * len(metrics)), sharex=True)
    x = np.arange(len(rps_points))
    width = 0.8 / len(CELL_ORDER)
    cell_colors = plt.get_cmap("tab10").colors
    for ax, (col, label) in zip(axes, metrics):
        for i, cell in enumerate(CELL_ORDER):
            sub = per_cell[per_cell["cell"] == cell].set_index("rps").reindex(rps_points)
            ax.bar(x + i * width, sub[col].values, width, label=cell,
                   color=cell_colors[i % len(cell_colors)])
        ax.set_ylabel(label)
        if "Latency" in label or "Throughput" in label:
            ax.set_yscale("symlog", linthresh=1.0)
        ax.grid(True, alpha=0.3)
    axes[-1].set_xticks(x + width * (len(CELL_ORDER) - 1) / 2)
    axes[-1].set_xticklabels([str(r) for r in rps_points])
    axes[-1].set_xlabel("RPS")
    axes[0].legend(ncol=6, loc="upper left", fontsize=8)
    fig.suptitle("RSEP single-machine ablation — all 6 cells vs RPS", fontsize=11)
    fig.tight_layout()
    outpath.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def plot_single_factor(per_cell: pd.DataFrame, factor: str, outpath: Path) -> None:
    """Paper Fig. 6/7/8 style: for each setting of the *other two* switches,
    plot (result_on - result_off) / result_off as a percentage per RPS.

    `factor` ∈ {'cc', 'pi', 'ei'}.
    """
    if per_cell.empty:
        return
    other = [f for f in ("cc", "pi", "ei") if f != factor]
    rps_points = sorted(per_cell["rps"].unique())
    metrics = [
        ("avg_ms_mean", "Avg Latency"),
        ("p95_ms_mean", "95th Latency"),
        ("std_ms_mean", "Std Latency"),
        ("throughput_mean", "Throughput"),
    ]
    combos = per_cell[other].drop_duplicates().to_records(index=False).tolist()

    fig, axes = plt.subplots(1, len(metrics), figsize=(4 * len(metrics), 3.4), sharex=True)
    if len(metrics) == 1:
        axes = [axes]
    x = np.arange(len(rps_points))
    width = 0.8 / max(1, len(combos))
    colors = plt.get_cmap("tab10").colors
    for ax, (col, label) in zip(axes, metrics):
        for i, combo in enumerate(combos):
            cond = {other[k]: combo[k] for k in range(len(other))}
            on_df = per_cell[(per_cell[factor] == "on") &
                             (per_cell[other[0]] == cond[other[0]]) &
                             (per_cell[other[1]] == cond[other[1]])].set_index("rps").reindex(rps_points)
            off_df = per_cell[(per_cell[factor] == "off") &
                              (per_cell[other[0]] == cond[other[0]]) &
                              (per_cell[other[1]] == cond[other[1]])].set_index("rps").reindex(rps_points)
            rel = (on_df[col] - off_df[col]) / off_df[col] * 100.0
            lbl = f"{other[0]}={cond[other[0]]}, {other[1]}={cond[other[1]]}"
            ax.bar(x + i * width, rel.values, width, label=lbl, color=colors[i % len(colors)])
        ax.axhline(0, color="k", lw=0.7)
        ax.set_title(label)
        ax.set_ylabel("Relative change (%)")
        ax.grid(True, alpha=0.3)
    axes[-1].set_xticks(x + width * (len(combos) - 1) / 2)
    axes[-1].set_xticklabels([str(r) for r in rps_points])
    axes[-1].set_xlabel("RPS")
    axes[0].legend(fontsize=8, loc="best")
    fig.suptitle(f"Single-factor effect of enabling {factor.upper()}", fontsize=11)
    fig.tight_layout()
    outpath.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="results", help="root dir of raw results")
    parser.add_argument("--warmup-frac", type=float, default=0.10,
                        help="fraction of leading requests to drop (default 0.10)")
    parser.add_argument("--no-trim-also", action="store_true",
                        help="also emit a no-warmup-trim set under results/summary_raw/")
    parser.add_argument("--qos-ms", type=float, default=None,
                        help="explicit QoS threshold; otherwise use per-row qos_ms")
    args = parser.parse_args()
    if not 0 <= args.warmup_frac < 1:
        parser.error("--warmup-frac must be in [0, 1)")
    if args.qos_ms is not None and (not np.isfinite(args.qos_ms) or args.qos_ms <= 0):
        parser.error("--qos-ms must be positive and finite")

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"results root not found: {root}", file=sys.stderr)
        return 1

    def run(suffix: str, warmup: float) -> None:
        per_run = collect_per_run(root, warmup, args.qos_ms)
        if per_run.empty:
            print(f"no data under {root}", file=sys.stderr)
            return
        per_cell = collect_per_cell(per_run)
        summary_dir = root / f"summary{suffix}"
        plots_dir = root / f"plots{suffix}"
        summary_dir.mkdir(parents=True, exist_ok=True)
        per_run.to_csv(summary_dir / "per_run.csv", index=False)
        per_cell.to_csv(summary_dir / "per_cell.csv", index=False)
        plot_fig5(per_cell, plots_dir / "fig5.png")
        plot_single_factor(per_cell, "cc", plots_dir / "fig6_cc.png")
        plot_single_factor(per_cell, "pi", plots_dir / "fig7_pi.png")
        plot_single_factor(per_cell, "ei", plots_dir / "fig8_ei.png")
        print(f"[analyze] {suffix or '(trim)'}: {len(per_run)} runs, {len(per_cell)} cells → {summary_dir}")

    run("", args.warmup_frac)
    if args.no_trim_also:
        run("_raw", 0.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
