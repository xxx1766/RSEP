#!/usr/bin/env python3
"""Independent MILP corroboration of model-01 (requires numpy and scipy).

Run from the Tide module directory:
  python3 scripts/revision/model_verify_milp.py

This script does not import the Go heuristic or branch-and-bound solver. It
models binary x[i,j], sum_j x[i,j] <= 1, and per-node service capacity
sum_i service[i,j]*x[i,j] <= max(0, deadline-network[j]), maximizing sum(x).
It records a supplemental verification artifact; original evidence is unchanged.
This verifies the restricted simulated instances, not general Problem 1.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import platform
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp


def require(condition, message):
    # Explicit exceptions also enforce validation under python -O.
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundled_highs_version():
    # SciPy has moved its bundled binding; version lookup is metadata only.
    for name in ('scipy.optimize._highspy._core', 'scipy.optimize._highs._highs_constants'):
        try:
            core = importlib.import_module(name)
            return '.'.join(str(getattr(core, f'HIGHS_VERSION_{part}'))
                            for part in ('MAJOR', 'MINOR', 'PATCH'))
        except (ImportError, AttributeError):
            continue
    return 'unavailable via this SciPy build; see recorded scipy_version'


def verify(root, directory):
    names = ('instances.json', 'results.json', 'manifest.json', 'calibration.json',
             'summary.json', 'results.csv')
    hashes = {name: digest(directory / name) for name in names}
    rows = json.loads((directory / 'results.json').read_text())
    instances = json.loads((directory / 'instances.json').read_text())
    manifest = json.loads((directory / 'manifest.json').read_text())
    calibration = json.loads((directory / 'calibration.json').read_text())
    summaries = json.loads((directory / 'summary.json').read_text())
    with (directory / 'results.csv').open(newline='') as f:
        csv_rows = list(csv.DictReader(f))
    require(len(rows) == len(instances) == len(csv_rows) == manifest['instance_count'] == 120,
            'expected the complete saved 120-instance model-01 design')
    require(instances == [row['instance'] for row in rows], 'instances/results differ')
    require(len({instance['id'] for instance in instances}) == 120, 'duplicate instance ID')
    expected_design = Counter({(n, m, p): 5 for n in (6, 8, 10, 12)
                               for m in (2, 3) for p in (.55, .75, .95)})
    actual_design = Counter((len(i['tasks_in_arrival_order']), len(i['nodes']), i['horizon_multiplier'])
                            for i in instances)
    require(actual_design == expected_design, 'incomplete or different experiment design')
    for name, expected in manifest['source_sha256'].items():
        require(digest(root / name) == expected, f'source hash mismatch: {name}')
    original_path = root / manifest['calibration_path']
    require(digest(original_path) == manifest['calibration_sha256'], 'original calibration hash mismatch')
    original = json.loads(original_path.read_text())
    require(all(calibration[k]['service_p95_ms'] == original[k]['service_p95_ms']
                for k in ('cpu', 'memory', 'io')), 'copied p95 calibration mismatch')

    checks = []
    for row, csv_row in zip(rows, csv_rows):
        instance = row['instance']
        ident = instance['id']
        tasks, nodes = instance['tasks_in_arrival_order'], instance['nodes']
        n, m = len(tasks), len(nodes)
        deadline = instance['common_deadline_ticks']
        require(csv_row['instance'] == ident, f'{ident}: CSV order/identity mismatch')
        for column, method in (('heuristic_accepted', 'heuristic'), ('optimum_accepted', 'exact')):
            require(int(csv_row[column]) == row[method]['accepted'], f'{ident}: CSV {column} mismatch')
        for key in ('objective_gap', 'heuristic_compute_ns', 'exact_compute_ns'):
            require(int(csv_row[key]) == row[key], f'{ident}: CSV {key} mismatch')
        require(float(csv_row['heuristic_over_optimum']) == row['heuristic_over_optimum'],
                f'{ident}: CSV ratio mismatch')
        baseline = []
        for task in tasks:
            kind = ('cpu', 'memory', 'io').index(task['kind'])
            base = calibration[task['kind']]['service_p95_ms'] * task['size_multiplier']
            baseline.append(base)
            expected = [math.ceil(base * node['service_factors_cpu_memory_io'][kind] / instance['tick_ms'])
                        for node in nodes]
            require(task['service_ticks_by_node'] == expected, f'{ident}: service formula mismatch')
        require(deadline == math.ceil(instance['horizon_multiplier'] * sum(baseline) / m / instance['tick_ms']),
                f'{ident}: deadline formula mismatch')
        for method in ('heuristic', 'exact'):
            solution = row[method]
            assignment = solution['node_index_by_task_minus_one_rejected']
            require(len(assignment) == n and all(isinstance(j, int) and -1 <= j < m for j in assignment),
                    f'{ident}: invalid {method} assignment')
            loads = [sum(tasks[i]['service_ticks_by_node'][j]
                         for i, chosen in enumerate(assignment) if chosen == j) for j in range(m)]
            require(sum(j >= 0 for j in assignment) == solution['accepted'], f'{ident}: certificate count mismatch')
            require(all(load == 0 or load + nodes[j]['network_ticks'] <= deadline for j, load in enumerate(loads)),
                    f'{ident}: infeasible {method} certificate')
        saved = row['exact']['accepted']
        require(row['exact']['proven_optimal'], f'{ident}: saved optimum is not certified')
        require(row['objective_gap'] == saved - row['heuristic']['accepted'] >= 0, f'{ident}: invalid saved gap')
        ratio = row['heuristic']['accepted'] / saved if saved else 1.0
        require(abs(row['heuristic_over_optimum'] - ratio) < 1e-12, f'{ident}: invalid ratio')

        # Independent formulation: constraints are constructed from raw inputs.
        matrix = np.zeros((n + m, n * m))
        for i, task in enumerate(tasks):
            for j in range(m):
                matrix[i, i * m + j] = 1
                matrix[n + j, i * m + j] = task['service_ticks_by_node'][j]
        capacity = [max(0, deadline - node['network_ticks']) for node in nodes]
        upper = np.array([1] * n + capacity)
        result = milp(-np.ones(n * m), integrality=np.ones(n * m), bounds=Bounds(0, 1),
                      constraints=LinearConstraint(matrix, -np.inf, upper),
                      options={'mip_rel_gap': 0.0})
        require(result.success and result.status == 0 and result.mip_gap == 0,
                f'{ident}: MILP did not prove zero-gap optimum: {result.message}')
        require(abs(-result.fun - saved) < 1e-7, f'{ident}: independent optimum differs from saved objective')
        rounded = np.rint(result.x).astype(int)
        require(np.max(np.abs(result.x - rounded)) < 1e-7, f'{ident}: nonintegral solution')
        require(np.all((rounded == 0) | (rounded == 1)) and np.all(matrix @ rounded <= upper),
                f'{ident}: infeasible independent certificate')
        assignment = [next((j for j in range(m) if rounded[i * m + j]), -1) for i in range(n)]
        require(sum(j >= 0 for j in assignment) == saved, f'{ident}: independent certificate objective mismatch')
        checks.append({
            'instance_id': ident, 'solver_status': int(result.status), 'solver_message': result.message,
            'success': bool(result.success), 'saved_exact_accepted': saved,
            'milp_accepted': int(round(-result.fun)), 'mip_gap': float(result.mip_gap),
            'maximization_dual_bound': float(-result.mip_dual_bound),
            'mip_node_count': int(result.mip_node_count),
            'assignment_node_index_minus_one_rejected': assignment,
        })

    require(len(summaries) == 8, 'expected eight summary groups')
    require({(s['tasks'], s['nodes']) for s in summaries} == {(n, m) for n in (6, 8, 10, 12) for m in (2, 3)},
            'summary group coverage mismatch')
    for summary in summaries:
        group = [r for r in rows if len(r['instance']['tasks_in_arrival_order']) == summary['tasks']
                 and len(r['instance']['nodes']) == summary['nodes']]
        require(len(group) == summary['instances'], 'summary count mismatch')
        for field, expected in (
            ('mean_heuristic_over_optimum', sum(r['heuristic_over_optimum'] for r in group) / len(group)),
            ('mean_objective_gap', sum(r['objective_gap'] for r in group) / len(group)),
            ('min_heuristic_over_optimum', min(r['heuristic_over_optimum'] for r in group)),
            ('max_objective_gap', max(r['objective_gap'] for r in group)),
            ('heuristic_matches_optimum', sum(r['objective_gap'] == 0 for r in group)),
        ):
            require(abs(summary[field] - expected) < 1e-12, f'summary {field} mismatch')
    require(hashes == {name: digest(directory / name) for name in names}, 'inputs changed during verification')
    return {
        'schema_version': 1, 'status': 'passed', 'verified_utc': datetime.now(timezone.utc).isoformat(),
        'evidence_type': 'independent MILP corroboration of the 120 saved restricted simulated instances',
        'scope': 'Model-01 only; no general Problem 1 optimum, approximation guarantee, or runtime measurement claim.',
        'python_version': platform.python_version(), 'numpy_version': np.__version__,
        'scipy_version': scipy.__version__, 'solver': 'HiGHS via scipy.optimize.milp',
        'highs_version': bundled_highs_version(), 'solver_options': {'mip_rel_gap': 0.0},
        'formulation': 'binary x[i,j]; maximize sum x; sum_j x[i,j]<=1; sum_i service[i,j]*x[i,j]<=max(0,H-network[j])',
        'validator_sha256': digest(Path(__file__)),
        'input_sha256': hashes, 'original_calibration_sha256': digest(original_path),
        'verified_source_sha256': manifest['source_sha256'],
        'instances_checked': len(checks), 'all_objectives_match': True, 'all_solver_gaps_zero': True,
        'artifact_consistency_checks': ['complete fixed design', 'unique IDs', 'instances equal result inputs',
            'CSV outcomes and timings', '240 saved assignment certificates', 'service and deadline formulas',
            'grouped summaries', 'source and calibration hashes'],
        'summary': {'heuristic_matches_optimum': sum(r['objective_gap'] == 0 for r in rows),
                    'mean_heuristic_over_optimum': sum(r['heuristic_over_optimum'] for r in rows) / len(rows),
                    'min_heuristic_over_optimum': min(r['heuristic_over_optimum'] for r in rows),
                    'max_objective_gap': max(r['objective_gap'] for r in rows)},
        'instances': checks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path('.'))
    parser.add_argument('--results', type=Path, default=Path('results-revision-20260928/model-01'))
    parser.add_argument('--output', type=Path, help='default: RESULTS/milp_validation.json')
    args = parser.parse_args()
    output = args.output or args.results / 'milp_validation.json'
    require(output.resolve() not in {(args.results / name).resolve() for name in
            ('instances.json', 'results.json', 'manifest.json', 'calibration.json', 'summary.json', 'results.csv', 'validation.json', 'README.md')},
            'refusing to overwrite original evidence')
    report = verify(args.source_root, args.results)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(f'PASS: {report["instances_checked"]} MILP optima matched with zero gap; supplement: {output}')


if __name__ == '__main__':
    main()
