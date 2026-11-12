import csv
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


def load(directory):
    directory = Path(directory)
    with (directory / 'return-counts.csv').open(newline='') as stream:
        counts = list(csv.DictReader(stream))
    with (directory / 'r-srv.csv').open(newline='') as stream:
        rates = list(csv.DictReader(stream))
    meta = json.loads((directory / 'meta.json').read_text())
    return counts, rates, meta


def aggregate(counts, rates, meta):
    systems = ('tide', 'kubeedge', 'ray')
    values = ('offered', 'timely_returns', 'A_returns', 'B_returns', 'all_correct_returns')
    means = {}
    for system in systems:
        means[system] = []
        for left in range(0, 150, 5):
            runs = [r for r in counts if r['system'] == system
                    and r['aggregation'] == 'run' and int(r['start_s']) == left]
            assert len(runs) == 3 and {r['repeat'] for r in runs} == {'1', '2', '3'}
            cached, = [r for r in counts if r['system'] == system
                       and r['aggregation'] == 'mean_n3' and int(r['start_s']) == left]
            group = {k: statistics.mean(float(r[k]) for r in runs) for k in values}
            assert all(abs(group[k] - float(cached[k])) < 1e-10 for k in values)
            assert abs(group['A_returns'] + group['B_returns'] - group['timely_returns']) < 1e-10
            means[system].append(group)
        assert sum(r['offered'] for r in means[system]) == 930
    r_means = {}
    for system in systems:
        r_means[system] = []
        for left in range(0, 150, 5):
            runs = [r for r in rates if r['system'] == system
                    and r['aggregation'] == 'run' and int(r['start_s']) == left]
            assert len(runs) == 3 and {r['repeat'] for r in runs} == {'1', '2', '3'}
            values = [100 * int(r['timely']) / int(r['offered']) for r in runs]
            assert all(abs(value - float(row['r_srv_pct'])) < 1e-10
                       for value, row in zip(values, runs))
            cached, = [r for r in rates if r['system'] == system
                       and r['aggregation'] == 'mean_n3' and int(r['start_s']) == left]
            item = {'r_srv_pct': statistics.mean(values),
                    'r_srv_min_pct': min(values), 'r_srv_max_pct': max(values)}
            assert all(abs(item[k] - float(cached[k])) < 1e-10 for k in item)
            r_means[system].append(item)
    return means, r_means, meta['tide_first_B_timely_return_s']


