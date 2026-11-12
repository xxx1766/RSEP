import csv
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import NullLocator

SERIES = [('Tide', 'Tide', 'RSEP'), ('Bare metal', 'Bare Metal', 'Bare metal'),
          ('KubeEdge', 'KubeEdge', 'KubeEdge'), ('KubeEdge 2C4C', 'KubeEdge-2C4C', 'KubeEdge 2C4C'),
          ('Ray Task', 'Ray Task', 'Ray Task'), ('Ray Actor-40', 'Ray-Actor-40', 'Ray Actor-40')]
COLORS = ['#be3d35', '#4d5965', '#237bb5', '#78b7d2', '#55936b', '#8873a9']
MARKERS = ['o', 's', '^', 'v', 'D', 'P']
HISTORICAL_ORDER = ['Bare Metal', 'KubeEdge', 'KubeEdge-2C4C', 'KubeEdge-3C8C',
                    'Ray Task', 'Ray-Actor-15', 'Ray-Actor-20', 'Ray-Actor-25',
                    'Ray-Actor-40', 'Tide']
GOODPUT_COLORS = ['#e2f0ed', '#8dcec3', '#58b6b2', '#32978c', '#cecee2',
                  '#c5d3e2', '#9fc5d6', '#72a6c9', '#487fb3', '#df3c3b']
PLACEMENT_ALIASES = {'KubeEdge-Full': 'KubeEdge', 'Ray-Task': 'Ray Task'}


def load(directory):
    directory = Path(directory)
    with (directory / 'placement.csv').open(newline='') as stream:
        placement = list(csv.DictReader(stream))
    with (directory / 'allocation.csv').open(newline='') as stream:
        allocation = list(csv.DictReader(stream))
    with (directory / 'goodput.csv').open(newline='') as stream:
        goodput = list(csv.DictReader(stream))
    latency = json.loads((directory / 'latency.json').read_text())['series']
    return {'placement': placement, 'allocation': allocation,
            'goodput': goodput, 'latency': latency}


def validate(data):
    def require(ok, message):
        if not ok:
            raise ValueError(message)
    rows = data['placement']
    require(len(rows) == 100, 'Expected all 100 placement summaries')
    require({(PLACEMENT_ALIASES.get(r['platform'], r['platform']), int(r['rate_rps_per_client']))
             for r in rows} == {(p, t) for p in HISTORICAL_ORDER for t in range(5, 51, 5)},
            'Placement grid incomplete')
    for r in rows:
        a, b = float(r['edge_percent']), float(r['cloud_percent'])
        require(0 <= a <= 100 and 0 <= b <= 100 and abs(a + b - 100) < .001,
                'Invalid placement proportions')
    rows = data['allocation']
    require(len(rows) == 77, 'Expected all 77 native vector vertices')
    for platform, n in [('KubeEdge-2C4C', 15), ('KubeEdge-3C8C', 13), ('Tide', 49)]:
        group = [r for r in rows if r['platform'] == platform]
        require(len(group) == n and [int(r['vertex_index']) for r in group] == list(range(n)),
                'Trace vertices missing')
        x = [float(r['time_s_from_pdf_vector']) for r in group]
        require(all(math.isfinite(t) and 0 <= t <= 100 for t in x)
                and all(a <= b for a, b in zip(x, x[1:])), 'Trace time nonmonotone')
        require(all(0 <= float(r['allocated_cpus_from_pdf_vector']) <= 36 for r in group),
                'Trace CPU range')
    rows = data['goodput']
    require(len(rows) == 50 and {(r['platform'], int(r['rate_rps_per_client'])) for r in rows}
            == {(p, t) for p in HISTORICAL_ORDER for t in [10, 20, 30, 40, 50]},
            'Goodput grid incomplete')
    for r in rows:
        full = float(r['throughput_rps_from_pdf_vector'])
        good = float(r['goodput_rps_from_pdf_vector'])
        require(math.isfinite(full) and 0 <= good <= full, 'Invalid throughput/goodput pair')
        require(str(r['pattern_patch_present']) in ['True', 'False'], 'Missing hatch provenance')
        require(str(r['pattern_patch_present']) == 'True' or good == 0,
                'Absent hatch must remain zero')


