#!/usr/bin/env python3
"""Independent local-only audit of frozen three-node mixed-deadline raw evidence.

No collector, outcome, or summarizer imports. Outputs are published atomically
only after all 60 treatments / 120 origin commits pass the evidence audit.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time

SYSTEMS = ('bare', 'tide', 'ray', 'kubeedge')
LEVELS = ('low', 'high', 'overload')
FACTORS = {'low': .3, 'high': .8, 'overload': 1.2}
ORIGINS = ('k2', 'k3')
NODES = ('nj1', 'nj2', 'nj3')
CLASSES = (100, 500)
WORKLOAD = {'kind': 'cpu', 'bytes': 65536, 'iterations': 100}
QOS = {'cpu-qos100': 100, 'cpu-qos500': 500}
MIXED_PROTOCOL = 'cpu65536x100-balanced-slot-orthogonal-v2'
T4 = 2.7764451051977987
COUNTS = ('offered', 'cohort_successful', 'cohort_on_time', 'failed', 'completed_in_window', 'on_time_in_window')
GROUPS = ('successful_latency_ms', 'successful_planned_to_return_ms', 'replay_lateness_ms', 'kernel_service_ms')
STATS = ('count', 'mean', 'p50', 'p95', 'p99', 'max')
METRICS = (*COUNTS, 'throughput_rps', 'goodput_rps', 'offered_rps', 'cohort_yield',
           'failure_fraction', 'successful_outside_window', 'successful_deadline_misses',
           *[f'{group}_{stat}' for group in GROUPS for stat in STATS],
           *[f'origin_{origin}_{key}' for origin in ORIGINS for key in COUNTS],
           *[f'destination_{node}_successes' for node in NODES])
BASE = ('job_id', 'sequence', 'block', 'repeat', 'kind', 'level', 'system', 'deadline_ms',
        'function', 'load_factor', 'rate_rps', 'trace_sha256', 'start_unix', 'duration_s')
CELL_BASE = ('kind', 'level', 'system', 'deadline_ms', 'function', 'load_factor', 'rate_rps', 'n')
CI_STATS = ('n', 'mean', 'sd', 'se', 'ci95_halfwidth', 'ci95_low', 'ci95_high')
CELL_COLUMNS = (*CELL_BASE, *[f'{metric}_{stat}' for metric in METRICS for stat in CI_STATS])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def cpu_reference():
    """Exact SHA-256 kernel; independently reproduce Go uint64 checksum."""
    buf = bytearray((bytes((index * 17 + 3) % 256 for index in range(256))) * 256)
    for _ in range(100):
        hashed = hashlib.sha256(buf).digest()
        buf[:32] = hashed
    return {'bytes_processed': 65536 * 100, 'checksum': int.from_bytes(hashed[:8], 'little')}


def integer(value):
    return type(value) is int


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def ci5(values):
    require(len(values) == 5, 'Exactly five run-level repetitions required')
    valid = [value for value in values if value is not None]
    result = dict.fromkeys(CI_STATS)
    result['n'] = len(valid)
    require(all(finite(value) for value in valid), 'Nonfinite metric')
    if len(valid) == 5:
        mean, sd = statistics.mean(valid), statistics.stdev(valid)
        se = sd / math.sqrt(5)
        result.update(mean=mean, sd=sd, se=se, ci95_halfwidth=T4 * se,
                      ci95_low=mean - T4 * se, ci95_high=mean + T4 * se)
    return result


def describe(values):
    ordered = sorted(values)
    if not ordered:
        return {key: 0 if key == 'count' else None for key in STATS}
    return {'count': len(ordered), 'mean': math.fsum(ordered) / len(ordered), 'max': ordered[-1],
            **{key: ordered[math.ceil(len(ordered) * quantile) - 1]
               for key, quantile in (('p50', .5), ('p95', .95), ('p99', .99))}}


class Evidence:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.hashes = {}

    def read(self, relative, wanted=None, jsonl=False, parse=True):
        path = (self.root / relative).resolve()
        require(path.is_relative_to(self.root), 'Evidence path escapes root')
        raw = path.read_bytes()
        actual = digest(raw)
        require(wanted is None or actual == wanted, f'Hash mismatch: {relative}')
        self.hashes[str(path.relative_to(self.root))] = actual
        if not parse:
            return raw
        if jsonl:
            require(bool(raw) and all(line.strip() for line in raw.splitlines()), 'Empty raw record/blank JSONL line')
            return [json.loads(line) for line in raw.splitlines()]
        return json.loads(raw)


def validate_trace(trace, info, rate, expected):
    require(trace['workload'] == WORKLOAD and trace['expected_result'] == expected,
            'Trace workload or independently recomputed checksum mismatch')
    require(trace['duration_ns'] == 10_000_000_000 and trace['pattern'] == 'balanced', 'Invalid trace window/pattern')
    require(trace.get('schema_version') == 2 and trace.get('mixed_protocol') == MIXED_PROTOCOL
            and trace.get('deadline_classes_ms') == list(CLASSES)
            and trace.get('qos_by_function_ms') == QOS, 'Invalid mixed trace schema/class mapping')
    requests = trace['requests']; count = len(requests)
    require(integer(rate) and rate > 0 and count == rate * 10 == info['offered'], 'Rate/trace offered count mismatch')
    require(trace['requested_rate_rps'] == rate and trace['actual_rate_rps'] == rate, 'Trace rate mismatch')
    ids = [row['id'] for row in requests]
    require(len(set(ids)) == count and all(isinstance(value, str) and value for value in ids), 'Duplicate/invalid frozen trace IDs')
    # Rebuild uniform arrivals and both classes on every round-robin slot per eight origin offers.
    for ordinal, row in enumerate(requests):
        require(row['origin'] == ORIGINS[ordinal % 2]
                and integer(row['offset_ns']) and row['offset_ns'] == ordinal * trace['duration_ns'] // count
                and integer(row['deadline_ms']) and row['deadline_ms'] == CLASSES[((ordinal // 2) % 2) ^ (((ordinal // 2) // 4) % 2) ^ (ordinal % 2)],
                'Frozen offer violates deterministic arrival/origin/class protocol')
    for origin in (None, *ORIGINS):
        counts = Counter(row['deadline_ms'] for row in requests if origin is None or row['origin'] == origin)
        require(abs(counts[100] - counts[500]) <= (0 if origin is None else 1), 'Unbalanced frozen deadline classes')


def calibrations_and_rates(evidence, plan, expected, test_only):
    required = {f'{node}-{system}.json' for node in NODES for system in ('bare', 'tide')}
    hashes = plan['calibration_sha256']
    require(test_only or set(hashes) == required, 'Missing frozen fresh calibration inventory')
    records = {}
    for name, wanted in hashes.items():
        require(name in required, 'Unexpected calibration artifact')
        obj = evidence.read('frozen/calibration/' + name, wanted)
        node, system = Path(name).stem.split('-')
        require(obj['node'] == node and obj['system'] == system and obj['workloads'] == {'cpu': WORKLOAD}, 'Calibration identity/workload mismatch')
        rows = obj['rows']; summary = obj['summary']['cpu']
        require(rows and len(rows) == summary['samples'] and len({row['sample'] for row in rows}) == len(rows), 'Calibration sample count mismatch')
        for row in rows:
            require(row['kind'] == 'cpu' and row['response']['node'] == node and row['response']['result'] == expected
                    and finite(row['elapsed_ns']) and row['elapsed_ns'] > 0
                    and finite(row['response']['service_ns']) and row['response']['service_ns'] > 0,
                    'Invalid raw calibration result')
        service = statistics.median(row['response']['service_ns'] / 1e6 for row in rows)
        path = statistics.median(row['elapsed_ns'] / 1e6 for row in rows)
        require(math.isclose(summary['median_service_ms'], service, rel_tol=1e-10)
                and math.isclose(summary['median_path_ms'], path, rel_tol=1e-10), 'Calibration summary differs from raw')
        records[Path(name).stem] = obj
    if records:
        capacity = sum((2 if node == 'nj1' else 1) * 1000 / records[f'{node}-bare']['summary']['cpu']['median_service_ms'] for node in NODES)
        require(set(plan['capacity_rps']) == {'cpu'} and math.isclose(plan['capacity_rps']['cpu'], capacity, rel_tol=1e-12), 'Calibration capacity mismatch')
        rule = 'round(max(2, sum(node_slots * 1000 / bare median service ms) * factor)); factors=0.30,0.80,1.20; Python round'
        require(plan['rate_rule'] == rule, 'Unsupported rate-selection rule')
        for level, factor in FACTORS.items():
            require(plan['rates_rps']['cpu'][level] == round(max(2, capacity * factor)), 'Calibration rate mismatch')
        config = plan['tide_configs']['cpu']
        estimates = {node: math.ceil(records[f'{node}-tide']['summary']['cpu']['median_path_ms']) for node in NODES}
        require(config['node_execution_ms'] == estimates and config['execution_ms'] == max(estimates.values()), 'Tide estimates differ from frozen calibration')
    return records


def frozen_plan(evidence, source_dir):
    wanted = evidence.read('frozen/manifest.sha256', parse=False).decode().strip()
    require(len(wanted) == 64 and all(c in '0123456789abcdef' for c in wanted), 'Invalid manifest SHA marker')
    plan = evidence.read('frozen/manifest.json', wanted)
    test_only = plan.get('test_only') is True
    require(not test_only or 'TEST_ONLY' in plan['campaign'], 'Synthetic campaign must be explicitly TEST_ONLY')
    require(plan['repetitions'] == 5 and len(plan['systems']) == 4 and set(plan['systems']) == set(SYSTEMS), 'Unexpected repetition/system design')
    require(plan['duration_s'] == 10 and plan['deadline_classes_ms'] == list(CLASSES)
            and plan['workloads'] == {'cpu': WORKLOAD} and plan['load_factors'] == FACTORS, 'Unexpected mixed CPU experiment protocol')
    require(finite(plan['timeout_s']) and plan['timeout_s'] >= .5, 'Invalid timeout')
    rates = plan['rates_rps']
    require(set(rates) == {'cpu'} and set(rates['cpu']) == set(LEVELS)
            and all(integer(rate) and rate > 0 for rate in rates['cpu'].values()), 'Invalid rate map')
    require(rates['cpu']['low'] < rates['cpu']['high'] < rates['cpu']['overload'], 'Load levels must increase')
    config = plan['tide_configs']['cpu']
    require(set(plan['tide_configs']) == {'cpu'} and config['qos_by_function_ms'] == QOS, 'Frozen Tide class mapping mismatch')
    require(evidence.read('frozen/configs/tide-cpu.json') == config, 'Frozen Tide configuration changed')
    jobs = plan['jobs']
    keys = [(j['level'], j['system'], j['repeat']) for j in jobs]
    require(len(jobs) == len({j['id'] for j in jobs}) == 60
            and set(keys) == set(itertools.product(LEVELS, SYSTEMS, range(1, 6)))
            and [j['sequence'] for j in jobs] == list(range(1, 61)), 'Matrix is not the complete 60-treatment factorial')
    for job in jobs:
        require(job['kind'] == 'cpu' and job['pattern'] == 'balanced'
                and job['block'] == f"r{job['repeat']}-cpu-{job['level']}-balanced"
                and job['trace'] == f"cpu-{job['level']}-balanced.json"
                and job['rate_rps'] == rates['cpu'][job['level']]
                and job.get('load_factor', FACTORS[job['level']]) == FACTORS[job['level']], 'Unpaired treatment identity/rate')
    require(test_only or plan.get('seed') == 20261016, 'Unexpected frozen randomization seed')
    if not test_only:
        require(plan['slots'] == {'nj1': 2, 'nj2': 1, 'nj3': 1}, 'Shared slot inventory differs from four')
        order = []; rng = random.Random(20261016)
        for repeat in range(1, 6):
            levels = list(LEVELS); rng.shuffle(levels)
            for level in levels:
                systems = list(SYSTEMS); rng.shuffle(systems)
                order.extend((level, system, repeat) for system in systems)
        require(keys == order, 'Treatment order differs from frozen paired randomization')
        require('fig7_cluster_mixed_audit.py' in plan['source_sha256'], 'Missing frozen auditor source hash')
    source_dir = Path(source_dir).resolve()
    for name, sha in plan['source_sha256'].items():
        require(Path(name).name == name and digest((source_dir / name).read_bytes()) == sha, 'Frozen measurement source changed: ' + name)
    if 'fig7_cluster_mixed_audit.py' in plan['source_sha256']:
        require(plan['source_sha256']['fig7_cluster_mixed_audit.py'] == digest(Path(__file__).read_bytes()),
                'Executing auditor differs from frozen auditor source')
    expected = cpu_reference()
    calibrations_and_rates(evidence, plan, expected, test_only)
    names = {f'cpu-{level}-balanced.json' for level in LEVELS}
    require(set(plan['traces']) == names, 'Invalid frozen trace inventory')
    traces = {}
    for name, info in plan['traces'].items():
        trace = evidence.read('frozen/traces/' + name, info['file_sha256'])
        require(digest(canonical(trace)) == info['sha256'], 'Frozen canonical trace hash mismatch')
        validate_trace(trace, info, rates['cpu'][name.split('-')[1]], expected)
        traces[name] = trace
    return plan, wanted, traces


def proven_instances(evidence, plan, manifest_sha):
    used = evidence.read('formal/readiness-used.json')
    marker = evidence.read('integrated_smoke.json', used['marker_sha256'])
    require(used['manifest_sha256'] == manifest_sha and used['marker'] == marker and marker['ok'] is True, 'Readiness binding changed')
    actual = marker['native_configs']
    require(set(actual) == set(NODES) and all(actual[node] == plan['tide_configs']['cpu'] for node in NODES), 'Readiness native config differs from frozen Tide config on all three nodes')
    instances = {system: defaultdict(set) for system in SYSTEMS}

    def visit(obj, system=None):
        if isinstance(obj, list):
            for child in obj:
                visit(child, system)
        elif isinstance(obj, dict):
            system = obj.get('system', system)
            if system in instances:
                values = obj.get('instances', [])
                if isinstance(values, dict):
                    values = [value for group in values.values() for value in group]
                values = values + ([obj['worker_instance']] if 'worker_instance' in obj else [])
                for value in values:
                    require(isinstance(value, str) and value.split('-', 1)[0] in NODES, 'Invalid proven worker identity')
                    instances[system][value.split('-', 1)[0]].add(value)
            for child in obj.values():
                if isinstance(child, (list, dict)):
                    visit(child, system)

    require(marker['evidence'], 'Missing readiness evidence')
    for item in marker['evidence']:
        visit(evidence.read(item['path'], item['sha256']))
    for system in SYSTEMS:
        require({node: len(instances[system][node]) for node in NODES} == {'nj1': 2, 'nj2': 1, 'nj3': 1}, 'Readiness worker inventory not exactly 2/1/1 for ' + system)
    return {system: {node: sorted(values) for node, values in nodes.items()} for system, nodes in instances.items()}


def routes(system, origin, selected):
    gateway = system in ('tide', 'ray')
    port = {'tide': 29801, 'ray': 29870, 'bare': 29810, 'kubeedge': 29890}[system]
    urls = [f'http://127.0.0.1:{port}/run'] if gateway else [f'http://11.0.4.47:{port}/run', f'http://11.0.4.47:{port + 1}/run', f'http://11.0.4.48:{port}/run', f'http://11.0.4.49:{port}/run']
    assigned = {r['id']: urls[0] if gateway else urls[(ordinal + (2 if origin == 'k3' else 0)) % 4] for ordinal, r in enumerate(selected)}
    return 'gateway' if gateway else 'direct', urls, assigned


def response_outcome(row, expected):
    """Compute outcome from raw HTTP status/body; never trust collector outcome."""
    status = row.get('http_status')
    if status is None:
        require(row.get('response') is None and not row.get('response_body')
                and isinstance(row.get('error'), str) and row['error'], 'Transport failure carries a fabricated response or lacks an error')
        return str(row.get('error') or 'missing_http_response'), None
    require(integer(status) and 100 <= status <= 599, 'Invalid HTTP status')
    raw = row.get('response_body')
    require(isinstance(raw, str), 'HTTP response lacks raw body')
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        require(row.get('response') is None, 'Decoded response differs from raw body')
        return 'invalid_json' if status == 200 else f'http_{status}', None
    require(body == row.get('response'), 'Decoded response differs from raw body')
    if status != 200:
        return f'http_{status}', body
    if not isinstance(body, dict) or not isinstance(body.get('result'), dict):
        return 'invalid_response', body
    result = body['result']
    if any(not integer(result.get(key)) or result[key] != value for key, value in expected.items()):
        return 'result_mismatch', body
    return None, body


def check_tide_headers(row, deadline):
    if row.get('http_status') is None:
        return
    headers = row.get('response_headers')
    require(isinstance(headers, dict), 'Tide response missing audit headers')
    expected = {'x-tide-function': f'cpu-qos{deadline}', 'x-tide-deadline-ms': str(deadline),
                'x-tide-effective-deadline-ms': str(deadline)}
    require(all(headers.get(key) == value for key, value in expected.items())
            and isinstance(headers.get('x-tide-request-id'), str) and headers['x-tide-request-id'],
            'Tide response class audit header mismatch')


def verify_quiescence(evidence, observation, job, state, manifest_sha):
    """Recheck the bounded observation using raw samples, independently of helper flags."""
    require(observation.get('criterion') == 'bounded observed quiescence'
            and observation.get('passed') is True and observation.get('minimum_samples') == 3
            and observation.get('minimum_observation_s') == 2 and observation.get('minimum_age_s') == 0
            and observation.get('last_window_errors') == [],
            'Invalid quiescence criterion/limits/pass marker')
    audit = observation['audit']
    require(audit['job'] == job and audit['manifest_sha256'] == manifest_sha
            and all(audit['state'][key] == state[key] for key in ('job', 'completed_ns', 'summary_sha256', 'pending_execution')),
            'Quiescence target identity mismatch')
    first = observation['accepted_first_sample']; samples = observation['samples']
    require(integer(first) and 0 <= first < len(samples), 'Invalid quiescence accepted sample index')
    window = samples[first:]
    require(len(window) >= 3
            and window[-1]['monotonic_ns'] - window[0]['finished_monotonic_ns'] >= 2_000_000_000,
            'Quiescence requires at least three samples spanning two seconds')
    require(integer(observation['verified_ns']) and observation['verified_ns'] > window[-1]['time_ns']
            and window[0]['time_ns'] > state['completed_ns'], 'Invalid quiescence post-completion timing')
    for before, after in zip(window, window[1:]):
        require(before['monotonic_ns'] <= before['finished_monotonic_ns'] <= after['monotonic_ns']
                and 0 < after['monotonic_ns'] - before['monotonic_ns'] <= 5_000_000_000,
                'Quiescence observations overlap or sampling gap exceeds five seconds')
    sources = observation['identity_sources']
    require(sources, 'Quiescence lacks hash-bound process identities')
    identities = {node: set() for node in NODES}

    def collect_ids(obj, target):
        if isinstance(obj, dict):
            ticks = obj.get('start_ticks', obj.get('process_start_ticks'))
            if integer(obj.get('pid')) and ticks is not None:
                target.add((obj['pid'], str(ticks)))
            for value in obj.values():
                collect_ids(value, target)
        elif isinstance(obj, list):
            for value in obj:
                collect_ids(value, target)

    for source in sources:
        obj = evidence.read(source['path'], source['sha256'])
        for node in NODES:
            if node in Path(source['path']).parts or '-' + node in source['path'] or node + '-' in source['path']:
                collect_ids(obj, identities[node])
    baseline = {}; replica_baseline = None
    gauges = ('ray_serve_deployment_queued_queries', 'ray_serve_num_ongoing_requests_at_replicas',
              'ray_serve_replica_processing_queries')
    for sample in window:
        require(set(sample['nodes']) == set(NODES), 'Quiescence missing a node sample')
        require(integer(sample['monotonic_ns']) and integer(sample['finished_monotonic_ns'])
                and sample['monotonic_ns'] <= sample['finished_monotonic_ns']
                and state['completed_ns'] < sample['time_ns'] < observation['verified_ns'], 'Invalid quiescence sample timestamps')
        replicas = set()
        for node in NODES:
            row = sample['nodes'][node]
            require(not row.get('error') and row.get('locks_available') is True
                    and all(row.get(field) == [] for field in ('identity_errors', 'active_load', 'nonterminal_states',
                                                              'missing_target_states', 'sockets')),
                    'Quiescence probe reports activity/error/unavailable lock')
            workers = row['workers']; expected_count = 2 if node == 'nj1' else 1
            worker_ids = [(worker['pid'], str(worker['start_ticks'])) for worker in workers]
            require(len(workers) == len(set(worker_ids)) == expected_count
                    and set(worker_ids).issubset(identities[node]), 'Quiescence worker inventory/identity mismatch')
            for worker in workers:
                require(worker.get('threads') and all(thread['state'] in ('S', 'I')
                        for thread in worker['threads']), 'Quiescence worker thread active/blocked/dead')
            counters = {identity: (worker['cpu_ticks'], worker['io']) for identity, worker in zip(worker_ids, workers)}
            require(node not in baseline or baseline[node] == counters, 'Quiescence worker identities/CPU/IO changed')
            baseline.setdefault(node, counters)
            if job['system'] == 'ray':
                values = [metric for metric in row.get('metrics', [])
                          if metric['labels'].get('application') == 'tmc-fig6-ray'
                          and metric['labels'].get('deployment') == 'MatchedGoKernel']
                required = gauges if node != 'nj1' else (gauges[2],)
                require(all(any(metric['name'] == name for metric in values) for name in required)
                        and all(finite(metric['value']) and metric['value'] == 0 for metric in values),
                        'Quiescence Ray queue/in-flight gauges nonzero/missing')
                ids = [metric['labels'].get('replica') for metric in values if metric['name'] == gauges[2]]
                require(None not in ids and len(ids) == len(set(ids)) == expected_count
                        and not replicas.intersection(ids), 'Quiescence Ray replica coverage mismatch')
                replicas.update(ids)
        if job['system'] == 'ray':
            require(replica_baseline is None or replica_baseline == replicas, 'Quiescence Ray replica identity changed')
            replica_baseline = replicas
    return {'samples': len(window), 'span_s': (window[-1]['monotonic_ns'] - window[0]['finished_monotonic_ns']) / 1e9,
            'verified_ns': observation['verified_ns'], 'criterion': 'bounded observed quiescence'}


def recompute_class(pairs, deadline, expected, proven):
    counts = Counter({key: 0 for key in COUNTS}); samples = {key: [] for key in GROUPS}
    reasons = Counter(); destinations = Counter(); instances = Counter(); statuses = Counter()
    declared_errors = Counter(); failed_destinations = Counter(); origins = {}
    for meta, records in pairs:
        local = Counter({key: 0 for key in COUNTS}); start = meta['window_start_monotonic_ns']; stop = start + meta['duration_ns']
        local_reasons = Counter(); local_lag = []; local_destinations = Counter()
        for row in records:
            if row['deadline_ms'] != deadline:
                continue
            local['offered'] += 1
            planned, sent, returned = (row[key] for key in ('planned_submit_monotonic_ns', 'actual_submit_monotonic_ns', 'return_monotonic_ns'))
            lag_ms = (sent - planned) / 1e6
            local_lag.append(lag_ms); samples['replay_lateness_ms'].append(lag_ms)
            statuses[str(row.get('http_status'))] += 1
            if row.get('error'):
                declared_errors[str(row['error'])] += 1
            reason, body = response_outcome(row, expected)
            if reason:
                local['failed'] += 1; reasons[reason] += 1; local_reasons[reason] += 1
                failed_destinations[str(body.get('node', 'unknown')) if isinstance(body, dict) else 'unknown'] += 1
                continue
            node, instance = body.get('node'), body.get('worker_instance')
            require(node in NODES and instance in proven[meta['system']][node], 'Successful response worker identity not proven')
            require(integer(body.get('service_ns')) and body['service_ns'] > 0
                    and integer(body.get('worker_start_ns')) and integer(body.get('worker_end_ns'))
                    and body['worker_end_ns'] >= body['worker_start_ns'], 'Successful response lacks valid worker timestamps')
            timely = returned - sent <= deadline * 1_000_000
            inside = start <= returned < stop
            local.update(cohort_successful=1, cohort_on_time=int(timely), completed_in_window=int(inside), on_time_in_window=int(timely and inside))
            destinations[node] += 1; instances[instance] += 1; local_destinations[node] += 1
            for group, value in (('successful_latency_ms', returned - sent), ('successful_planned_to_return_ms', returned - planned), ('kernel_service_ms', body['service_ns'])):
                samples[group].append(value / 1e6)
        origins[meta['origin']] = {**dict(local), 'failure_reasons': dict(local_reasons), 'replay_lateness_ms': describe(local_lag), 'destinations': dict(local_destinations)}
        counts.update(local)
    offered, successful = counts['offered'], counts['cohort_successful']
    require(offered > 0 and successful + counts['failed'] == offered, 'Class count conservation failed')
    seconds = pairs[0][0]['duration_ns'] / 1e9
    metrics = {**dict(counts), 'throughput_rps': counts['completed_in_window'] / seconds,
               'goodput_rps': counts['on_time_in_window'] / seconds, 'offered_rps': offered / seconds,
               'cohort_yield': counts['cohort_on_time'] / offered, 'failure_fraction': counts['failed'] / offered,
               'successful_outside_window': successful - counts['completed_in_window'],
               'successful_deadline_misses': successful - counts['cohort_on_time']}
    for group, values in samples.items():
        metrics.update({f'{group}_{key}': value for key, value in describe(values).items()})
    for origin in ORIGINS:
        metrics.update({f'origin_{origin}_{key}': origins[origin][key] for key in COUNTS})
    metrics.update({f'destination_{node}_successes': destinations[node] for node in NODES})
    return metrics, {'origins': origins, 'failure_reasons': dict(reasons), 'destinations': dict(destinations),
                     'failed_destinations': dict(failed_destinations), 'worker_instances': dict(instances),
                     'http_statuses': dict(statuses), 'collector_error_labels': dict(declared_errors)}


def audit_job(evidence, plan, manifest_sha, traces, proven, job):
    job_id = job['id']; trace = traces[job['trace']]
    state = evidence.read(f'formal/state/{job_id}.json')
    require(state['status'] == 'completed' and state['job'] == job and state['manifest_sha256'] == manifest_sha, 'Unfinished/mismatched treatment state')
    pairs = []; risks = []; timing = {'collection_end_unix_ns': {}, 'remote_finished_ns': {}, 'drain_clearance_verified_ns': None}
    for origin in ORIGINS:
        prefix = f'{job_id}-{origin}'
        meta = evidence.read(f'formal/{prefix}.meta.json')
        require(meta['complete'] is True and meta['raw_file'] == prefix + '.jsonl', 'Missing origin commit marker/invalid raw path')
        rows = evidence.read('formal/' + meta['raw_file'], meta['raw_sha256'], jsonl=True)
        selected = sorted((r for r in trace['requests'] if r['origin'] == origin), key=lambda r: r['offset_ns'])
        requests = {r['id']: r for r in selected}; ids = [r['id'] for r in rows]
        require(len(ids) == len(set(ids)) == len(requests) and set(ids) == set(requests), 'Missing/duplicate/unoffered raw IDs')
        route, urls, assigned = routes(job['system'], origin, selected)
        payloads = {str(deadline): digest(canonical({**WORKLOAD, 'deadline_ms': deadline})) for deadline in CLASSES}
        require(meta.get('schema_version') == 2 and meta.get('mixed_protocol') == MIXED_PROTOCOL
                and meta['origin'] == origin and meta['system'] == job['system']
                and meta['route'] == route and meta['urls'] == urls and meta['workload'] == WORKLOAD
                and meta['trace_sha256'] == digest(canonical(trace)) and meta['expected_result'] == trace['expected_result']
                and meta['duration_ns'] == trace['duration_ns'] and meta['start_unix'] == state['start_unix']
                and meta['timeout_s'] == plan['timeout_s'] and meta['expected_ids'] == list(requests)
                and meta.get('qos_by_function_ms') == QOS and meta.get('payload_sha256_by_deadline_ms') == payloads,
                'Origin metadata differs from frozen trace/routing/class payload')
        require(integer(meta['window_start_monotonic_ns']) and integer(meta['collection_end_monotonic_ns'])
                and integer(meta['collection_end_unix_ns']) and integer(meta['clock_mapping_drift_ns'])
                and integer(meta['clock_sample']['bracket_ns']) and meta['clock_sample']['bracket_ns'] >= 0,
                'Invalid origin clock metadata')
        remote = state['remote_terminal'][origin]
        require(remote['status'] == 'completed' and remote['exit_code'] == 0 and remote['job'] == job_id
                and remote['origin'] == origin and remote['manifest_sha256'] == manifest_sha
                and integer(remote['finished_ns']) and remote['finished_ns'] >= meta['collection_end_unix_ns'], 'Uncommitted/mismatched remote terminal state')
        timing['collection_end_unix_ns'][origin] = meta['collection_end_unix_ns']; timing['remote_finished_ns'][origin] = remote['finished_ns']
        for row in rows:
            request = requests[row['id']]; deadline = request['deadline_ms']
            payload = {**WORKLOAD, 'deadline_ms': deadline}
            planned, sent, returned = (row[key] for key in ('planned_submit_monotonic_ns', 'actual_submit_monotonic_ns', 'return_monotonic_ns'))
            require(row['origin'] == origin and row['offset_ns'] == request['offset_ns']
                    and integer(row['deadline_ms']) and row['deadline_ms'] == deadline
                    and canonical(row['request']) == canonical(payload) and row['url'] == assigned[row['id']]
                    and integer(row.get('transmitted_deadline_ms')) and row['transmitted_deadline_ms'] == deadline
                    and row.get('expected_function') == f'cpu-qos{deadline}'
                    and row.get('request_sha256') == payloads[str(deadline)], 'Record differs from frozen offer/class body/route')
            require(all(integer(value) for value in (planned, sent, returned))
                    and planned == meta['window_start_monotonic_ns'] + request['offset_ns']
                    and planned <= sent <= returned <= meta['collection_end_monotonic_ns'], 'Invalid request/client timestamp ordering')
            if job['system'] == 'tide':
                check_tide_headers(row, deadline)
            status, error = row.get('http_status'), row.get('error')
            if error == 'timeout' or status == 408 or (integer(status) and status >= 500) or (status is None and error):
                risks.append({'origin': origin, 'id': row['id'], 'error': error, 'http_status': status})
        pairs.append((meta, rows))
    mandatory = {'reason': 'mandatory_mixed_campaign_quiescence'}
    declared = state['pending_execution']
    require(declared == [mandatory, *risks], 'Pending execution/quiescence accounting mismatch')
    quiescence = []
    if declared:
        clearance = evidence.read(f'formal/drain-clearance/{job_id}.json', state['drain_clearance_sha256'])
        require(state['readiness'] == 'drain_verified' and clearance['job'] == job_id and clearance['idle_verified'] is True
                and clearance['summary_sha256'] == state['summary_sha256']
                and clearance['verified_ns'] > state['completed_ns'] and clearance['evidence'], 'Unverified drain after treatment')
        for item in clearance['evidence']:
            observed = evidence.read(item['path'], item['sha256'])
            if isinstance(observed, dict) and observed.get('criterion') == 'bounded observed quiescence':
                quiescence.append(verify_quiescence(evidence, observed, job, state, manifest_sha))
        require(quiescence and max(item['verified_ns'] for item in quiescence) == clearance['verified_ns'],
                'Clearance lacks valid raw bounded quiescence observation')
        timing['drain_clearance_verified_ns'] = clearance['verified_ns']
    rows = []; details = {}
    for deadline in CLASSES:
        metrics, detail = recompute_class(pairs, deadline, trace['expected_result'], proven)
        require(metrics['offered_rps'] == job['rate_rps'] / 2, 'Offered class rate is not half total')
        row = {key: job[key] for key in BASE if key in job}
        row.update(job_id=job_id, deadline_ms=deadline, function=f'cpu-qos{deadline}', load_factor=FACTORS[job['level']],
                   trace_sha256=digest(canonical(trace)), start_unix=state['start_unix'], duration_s=10, **metrics)
        rows.append(row); details[str(deadline)] = detail
    details['timing'] = timing
    details['quiescence'] = quiescence
    details['clock'] = {meta['origin']: {'mapping_drift_ns': meta['clock_mapping_drift_ns'], 'bracket_ns': meta['clock_sample']['bracket_ns']} for meta, _ in pairs}
    return rows, details


def verify_sequence(plan, details, starts):
    drain_ns = round(plan['inter_treatment_drain_s'] * 1e9)
    require(drain_ns >= 1_000_000_000, 'Insufficient nominal drain margin')
    transitions = []
    for previous, following in zip(plan['jobs'], plan['jobs'][1:]):
        timing = details[previous['id']]['timing']
        latest = max(*timing['collection_end_unix_ns'].values(), *timing['remote_finished_ns'].values(),
                     timing['drain_clearance_verified_ns'] or 0, round((starts[previous['id']] + 10) * 1e9))
        following_ns = round(starts[following['id']] * 1e9)
        require(following_ns >= latest + drain_ns, 'Treatments overlap collection/drain evidence: ' + following['id'])
        transitions.append({'previous': previous['id'], 'following': following['id'], 'latest_prior_end_ns': latest,
                            'following_start_ns': following_ns, 'nominal_gap_s': (following_ns - latest) / 1e9})
    return transitions


def publish(root, output, source_dir=Path(__file__).parent):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(not output.exists(), 'Output already exists; choose a new directory')
    evidence = Evidence(root)
    plan, manifest_sha, traces = frozen_plan(evidence, source_dir)
    expected_meta = {f"{job['id']}-{origin}.meta.json" for job in plan['jobs'] for origin in ORIGINS}
    formal = root / 'formal'
    require({p.name for p in formal.glob('*.meta.json')} == expected_meta, 'Incomplete/unexpected origin commit inventory (120 required)')
    require({p.name for p in formal.glob('*.jsonl')} == {name.replace('.meta.json', '.jsonl') for name in expected_meta}, 'Missing/unplanned raw artifacts')
    require(not list(formal.rglob('*.partial')), 'Uncommitted partial artifacts present')
    proven = proven_instances(evidence, plan, manifest_sha)
    rows, details, starts = [], {}, {}
    for job in plan['jobs']:
        class_rows, detail = audit_job(evidence, plan, manifest_sha, traces, proven, job)
        rows.extend(class_rows); details[job['id']] = detail; starts[job['id']] = class_rows[0]['start_unix']
    transitions = verify_sequence(plan, details, starts)
    groups = defaultdict(list)
    for row in rows:
        groups[(row['level'], row['system'], row['deadline_ms'])].append(row)
    require(len(rows) == 120 and len(groups) == 24, 'Unexpected class export cardinality')
    cells = []
    for key, group in sorted(groups.items()):
        require(len(group) == 5 and {row['repeat'] for row in group} == set(range(1, 6)), 'Cell lacks five paired repeats')
        first = group[0]; cell = {key: first[key] for key in CELL_BASE if key != 'n'}; cell['n'] = 5
        for metric in METRICS:
            cell.update({f'{metric}_{key}': value for key, value in ci5([row[metric] for row in group]).items()})
        cells.append(cell)
    for relative, wanted in evidence.hashes.items():
        require(digest((root / relative).read_bytes()) == wanted, 'Evidence changed during audit: ' + relative)
    audit = {'status': 'PASS', 'test_only': plan.get('test_only') is True, 'campaign': plan['campaign'],
             'manifest_sha256': manifest_sha, 'created_ns': time.time_ns(), 'treatments': 60, 'origin_runs': 120,
             'class_run_rows': 120, 'cells': 24, 'repetitions': 5,
             'offered_total': sum(row['offered'] for row in rows), 'failed_total': sum(row['failed'] for row in rows),
             'cpu_reference': cpu_reference(), 'proven_instances': proven, 'job_details': details,
             'evidence_sha256': evidence.hashes, 'source_sha256': plan['source_sha256'],
             'auditor_sha256': digest(Path(__file__).read_bytes()), 'sequential_transitions': transitions,
             'runs_columns': list(BASE + METRICS), 'cells_columns': list(CELL_COLUMNS),
             'inference': {'unit': 'five treatment-level class metrics, each combining both origins', 'confidence': .95,
                           'distribution': 'Student t', 'df': 4, 'critical_value': T4,
                           'missing': 'Any missing run metric leaves mean and CI blank; metric_n records available runs',
                           'bounds': 'unclipped symmetric intervals'},
             'definitions': {'goodput_rps': 'checksum-valid returns with inclusive submission-to-return deadline AND return in fixed half-open 10 s window, divided by 10 s',
                             'cohort_yield': 'all checksum-valid on-time returns, including after window, divided by all frozen class offers including failures',
                             'offered_rps': 'all frozen class offers / 10 s; exactly half the total offered rate',
                             'latency': 'origin-local actual-submit to return monotonic time; replay lag retained separately',
                             'destination_denominator': 'checksum-valid successful returns; failures retained separately',
                             'outcomes': 'recomputed from raw HTTP status and body, not collector outcome labels'}}
    staging = output.with_name(output.name + f'.partial-{time.time_ns()}')
    staging.mkdir(parents=True)
    for name, columns, data in (('runs.csv', BASE + METRICS, rows), ('cells.csv', CELL_COLUMNS, cells)):
        with (staging / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader(); writer.writerows(data)
    readme = ('TEST ONLY — synthetic data; never publication evidence.\n\n' if audit['test_only'] else '')
    readme += ('Independent raw audit: 60 treatments, 120 origin commits, 120 class run rows, 24 class cells.\n\n'
               'Each run row combines both origins for one treatment and one deadline class. Every request is in its denominator exactly once, including HTTP errors, invalid checksums and timeouts. The CPU result is independently recomputed with hashlib. Collector outcome labels and summaries are not metric inputs.\n\n'
               'Goodput = checksum-valid returns meeting the inclusive actual-submit-to-return deadline AND returning inside each origin’s fixed half-open [start,start+10s) window, divided by 10 seconds. Throughput drops the deadline condition. Cohort yield = all checksum-valid on-time returns / all class offers, including returns after the window. Offered class rate = half total rate. A late submission is retained with its scheduled-to-submit lag.\n\n'
               'Cells contain five run-level metrics. Mean, sample SD, SE and unclipped symmetric 95% Student t intervals use df=4, t=2.7764451051977987. Percentiles are nearest-rank per run and then averaged across five runs; requests are never pooled for inference. If any replicate lacks a latency metric, its mean and CI are blank and metric_n records availability.\n\n'
               'audit.json preserves per-origin class counts, replay lag, failures, destinations, worker identities, clock diagnostics, artifact hashes and treatment separation. Client latency uses only local monotonic clocks. Wall clocks support campaign ordering; they do not estimate latency or queue time.\n')
    (staging / 'README.md').write_text(readme)
    audit['export_sha256'] = {name: digest((staging / name).read_bytes()) for name in ('runs.csv', 'cells.csv', 'README.md')}
    (staging / 'audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
    staging.rename(output)
    return {'status': 'PASS', 'test_only': audit['test_only'], 'output': str(output), 'treatments': 60, 'origin_runs': 120, 'class_run_rows': 120, 'cells': 24}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--source-dir', type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    try:
        result = publish(args.root, args.output, args.source_dir)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'status': 'NOT_PUBLISHED', 'error': f'{type(error).__name__}: {error}'}), file=sys.stderr)
        return 1
    print(json.dumps(result)); return 0


if __name__ == '__main__':
    sys.exit(main())
