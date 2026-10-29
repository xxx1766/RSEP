"""Compact main-text ablation plot from the unchanged audited 42-cell summary."""
from pathlib import Path
import argparse
import csv
import json
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator


def render_ablation_main(rows, figsize=(7.16, 3.0), colors=None):
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    colors = colors or ['#b5b5b5', '#e3a839', '#75a868', '#4d88bf', '#df7063', '#8d69b5']
    cells = ['C1', 'C2', 'C3', 'C4', 'C5', 'C6']
    rates = [1, 2, 4, 8, 16, 32]
    metrics = [('avg_ms_mean', 'Mean latency (ms)'),
               ('p95_ms_mean', 'P95 latency (ms)'),
               ('std_ms_mean', 'Latency SD (ms)'),
               ('throughput_mean', 'Throughput (req/s)')]
    values = {(int(r['rps']), r['cell']): r for r in rows}
    assert len(rows) == 42 and set(values) == {
        (r, c) for r in [1, 2, 4, 8, 12, 16, 32] for c in cells}
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 5.7,
                         'axes.linewidth': .45, 'lines.linewidth': .65,
                         'pdf.fonttype': 42, 'axes.axisbelow': True})

    def tick_text(value, position):
        if value == 0:
            return '0'
        if .001 <= abs(value) < 100000:
            return f'{value:g}'
        mantissa, exponent = f'{value:.2e}'.split('e')
        return f"{mantissa.rstrip('0').rstrip('.')}e{int(exponent)}"

    fig = plt.figure(figsize=figsize)
    outer = fig.add_gridspec(2, 3, left=.053, right=.993, top=.90,
                             bottom=.065, wspace=.23, hspace=.23)
    plotted = []
    for idx, rate in enumerate(rates):
        inner = outer[idx // 3, idx % 3].subgridspec(
            3, 2, height_ratios=[.12, 1, 1], wspace=.40, hspace=.50)
        title_ax = fig.add_subplot(inner[0, :])
        title_ax.axis('off')
        title_ax.text(.5, .5, f'Offered rate: {rate} req/s', ha='center',
                      va='center', fontsize=6.3, fontweight='bold')
        for mi, (metric, title) in enumerate(metrics):
            ax = fig.add_subplot(inner[1 + mi // 2, mi % 2])
            ci_key = metric.removesuffix('_mean') + '_ci95_half'
            y = [values[rate, c][metric] for c in cells]
            ci = [values[rate, c][ci_key] for c in cells]
            valid = [i for i, v in enumerate(y) if v is not None]
            assert all(ci[i] is not None and math.isfinite(float(ci[i]))
                       and float(ci[i]) >= 0 for i in valid)
            ax.bar(valid, [float(y[i]) for i in valid],
                   color=[colors[i] for i in valid], width=.72,
                   yerr=[float(ci[i]) for i in valid],
                   error_kw={'elinewidth': .45, 'capsize': 1.1, 'capthick': .45})
            for i, value in enumerate(y):
                if value is None:
                    ax.text(i, .05, 'undefined', rotation=90,
                            transform=ax.get_xaxis_transform(),
                            ha='center', va='bottom', fontsize=5)
                plotted.append({'rps': rate, 'cell': cells[i], 'metric': metric,
                                'mean': value, 'ci95_half': ci[i]})
            ax.set_title(title, fontsize=5.7, pad=2)
            ax.set_xticks(range(6), cells, fontsize=5.1)
            ax.tick_params(axis='both', labelsize=5.1, length=1.5, width=.45, pad=1)
            if mi < 2:
                ax.tick_params(axis='x', labelbottom=False, bottom=False)
            ax.set_ylim(bottom=min([0] + [float(y[i]) - float(ci[i]) for i in valid]))
            ax.grid(axis='y', alpha=.25, lw=.35)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=2))
            ax.yaxis.set_major_formatter(FuncFormatter(tick_text))
            ax.spines[['top', 'right']].set_visible(False)
    descriptions = ['CC off / PI off / EI off', 'CC on / PI off / EI off',
                    'CC off / PI off / EI on', 'CC on / PI off / EI on',
                    'CC off / PI on / EI on', 'CC on / PI on / EI on']
    fig.legend(handles=[Patch(facecolor=colors[i], label=f'{c}: {descriptions[i]}')
                        for i, c in enumerate(cells)], ncol=3, loc='upper center',
               bbox_to_anchor=(.52, .998), fontsize=5.8, frameon=False,
               handlelength=1.1, handletextpad=.35, columnspacing=1.7,
               labelspacing=.3, borderaxespad=0)
    fig._ablation_main_values = plotted
    return fig


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, default=here.parents[1] /
                        here.parent/'data/summaries/appendix_ablation_4plus12/per_cell.csv')
    parser.add_argument('--output-dir', type=Path,
                        default=here.parent / 'revision-figs/evaluation')
    args = parser.parse_args()
    rows = list(csv.DictReader(args.summary.open()))
    rows = [{k: v if k == 'cell' else float(v) if v else None
             for k, v in row.items()} for row in rows]
    fig = render_ablation_main(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / 'fig5_dynamic_binding_2x3'
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(out.with_suffix('.' + suffix), dpi=180, facecolor='white')
    out.with_suffix('.json').write_text(json.dumps(fig._ablation_main_values, indent=2) + '\n')
    plt.close(fig)


if __name__ == '__main__':
    main()