def render_pair(data, kind='placement', layout='2x2'):
    assert kind in ['performance', 'placement'] and layout in ['1x4', '2x2']
    plt.rcdefaults()
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 6.7,
                         'axes.linewidth': .5, 'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'axes.axisbelow': True})
    if layout == '2x2':
        fig, grid = plt.subplots(2, 2, figsize=(3.5, 2.7))
        axes = grid.ravel()
        fig.subplots_adjust(left=.145, right=.99, top=.826, bottom=.12,
                            wspace=.58, hspace=.72)
    else:
        fig, axes = plt.subplots(1, 4, figsize=(7.16, 1.82))
        fig.subplots_adjust(left=.065, right=.99, top=.81, bottom=.26, wspace=.49)
    records = []
    metrics = [('throughput_rps_from_pdf_vector', '(a) Throughput'),
               ('goodput_rps_from_pdf_vector', '(b) 350-ms goodput'),
               ('mean_ms', '(c) Mean latency'), ('p95_ms', '(d) P95 latency')]
    if kind == 'placement':
        metrics[:2] = [('edge_percent', '(a) Edge execution share'),
                       ('cloud_percent', '(b) Cloud execution share')]
    for col, (metric, title) in enumerate(metrics):
        ax = axes[col]
        for (latname, goodname, label), color, marker in zip(SERIES, COLORS, MARKERS):
            if col < 2 and kind == 'placement':
                place_name = {'KubeEdge': 'KubeEdge-Full', 'Ray Task': 'Ray-Task'}.get(goodname, goodname)
                rows = sorted([r for r in data['placement'] if r['platform'] == place_name],
                              key=lambda r: int(r['rate_rps_per_client']))
                assert [int(r['rate_rps_per_client']) for r in rows] == list(range(5, 51, 5))
                xs = [int(r['rate_rps_per_client']) for r in rows]
                ys = [float(r[metric]) for r in rows]
                scope = 'per-client offered rate; share denominator=executed work'
            elif col < 2:
                rows = sorted([r for r in data['goodput'] if r['platform'] == goodname],
                              key=lambda r: int(r['rate_rps_per_client']))
                assert [int(r['rate_rps_per_client']) for r in rows] == [10, 20, 30, 40, 50]
                xs = [int(r['rate_rps_per_client']) for r in rows]
                ys = [float(r[metric]) for r in rows]
                scope = 'per-client offered rate; aggregate input=3r'
            else:
                rows = next(s for s in data['latency'] if s['name'] == latname)['points']
                assert [p['rate'] for p in rows] == list(range(5, 51, 5))
                xs = [p['rate'] for p in rows]
                ys = [p[metric] for p in rows]
                scope = 'original reported offered rate; not rescaled'
            ax.plot(xs, ys, label=label, color=color, marker=marker, ms=2.35,
                    mew=.45, lw=.8, zorder=4 if latname == 'Tide' else 3)
            for x, y, row in zip(xs, ys, rows):
                record = dict(series=label,
                              source_series=(place_name if kind == 'placement' else goodname) if col < 2 else latname,
                              metric=metric, rate=x, value=y, rate_scope=scope)
                if col == 1 and kind == 'performance':
                    record['pattern_patch_present'] = row['pattern_patch_present']
                records.append(record)
        ax.set_title(title, fontsize=6.5 if layout == '2x2' else 7, pad=3)
        ax.set_xticks([10, 20, 30, 40, 50])
        ax.set_xlim(7, 53) if col < 2 and kind == 'performance' else ax.set_xlim(3, 52)
        ax.tick_params(length=2, width=.5, pad=1.8, labelsize=6)
        ax.grid(axis='y', color='#dedede', lw=.4)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color('black')
            spine.set_linewidth(.6)
        if col < 2:
            if kind == 'placement':
                ax.set_ylim(-3, 103)
                ax.set_yticks([0, 25, 50, 75, 100])
                ax.set_ylabel('Executed work (%)', labelpad=3, fontsize=6.5)
            else:
                ax.set_ylim(0, 160)
                ax.set_yticks([0, 50, 100, 150])
                ax.set_ylabel('Tasks/s', labelpad=3, fontsize=6.5)
            ax.set_xlabel('Requests/s per client', labelpad=4, fontsize=6.3)
        else:
            ax.set_yscale('log')
            ax.set_ylim(70, 90000)
            ax.set_yticks([100, 1000, 10000])
            ax.yaxis.set_minor_locator(NullLocator())
            ax.set_ylabel('Latency (ms)', labelpad=3, fontsize=6.5)
            ax.set_xlabel('Reported requests/s', labelpad=4, fontsize=6.3)
        assert all(ax.get_ylim()[0] <= r['value'] <= ax.get_ylim()[1]
                   for r in records if r['metric'] == metric)
    if kind == 'performance':
        axes[0].plot([10, 20, 30, 40, 50], [30, 60, 90, 120, 150], ls='--',
                     color='#a1a1a1', lw=.65, label='Ideal = 3r', zorder=1)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center',
               bbox_to_anchor=(.54, .995) if layout == '2x2' else (.52, .985),
               ncol=3 if layout == '2x2' else (7 if kind == 'performance' else 6),
               frameon=False, fontsize=6.1, handlelength=1.3, handletextpad=.35,
               columnspacing=.9 if layout == '2x2' else 1.1, borderaxespad=0)
    fig.values = records
    assert len(records) == (180 if kind == 'performance' else 240)
    return fig


