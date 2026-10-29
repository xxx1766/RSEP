"""Plot the eight original controlled-placement table values, without substitution."""
from pathlib import Path
import hashlib
import json
import re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
meta = json.loads((HERE / 'GOODPUT_YIELD_VALUES.json').read_text())
source = HERE.parent / 'paper' / meta['source']
assert hashlib.sha256(source.read_bytes()).hexdigest() == meta['source_sha256']
original = re.findall(r'(Gradual|Burst) & ([\d.]+) & ([\d.]+) & ([\d.]+) & ([\d.]+)', source.read_text())
assert [list(row.values()) for row in meta['values']] == [list(row) for row in original]
plt.rcParams.update({'font.size': 7, 'axes.titlesize': 7, 'axes.labelsize': 6.5,
                     'xtick.labelsize': 7, 'ytick.labelsize': 6.5,
                     'axes.linewidth': .5, 'pdf.fonttype': 42, 'axes.axisbelow': True})
fig, axes = plt.subplots(1, 2, figsize=(3.5, 1.5))
fig.subplots_adjust(left=.10, right=.99, bottom=.18, top=.71, wspace=.42)
for ax, metric, title, unit, ylim, ticks in zip(
        axes, ['goodput', 'ddl_yield'], ['(a) Goodput', '(b) DDL yield'],
        ['Requests/s', '%'], [4.15, 108], [[0, 2, 4], [0, 50, 100]]):
    for j, (system, label, color) in enumerate([
            ('tide', 'Tide', '#2875a3'), ('k8s_rr', 'K8s RR', '#bd7847')]):
        labels = [row[metric+'_'+system] for row in meta['values']]
        bars = ax.bar(np.arange(2) + (j-.5)*.34, [float(v) for v in labels],
                      width=.32, color=color, label=label)
        ax.bar_label(bars, labels=labels, padding=2, fontsize=6)
    ax.set_title(title, pad=5)
    ax.set_xticks(range(2), [row['trace'] for row in meta['values']])
    ax.set_ylim(0, ylim)
    ax.set_yticks(ticks)
    ax.set_ylabel(unit, labelpad=1)
    ax.grid(axis='y', alpha=.25, linewidth=.4)
    ax.spines[['top', 'right']].set_visible(False)
    ax.tick_params(length=2, width=.5, pad=2)
fig.legend(*axes[0].get_legend_handles_labels(), loc='upper center', ncol=2,
           frameon=False, fontsize=7, handlelength=1.1, columnspacing=1.5)
out = HERE / 'rendered/evaluation/goodput_yield_compact.pdf'
fig.savefig(out)
plt.close(fig)
print(f'Wrote {out.name}: all eight values match the original table exactly.')