def render(means, first_b, r_means, figsize=(3.5, 1.5)):
    width, duration = 5, 150
    offered = [row['offered'] for row in means['tide']]
    plt.rcdefaults()
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 6,
                         'axes.labelsize': 6.8, 'axes.linewidth': .5,
                         'xtick.major.width': .5, 'ytick.major.width': .5,
                         'xtick.major.size': 2.5, 'ytick.major.size': 2.5,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    fig, axes = plt.subplots(1, 2, figsize=figsize)
    ax, rate = axes
    fig.subplots_adjust(left=.13, right=.975, bottom=.24, top=.83, wspace=.40)
    edges = list(range(0, duration + 1, width))
    centers = [t + width / 2 for t in edges[:-1]]

    def y(system, key):
        values = [b[key] for b in means[system]]
        return values + [values[-1]]

    ax.axvspan(20, 110, facecolor='#f2a95c', edgecolor='none', linewidth=0,
               alpha=.13, zorder=0)
    ax.stackplot(edges, y('tide', 'A_returns'), y('tide', 'B_returns'), step='post',
                 colors=['#c8e5df', '#7dc9b7'], alpha=.70, linewidth=0, zorder=1)
    offered_line, = ax.step(edges, offered + [offered[-1]], where='post',
                            color='#555555', lw=.9, ls=(0, (5, 3)),
                            label='Offered', zorder=3)
    tide_line, = ax.step(edges, y('tide', 'timely_returns'), where='post',
                         color='#087e65', lw=.9, label='RSEP', zorder=5)
    other_lines = []
    for system, label, color, style, marker, indices in [
            ('kubeedge', 'KubeEdge / EdgeMesh', '#ce623a', '--', 's', range(0, 30, 2)),
            ('ray', 'Ray Serve', '#5b58a5', '-.', '^', range(1, 30, 2))]:
        line, = ax.step(edges, y(system, 'timely_returns'), where='post',
                        color=color, lw=.9, ls=style, label=label, zorder=6)
        ax.plot([centers[i] for i in indices],
                [means[system][i]['timely_returns'] for i in indices],
                linestyle='none', marker=marker, markersize=2.0, color=color,
                markerfacecolor='white', markeredgewidth=.45, clip_on=False, zorder=7)
        other_lines.append(line)
    first = statistics.mean(first_b)
    ax.axvline(first, ymin=0, ymax=.72, color='#087e65', lw=.5, ls=':', zorder=3)
    ax.annotate(f'Edge2 first return\nat t = {first:.1f} s', xy=(first, 28),
                xytext=(66, 23), fontsize=5.2, color='black',
                arrowprops=dict(arrowstyle='->', color='black', lw=.45),
                ha='left', va='center', zorder=10)
    ax.text(65, 45, 'cloud disconnected', ha='center', va='center', fontsize=5.2,
            color='#76522c')
    ax.set(xlim=(0, 150), ylim=(0, 48), xlabel='Elapsed time (s)',
           ylabel='Timely returns (per 5 s)')
    ax.set_xticks([0, 20, 50, 80, 110, 150])
    ax.set_yticks([0, 10, 20, 30, 40])
    ax.grid(axis='y', color='#e5e5e5', lw=.4, zorder=0)
    ax.spines[['top', 'right']].set_visible(False)
    rate.axvspan(20, 110, facecolor='#f2a95c', edgecolor='none', linewidth=0,
                 alpha=.13, zorder=0)
    for system, color, style, marker, start in [
            ('tide', '#087e65', '-', None, 0),
            ('kubeedge', '#ce623a', '--', 's', 0),
            ('ray', '#5b58a5', '-.', '^', 1)]:
        rows = r_means[system]
        assert len(rows) == 30

        def series(key):
            values = [row[key] for row in rows]
            return values + [values[-1]]

        rate.step(edges, series('r_srv_pct'), where='post', color=color,
                  ls=style, lw=.9,
                  label={'tide': 'RSEP', 'kubeedge': 'KubeEdge / EdgeMesh',
                         'ray': 'Ray Serve'}[system], zorder=4)
        rate.fill_between(edges, series('r_srv_min_pct'), series('r_srv_max_pct'),
                          step='post', color=color, alpha=.12, linewidth=0)
        if marker:
            indices = range(start, 30, 2)
            rate.plot([centers[i] for i in indices],
                      [rows[i]['r_srv_pct'] for i in indices], ls='none',
                      marker=marker, ms=2, color=color, mfc='white',
                      mew=.45, clip_on=False, zorder=5)
    rate.text(65, 112, 'cloud disconnected', ha='center', va='center', fontsize=5.2,
              color='#76522c')
    rate.set(xlim=(0, 150), ylim=(0, 120), xlabel='Elapsed time (s)',
             ylabel=r'$R_{\rm srv}$ (%)')
    rate.set_xticks([0, 20, 50, 80, 110, 150])
    rate.set_yticks([0, 50, 100])
    rate.grid(axis='y', color='#e5e5e5', lw=.4, zorder=0)
    rate.spines[['top', 'right']].set_visible(False)
    for axis, label in zip(axes, ('(a)', '(b)')):
        axis.text(.015, .98, label, transform=axis.transAxes, fontsize=6.8, va='top')
    handles = [tide_line, offered_line, other_lines[0],
               Patch(facecolor='#c8e5df', label='RSEP on Edge1'), other_lines[1],
               Patch(facecolor='#7dc9b7', label='RSEP on Edge2')]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.53, 1.0),
               ncol=3, frameon=False, fontsize=5.6, columnspacing=1.0,
               handlelength=1.8, handletextpad=.4, labelspacing=.25, borderpad=.1)
    return fig


def draw(counts, rates, meta, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    means, r_means, first_b = aggregate(counts, rates, meta)
    fig = render(means, first_b, r_means)
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(output / ('cold-domain-return-counts.' + suffix), dpi=240)
    (output / 'cold-domain-return-counts.json').write_text(json.dumps({
        'means': means, 'arrival_cohort_R_srv': r_means,
        'first_Edge2_return_times_s': first_b,
        'cloud_cut_s': [20, 110], 'bin_width_s': 5}, indent=2) + '\n')
    plt.close(fig)
