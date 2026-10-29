#!/usr/bin/env python3
"""Exploratory R_srv/CPU previews from every audited cold-start outage run.

No primary data, frozen analysis, or manuscript figure is overwritten. CPU
accounting is common Go application-worker cgroup time, excluding framework
serving/control processes. It is not the manuscript's CPU+PSS U_obs.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

SECOND = 10**9
SYSTEMS = ('tide', 'kubeedge', 'ray')
METRICS = ('r_srv_pct', 'domain_cpu_pct', 'edge1_cpu_pct', 'edge2_cpu_pct')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(row, start_ns, offset_ns=0):
    """Return one identity-checked, deduplicated cumulative CPU observation."""
    if row['errors']:
        return None, 'observer_error'
    scan = (row['ended_monotonic_ns'] - row['monotonic_ns']) / SECOND
    if not 0 <= scan <= .1:
        return None, 'scan_duration'
    groups = {}
    for worker in row['workers']:
        if not worker.get('budget_matches') or worker.get('budget_errors'):
            return None, 'worker_budget_or_identity'
        if 'cpu_stat' not in worker:
            return None, 'missing_cpu_counter'
        fields = dict(line.split() for line in worker['cpu_stat'].splitlines())
        value = int(fields['usage_usec'])
        if value < 0:
            return None, 'negative_cpu_counter'
        group = worker['cgroup']
        entry = groups.setdefault(group, dict(cpu_usec=value, identities=[]))
        # Multiple workers can expose the same inclusive cgroup counter.
        entry['cpu_usec'] = max(entry['cpu_usec'], value)
        entry['identities'].append((worker['pid'], worker['start_ticks']))
    for entry in groups.values():
        entry['identities'] = tuple(sorted(entry['identities']))
    t = (row['unix_ns'] - offset_ns - start_ns) / SECOND + scan / 2
    return dict(t=t, groups=groups, scan_s=scan), None


def cpu_intervals(frames):
    """Use counter differences only across stable worker/cgroup identities."""
    intervals = []
    rejected = []
    for a, b in zip(frames, frames[1:]):
        dt = b['t'] - a['t']
        if not 0 < dt <= 3:
            rejected.append(dict(left=a['t'], right=b['t'], reason='sample_gap'))
            continue
        if (a['groups'].keys() != b['groups'].keys() or
                any(a['groups'][g]['identities'] != b['groups'][g]['identities']
                    for g in a['groups'])):
            rejected.append(dict(left=a['t'], right=b['t'], reason='worker_lifecycle'))
            continue
        increments = [b['groups'][g]['cpu_usec'] - a['groups'][g]['cpu_usec']
                      for g in a['groups']]
        cpu_s = sum(increments) / 1e6
        if any(n < 0 for n in increments) or cpu_s > 2.05 * dt:
            rejected.append(dict(left=a['t'], right=b['t'], reason='counter_reset_or_capacity'))
            continue
        intervals.append(dict(left=a['t'], right=b['t'], cpu_s=cpu_s))
    return intervals, rejected


def integrate(intervals, left, right):
    """Apportion observed interval-average CPU across bin boundaries.

    No extrapolation: an incompletely covered bin has no reported utilization.
    Empty-to-empty, error-free worker inventories are explicit idle evidence.
    """
    cpu_s = covered_s = 0.
    for interval in intervals:
        duration = interval['right'] - interval['left']
        overlap = max(0., min(right, interval['right']) - max(left, interval['left']))
        cpu_s += interval['cpu_s'] * overlap / duration
        covered_s += overlap
    assert covered_s <= right - left + 1e-6
    complete = abs(covered_s - (right - left)) <= 1e-6
    return dict(cpu_s=cpu_s if complete else None, covered_s=covered_s,
                cpu_pct=100 * cpu_s / (2 * (right - left)) if complete else None)


def observe_host(root, run, host, input_hashes):
    path = root / 'runs' / run['run_id'] / host / 'observations.jsonl'
    input_hashes[str(path.relative_to(root))] = sha(path)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert all(row['physical_host'] == host for row in rows)
    assert [r['sequence'] for r in rows] == list(range(1, len(rows) + 1))
    assert all(b['monotonic_ns'] > a['monotonic_ns'] for a, b in zip(rows, rows[1:]))
    offset = 0 if host == 'A' else run['B_events']['B_clock']['offset_B_minus_A_ns']
    assert run['B_events']['B_clock']['bound_ns'] < 10_000_000
    frames, rejected = [], []
    for row in rows:
        frame, reason = snapshot(row, run['spec']['start_unix_ns'], offset)
        if frame is None:
            rejected.append(dict(sequence=row['sequence'], reason=reason, errors=row['errors']))
        else:
            frames.append(frame)
    intervals, omitted = cpu_intervals(frames)
    if host == 'B' and all(not f['groups'] for f in frames):
        assert not run['B_events']['worker_instances']
        assert run['B_events']['worker_seen_during_window'] is False
        assert run['B_events']['observation_coverage_complete']
    return intervals, dict(samples=len(rows), valid_samples=len(frames),
                           excluded_snapshots=rejected, excluded_intervals=omitted,
                           max_scan_s=max(f['scan_s'] for f in frames))


def analyze(root):
    source = root / 'report/comparison-v3-results.json'
    audit_path = root / 'report/comparison-v3-audit.json'
    data = json.loads(source.read_text())
    audit = json.loads(audit_path.read_text())
    assert data['status'] == audit['status'] == 'PASS'
    assert len(data['runs']) == len(audit['runs']) == 36
    assert {r['run_id'] for r in data['runs']} == {r['run_id'] for r in audit['runs']}
    selected = [r for r in data['runs'] if r['initial'] == 'cold' and r['fault']]
    assert len(selected) == 9 and all(r['status'] == 'PASS' for r in selected)
    assert {(r['system'], r['repeat']) for r in selected} == {
        (system, repeat) for system in SYSTEMS for repeat in (1, 2, 3)}
    hashes = {str(p.relative_to(root)): sha(p) for p in (source, audit_path)}
    bins, coverage, windows = [], {}, []
    for run in selected:
        hosts = {}
        for host in ('A', 'B'):
            hosts[host], coverage[run['run_id'] + '/' + host] = observe_host(root, run, host, hashes)
        assert len(run['requests']) == len({r['id'] for r in run['requests']}) == 930
        assert sum(r['timely'] for r in run['requests'] if r['phase'] == 'outage') == run['phases']['outage']['timely']
        for left, right, label in [(t, t + 5, 'bin') for t in range(0, 150, 5)] + [
                (60, run['phases']['outage']['end_relative_s'], 'high_load_outage')]:
            requests = [r for r in run['requests'] if left * SECOND <= r['scheduled_offset_ns'] < right * SECOND]
            a = integrate(hosts['A'], left, right)
            b = integrate(hosts['B'], left, right)
            domain = ((a['cpu_pct'] + b['cpu_pct']) / 2
                      if a['cpu_pct'] is not None and b['cpu_pct'] is not None else None)
            row = dict(run_id=run['run_id'], system=run['system'], repeat=run['repeat'],
                       start_s=left, end_s=right, offered=len(requests),
                       timely=sum(r['timely'] for r in requests),
                       r_srv_pct=100 * sum(r['timely'] for r in requests) / len(requests),
                       domain_cpu_pct=domain, edge1_cpu_pct=a['cpu_pct'], edge2_cpu_pct=b['cpu_pct'],
                       edge1_covered_s=a['covered_s'], edge2_covered_s=b['covered_s'])
            (bins if label == 'bin' else windows).append(row)
        own_bins = [b for b in bins if b['run_id'] == run['run_id']]
        assert sum(b['offered'] for b in own_bins) == 930
        assert sum(b['timely'] for b in own_bins) == run['totals']['timely']
    means = []
    for system in SYSTEMS:
        for left in range(0, 150, 5):
            rows = [b for b in bins if b['system'] == system and b['start_s'] == left]
            assert len(rows) == 3
            item = dict(system=system, start_s=left, end_s=left + 5)
            for key in METRICS:
                values = [r[key] for r in rows if r[key] is not None]
                item[key + '_n'] = len(values)
                item[key] = statistics.mean(values) if len(values) == 3 else None
                item[key + '_min'] = min(values) if len(values) == 3 else None
                item[key + '_max'] = max(values) if len(values) == 3 else None
            means.append(item)
    summary = {system: {key: statistics.mean(r[key] for r in windows if r['system'] == system)
                        if all(r[key] is not None for r in windows if r['system'] == system) else None
                        for key in METRICS} for system in SYSTEMS}
    return dict(status='PASS', exploratory=True, scope='All nine qualified A-only-start interrupted runs',
                bins=bins, means=means, high_load_outage_runs=windows,
                high_load_outage_means=summary, observation_audit=coverage, input_sha256=hashes,
                methods=dict(bin_width_s=5, r_time_basis='Scheduled arrival cohort; all offers in denominator',
                    cpu_time_basis='Observer scan midpoint; B aligned to A using measured offset',
                    cpu_scope='Common Go application-worker leaf cgroups, deduplicated by cgroup; excludes framework serving/control',
                    domain_cpu_denominator='Four fixed allocated vCPUs across Edge1/Edge2',
                    host_cpu_denominator='Two fixed allocated vCPUs per host, including idle Edge2',
                    cpu_boundary_estimation='Adjacent observed counter differences apportioned at their interval-average rate',
                    missing='No extrapolation over >3s gaps, worker/group changes or resets; incomplete bins are gaps; mean shown only with n=3',
                    uncertainty='Shaded envelopes are min-max across all three runs, not confidence intervals',
                    limits='CPU only, not CPU+PSS U_obs; busy CPU does not establish useful work or efficiency; scope is control node on cloud'))


def render_preview(means, panels=('r_srv_pct', 'domain_cpu_pct', 'edge_cpu_pct')):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    plt.rcdefaults()
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 7,
                         'axes.labelsize': 7.5, 'axes.linewidth': .5,
                         'xtick.major.width': .5, 'ytick.major.width': .5,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    figure, axes = plt.subplots(1, len(panels), figsize=(3.1 * len(panels), 2.25), squeeze=False)
    axes = axes[0]
    figure.subplots_adjust(left=.065 if len(panels) > 2 else .09, right=.98,
                           bottom=.23, top=.76, wspace=.34)
    names = {'r_srv_pct': 'Timely service', 'domain_cpu_pct': 'Domain application CPU',
             'edge2_cpu_pct': 'Edge2 application CPU', 'edge_cpu_pct': 'Edge1 / Edge2 application CPU'}
    labels = {'r_srv_pct': r'$R_{\rm srv}$ (%)',
              'domain_cpu_pct': r'$U_{\rm CPU}^{\rm domain}$ (%)',
              'edge2_cpu_pct': r'$U_{\rm CPU}^{\rm Edge2}$ (%)',
              'edge_cpu_pct': r'$U_{\rm CPU}^{\rm host}$ (%)'}
    colors = {'tide': '#087e65', 'kubeedge': '#ce623a', 'ray': '#5b58a5'}
    legends = {'tide': 'Tide', 'kubeedge': 'KubeEdge / EdgeMesh', 'ray': 'Ray Serve'}
    styles = {'tide': '-', 'kubeedge': '--', 'ray': '-.'}
    for index, (ax, key) in enumerate(zip(axes, panels)):
        ax.axvspan(20, 110, facecolor='#f2a95c', alpha=.13, edgecolor='none', linewidth=0)
        maxima = []
        for system in ('tide', 'kubeedge', 'ray'):
            rows = [r for r in means if r['system'] == system]
            assert len(rows) == 30
            def values(field):
                series = [r[field] if r[field] is not None else float('nan') for r in rows]
                return series + [series[-1]]
            x = list(range(0, 151, 5))
            fields = ('edge1_cpu_pct', 'edge2_cpu_pct') if key == 'edge_cpu_pct' else (key,)
            for field in fields:
                style = ('-' if field == 'edge1_cpu_pct' else '--') if key == 'edge_cpu_pct' else styles[system]
                label = legends[system] + (' / Edge1' if field == 'edge1_cpu_pct' else ' / Edge2') if key == 'edge_cpu_pct' else legends[system]
                ax.step(x, values(field), where='post', lw=.9, color=colors[system],
                        ls=style, label=label, zorder=4)
                if system != 'tide':
                    positions = range(0 if system == 'kubeedge' else 1, 30, 2)
                    ax.plot([i * 5 + 2.5 for i in positions],
                            [rows[i][field] if rows[i][field] is not None else float('nan') for i in positions],
                            linestyle='none', marker='s' if system == 'kubeedge' else '^',
                            ms=2.5, mec=colors[system], mfc='white', mew=.6, zorder=5, clip_on=False)
                ax.fill_between(x, values(field + '_min'), values(field + '_max'),
                                step='post', color=colors[system], alpha=.12, linewidth=0)
                maxima.extend(r[field + '_max'] for r in rows if r[field + '_max'] is not None)
        if key == 'edge_cpu_pct':
            ax.legend(handles=[Line2D([], [], color='#333333', lw=.9, ls='-', label='Edge1'),
                               Line2D([], [], color='#333333', lw=.9, ls='--', label='Edge2')],
                      loc='upper left', ncol=2, frameon=False, fontsize=6.5,
                      handlelength=2, handletextpad=.5, columnspacing=1.2, borderpad=.25)
        upper = 105 if key == 'r_srv_pct' else max(10, math.ceil(max(maxima) * 1.15 / 5) * 5)
        ax.set(xlim=(0, 150), ylim=(0, upper), xlabel='Elapsed time (s)', ylabel=labels[key])
        ax.set_xticks([0, 20, 60, 110, 150])
        ax.set_yticks([0, 50, 100] if key == 'r_srv_pct' else [0, upper / 2, upper])
        ax.set_title(f'({chr(97 + index)}) {names[key]}', fontsize=8, pad=4)
        ax.grid(axis='y', color='#e5e5e5', lw=.4)
        ax.spines[['top', 'right']].set_visible(False)
        ax.tick_params(length=2.5, pad=2)
    handles = [Patch(facecolor=colors[s], edgecolor='none', label=legends[s]) for s in ('tide', 'kubeedge', 'ray')]
    figure.legend(handles=handles, ncol=3, loc='upper center', bbox_to_anchor=(.52, 1),
                  frameon=False, fontsize=7, handlelength=2, columnspacing=1.5)
    return figure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).parent / 'ru-preview')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.output_dir
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        raise FileExistsError('Preview directory exists; use --overwrite.')
    out.mkdir(parents=True, exist_ok=True)
    report = analyze(root)
    (out / 'domain-ru-trends.json').write_text(json.dumps(report, indent=2) + '\n')
    for key in ('bins', 'means', 'high_load_outage_runs'):
        with (out / (key + '.csv')).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(report[key][0]))
            writer.writeheader()
            writer.writerows(report[key])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for name, panels in [('r-u-trends-preview', ('r_srv_pct', 'domain_cpu_pct', 'edge_cpu_pct')),
                         ('r-srv-edges-preview', ('r_srv_pct', 'edge_cpu_pct'))]:
        figure = render_preview(report['means'], panels)
        for ext in ('pdf', 'png', 'svg'):
            figure.savefig(out / (name + '.' + ext), dpi=220, facecolor='white')
        plt.close(figure)
    print(json.dumps(dict(status='PASS', runs=9, bins=len(report['bins']),
                         mean_bins=len(report['means']), high_load=report['high_load_outage_means'],
                         incomplete_cpu_bins=sum(b['domain_cpu_pct'] is None for b in report['bins']),
                         output=str(out)), indent=2))


if __name__ == '__main__':
    main()
