#!/usr/bin/env python3
"""Audit measured D/E matrices and the limited A controller-cost fragment.

Uses run/run.json measurement bounds (no warmup trimming). Requests are never
pooled across repetitions: each plotted mean/95% Student-t CI uses run metrics.
CPU counters are linearly interpolated at both exact epoch window boundaries;
bracketing is mandatory and counters must not decrease. Tide worker CPU is the
exclusive /tide parent cgroup; Bare/Kubernetes worker CPU is /proc worker ticks.
Both are normalized to the recorded two-logical-CPU budget. Controller CPU is
separate and normalized to ONE logical CPU. memory.current is not process RSS.

Example (from Tide):
  python3 scripts/revision/analyze_workloads.py --tide results-revision-20260928/workload-matrix-01 --out /tmp/tide-audit
  # Add --baseline results-revision-20260928/baseline-matrix-01 after completion.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import t as student_t

_spec = importlib.util.spec_from_file_location('revision_metrics', Path(__file__).with_name('metrics.py'))
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)

LABELS = {'tide': 'Tide', 'bare': 'Bare HTTP FCFS', 'kubernetes': 'Kubernetes HTTP FCFS'}
COLORS = {'tide': '#1768ac', 'bare': '#d17a22', 'kubernetes': '#478c58'}
IO_SCOPE = 'I/O: identical fsync/readback kernel; Bare uses host /tmp, containers use writable-overlay /tmp.'
BASELINE_SCOPE = 'Kubernetes is one hostNetwork Pod, serial FCFS; no HPA or KubeEdge claim.'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def points_for(samples, field):
    points = [(s['unix_ns'], s[field]) for s in samples if field in s]
    require(len(points) >= 2, f'{field}: insufficient points to bracket window')
    require(all(isinstance(ts, int) and math.isfinite(v) and v >= 0 for ts, v in points),
            f'{field}: invalid sample')
    require(all(a[0] < b[0] for a, b in zip(points, points[1:])), f'{field}: timestamps not increasing')
    return points


def interpolate(points, target, field):
    require(points[0][0] <= target <= points[-1][0], f'{field}: samples do not bracket boundary {target}')
    index = bisect_left([p[0] for p in points], target)
    if points[index][0] == target:
        return points[index][1]
    left, right = points[index-1], points[index]
    # Subtract integer epoch timestamps BEFORE conversion to floating point.
    fraction = (target-left[0]) / (right[0]-left[0])
    return left[1] + (right[1]-left[1]) * fraction


def counter_percent(samples, field, start_ns, end_ns, units_per_second, cpu_budget):
    require(end_ns > start_ns and units_per_second > 0 and cpu_budget > 0, 'invalid CPU window or units')
    points = points_for(samples, field)
    require(all(a[1] <= b[1] for a, b in zip(points, points[1:])), f'{field}: counter decreases/resets')
    used = interpolate(points, end_ns, field) - interpolate(points, start_ns, field)
    return used / units_per_second / ((end_ns-start_ns)/1e9) / cpu_budget * 100


def gauge_summary(samples, field, start_ns, end_ns):
    require(end_ns > start_ns, 'invalid gauge window')
    points = points_for(samples, field)
    window = [(start_ns, interpolate(points, start_ns, field))]
    window += [p for p in points if start_ns < p[0] < end_ns]
    window += [(end_ns, interpolate(points, end_ns, field))]
    integral = sum((b[0]-a[0]) * (a[1]+b[1])/2 for a, b in zip(window, window[1:]))
    return integral/(end_ns-start_ns), max(p[1] for p in window)


def run_mean_ci(values):
    values = [float(v) for v in values if v is not None and math.isfinite(v)]
    if not values:
        return dict(n=0, mean=None, ci95_low=None, ci95_high=None)
    mean = statistics.mean(values)
    half = student_t.ppf(.975, len(values)-1)*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>1 else None
    return dict(n=len(values), mean=mean, ci95_low=mean-half if half is not None else None,
                ci95_high=mean+half if half is not None else None)


def verify_outer_manifest(directory, manifest):
    require(manifest.get('status') == 'completed' and manifest.get('exit_code') == 0
            and manifest.get('timed_out') is False, f'{directory}: unsuccessful outer runner')
    require(bool(manifest.get('files_sha256')), f'{directory}: missing file hashes')
    for name, expected in manifest['files_sha256'].items():
        path = directory / name
        require(path.resolve().is_relative_to(directory.resolve()), f'{directory}: invalid hashed path')
        require(path.is_file() and sha(path) == expected, f'{directory}: hash mismatch {name}')


def load_json(path):
    return json.loads(path.read_text())


def qos_class_metrics(records, classes, kind, run):
    require(len(classes) >= 2 and len(set(classes)) == len(classes)
            and all(isinstance(v, int) and v > 0 for v in classes), 'invalid QoS classes')
    require(run.get('qos_classes_ms') == classes, 'run/matrix deadline classes differ')
    for record in records:
        deadline = classes[int(record['req_id']) % len(classes)]
        require(float(record['qos_ms']) == deadline and record.get('fn_name') == f'{kind}-qos{deadline}',
                f'deadline class mismatch for offered ID {record["req_id"]}')
    result = []
    for deadline in classes:
        selected = [r for r in records if float(r['qos_ms']) == deadline]
        stats = metrics.aggregate_records(selected, manifest=run)
        result.append({'qos_ms':deadline, 'class_offered_rps':len(selected)/stats['duration_s'], **stats})
    return result


def audit_matrix(directory, systems, rates, mixed=False):
    matrix_path = directory / 'matrix.json'
    matrix = load_json(matrix_path)
    require(matrix['status'] == 'completed', f'{directory}: matrix not completed')
    require(matrix['cpus'] == 2, f'{directory}: expected two logical CPUs')
    jobs = matrix['jobs']
    repeats = matrix['repeats']
    kinds = ['mixed-qos'] if mixed else sorted(matrix['calibration'])
    expected = Counter((system, kind, rate, repeat) for system in systems for kind in kinds
                       for rate in rates for repeat in range(1, repeats+1))
    actual = Counter((j.get('system', 'tide'), j['workload'], j['rps'], j['repeat']) for j in jobs)
    require(actual == expected, f'{directory}: missing/duplicate/unexpected matrix jobs')
    if mixed:
        for name, expected_hash in matrix['binary_sha256'].items():
            require(sha(directory/'bin'/name) == expected_hash, f'{directory}: frozen {name} binary hash mismatch')
        require(set(matrix['binary_sha256']) == {'tide','http','worker'}, 'missing mixed matrix binaries')
        require(sha(directory/'cpu_config.json') == matrix['config_sha256'], 'mixed configuration hash mismatch')
        mixed_config = load_json(directory/'cpu_config.json')
    else:
        tide_matrix = systems == ['tide']
        binary = directory / ('revision-run' if tide_matrix else 'revision-http-run')
        require(sha(binary) == matrix['binary_sha256'], f'{directory}: frozen binary hash mismatch')
        if not tide_matrix:
            require(sha(directory/'revision-worker') == matrix['worker_sha256'], f'{directory}: worker binary hash mismatch')
    rows, series, audited, class_rows = [], [], [], []
    for job in jobs:
        system, kind, rate, repeat = job.get('system', 'tide'), job['workload'], job['rps'], job['repeat']
        tide = system == 'tide'
        identity = dict(system=system, workload=kind, rps=rate, repeat=repeat)
        require(job['status'] == 'completed', f'{directory}: job not completed {identity}')
        run_root = directory / kind / f'rps{rate}' / f'run{repeat}' if tide and not mixed else directory / job['path']
        require(run_root.resolve().is_relative_to(directory.resolve()), 'invalid run path')
        measured = run_root if tide and not mixed else run_root/'measurement'
        outer = load_json(measured/'manifest.json')
        verify_outer_manifest(measured, outer)
        require({'run/raw.csv', 'run/run.json', 'samples.jsonl'}.issubset(outer['files_sha256']),
                f'{measured}: missing required hashes')
        binary_hash = matrix['binary_sha256']['tide' if tide else 'http'] if mixed else matrix['binary_sha256']
        require(outer['binary_sha256'] == binary_hash, f'{measured}: binary identity mismatch')
        run = load_json(measured/'run/run.json')
        require(run['status'] == 'completed' and run['requests'] == job['requests'] == rate*matrix['duration_s'],
                f'{measured}: status/request count mismatch')
        require(run['rps'] == rate and run['cpus'] == matrix['cpus'], f'{measured}: rate/CPU mismatch')
        calibration = matrix['calibration'] if mixed else matrix['calibration'][kind]
        require(run['workload'] == calibration['workload'], f'{measured}: workload mismatch')
        require(run['argv'] == outer['command'], f'{measured}: command mismatch')
        if '--' in job['command']:
            require(job['command'][job['command'].index('--')+1:] == outer['command'], f'{measured}: matrix command mismatch')
        require(run['pid'] == outer['pid'], f'{measured}: sampled controller identity mismatch')
        records = metrics.read_records(measured/'run/raw.csv', job['requests'])
        require({int(r['req_id']) for r in records} == set(range(job['requests'])), f'{measured}: missing/unexpected request IDs')
        require(sum(r['status'] == 200 for r in records) == run['successes'] == job['successes'],
                f'{measured}: successful outcome count mismatch')
        require(all(r.get('qos_ms') and float(r['qos_ms']) > 0 for r in records), f'{measured}: missing QoS')
        require(run['measurement_end_ns'] > run['measurement_start_ns'] >= 0, f'{measured}: invalid window')
        require(run['measurement_end_ns']-run['measurement_start_ns'] == int(matrix['duration_s']*1e9),
                f'{measured}: window duration mismatch')
        aggregate = metrics.aggregate_records(records, manifest=run)
        if mixed:
            classes = qos_class_metrics(records, matrix['classes_ms'], calibration['workload']['kind'], run)
            require(math.isclose(sum(c['on_time_goodput'] for c in classes), aggregate['on_time_goodput'], abs_tol=1e-12),
                    f'{measured}: class and overall goodput disagree')
            if tide:
                effective = run['effective_config']
                require(all(effective.get(k) == v for k,v in mixed_config.items()), f'{measured}: effective config mismatch')
                require(all(effective['qos_by_function_ms'].get(f'{calibration["workload"]["kind"]}-qos{v}') == v
                            for v in matrix['classes_ms']), f'{measured}: configured deadline classes mismatch')
            class_rows.extend({**identity, **c} for c in classes)
        require(aggregate['n_eligible'] == job['requests'], f'{measured}: offered requests outside measurement window')
        epoch = run['measurement_start_unix_ns']
        start, end = epoch+run['measurement_start_ns'], epoch+run['measurement_end_ns']
        require(outer['started_unix_ns'] <= start < end <= outer['finished_unix_ns'], f'{measured}: outer runner does not cover window')
        samples = [json.loads(line) for line in (measured/'samples.jsonl').read_text().splitlines()]
        require(all(a['unix_ns']<b['unix_ns'] and a['monotonic_ns']<b['monotonic_ns'] for a,b in zip(samples,samples[1:])),
                f'{measured}: sample clocks not increasing')
        hz = outer['clock_ticks_per_second']
        field = 'tide_parent_cpu_usage_usec' if tide else 'workload_cpu_ticks'
        units = 1e6 if tide else hz
        resource = dict(worker_cpu_pct_2cpu=counter_percent(samples, field, start, end, units, 2),
                        controller_cpu_pct_1cpu=counter_percent(samples, 'process_cpu_ticks', start, end, hz, 1))
        for name, column in [('process_rss_bytes','controller_rss'),
                             ('tide_parent_memory_bytes','tide_cgroup_memory') if tide else ('workload_rss_bytes','worker_rss')]:
            mean, maximum = gauge_summary(samples, name, start, end)
            resource[column+'_mean_mib'] = mean/1024**2
            resource[column+'_sampled_max_mib'] = maximum/1024**2
        if tide:
            submit = [int(r['submit_call_ns'])/1000 for r in records]
            resource['submit_call_mean_us'] = statistics.mean(submit)
            resource['submit_call_p95_us'] = float(np.quantile(submit, .95))
            resource['stub_initial_executor_create_ms'] = run['stub_with_initial_executor_create_ns']/1e6
        else:
            setup = load_json(run_root/'setup.json')
            require(sha(run_root/'setup.json') == job['setup_sha256'], f'{run_root}: setup hash mismatch')
            require(setup['worker_pid'] == outer['workload_pid'], f'{run_root}: sampled worker identity mismatch')
            require(setup['gomaxprocs'] == 1 and all(v == [0,1] for v in setup['threads_affinity'].values()),
                    f'{run_root}: unmatched worker CPU settings')
            resource['worker_ready_observed_ms'] = setup['worker_ready_observed_ns']/1e6
        rows.append({**identity, **aggregate, **resource, 'cpu_counter_source':field,
                     'measurement_start_unix_ns':start, 'measurement_end_unix_ns':end})
        # Half-second CPU windows, with interpolation at BOTH bin endpoints.
        for left in range(start, end, 500_000_000):
            right = min(left+500_000_000, end)
            series.append({**identity, 'time_start_s':(left-start)/1e9, 'time_end_s':(right-start)/1e9,
                           'worker_cpu_pct_2cpu':counter_percent(samples, field, left, right, units, 2),
                           'controller_cpu_pct_1cpu':counter_percent(samples,'process_cpu_ticks',left,right,hz,1)})
        audited.append({**identity, 'path':str(measured), 'outer_manifest_sha256':sha(measured/'manifest.json'),
                        'files_sha256':outer['files_sha256'], 'raw_rows':len(records),
                        'resource_window_bracketed':True, 'window_start_unix_ns':start, 'window_end_unix_ns':end,
                        'samples':len(samples), 'worker_cpu_counter':field})
    return rows, series, dict(path=str(directory), matrix_sha256=sha(matrix_path), status='passed',
                              validated_jobs=len(jobs), raw_rows=sum(j['requests'] for j in jobs),
                              logical_cpu_budget=2, repeats=repeats, runs=audited,
                              qos_classes_ms=matrix.get('classes_ms'), mixed_deadline_classes_audited=mixed), class_rows


METRIC_COLUMNS = ['throughput','on_time_goodput','success_yield','on_time_yield','avg_ms','p95_ms',
                  'drop_pct','n_timeout','n_failure','n_late','drained_successes','worker_cpu_pct_2cpu',
                  'controller_cpu_pct_1cpu','controller_rss_mean_mib','controller_rss_sampled_max_mib',
                  'tide_cgroup_memory_mean_mib','tide_cgroup_memory_sampled_max_mib',
                  'worker_rss_mean_mib','worker_rss_sampled_max_mib','submit_call_mean_us','submit_call_p95_us',
                  'stub_initial_executor_create_ms','worker_ready_observed_ms']


def summarize(rows, group_keys, columns):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in group_keys)].append(row)
    out = []
    for key, group in sorted(groups.items()):
        result = dict(zip(group_keys,key))
        result['runs'] = len(group)
        for column in columns:
            for stat, value in run_mean_ci([r.get(column) for r in group]).items():
                result[column+'_'+stat] = value
        out.append(result)
    return out


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def draw_metric(ax, rows, column, x='rps'):
    for system in ('tide','bare','kubernetes'):
        group = sorted((r for r in rows if r['system']==system), key=lambda r:r[x])
        if not group: continue
        group = [r for r in group if r.get(column+'_mean') is not None]
        if not group: continue
        xs=[r[x] for r in group]; ys=[r[column+'_mean'] for r in group]
        lower=[r[column+'_ci95_low'] for r in group]; upper=[r[column+'_ci95_high'] for r in group]
        ax.plot(xs,ys,'o-',label=LABELS[system],color=COLORS[system],markersize=4)
        if all(v is not None for v in lower+upper):
            ax.fill_between(xs,lower,upper,color=COLORS[system],alpha=.14)
    ax.grid(alpha=.25)


def save_figure(fig, directory, name):
    for suffix in ('png','pdf'):
        fig.savefig(directory/f'{name}.{suffix}',dpi=170,bbox_inches='tight')
    plt.close(fig)


def plots(summary, timeseries, directory, baseline_present, class_summary=None):
    order = {'cpu':0, 'memory':1, 'io':2, 'mixed-qos':3}
    kinds = sorted({r['workload'] for r in summary}, key=lambda k:(order.get(k,4),k))
    has_mixed = any('mixed' in kind.lower() for kind in kinds)
    cols = len(kinds) + (not has_mixed)
    fig, axes=plt.subplots(3,cols,figsize=(4.0*cols,8.3),squeeze=False)
    for col,kind in enumerate(kinds):
        sub=[r for r in summary if r['workload']==kind]
        for row,(metric,label) in enumerate([('on_time_goodput','On-time completions / s'),('success_yield','Success yield, including drain'),('p95_ms','Per-run successful latency p95 (ms)')]):
            draw_metric(axes[row,col],sub,metric)
            axes[row,col].set_ylabel(label); axes[row,col].set_xlabel('Offered requests / s')
        title = kind.upper()
        if 'mixed' in kind.lower() and class_summary:
            deadlines = sorted({r['qos_ms'] for r in class_summary if r['workload']==kind})
            title += '\nCPU kernel; '+ '/'.join(str(v) for v in deadlines)+' ms'
        axes[0,col].set_title(title)
    for col in range(len(kinds),cols):
        for row in range(3):
            axes[row,col].axis('off')
        axes[1,col].text(.5,.5,'Mixed-QoS panel\npending measurement',ha='center',va='center',transform=axes[1,col].transAxes)
    axes[0,0].legend(fontsize=8)
    state='' if baseline_present else ' — Tide audit only; baselines pending'
    fig.suptitle('D. Measured workload/load response'+state+'\nMean and 95% Student-t CI across independent repetitions (no request pooling)',fontsize=12)
    fig.text(.5,.01,IO_SCOPE+'\n'+BASELINE_SCOPE,ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.06,1,.93));save_figure(fig,directory,'D_workloads')

    fig,axes=plt.subplots(1,len(kinds),figsize=(4.5*len(kinds),3.7),squeeze=False)
    for ax,kind in zip(axes[0],kinds):
        draw_metric(ax,[r for r in summary if r['workload']==kind],'worker_cpu_pct_2cpu')
        ax.set_title(kind.upper());ax.set_xlabel('Offered requests / s');ax.set_ylabel('Actual worker CPU (% of 2 logical CPUs)')
    axes[0,0].legend(fontsize=8)
    fig.suptitle('E. Measured worker CPU'+state+'; mean ± 95% CI',fontsize=12)
    fig.text(.5,.005,'Tide: exclusive parent cgroup CPU; baselines: worker /proc CPU. Counters interpolated at bracketed window boundaries.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.04,1,.90));save_figure(fig,directory,'E_worker_cpu')

    fig,axes=plt.subplots(1,len(kinds),figsize=(4.5*len(kinds),3.7),squeeze=False)
    for ax,kind in zip(axes[0],kinds):
        highest=max(r['rps'] for r in summary if r['workload']==kind)
        sub=[r for r in timeseries if r['workload']==kind and r['rps']==highest]
        draw_metric(ax,sub,'worker_cpu_pct_2cpu',x='time_end_s')
        ax.set_title(f'{kind.upper()} @ {highest} req/s');ax.set_xlabel('Time since window start (s)');ax.set_ylabel('Actual worker CPU (% of 2 logical CPUs)')
    axes[0,0].legend(fontsize=8)
    fig.suptitle('E. CPU time series at highest tested load per workload'+state,fontsize=12)
    fig.text(.5,.005,'Each point: preceding 0.5 s bin; mean and pointwise 95% t CI across repetitions. Same bin boundaries for all systems.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.04,1,.90));save_figure(fig,directory,'E_cpu_timeseries')

    fig,axes=plt.subplots(2,len(kinds),figsize=(4.5*len(kinds),6.3),squeeze=False)
    for col,kind in enumerate(kinds):
        sub=[r for r in summary if r['workload']==kind]
        for row,(metric,label) in enumerate([('controller_cpu_pct_1cpu','Controller CPU (% of one logical CPU)'),('controller_rss_mean_mib','Controller process RSS (MiB)')]):
            draw_metric(axes[row,col],sub,metric)
            axes[row,col].set_ylabel(label);axes[row,col].set_xlabel('Offered requests / s')
        axes[0,col].set_title(kind.upper())
    axes[0,0].legend(fontsize=8)
    fig.suptitle('A. Controller-process cost fragment'+state+'\nTide harness/controller vs baseline HTTP load generator; not full scheduling-overhead experiment',fontsize=11)
    fig.text(.5,.005,'Mean ± 95% t CI across repetitions. Workers excluded. Tide parent memory.current is not RSS and is not plotted here.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.04,1,.9));save_figure(fig,directory,'A_controller_fragment')


def write_class_table(path, rows):
    def interval(row, name):
        mean,lo,hi = (row[name+'_'+k] for k in ('mean','ci95_low','ci95_high'))
        return f'{mean:.3f} [{lo:.3f}, {hi:.3f}]' if lo is not None else f'{mean:.3f} (CI unavailable)'
    lines = ['# Mixed-QoS class results', '',
             'Same CPU kernel, alternating deadline classes by offered request ID. No priority queue.',
             'Goodput uses the complete shared measurement window. Yields retain drained outcomes.',
             'Entries are means [95% Student-t CI] over independent runs; class request counts are not pooled.', '',
             '| System | Overall offered req/s | Class QoS (ms) | Runs | Class on-time goodput (req/s) | Class on-time yield | Class success yield |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for row in rows:
        lines.append(f'| {LABELS[row["system"]]} | {row["rps"]} | {row["qos_ms"]} | {row["runs"]} | '
                     + ' | '.join(interval(row,k) for k in ('on_time_goodput','on_time_yield','success_yield'))+' |')
    path.write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tide',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--mixed',type=Path)
    parser.add_argument('--mixed-rates',default='20,40,80')
    parser.add_argument('--mixed-systems',default='tide,bare,kubernetes')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--rates',default='5,10,20,40')
    parser.add_argument('--baseline-systems',default='bare,kubernetes')
    args=parser.parse_args()
    rates=[int(v) for v in args.rates.split(',')]
    args.out.mkdir(parents=True,exist_ok=True)
    rows,series,audits,class_rows=[],[],[],[]
    try:
        inputs = [(args.tide,['tide'],rates,False)]
        if args.baseline:
            inputs.append((args.baseline,args.baseline_systems.split(','),rates,False))
        if args.mixed:
            inputs.append((args.mixed,args.mixed_systems.split(','),[int(v) for v in args.mixed_rates.split(',')],True))
        for path,systems,matrix_rates,mixed in inputs:
            rr,ss,audit,cc=audit_matrix(path,systems,matrix_rates,mixed=mixed)
            rows.extend(rr);series.extend(ss);audits.append(audit);class_rows.extend(cc)
        summary=summarize(rows,['system','workload','rps'],METRIC_COLUMNS)
        time_summary=summarize(series,['system','workload','rps','time_start_s','time_end_s'],['worker_cpu_pct_2cpu','controller_cpu_pct_1cpu'])
        for name,data in [('per_run',rows),('per_cell',summary),('timeseries_per_run',series),('timeseries_summary',time_summary)]:
            write_csv(args.out/f'{name}.csv',data)
        class_summary=summarize(class_rows,['system','workload','rps','qos_ms'],
                                ['class_offered_rps','n_eligible','n_ok','n_on_time','n_failure','n_timeout','n_late',
                                 'on_time_goodput','on_time_yield','success_yield','p95_ms','drained_successes'])
        if class_rows:
            write_csv(args.out/'mixed_qos_per_run.csv',class_rows)
            write_csv(args.out/'mixed_qos_per_cell.csv',class_summary)
            write_class_table(args.out/'mixed_qos_table.md',class_summary)
        plots(summary,time_summary,args.out,args.baseline is not None,class_summary)
        report=dict(status='passed',created_utc=datetime.now(timezone.utc).isoformat(),
                    validator_sha256=sha(Path(__file__)),metrics_sha256=sha(Path(__file__).with_name('metrics.py')),
                    matrices=audits,total_runs=len(rows),total_requests=sum(r['n_total'] for r in rows),
                    statistics='run-level means with two-sided 95% Student-t CI; no pooled request latency quantiles; n shown per metric',
                    rate_semantics='run.json explicit admission/completion window; no warmup trim; cohort yields include drain',
                    worker_cpu='actual CPU use / elapsed seconds / 2 logical CPUs; linear interpolation with mandatory bracketing',
                    controller_scope='process CPU normalized to one logical CPU; process RSS; A fragment only',
                    memory_scope='Tide cgroup memory.current and baseline worker RSS retained in separate columns, not compared',
                    mixed_qos='measured; alternating offered-ID classes, no priority ordering; class rates use full common window' if class_rows else 'pending measurement',baseline_scope=BASELINE_SCOPE,io_scope=IO_SCOPE,
                    limitations=['three repetitions give imprecise t intervals; time-series intervals are pointwise',
                                 'Tide cgroup attribution requires exclusive experiment use; sampler cannot prove absence of unrelated cgroup tasks',
                                 'approximately 100 ms resource sampling; peaks between samples are not measured',
                                 'baseline controller is an HTTP load generator, Tide controller also schedules requests'])
        (args.out/'README.md').write_text('# Measured D/E analysis and A controller fragment\n\n'+report['statistics']+'.\n\n'+report['worker_cpu']+'.\n\n'+report['memory_scope']+'.\n\n'+IO_SCOPE+' '+BASELINE_SCOPE+'\n\nMixed-QoS: '+report['mixed_qos']+'. A is a controller-process fragment, not the complete scheduling-overhead experiment.\n\n'+'\n'.join('- '+v for v in report['limitations'])+'\n')
        report['outputs_sha256']={p.name:sha(p) for p in args.out.iterdir() if p.is_file() and p.name!='audit.json'}
        (args.out/'audit.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
        print(f'PASS: {len(rows)} runs, {report["total_requests"]} requests; {args.out}/audit.json')
    except (ValueError,OSError,KeyError) as error:
        (args.out/'audit.json').write_text(json.dumps(dict(status='failed',error=str(error),matrices=audits),indent=2)+'\n')
        parser.exit(1,f'Audit failed: {error}\n')


if __name__=='__main__':
    main()
