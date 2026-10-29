"""Include Tide's sampled host containerd in control/runtime totals.

Sum components within each measurement block before computing SD or CI.
The original control_summary.csv and component measurements remain unchanged.
Run before compact_remaining_figures.py --control-only.
"""
from pathlib import Path
import csv
import hashlib
import json
import math
import statistics as st

HERE = Path(__file__).resolve().parent
GROUPS = {
    'tide': ['core', 'runtime_support', 'shared_host_runtime'],
    'kubeedge': ['cloudcore', 'edgecore', 'dedicated_cluster_support'],
    'ray': ['core', 'request_path'],
}
T95 = 4.30265273


def summarize(values):
    assert len(values) == 3
    mean, sd = st.mean(values), st.stdev(values)
    return mean, sd, T95 * sd / math.sqrt(3)


def main():
    provenance_path = HERE / 'DATA_PROVENANCE.json'
    provenance = json.loads(provenance_path.read_text())
    for name in ['control_summary.csv', 'control_components.csv']:
        assert hashlib.sha256((HERE / name).read_bytes()).hexdigest() == provenance[name]['sha256']
    with (HERE / 'control_summary.csv').open() as f:
        original = list(csv.DictReader(f))
    with (HERE / 'control_components.csv').open() as f:
        components = list(csv.DictReader(f))
    index = {(r['system'], int(r['repeat']), int(r['rps']), int(r['ddl_ms']), r['group']): r
             for r in components}
    assert len(index) == len(components)
    totals = {}
    result = []
    for row in original:
        system, rate, ddl = row['system'], int(row['rps']), int(row['ddl_ms'])
        updated = dict(row)
        for metric in ['cpu_pct', 'pss_mib']:
            values = []
            for repeat in [1, 2, 3]:
                parts = [index[system, repeat, rate, ddl, group] for group in GROUPS[system]]
                assert all(float(p['missing_cpu_intervals']) == 0 and float(p['missing_pss']) == 0 for p in parts)
                value = sum(float(p[metric]) for p in parts)
                totals[system, repeat, rate, ddl, metric] = value
                values.append(value)
            mean, sd, half = summarize(values)
            if system == 'tide':
                updated.update({f'stack_{metric}': mean, f'stack_{metric}_sd': sd,
                                f'stack_{metric}_ci95_half': half})
            else:
                for suffix, value in [('', mean), ('_sd', sd), ('_ci95_half', half)]:
                    assert math.isclose(value, float(row[f'stack_{metric}{suffix}']), abs_tol=1e-9)
        result.append(updated)
    output = HERE / 'control_runtime_summary.csv'
    with output.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(original[0]))
        writer.writeheader()
        writer.writerows(result)
    contrasts = []
    for system in GROUPS:
        conditions = [('pressure', 200, (5, 200), (80, 200))]
        conditions += [('deadline', rate, (rate, 50), (rate, 1000)) for rate in [5, 20, 80]]
        for factor, condition, low, high in conditions:
            values = [totals[system, repeat, *high, 'cpu_pct'] - totals[system, repeat, *low, 'cpu_pct']
                      for repeat in [1, 2, 3]]
            mean, sd, half = summarize(values)
            contrasts.append(dict(system=system, factor=factor, condition=condition,
                                  mean=mean, sd=sd, ci95_low=mean-half, ci95_high=mean+half))
    (HERE / 'CONTROL_RUNTIME_VALUES.json').write_text(json.dumps({
        'included_groups': GROUPS,
        'tide_host_containerd': 'Full sampled daemon cost, without subtracting other host activity.',
        'excluded': 'Application worker processes, other host services, and unsampled outer kind shims.',
        'contrasts': contrasts,
    }, indent=2) + '\n')
    table = ['\\begin{tabular}{@{}llrr@{}}', '\\toprule',
             'Condition & System & Control + runtime CPU & Control + runtime PSS \\\\', '\\midrule']
    labels = {'tide': 'Tide', 'kubeedge': 'KubeEdge', 'ray': 'Ray Serve'}
    for rate in [0, 20, 80]:
        for system in GROUPS:
            row = next(r for r in result if r['system'] == system and int(r['rps']) == rate
                       and int(r['ddl_ms']) == (200 if rate else 0))
            values = [f"{float(row['stack_'+metric]):.2f} $\\pm$ {float(row['stack_'+metric+'_sd']):.2f}"
                      for metric in ['cpu_pct', 'pss_mib']]
            table.append(' & '.join(['Idle' if rate == 0 else f'{rate} req/s', labels[system], *values]) + r' \\')
    table += ['\\bottomrule', '\\end{tabular}']
    (HERE.parent / 'revision_data/control_resource_table.tex').write_text('\n'.join(table) + '\n')
    provenance[output.name] = {
        'source': 'Derived by plotting/control_runtime_scope.py from control_summary.csv and control_components.csv',
        'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    provenance_path.write_text(json.dumps(provenance, indent=2) + '\n')
    for row in result:
        if row['system'] == 'tide' and int(row['ddl_ms']) in [0, 200]:
            print({k: row[k] for k in ['rps', 'ddl_ms', 'stack_cpu_pct', 'stack_pss_mib']})
    print(json.dumps(contrasts, indent=2))


if __name__ == '__main__':
    main()