def render_allocation(data, figsize=(3.5, 1.55), colors=None):
    validate(data)
    colors = colors or ['#0070c0', '#008000', '#c00000']
    fig, ax = plt.subplots(figsize=figsize)
    fig.subplots_adjust(left=.13, right=.99, bottom=.27, top=.79)
    for platform, color, style in zip(['KubeEdge-2C4C', 'KubeEdge-3C8C', 'Tide'],
                                     colors, ['-.', '--', '-']):
        rows = [r for r in data['allocation'] if r['platform'] == platform]
        ax.plot([float(r['time_s_from_pdf_vector']) for r in rows],
                [float(r['allocated_cpus_from_pdf_vector']) for r in rows],
                color=color, linestyle=style, lw=.9, label=platform)
    ax.set_xlim(-2, 102)
    ax.set_ylim(-1, 38)
    ax.set_xticks(range(0, 101, 10))
    ax.set_yticks(range(0, 36, 5))
    ax.set_xlabel('Time since startup (s)', labelpad=1.5)
    ax.set_ylabel('Allocated CPUs', labelpad=1.5)
    ax.grid(alpha=.35, lw=.4, linestyle=':')
    ax.tick_params(length=2, pad=1.5, width=.5)
    fig.legend(*ax.get_legend_handles_labels(), loc='upper center',
               bbox_to_anchor=(.55, .995), ncol=3, frameon=False, fontsize=5.5,
               columnspacing=.8, handletextpad=.3, handlelength=1.5)
    return fig


def render_placement(data, figsize=(3.5, 1.95), colors=None):
    validate(data)
    colors = colors or ['#697580', '#238b8e', '#68aaa5', '#165f66', '#c58a36',
                        '#b198c4', '#9571ae', '#725394', '#49326e', '#c7423a']
    style = {'font.family': 'DejaVu Sans', 'font.size': 6.5, 'axes.labelsize': 6.5,
             'xtick.labelsize': 6, 'ytick.labelsize': 6, 'axes.linewidth': .5,
             'pdf.fonttype': 42, 'ps.fonttype': 42}
    with plt.rc_context(style):
        fig, axs = plt.subplots(1, 2, sharey=True, figsize=figsize)
        fig.subplots_adjust(left=.12, right=.985, bottom=.205, top=.77, wspace=.13)
        markers = ['o', 's', 'v', '^', 'x', 'D', 'P', 'd', 'p', '*']
        styles = ['-', '-', '--', ':', '-.', '-', '--', '-.', ':', '-']
        for ax, metric, title in zip(axs, ['edge_percent', 'cloud_percent'],
                                     ['(a) Edge', '(b) Cloud']):
            for i, platform in enumerate(HISTORICAL_ORDER):
                rows = sorted([r for r in data['placement']
                               if PLACEMENT_ALIASES.get(r['platform'], r['platform']) == platform],
                              key=lambda r: int(r['rate_rps_per_client']))
                tide = platform == 'Tide'
                ax.plot([int(r['rate_rps_per_client']) for r in rows],
                        [float(r[metric]) for r in rows], color=colors[i],
                        marker=markers[i], linestyle=styles[i],
                        markersize=3.7 if tide else 2.8, markeredgewidth=.6,
                        markerfacecolor=colors[i] if tide or markers[i] == 'x' else 'white',
                        lw=1.0 if tide else .75,
                        label=platform.replace('Ray-Actor-', 'Ray-'),
                        zorder=5 if tide else 2)
            ax.set_title(title, fontsize=6.5, pad=3)
            ax.set_xticks([10, 20, 30, 40, 50])
            ax.set_yticks([0, 50, 100])
            ax.tick_params(length=2, pad=2, width=.5)
            ax.grid(axis='y', alpha=.24, lw=.4)
            ax.set_xlim(3, 52)
            ax.set_ylim(-3, 103)
            ax.spines[['top', 'right']].set_visible(False)
        axs[0].set_ylabel('Execution share (%)', labelpad=2)
        axs[1].tick_params(axis='y', left=False)
        fig.text(.55, .035, 'Requests/s per client', ha='center', fontsize=6.5)
        legend = fig.legend(*axs[0].get_legend_handles_labels(), loc='upper center',
                            bbox_to_anchor=(.5, 1.005), ncol=5, frameon=False,
                            fontsize=5.3, columnspacing=.8, handletextpad=.35,
                            handlelength=1.25, labelspacing=.5, borderaxespad=.2)
        for text in legend.get_texts():
            if text.get_text() == 'Tide':
                text.set_fontweight('bold')
    return fig


