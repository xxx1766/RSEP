"""Render Supplement Fig. S7 only from a complete, matching mixed-campaign audit."""
import argparse
import csv
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

SYSTEMS = ('bare', 'kubeedge', 'ray', 'tide')
LABELS = ('Bare', 'KubeEdge', 'Ray Serve', 'Tide')
COLORS = ('tab:blue', 'tab:orange', 'tab:green', 'tab:red')
LEVELS = ('low', 'high', 'overload')
FACTORS = {'low': .3, 'high': .8, 'overload': 1.2}
T4 = 2.7764451051977987
OUTPUT = 'revision-figs/evaluation/figS7_cluster_mixed.pdf'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def key(row):
    return row['level'], row['system'], int(row['deadline_ms'])


def close(actual, expected, message):
    actual = float(actual)
    require(math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-9,
                                                  abs_tol=1e-9), message)


def validate_publication(blobs):
    """Verify the audit binding and recompute all plotted CIs from run exports.

    This is a publication gate, not a replacement for the independent raw-data
    auditor. No output may be written before it returns successfully.
    """
    audit = json.loads(blobs['audit.json'])
    manifest = json.loads(blobs['manifest.json'])
    for name, value in dict(status='PASS', treatments=60, origin_runs=120,
                            class_run_rows=120, cells=24, repetitions=5, offered_total=177600).items():
        require(audit.get(name) == value, f'Incomplete publication audit: {name}')
    require(audit.get('test_only') is False and not manifest.get('test_only'),
            'Synthetic/test-only audit cannot supply publication data')
    exports = audit['export_sha256']
    require(set(exports) == {'runs.csv', 'cells.csv', 'README.md'}, 'Export hash inventory')
    for name, wanted in exports.items():
        require(sha(blobs[name]) == wanted, f'Export SHA mismatch: {name}')
    require(sha(blobs['manifest.json']) == audit['manifest_sha256'], 'Manifest SHA mismatch')
    require(manifest['campaign'] == audit['campaign'], 'Campaign mismatch')
    require(audit['source_sha256'] == manifest['source_sha256'] and
            audit['auditor_sha256'] == manifest['source_sha256']['fig7_cluster_mixed_audit.py'],
            'Frozen auditor identity mismatch')
    require(manifest['repetitions'] == 5 and manifest['duration_s'] == 10 and
            manifest['deadline_classes_ms'] == [100, 500] and
            manifest['load_factors'] == FACTORS and
            manifest['slots'] == {'nj1': 2, 'nj2': 1, 'nj3': 1}, 'Mixed campaign design mismatch')
    capacity = float(manifest['capacity_rps']['cpu'])
    rates = manifest['rates_rps']['cpu']
    require(math.isfinite(capacity) and capacity > 0 and set(rates) == set(LEVELS)
            and all(type(rates[level]) is int and
                    rates[level] == round(max(2, capacity * FACTORS[level])) for level in LEVELS),
            'Capacity labels require integer-rounded 30%/80%/120% rates')
    jobs = manifest['jobs']
    expected_jobs = set(itertools.product(LEVELS, SYSTEMS, range(1, 6)))
    require(len(jobs) == 60 and len({j['id'] for j in jobs}) == 60 and
            {(j['level'], j['system'], j['repeat']) for j in jobs} == expected_jobs,
            'Manifest lacks the complete 60-treatment factorial')
    require(set(audit['job_details']) == {j['id'] for j in jobs}, 'Audit job inventory')
    by_id = {j['id']: j for j in jobs}
    runs = list(csv.DictReader(io.StringIO(blobs['runs.csv'].decode())))
    cells = list(csv.DictReader(io.StringIO(blobs['cells.csv'].decode())))
    require(len(runs) == 120 and
            {(r['job_id'], int(r['deadline_ms'])) for r in runs} ==
            set(itertools.product(by_id, (100, 500))), 'Class run inventory (120 rows required)')
    groups = {}
    for row in runs:
        job = by_id[row['job_id']]
        level, system, deadline = key(row)
        require(row['kind'] == job['kind'] == 'cpu' and level == job['level'] and
                system == job['system'] and int(row['repeat']) == job['repeat'] and
                row['function'] == f'cpu-qos{deadline}', 'Class run/job mismatch')
        close(row['rate_rps'], rates[level], 'Run rate mismatch')
        close(job['rate_rps'], rates[level], 'Job rate mismatch')
        close(row['load_factor'], FACTORS[level], 'Run load factor mismatch')
        close(row['duration_s'], 10, 'Run window mismatch')
        close(row['offered_rps'], rates[level] / 2, 'Per-class offered rate mismatch')
        close(row['offered'], rates[level] * 5, 'Per-class offered count mismatch')
        on_time = int(row['on_time_in_window'])
        require(0 <= on_time <= int(row['offered']), 'Invalid on-time count')
        close(row['goodput_rps'], on_time / 10, 'Run goodput/count mismatch')
        groups.setdefault(key(row), []).append(row)
    require(sum(int(row['offered']) for row in runs) == audit['offered_total'],
            'Audit offered total differs from complete run exports')
    expected_cells = set(itertools.product(LEVELS, SYSTEMS, (100, 500)))
    require(set(groups) == expected_cells and len(cells) == 24 and
            {key(r) for r in cells} == expected_cells, 'Incomplete/duplicate 24-cell factorial')
    for row in cells:
        level, system, deadline = key(row)
        require(row['kind'] == 'cpu' and row['function'] == f'cpu-qos{deadline}' and
                int(row['n']) == int(row['goodput_rps_n']) == 5, 'Invalid plotted class cell')
        close(row['rate_rps'], rates[level], 'Cell rate mismatch')
        close(row['load_factor'], FACTORS[level], 'Cell load factor mismatch')
        observations = groups[key(row)]
        require(len(observations) == 5 and {int(r['repeat']) for r in observations} ==
                set(range(1, 6)), 'Missing/duplicate class repetition')
        values = [float(r['goodput_rps']) for r in observations]
        mean, sd = statistics.mean(values), statistics.stdev(values)
        se = sd / math.sqrt(5)
        for name, wanted in dict(mean=mean, sd=sd, se=se, ci95_halfwidth=T4 * se,
                                ci95_low=mean-T4*se, ci95_high=mean+T4*se).items():
            close(row['goodput_rps_' + name], wanted, f'Run/CI mismatch: {key(row)} {name}')
    return cells, manifest, {name: sha(data) for name, data in blobs.items()}


