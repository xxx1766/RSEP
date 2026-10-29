"""Render audited three-node Fig. 6 measurements; requires a complete campaign."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

SYSTEMS = ['bare', 'kubeedge', 'ray', 'tide']
LABELS = ['Bare', 'KubeEdge', 'Ray Serve', 'Tide']
COLORS = ['tab:blue', 'tab:orange', 'tab:green', 'tab:red']
KINDS = ['cpu', 'memory', 'io']
PATTERNS = ['balanced', 'hotspot', 'burst']


def figure(cells, rates, figsize=(3.5, 4.0), colors=None, load_fractions=None):
    """All 72 system/workload/load/pattern means; no outcome-based selection."""
    selected = [r for r in cells if r['metric'] == 'goodput_rps']
    if len(selected) != 72 or any(int(r['n']) != 5 for r in selected):
        raise ValueError('Fig. 6 requires all 72 cells with five repetitions each')
    index = {(r['kind'], r['level'], r['pattern'], r['system']): r for r in selected}
    if len(index) != 72:
        raise ValueError('Duplicate plotted cell')
    colors = COLORS if colors is None else colors
    plt.rcParams.update({'font.size': 7, 'axes.titlesize': 7,
        'axes.labelsize': 7, 'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5,
        'legend.fontsize': 7, 'axes.linewidth': .6, 'pdf.fonttype': 42,
        'axes.axisbelow': True})
    fig, axes = plt.subplots(3, 2, figsize=figsize)
    fig.subplots_adjust(left=.14, right=.985, bottom=.065, top=.825,
                        wspace=.28, hspace=.72)
    for row, kind in enumerate(KINDS):
        for col, level in enumerate(['low', 'high']):
            ax = axes[row, col]
            x = np.arange(3)
            limits = [0, rates[kind][level]]
            for i, system in enumerate(SYSTEMS):
                values = [index[kind, level, p, system] for p in PATTERNS]
                means = np.array([float(r['mean']) for r in values])
                lo = np.array([float(r['ci95_low']) for r in values])
                hi = np.array([float(r['ci95_high']) for r in values])
                limits.extend(lo); limits.extend(hi)
                ax.bar(x + (i - 1.5) * .20, means, .20,
                       yerr=np.vstack([means - lo, hi - means]), capsize=1.2,
                       error_kw={'elinewidth': .55, 'capthick': .55},
                       color=colors[i], label=LABELS[i])
            ax.axhline(rates[kind][level], color='.3', linestyle=':', lw=.7)
            ax.set_xticks(x, ['Balanced', 'Hotspot', 'Burst'])
            ax.set_xlim(-.45, 2.45)
            if load_fractions is not None:
                title = (f"({chr(97 + row * 2 + col)}) "
                         f"{['CPU', 'Memory', 'Sync. I/O'][row]}\n"
                         f"{load_fractions[level]:.0%} of $C_w$")
            else:
                title = (f"({chr(97 + row * 2 + col)}) "
                         f"{['CPU', 'Memory', 'Sync. I/O'][row]}, {level}\n"
                         f"{rates[kind][level]} req/s")
            ax.set_title(title, pad=3)
            ax.set_ylim(min(limits) * 1.06, max(limits) * 1.1)
            ax.grid(axis='y', alpha=.22, lw=.4)
            ax.tick_params(length=2, pad=2, width=.5)
    fig.supylabel('On-time goodput (req/s)', x=.015, y=.445, fontsize=7)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=2, frameon=False,
               bbox_to_anchor=(.55, 1.005), handlelength=1.3,
               columnspacing=1.7, labelspacing=.4)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cells', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--capacity-labels', action='store_true',
                        help='Label nominal 30%%/80%% capacity targets; verify integer-rounded rates')
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text())
    if (audit.get('status') != 'PASS' or audit.get('treatments', 0) < 360
            or audit.get('origin_runs', 0) < 720
            or hashlib.sha256(args.cells.read_bytes()).hexdigest() != audit['export_sha256']['cells.csv']
            or hashlib.sha256(args.manifest.read_bytes()).hexdigest() != audit['manifest_sha256']):
        raise ValueError('A complete, matching independent publication audit is required')
    cells = list(csv.DictReader(args.cells.open()))
    manifest = json.loads(args.manifest.read_text())
    fractions = {'low': .3, 'high': .8} if args.capacity_labels else None
    if fractions is not None:
        for kind in KINDS:
            capacity = float(manifest['capacity_rps'][kind])
            if (not np.isfinite(capacity) or capacity <= 0 or any(
                    manifest['rates_rps'][kind][level] != round(max(2, capacity * fraction))
                    for level, fraction in fractions.items())):
                raise ValueError('Capacity labels require rates rounded from 30%/80% of capacity')
    fig = figure(cells, manifest['rates_rps'], load_fractions=fractions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output)
    plt.close(fig)


if __name__ == '__main__':
    main()
