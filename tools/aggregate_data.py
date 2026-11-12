#!/usr/bin/env python3
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/compact/current'


def read_csv(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', newline='') as stream:
        return list(csv.DictReader(stream))


def true(value):
    return str(value).lower() in {'true', '1'}


def close(actual, expected, tolerance=1e-6):
    if not math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance):
        raise ValueError(f'Numerical mismatch: {actual} versus {expected}')


def native_weights():
    runs = read_csv(DATA / 'native_weight_study/runs.csv')
    requests = read_csv(DATA / 'native_weight_study/requests.csv.gz')
    counts = defaultdict(Counter)
    ids = set()
    for row in requests:
        key = (row['run_key'], row['request_id'])
        if key in ids:
            raise ValueError('Duplicate weight-study request')
        ids.add(key)
        counts[row['run_key']]['offered'] += 1
        counts[row['run_key']]['on_time'] += true(row['on_time'])
        counts[row['run_key']]['success'] += row['status'] == 'success'
    assert len(requests) == 210000 and len(runs) == 180
    cells = defaultdict(list)
    for row in runs:
        observed = counts[row['run_key']]
        assert observed['offered'] == int(row['submitted'])
        assert observed['on_time'] == int(row['on_time'])
        assert observed['success'] == int(row['success'])
        if true(row['retained']):
            cells[row['layout'], int(row['rate']), row['pattern'], row['weight']].append(
                100 * observed['on_time'] / observed['offered'])
    close(statistics.mean(cells['edge', 1000, 'burst', 'latency']), 80.075)
    result = []
    groups = [('NJ only', 50, 'steady & burst'), ('NJ only', 700, 'steady'),
              ('NJ only', 700, 'burst'), ('NJ only', 1000, 'steady'),
              ('NJ only', 1000, 'burst'), ('NJ-BJ', 'all', 'steady & burst')]
    for placement, rate, pattern in groups:
        output = {'placement': placement, 'rate_rps': rate, 'pattern': pattern}
        for weight, name in [('equal', 'equal_pct'), ('latency', 'calibrated_pct'),
                             ('neighbor', 'neighbor_pct')]:
            selected = [x for (layout, r, p, w), values in cells.items()
                        if layout == ('edge' if placement == 'NJ only' else 'edge-cloud')
                        and (rate == 'all' or r == rate)
                        and (pattern == 'steady & burst' or p == pattern)
                        and w == weight for x in values]
            output[name] = statistics.mean(selected)
            output[name.replace('_pct', '_runs')] = len(selected)
        result.append(output)
    return result


def native_ablation():
    runs = read_csv(DATA / 'native_pytorch/per_run.csv')
    requests = read_csv(DATA / 'native_pytorch/requests.csv.gz')
    counts = defaultdict(Counter)
    latencies = defaultdict(list)
    ids = set()
    for row in requests:
        key = row['cell'], int(row['rps']), int(row['block'])
        identity = key + (row['id'],)
        if identity in ids:
            raise ValueError('Duplicate ablation request')
        ids.add(identity)
        counts[key]['offered'] += 1
        if row['outcome'] == 'valid_return':
            counts[key]['valid'] += 1
            latencies[key].append(float(row['latency_ms']))
    assert len(runs) == 180 and len(requests) == 37800
    assert sum(x['valid'] for x in counts.values()) == 20814
    for run in runs:
        key = run['cell'], int(run['rps']), int(run['block'])
        assert counts[key]['offered'] == int(run['offered'])
        assert counts[key]['valid'] == int(run['valid'])
        close(int(run['valid']) / float(run['observation_s']), run['throughput_rps'])
        if latencies[key]:
            close(statistics.mean(latencies[key]), run['mean_ms'])
    return {'runs': len(runs), 'offered': len(requests), 'valid': 20814}


def wan():
    runs = read_csv(DATA / 'wan/runs.csv')
    requests = read_csv(DATA / 'wan/requests.csv.gz')
    counts = defaultdict(Counter)
    for row in requests:
        counts[row['condition'], row['arm']]['offered'] += 1
        counts[row['condition'], row['arm']]['success'] += row['status'] == 'success'
        counts[row['condition'], row['arm']]['on_time'] += true(row['on_time'])
    assert len(requests) == 4000 and len(runs) == 4
    for row in runs:
        c = counts[row['condition'], row['arm']]
        for field in ['offered', 'success', 'on_time']:
            assert c[field] == int(row[field])
        close(c['on_time'] / (float(row['observation_window_ms']) / 1000), row['goodput_rps'])
    return runs