def render_goodput(data, figsize=(7.16, 1.3), colors=None):
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 6.7,
                         'axes.linewidth': .5, 'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'axes.axisbelow': True})
    validate(data)
    colors = colors or GOODPUT_COLORS
    fig, axs = plt.subplots(1, 5, figsize=figsize)
    fig.subplots_adjust(left=.06, right=.995, bottom=.15, top=.76, wspace=.28)
    ymax = [3 * r * 1.06 for r in [10, 20, 30, 40, 50]]
    ticks = [[0, 10, 20, 30], [0, 20, 40, 60], [0, 20, 40, 60, 80, 100],
             [0, 25, 50, 75, 100, 125], [0, 50, 100, 150]]
    for ax, rate, top, yticks in zip(axs, [10, 20, 30, 40, 50], ymax, ticks):
        for i, platform in enumerate(HISTORICAL_ORDER):
            row = next(r for r in data['goodput'] if r['platform'] == platform
                       and int(r['rate_rps_per_client']) == rate)
            ax.bar(i, float(row['throughput_rps_from_pdf_vector']), width=.76,
                   color=colors[i], edgecolor='#222222', linewidth=.55)
            if str(row['pattern_patch_present']) == 'True':
                ax.bar(i, float(row['goodput_rps_from_pdf_vector']), width=.76,
                       facecolor='white', edgecolor=colors[i], linewidth=.5, hatch='////')
        ax.axhline(3 * rate, color='#ef7b32', ls='--', lw=.65)
        ax.set_title(f'RPS = {rate}', fontsize=6.7, fontweight='normal', pad=3)
        ax.set_xticks([])
        ax.set_ylim(0, top)
        ax.set_yticks([y for y in yticks if y <= top])
        ax.grid(axis='y', alpha=.35, lw=.4, linestyle='--')
        ax.tick_params(length=2, pad=1.5, width=.5)
    axs[0].set_ylabel('Tasks/s', labelpad=2)
    handles = [Patch(facecolor=c, edgecolor='#222222', linewidth=.5,
                     label='RSEP' if p == 'Tide' else p.replace('Ray-Actor-', 'Ray-'))
               for p, c in zip(HISTORICAL_ORDER, colors)]
    handles += [Patch(facecolor='white', edgecolor='#222222', label='Throughput'),
                Patch(facecolor='white', edgecolor='#555555', hatch='////', label='Goodput'),
                Line2D([], [], color='#ef7b32', ls='--', lw=.65, label='Ideal = 3r')]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5, .995),
               ncol=13, frameon=False, fontsize=5.4, handlelength=1.05,
               handletextpad=.3, columnspacing=1.05, labelspacing=.3, borderaxespad=0)
    return fig


def draw_main(data, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fig = render_pair(data, 'placement', '2x2')
    stem = output / 'historical_placement_latency'
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(stem.with_suffix('.' + suffix), dpi=280)
    (output / 'historical_placement_latency.json').write_text(json.dumps(fig.values, indent=2) + '\n')
    (output / 'historical_placement_latency_provenance.json').write_text(json.dumps({
        'displayed_platforms': [r[2] for r in SERIES],
        'placement_points': 120, 'latency_points': 120,
        'layout': {'rows': 2, 'columns': 2, 'width_inches': 3.5, 'height_inches': 2.7},
        'scope': ('Placement uses executed-work denominator and per-client rate; '
                  'latency retains original reported rate. Matching labels do not '
                  'establish a common run manifest; no reconstructed uncertainty.')}, indent=2) + '\n')
    plt.close(fig)

    fig = render_goodput(data, figsize=(7.16, 1.3))
    stem = output / 'historical_goodput_redrawn'
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(stem.with_suffix('.' + suffix), dpi=280)
    plt.close(fig)


def draw_supplement(data, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for stem, figure in [
        ('historical_allocation', render_allocation(data, figsize=(3.5, 1.55))),
        ('historical_placement_original', render_placement(data, figsize=(3.5, 1.95))),
        ('historical_goodput_original', render_goodput(data, figsize=(7.16, 1.3)))]:
        for suffix in ('pdf', 'png', 'svg'):
            figure.savefig(output / (stem + '.' + suffix), dpi=240)
        plt.close(figure)
