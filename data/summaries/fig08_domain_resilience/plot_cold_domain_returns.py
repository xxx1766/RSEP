#!/usr/bin/env python3
"""Plot audited A-only-start fault episodes; never modify frozen analysis/data."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


SYSTEMS = ('tide', 'kubeedge', 'ray')
WIDTH = 5
DURATION = 150
SECOND = 10**9


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bin_run(run):
    """Independently bin user-side return times; check cached one-second bins."""
    start = run['spec']['start_unix_ns']
    rows = run['requests']
    assert len(rows) == len({r['id'] for r in rows}) == 930
    assert run['spec']['deadline_ms'] == 500
    bins = []
    for left in range(0, DURATION, WIDTH):
        right = left + WIDTH
        returned = [r for r in rows if r['timely'] and r['ended_unix_ns'] is not None
                    and left * SECOND <= r['ended_unix_ns'] - start < right * SECOND]
        correct = [r for r in rows if r['correct'] and r['ended_unix_ns'] is not None
                   and left * SECOND <= r['ended_unix_ns'] - start < right * SECOND]
        arrivals = [r for r in rows if left * SECOND <= r['scheduled_offset_ns'] < right * SECOND]
        a = sum(r['physical_host'] == 'A' for r in returned)
        b = sum(r['physical_host'] == 'B' for r in returned)
        assert a + b == len(returned)
        assert all(r['correct'] and 0 <= r['latency_ms'] <= 500 for r in returned)
        cached = run['seconds'][left:right]
        for key, count in [('offered', len(arrivals)), ('timely_returns', len(returned)),
                           ('A_timely_returned', a), ('B_timely_returned', b)]:
            assert sum(item[key] for item in cached) == count, (run['run_id'], left, key)
        bins.append(dict(start_s=left, end_s=right, offered=len(arrivals),
                         timely_returns=len(returned), A_returns=a, B_returns=b,
                         all_correct_returns=len(correct)))
    assert sum(b['offered'] for b in bins) == 930
    assert sum(b['timely_returns'] for b in bins) == run['totals']['timely_returns']
    return bins


def arrival_yields(runs):
    """All-offer five-second arrival cohorts, independently of return bins."""
    table = []
    for run in runs:
        for left in range(0, 150, 5):
            rows = [r for r in run['requests']
                    if left * SECOND <= r['scheduled_offset_ns'] < (left + 5) * SECOND]
            offered = len(rows)
            timely = sum(r['timely'] for r in rows)
            assert offered > 0
            table.append(dict(system=run['system'], repeat=run['repeat'], run_id=run['run_id'],
                              aggregation='run', start_s=left, end_s=left + 5,
                              offered=offered, timely=timely, r_srv_pct=100 * timely / offered,
                              r_srv_min_pct=None, r_srv_max_pct=None))
    means = {}
    for system in SYSTEMS:
        means[system] = []
        for left in range(0, 150, 5):
            rows = [r for r in table if r['system'] == system and r['start_s'] == left
                    and r['aggregation'] == 'run']
            assert len(rows) == 3 and {r['repeat'] for r in rows} == {1, 2, 3}
            values = [r['r_srv_pct'] for r in rows]
            item = dict(system=system, repeat='', run_id='', aggregation='mean_n3',
                        start_s=left, end_s=left + 5,
                        offered=statistics.mean(r['offered'] for r in rows),
                        timely=statistics.mean(r['timely'] for r in rows),
                        r_srv_pct=statistics.mean(values),
                        r_srv_min_pct=min(values), r_srv_max_pct=max(values))
            means[system].append(item)
            table.append(item)
    return means, table


def render_domain_returns(means, first_b, r_means, figsize=(3.5, 2.35 * 2 / 3)):
    """Counts left, arrival-cohort R_srv right, with one shared legend."""
    import statistics
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
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
        v = [b[key] for b in means[system]]
        return v + [v[-1]]
    ax.axvspan(20, 110, facecolor='#f2a95c', edgecolor='none', linewidth=0,
               alpha=.13, zorder=0)
    ax.stackplot(edges, y('tide', 'A_returns'), y('tide', 'B_returns'), step='post',
                 colors=['#c8e5df', '#7dc9b7'], alpha=.70, linewidth=0, zorder=1)
    offered_line, = ax.step(edges, offered + [offered[-1]], where='post', color='#555555',
                            lw=.9, ls=(0, (5, 3)), label='Offered', zorder=3)
    tide_line, = ax.step(edges, y('tide', 'timely_returns'), where='post', color='#087e65',
                         lw=.9, label='Tide', zorder=5)
    other_lines = []
    for system, label, color, style, marker, indices in [
        ('kubeedge', 'KubeEdge / EdgeMesh', '#ce623a', '--', 's', range(0, 30, 2)),
        ('ray', 'Ray Serve', '#5b58a5', '-.', '^', range(1, 30, 2))]:
        line, = ax.step(edges, y(system, 'timely_returns'), where='post', color=color,
                        lw=.9, ls=style, label=label, zorder=6)
        ax.plot([centers[i] for i in indices],
                [means[system][i]['timely_returns'] for i in indices],
                linestyle='none', marker=marker, markersize=2.0, color=color,
                markerfacecolor='white', markeredgewidth=.45, clip_on=False, zorder=7)
        other_lines.append(line)
    first = statistics.mean(first_b)
    ax.axvline(first, ymin=0, ymax=.72, color='#087e65', lw=.5, ls=':', zorder=3)
    ax.annotate(f'Edge2 first return\nat t = {first:.1f} s',
                xy=(first, 28), xytext=(66, 23), fontsize=5.2, color='black',
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
                  ls=style, lw=.9, label={'tide': 'Tide', 'kubeedge': 'KubeEdge / EdgeMesh',
                                         'ray': 'Ray Serve'}[system], zorder=4)
        rate.fill_between(edges, series('r_srv_min_pct'), series('r_srv_max_pct'),
                          step='post', color=color, alpha=.12, linewidth=0)
        if marker:
            indices = range(start, 30, 2)
            rate.plot([centers[i] for i in indices], [rows[i]['r_srv_pct'] for i in indices],
                      ls='none', marker=marker, ms=2, color=color, mfc='white',
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
    # Matplotlib fills columns first: systems occupy row 1, context row 2.
    handles = [tide_line, offered_line, other_lines[0],
               Patch(facecolor='#c8e5df', label='Tide on Edge1'), other_lines[1],
               Patch(facecolor='#7dc9b7', label='Tide on Edge2')]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.53, 1.0),
               ncol=3, frameon=False, fontsize=5.6, columnspacing=1.0,
               handlelength=1.8, handletextpad=.4, labelspacing=.25, borderpad=.1)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent / 'figures')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    report_dir = Path(__file__).resolve().parent
    source = report_dir / 'comparison-v3-results.json'
    audit_path = report_dir / 'comparison-v3-audit.json'
    report = json.loads(source.read_text())
    audit = json.loads(audit_path.read_text())
    assert report['status'] == audit['status'] == 'PASS'
    assert report['matrix_complete'] and len(report['runs']) == len(audit['runs']) == 36
    assert {r['run_id'] for r in report['runs']} == {r['run_id'] for r in audit['runs']}
    selected = [r for r in report['runs'] if r['initial'] == 'cold' and r['fault']]
    assert len(selected) == 9 and all(r['status'] == 'PASS' for r in selected)
    assert {(r['system'], r['repeat']) for r in selected} == {
        (s, repeat) for s in SYSTEMS for repeat in (1, 2, 3)}
    series = {s: [] for s in SYSTEMS}
    table = []
    values = ('offered', 'timely_returns', 'A_returns', 'B_returns', 'all_correct_returns')
    for run in selected:
        bins = bin_run(run)
        series[run['system']].append(bins)
        table.extend(dict(system=run['system'], repeat=run['repeat'], run_id=run['run_id'],
                          aggregation='run', **b) for b in bins)
    means = {}
    for system in SYSTEMS:
        means[system] = [{key: statistics.mean(bins[i][key] for bins in series[system])
                          for key in values} for i in range(DURATION // WIDTH)]
        for i, value in enumerate(means[system]):
            table.append(dict(system=system, repeat='', run_id='', aggregation='mean_n3',
                              start_s=i * WIDTH, end_s=(i + 1) * WIDTH, **value))
    offered = [r['offered'] for r in means['tide']]
    assert all([r['offered'] for r in means[s]] == offered for s in SYSTEMS)
    tide = [r for r in selected if r['system'] == 'tide']
    first_b = [r['B_events']['first_B_timely_return_s'] for r in tide]
    assert all(20 < t < 110 for t in first_b)
    assert all(r['B_events']['first_B_timely_return_s'] is None
               for r in selected if r['system'] != 'tide')
    on = [r['phases']['outage']['start_relative_s'] for r in selected]
    off = [r['phases']['outage']['end_relative_s'] for r in selected]
    assert max(abs(t - 20) for t in on) < 0.1
    assert max(abs(t - 110) for t in off) < 0.1
    stem = args.output_dir / 'cold-domain-return-counts'
    outputs = [stem.with_suffix('.' + suffix) for suffix in ('pdf', 'png', 'svg', 'csv', 'json')]
    if not args.overwrite and any(p.exists() for p in outputs):
        raise FileExistsError('Figure outputs exist; use --overwrite to regenerate them.')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    first = statistics.mean(first_b)
    r_means, r_table = arrival_yields(selected)
    r_path = args.output_dir / 'cold-domain-r-srv.csv'
    if r_path.exists() and not args.overwrite:
        raise FileExistsError('Arrival-cohort table exists; use --overwrite.')
    fig = render_domain_returns(means, first_b, r_means)
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(stem.with_suffix('.' + suffix), dpi=250, facecolor='white')
    plt.close(fig)
    fields = ['system', 'repeat', 'run_id', 'aggregation', 'start_s', 'end_s', *values]
    with stem.with_suffix('.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(table)
    with r_path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(r_table[0]))
        writer.writeheader()
        writer.writerows(r_table)
    metadata = dict(
        status='PASS', source=source.name, source_sha256=digest(source),
        audit=audit_path.name, audit_sha256=digest(audit_path),
        run_ids=[r['run_id'] for r in selected], repeats_per_system=3,
        bin_width_s=WIDTH, bin_time_basis='actual user-side return time at A',
        right_panel_time_basis='Scheduled-arrival cohorts; every offer in each five-second bin',
        right_panel_envelope='Minimum to maximum over all three run-level fractions, not a confidence interval',
        return_definition='Correct ID-matched result received at Edge1 within 500 ms of planned arrival; late returns excluded.',
        variability='Displayed curves are descriptive run means; CSV retains all 3 run series.',
        cloud_cut_s=[20, 110], actual_fault_start_s=on, actual_fault_end_s=off,
        display_host_names={'A': 'Edge1', 'B': 'Edge2', 'C': 'cloud'},
        event_time_basis='Elapsed seconds from the first scheduled arrival (t=0); not request processing duration.',
        tide_first_B_timely_return_s=first_b,
        outage_R_srv_mean={s: statistics.mean(r['phases']['outage']['timely_fraction']
                              for r in selected if r['system'] == s) for s in SYSTEMS},
        figure_caption=(
            'End-device domain autonomy under loss of cloud connectivity. Only Edge1 initially hosts '
            'an application executor; Edge2 is an idle compatible device in the same domain. '
            'The cloud control node is unreachable during 20-110 s while Edge1-Edge2 connectivity is retained. '
            '(a) shows correct responses received at Edge1 within 500 ms per non-overlapping 5-s return-time bin, '
            'averaged over three runs; dashed gray shows offered arrivals. Tide shading separates execution '
            'on Edge1 and Edge2, verified from physical-host journals. (b) shows the mean fraction of all '
            'scheduled arrivals in each 5-s cohort returning correctly within 500 ms, with min-max envelopes '
            'over three runs; failures remain in the denominator. Tide first returns a timely result via Edge2 '
            f'at elapsed t={first:.1f} s on average, measured from the first scheduled arrival, during the cloud interruption. '
            'This supports domain-local service and executor activation under the tested control placement; '
            'it does not establish a change in the structural capability metric R_cap.'),
        artifacts={p.name: digest(p) for p in [*outputs[:-1], r_path]})
    stem.with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(dict(status='PASS', runs=len(selected), csv_rows=len(table),
                          outputs=[str(p) for p in outputs],
                          tide_first_B_mean_s=first,
                          outage_R_srv_mean=metadata['outage_R_srv_mean']), indent=2))


if __name__ == '__main__':
    main()