def admission():
    rows = read_csv(DATA / 'admission_offline/results.csv')
    milp = read_csv(DATA / 'admission_offline/milp_results.csv')
    assert len(rows) == len(milp) == 120
    for row in milp:
        assert int(row['saved_exact_accepted']) == int(row['milp_accepted'])
        assert true(row['success']) and float(row['mip_gap']) == 0
    ratios = [int(r['heuristic_accepted']) / int(r['optimum_accepted']) for r in rows]
    close(statistics.mean(ratios), .9153688672438672)
    return {'instances': 120, 'mean_ratio': statistics.mean(ratios),
            'median_ratio': statistics.median(ratios), 'minimum_ratio': min(ratios),
            'exact_matches': sum(r == 1 for r in ratios), 'milp_matches_bb': 120}


def calibration():
    rows = read_csv(DATA / 'score_calibration/heldout_summary.csv')
    result = []
    for name, weights in [('Equal', (1/3, 1/3, 1/3)), ('Calibrated', (0, .9, .1)),
                          ('wu_ge_0.1', (.1, .9, 0))]:
        row, = [r for r in rows if all(math.isclose(float(r[k]), w)
                 for k, w in zip(['w_u', 'w_l', 'w_d'], weights))]
        result.append({'setting': name, 'goodput_pct': 100 * float(row['GoodputNorm']),
                       'outage_yield_pct': 100 * float(row['Resilience']),
                       'occupancy_pct': 100 * float(row['Util']),
                       'mean_response_ms': float(row['MeanLatencyMS'])})
    close(result[1]['goodput_pct'], 87.1283942381787)
    return result


def paix():
    rows = read_csv(DATA / 'paix_ipc/runs.csv')
    exchanges = read_csv(DATA / 'paix_ipc/exchanges.csv.gz')
    assert len(rows) == 3 and len(exchanges) == 9000
    result = {'runs': 3, 'measured_exchanges': len(exchanges),
              'mean_rtt_us': statistics.mean(float(r['mean_rtt_us']) for r in rows),
              'wall_rate_rps': statistics.mean(float(r['wall_rate_rps']) for r in rows)}
    close(result['mean_rtt_us'], 70.67421811111112)
    close(result['wall_rate_rps'], 13030.750619333334)
    return result


def paired_run_checks():
    capacity = read_csv(DATA / 'capacity_sweep/runs.csv')
    cells = read_csv(DATA / 'capacity_sweep/cells_goodput.csv')
    groups = defaultdict(list)
    for row in capacity:
        close(float(row['on_time_in_window']) / float(row['duration_s']), row['goodput_rps'])
        groups[tuple(row[k] for k in ['kind', 'level', 'pattern', 'system'])].append(float(row['goodput_rps']))
    assert len(capacity) == 1800 and len(cells) == 360
    for row in cells:
        values = groups[tuple(row[k] for k in ['kind', 'level', 'pattern', 'system'])]
        assert len(values) == 5
        close(statistics.mean(values), row['mean'])
        close(2.7764451051977987 * statistics.stdev(values) / math.sqrt(5), row['ci95_halfwidth'])
    mixed = read_csv(DATA / 'mixed_qos/runs.csv')
    mixed_cells = read_csv(DATA / 'mixed_qos/cells.csv')
    groups = defaultdict(list)
    for row in mixed:
        groups[tuple(row[k] for k in ['level', 'system', 'deadline_ms'])].append(float(row['goodput_rps']))
    assert sum(int(r['offered']) for r in mixed) == 177600
    for row in mixed_cells:
        values = groups[tuple(row[k] for k in ['level', 'system', 'deadline_ms'])]
        assert len(values) == 5
        close(statistics.mean(values), row['goodput_rps_mean'])
        close(statistics.mean(values) - 2.7764451051977987 * statistics.stdev(values) / math.sqrt(5), row['goodput_rps_ci95_low'])
    footprint = read_csv(DATA / 'control_cost/runs.csv')
    totals = defaultdict(float)
    for row in footprint:
        if row['system'] == 'tide' and row['rps'] == '80':
            totals[row['repeat']] += float(row['cpu_pct'])
    close(statistics.mean(totals.values()), 16, .001)
    assert len(read_csv(DATA / 'ttl_retention/runs.csv')) == 9
    return {'capacity_treatments': len(capacity), 'capacity_cells': len(cells),
            'mixed_treatments': 60, 'mixed_offers': 177600, 'role_rows': len(footprint)}


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output=None):
    tables = {'table_ii': calibration(), 'table_iii': native_weights(),
              'paix': [paix()], 'admission': [admission()], 'wan_runs': wan()}
    result = {'native_ablation': native_ablation(), 'paired_checks': paired_run_checks(), 'table_families': sorted(tables)}
    if output is not None:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        for name, rows in tables.items():
            write_csv(output / (name + '.csv'), rows)
            (output / (name + '.json')).write_text(json.dumps(rows, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))
