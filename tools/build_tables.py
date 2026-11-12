import csv
import json
import statistics
from pathlib import Path


def load_csv(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


def admission(directory):
    rows = load_csv(directory / 'admission_results.csv')
    assert len(rows) == 120
    assert all(r['proven_optimal'] == 'true' for r in rows)
    records = []
    for row in rows:
        heuristic = int(row['heuristic_accepted'])
        optimum = int(row['optimum_accepted'])
        assert optimum > 0 and 0 <= heuristic <= optimum
        ratio = heuristic / optimum
        records.append(dict(instance=row['instance'], seed=row['seed'],
                            tasks=int(row['tasks']), nodes=int(row['nodes']),
                            horizon_multiplier=float(row['horizon_multiplier']),
                            deadline_ms=float(row['deadline_ms']),
                            heuristic_accepted=heuristic, optimum_accepted=optimum,
                            absolute_deficit=optimum - heuristic,
                            ratio=ratio, gap=1 - ratio,
                            exact=heuristic == optimum))
    ratios = [r['ratio'] for r in records]
    deficits = [r['absolute_deficit'] for r in records]
    minimum = min(records, key=lambda r: r['ratio'])
    summary = dict(instances=len(records),
                   mean_ratio=statistics.mean(ratios),
                   median_ratio=statistics.median(ratios),
                   min_ratio=min(ratios),
                   min_ratio_instance=minimum['instance'],
                   exact_count=sum(r['exact'] for r in records),
                   exact_denominator=len(records),
                   max_absolute_deficit=max(deficits),
                   mean_gap=statistics.mean(r['gap'] for r in records))
    groups = []
    for tasks in sorted({r['tasks'] for r in records}):
        for nodes in sorted({r['nodes'] for r in records}):
            selected = [r for r in records if r['tasks'] == tasks and r['nodes'] == nodes]
            assert len(selected) == 15
            groups.append(dict(tasks=tasks, nodes=nodes, instances=len(selected),
                               mean_ratio=statistics.mean(r['ratio'] for r in selected),
                               min_ratio=min(r['ratio'] for r in selected),
                               mean_gap=statistics.mean(r['gap'] for r in selected),
                               max_absolute_deficit=max(r['absolute_deficit'] for r in selected),
                               exact_count=sum(r['exact'] for r in selected)))
    assert len(groups) == 8
    return records, summary, groups


def mixed(directory):
    meta = json.loads((directory / 'mixed_meta.json').read_text())
    rows = load_csv(directory / 'mixed_cells.csv')
    assert len(rows) == 24
    assert {(r['level'], int(r['deadline_ms']), r['system']) for r in rows} == {
        (level, deadline, system) for level in meta['levels']
        for deadline in (100, 500)
        for system in ('tide', 'bare', 'ray', 'kubeedge')}
    records = []
    for row in rows:
        assert int(row['n']) == 5
        records.append(dict(level=row['level'], display_rate=meta['display_rates'][row['level']],
                            actual_rate=float(row['rate_rps']),
                            deadline_ms=int(row['deadline_ms']), system=row['system'],
                            n=int(row['n']), mean=float(row['goodput_rps_mean']),
                            ci95_half=float(row['goodput_rps_ci95_halfwidth'])))
    table = []
    for level in meta['levels']:
        for deadline in (100, 500):
            row = dict(rate=meta['display_rates'][level], qos=deadline)
            for system in ('tide', 'bare', 'ray', 'kubeedge'):
                cell, = [r for r in records if r['level'] == level
                         and r['deadline_ms'] == deadline and r['system'] == system]
                row[system] = cell['mean']
                row[system + '_ci95_half'] = cell['ci95_half']
            table.append(row)
        mixed_row = dict(rate=meta['display_rates'][level], qos='Mixed')
        for system in ('tide', 'bare', 'ray', 'kubeedge'):
            mixed_row[system] = sum(r['mean'] for r in records
                                    if r['level'] == level and r['system'] == system)
        table.append(mixed_row)
    assert len(table) == 9
    return records, table, meta


def write_csv(path, rows):
    fields = list(rows[0])
    with Path(path).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write(directory, output):
    directory = Path(directory)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records, summary, groups = admission(directory)
    write_csv(output / 'admission_instances.csv', records)
    write_csv(output / 'admission_groups.csv', groups)
    write_csv(output / 'admission_summary.csv', [summary])
    (output / 'admission_instances.json').write_text(json.dumps(records, indent=2) + '\n')
    (output / 'admission_groups.json').write_text(json.dumps(groups, indent=2) + '\n')
    (output / 'admission_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    cells, table, meta = mixed(directory)
    write_csv(output / 'mixed_cells.csv', cells)
    write_csv(output / 'mixed_qos_table.csv', table)
    (output / 'mixed_cells.json').write_text(json.dumps(cells, indent=2) + '\n')
    (output / 'mixed_qos_table.json').write_text(json.dumps(table, indent=2) + '\n')
    (output / 'manifest.json').write_text(json.dumps({
        'admission_source': 'model-01 results.csv (120 instances)',
        'admission_summary': summary, 'admission_groups': len(groups),
        'mixed_source': meta['source'], 'mixed_n': 5,
        'mixed_levels': meta['levels'], 'mixed_display_rates': meta['display_rates'],
        'mixed_actual_rates': meta['actual_rates']}, indent=2) + '\n')
    return summary, table
