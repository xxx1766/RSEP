#!/usr/bin/env python3
"""Independent replay of every Go calibration decision and selection."""
import argparse
import csv
import gzip
import hashlib
import itertools
import json
import math
import statistics
from pathlib import Path


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def close(a, b, label):
    check(math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10), f'{label}: {a} != {b}')


def reward(m, lam):
    return sum(x*y for x, y in zip(lam, [m['GoodputNorm'], m['Resilience'], m['Util'], -m['LatencyCost']])) / sum(lam)


def verify_run(tr, nodes, weights, raw):
    actual_end = [0]*len(nodes)
    prediction = [0]*len(nodes)
    count = dict(Arrivals=len(tr['Requests']), Accepted=0, Rejected=0, Lost=0, Late=0,
                 OnTime=0, WindowOnTime=0, DrainedOnTime=0, OutageArrivals=0, OutageOnTime=0)
    cpu = mem = 0
    latencies, ratios = [], []
    for req, row in itertools.zip_longest(tr['Requests'], raw):
        check(req is not None and row is not None, 'request count mismatch')
        check(int(row['request_id']) == req['ID'], 'request identity/order')
        a = req['Arrival']
        outage = tr['OutStart'] <= a < tr['OutEnd']
        count['OutageArrivals'] += outage
        feasible = []
        for j, node in enumerate(nodes):
            if (not node['Local'] and outage) or req['Memory'] > node['Memory'] or node['Quota'] > node['CPU']:
                continue
            if actual_end[j] <= a:
                prediction[j] = a
            pred = max(a, prediction[j])+req['Estimate'][j]+node['Network']
            if pred > req['Deadline']:
                continue
            terms = [1-node['Quota']/node['CPU'], 1/(1+(pred-a)*.1/100), node['Device']]
            score = sum(w*x for w, x in zip(weights, terms))/sum(weights)
            feasible.append((score, -j, pred))
        chosen = max(feasible) if feasible else None
        j = -chosen[1] if chosen else -1
        check(int(row['node']) == j, f'wrong candidate at {req["ID"]}')
        if chosen is None:
            count['Rejected'] += 1
            check(row['status'] == 'rejected', 'rejection status')
            for key in ['predicted_response_ticks', 'start_ticks', 'finish_ticks', 'response_ticks', 'score']:
                close(float(row[key]), -1, key)
            continue
        count['Accepted'] += 1
        node = nodes[j]
        start = max(a, actual_end[j])
        finish = start+req['Actual'][j]
        response = finish+node['Network']
        prediction[j] = chosen[2]-node['Network']
        actual_end[j] = finish
        for key, expected in [('predicted_response_ticks', chosen[2]), ('start_ticks', start),
                              ('finish_ticks', finish), ('response_ticks', response), ('score', chosen[0])]:
            close(float(row[key]), expected, key)
        # Keep work after loss; resource-time integral excludes post-window drain.
        occupied = max(0, min(finish, tr['Duration'])-start)
        cpu += occupied*node['Quota']
        mem += occupied*req['Memory']
        lost = not node['Local'] and a < tr['OutEnd'] and response >= tr['OutStart']
        if lost:
            status = 'outage_lost'
            count['Lost'] += 1
        else:
            latencies.append((response-a)*.1)
            ratios.append((response-a)/(req['Deadline']-a))
            if response <= req['Deadline']:
                status = 'on_time'
                count['OnTime'] += 1
                count['WindowOnTime' if response < tr['Duration'] else 'DrainedOnTime'] += 1
                count['OutageOnTime'] += outage
            else:
                status = 'late'
                count['Late'] += 1
        check(row['status'] == status, 'incorrect returned outcome')
    check(count['Arrivals'] == count['Rejected']+count['Lost']+count['Late']+count['OnTime'], 'outcome conservation')
    count['GoodputRPS'] = count['WindowOnTime']/(tr['Duration']*.0001)
    count['GoodputNorm'] = count['WindowOnTime']/count['Arrivals'] if count['Arrivals'] else 0
    count['Resilience'] = count['OutageOnTime']/count['OutageArrivals'] if count['OutageArrivals'] else 0
    count['CPUUtil'] = cpu/(tr['Duration']*sum(n['CPU'] for n in nodes))
    count['MemoryUtil'] = mem/(tr['Duration']*sum(n['Memory'] for n in nodes))
    count['Util'] = (count['CPUUtil']+count['MemoryUtil'])/2
    count['MeanLatencyMS'] = statistics.mean(latencies) if latencies else 0
    count['P95LatencyMS'] = sorted(latencies)[math.ceil(.95*len(latencies))-1] if latencies else 0
    count['LatencyCost'] = min(1, statistics.mean(ratios)) if ratios else 1
    return count