def read_publication(publication, manifest):
    blobs = {name: (Path(publication) / name).read_bytes()
             for name in ('runs.csv', 'cells.csv', 'README.md', 'audit.json')}
    blobs['manifest.json'] = Path(manifest).read_bytes()
    return validate_publication(blobs)


def figure(cells, rates, figsize=(6.5, 2.7), colors=None):
    """Show all 24 means and unclipped treatment-level 95% Student t intervals."""
    index = {(r['level'], r['system'], int(r['deadline_ms'])): r for r in cells}
    expected = {(level, system, deadline) for level in LEVELS for system in SYSTEMS
                for deadline in (100, 500)}
    if len(cells) != 24 or set(index) != expected or any(
            int(r['n']) != 5 or int(r['goodput_rps_n']) != 5 for r in cells):
        raise ValueError('Fig. S7 requires all 24 class cells with five repetitions each')
    colors = COLORS if colors is None else colors
    plt.rcParams.update({'font.size': 7, 'axes.titlesize': 7.5, 'axes.labelsize': 7,
        'xtick.labelsize': 7, 'ytick.labelsize': 6.5, 'legend.fontsize': 7,
        'axes.linewidth': .6, 'pdf.fonttype': 42, 'axes.axisbelow': True})
    fig, axes = plt.subplots(1, 2, figsize=figsize, sharey=True)
    fig.subplots_adjust(left=.095, right=.985, bottom=.20, top=.76, wspace=.14)
    limits = [0, *[rates[level] / 2 for level in LEVELS]]
    x = np.arange(3)
    for ax, deadline, letter in zip(axes, (100, 500), 'ab'):
        for i, system in enumerate(SYSTEMS):
            values = [index[level, system, deadline] for level in LEVELS]
            means = np.array([float(r['goodput_rps_mean']) for r in values])
            lo = np.array([float(r['goodput_rps_ci95_low']) for r in values])
            hi = np.array([float(r['goodput_rps_ci95_high']) for r in values])
            if not (np.isfinite([means, lo, hi]).all() and
                    (lo <= means).all() and (means <= hi).all()):
                raise ValueError('Invalid/unordered plotted confidence interval')
            limits.extend(lo); limits.extend(hi)
            ax.bar(x + (i-1.5)*.19, means, .19,
                   yerr=np.vstack([means-lo, hi-means]), capsize=1.6,
                   error_kw={'elinewidth': .6, 'capthick': .6},
                   color=colors[i], label=LABELS[i])
        # A separate dotted segment spans each load's four bars; no interpolation.
        for j, level in enumerate(LEVELS):
            ax.hlines(rates[level]/2, j-.40, j+.40, colors='.22',
                      linestyles=':', lw=.9,
                      label='Offered / 2' if j == 0 else None)
        ax.set_xticks(x, [f'{FACTORS[level]:.0%}' for level in LEVELS])
        ax.set_xlim(-.52, 2.52)
        ax.set_title(f'({letter}) {deadline} ms deadline', pad=5)
        ax.grid(axis='y', alpha=.22, lw=.4)
        ax.tick_params(length=2, pad=2, width=.5)
    span = max(limits)-min(limits)
    for ax in axes:
        ax.set_ylim(min(limits)-.035*span if min(limits) < 0 else 0,
                    max(limits)+.10*span)
    axes[0].set_ylabel('On-time goodput (req/s)')
    fig.supxlabel('Total offered load (% of calibrated CPU capacity)', y=.035, fontsize=7)
    handles, labels = axes[0].get_legend_handles_labels()
    # Keep framework order identical to Fig. 6, followed by the reference.
    wanted = [*LABELS, 'Offered / 2']
    legend = dict(zip(labels, handles))
    fig.legend([legend[name] for name in wanted], wanted, loc='upper center',
               bbox_to_anchor=(.54, .99), ncol=5, frameon=False,
               handlelength=1.35, columnspacing=1.3, handletextpad=.4)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path,
                        help='New figS7_cluster_mixed.pdf destination; historical Fig. D2 is protected')
    args = parser.parse_args()
    require(args.output.name == 'figS7_cluster_mixed.pdf', 'Use the new Fig. S7 destination')
    cells, manifest, hashes = read_publication(args.publication, args.manifest)
    fig = figure(cells, manifest['rates_rps']['cpu'])
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.output)
    finally:
        plt.close(fig)
    print(json.dumps(dict(status='PASS', output=str(args.output), plotted_cells=24,
                          repetitions=5, sha256=hashes,
                          output_sha256=sha(args.output.read_bytes()),
                          helper_sha256=sha(Path(__file__).read_bytes())), indent=2))


if __name__ == '__main__':
    main()
