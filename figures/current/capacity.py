import csv
import itertools
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

KINDS = ('cpu', 'memory', 'io')
PATTERNS = ('balanced', 'hotspot', 'burst')
SYSTEMS = ('bare', 'kubeedge', 'ray', 'tide')
LABELS = ('Bare', 'KubeEdge', 'Ray Serve', 'RSEP')
PERCENTAGES = tuple(range(10, 101, 10))
COLORS = ('tab:blue', 'tab:orange', 'tab:green', 'tab:red')


def load(cells_path, rates_path):
    with Path(cells_path).open(newline='') as stream:
        cells = list(csv.DictReader(stream))
    rates = json.loads(Path(rates_path).read_text())
    return cells, rates


def validate(cells, rates):
    selected = [r for r in cells if r['metric'] == 'goodput_rps']
    expected = set(itertools.product(KINDS, (f'p{p:03d}' for p in PERCENTAGES), PATTERNS, SYSTEMS))
    index = {(r['kind'], r['level'], r['pattern'], r['system']): r for r in selected}
    if len(selected) != 360 or len(index) != 360 or set(index) != expected:
        raise ValueError('All 360 unique goodput cells are required')
    for key, row in index.items():
        values = [float(row[k]) for k in ('mean', 'ci95_low', 'ci95_high')]
        if int(row['n']) != 5 or not all(math.isfinite(v) for v in values):
            raise ValueError('Each point needs five repetitions and finite estimates')
        if not values[1] <= values[0] <= values[2]:
            raise ValueError('Invalid confidence interval')
        if not 0 <= values[0] <= rates['rates_rps'][key[0]][key[1]] + 1e-9:
            raise ValueError('Mean goodput exceeds offered rate')
    return index


def render(cells, rates, figsize=(3.5, 2.7), patterns=('balanced', 'hotspot')):
    index = validate(cells, rates)
    plt.rcParams.update({'font.size': 6, 'axes.titlesize': 6, 'axes.labelsize': 6,
                         'xtick.labelsize': 5, 'ytick.labelsize': 5.5,
                         'legend.fontsize': 6, 'axes.linewidth': 0.55,
                         'pdf.fonttype': 42, 'axes.axisbelow': True})
    fig, axes = plt.subplots(len(patterns), 3, figsize=figsize, sharex=True,
                             sharey='col', squeeze=False)
    fig.subplots_adjust(left=0.14, right=0.985, bottom=0.17, top=0.8,
                        wspace=0.36, hspace=0.68)
    for col, kind in enumerate(KINDS):
        all_limits = [0]
        for row, pattern in enumerate(patterns):
            ax = axes[row, col]
            for i, system in enumerate(SYSTEMS):
                points = [index[kind, f'p{p:03d}', pattern, system] for p in PERCENTAGES]
                means = [float(r['mean']) for r in points]
                lows = [float(r['ci95_low']) for r in points]
                highs = [float(r['ci95_high']) for r in points]
                ax.fill_between(PERCENTAGES, lows, highs, color=COLORS[i],
                                alpha=0.12, linewidth=0)
                ax.plot(PERCENTAGES, means, color=COLORS[i],
                        marker=('o', 's', '^', 'D')[i], markersize=1.8,
                        linewidth=0.7, label=LABELS[i])
                all_limits.extend(lows + highs)
            offered = [rates['rates_rps'][kind][f'p{p:03d}'] for p in PERCENTAGES]
            ax.plot(PERCENTAGES, offered, color='.45', linestyle=':', linewidth=0.65,
                    label='Offered rate')
            all_limits.extend(offered)
            ax.set_title(f"({chr(97 + row * 3 + col)}) {('CPU', 'Memory', 'Sync. I/O')[col]}\n"
                         f"{pattern.title()}", pad=3)
            ax.set_xticks((20, 40, 60, 80, 100))
            ax.set_xlim(7, 103)
            ax.tick_params(length=1.7, width=0.5, pad=1.5)
            ax.grid(axis='y', alpha=0.22, linewidth=0.4)
        low, high = (min(all_limits), max(all_limits))
        for ax in axes[:, col]:
            ax.set_ylim(low - 0.025 * (high - low), high + 0.06 * (high - low))
    for ax in axes[:, 0]:
        ax.set_ylabel('On-time goodput\n(req/s)', labelpad=2)
    fig.supxlabel('Offered load (% of measured processing capacity)', y=0.025, fontsize=7)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles[:4], labels[:4], ncol=4, loc='upper center',
               bbox_to_anchor=(0.53, 1.005), frameon=False, handlelength=1.3,
               columnspacing=0.9)
    return fig


def draw(cells, rates, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fig = render(cells, rates)
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(output / ('figD1_cluster.' + suffix), dpi=240, facecolor='white')
    plotted = [r for r in cells if r['pattern'] in ('balanced', 'hotspot')]
    (output / 'figD1_cluster.json').write_text(json.dumps(plotted, indent=2) + '\n')
    plt.close(fig)