def verify(root, module):
    manifest = json.loads((root/'manifest.json').read_text())
    for group, base in [('artifact_sha256', root), ('source_sha256', module)]:
        for name, expected in manifest[group].items():
            check(hashlib.sha256((base/name).read_bytes()).hexdigest() == expected, f'hash mismatch {name}')
    data = json.loads((root/'inputs.json').read_text())
    traces = {t['ID']: t for t in data['Traces']}
    grid = data['Grid']
    check(len(traces) == len(data['Traces']) == 36 and len(grid) == 68, 'input matrix size')
    seeds = {split: {t['Seed'] for t in traces.values() if t['Split'] == split} for split in ['calibration', 'test']}
    check(len(seeds['calibration']) == len(seeds['test']) == 18 and not seeds['calibration'] & seeds['test'], 'seed separation')
    check(len({tuple(w) for w in grid}) == 68, 'unique grid')
    for w in grid:
        close(sum(w), 1, 'weight normalization')
        check(all(x >= 0 for x in w), 'negative weight')
    for t in traces.values():
        check(t['Duration'] == 300000 and t['OutStart'] == 100000 and t['OutEnd'] == 200000, 'phase windows')
        check([r['ID'] for r in t['Requests']] == list(range(len(t['Requests']))), 'request ids')
        check([r['Arrival'] for r in t['Requests']] == sorted(r['Arrival'] for r in t['Requests']), 'arrival order')
        for r in t['Requests']:
            check(r['Deadline']-r['Arrival'] == 2000 and 0 <= r['Arrival'] < t['Duration'], 'arrival/deadline')
            for predicted, actual in zip(r['Estimate'], r['Actual']):
                check(.8*predicted <= actual <= math.ceil(1.2*predicted), 'actual service error bounds')
    rows = [json.loads(s) for s in (root/'summary.jsonl').read_text().splitlines()]
    index = {r['ID']: r for r in rows}
    check(len(index) == len(rows) == 4896, 'run ids/count')
    seen, n_decisions = set(), 0
    with gzip.open(root/'decisions.csv.gz', 'rt') as source:
        for rid, raw in itertools.groupby(csv.DictReader(source), key=lambda x: x['run_id']):
            check(rid not in seen and rid in index, 'unknown/duplicate run')
            seen.add(rid)
            run = index[rid]; tr = traces[run['TraceID']]
            check(run['Split'] == tr['Split'], 'split label')
            metrics = verify_run(tr, data['Nodes'][run['Network']], grid[run['WeightID']], raw)
            for key, val in metrics.items():
                close(run['Metrics'][key], val, rid+' '+key)
            n_decisions += metrics['Arrivals']
            if len(seen) % 1000 == 0:
                print(f'verified {len(seen)}/4896 runs', flush=True)
    check(seen == set(index) and n_decisions == manifest['decision_count'], 'raw completeness')
    grouped = {(split, wi): [r for r in rows if r['Split'] == split and r['WeightID'] == wi]
               for split in seeds for wi in range(68)}
    for rs in grouped.values():
        check(len(rs) == 36, 'balanced scenario count')
        check(len({(r['TraceID'], r['Network']) for r in rs}) == 36, 'duplicate scenario')
    def avg(split, wi, lam):
        return statistics.mean(reward(r['Metrics'], lam) for r in grouped[split, wi])
    def selected(lam):
        scores = [avg('calibration', wi, lam) for wi in range(68)]
        best = max(scores)
        tied = [wi for wi, x in enumerate(scores) if best-x <= 1e-12]
        return min(tied, key=lambda wi: (sum((x-1/3)**2 for x in grid[wi]), wi))
    choices = json.loads((root/'selection.json').read_text())
    check(len(choices) == len(data['Preferences']) == 6, 'preference coverage')
    for s, p in zip(choices, data['Preferences']):
        check(s['Name'] == p['Name'] and s['Lambda'] == p['Lambda'], 'preference changed')
        check(s['WeightID'] == selected(s['Lambda']) and s['Weights'] == grid[s['WeightID']], 'training-only winner')
        for key, val in [('CalibrationReward', avg('calibration', s['WeightID'], s['Lambda'])),
                         ('TestReward', avg('test', s['WeightID'], s['Lambda'])),
                         ('DefaultTestReward', avg('test', 66, s['Lambda'])),
                         ('BestGridTestReward', max(avg('test', wi, s['Lambda']) for wi in range(68)))]:
            close(s[key], val, key)
    perturb = json.loads((root/'lambda_sensitivity.json').read_text())
    check(len(perturb) == 48, 'lambda perturbation coverage')
    for p in perturb:
        s = p['Selection']
        base = next(x for x in choices if x['Name'] == p['Base'])['Lambda'].copy()
        base[p['Coordinate']] *= p['Factor']
        check(base == s['Lambda'] and s['WeightID'] == selected(base), 'perturbation selection')
        close(s['CalibrationReward'], avg('calibration', s['WeightID'], base), 'perturb train')
        close(s['TestReward'], avg('test', s['WeightID'], base), 'perturb test')
        close(s['DefaultTestReward'], avg('test', 66, base), 'perturb default')
        close(s['BestGridTestReward'], max(avg('test', wi, base) for wi in range(68)), 'perturb grid optimum')
    result = dict(verified_runs=len(seen), verified_decisions=n_decisions, verified_traces=len(traces),
                  independent_replay=True, training_only_selection=True, hashes_match=True,
                  verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (root/'verification.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    verify(args.root, Path(__file__).resolve().parents[2])
