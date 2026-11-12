#!/usr/bin/env python3
import argparse
import csv
import hashlib
import importlib.util
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'figures/current/data'
COMPACT = ROOT / 'data/compact/current'


def load_renderer(name):
    path = ROOT / 'figures/current' / (name + '.py')
    spec = importlib.util.spec_from_file_location('artifact_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_csv(path):
    with Path(path).open(newline='') as stream:
        return list(csv.DictReader(stream))


def read_json(path):
    return json.loads(Path(path).read_text())


def verify_index():
    index = read_json(DATA / 'INDEX.json')
    for relative, expected in index['files'].items():
        path = DATA / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError('Packaged input checksum mismatch: ' + relative)
    return index


def check_payloads():
    index = verify_index()
    compact_index = read_json(COMPACT / 'INDEX.json')
    compact = {}
    for name, entry in compact_index['datasets'].items():
        raw = (ROOT / entry['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('Export checksum mismatch: ' + entry['path'])
        compact[name] = json.loads(raw)
    for family, metadata_path in compact_index.get('families', {}).items():
        metadata = read_json(ROOT / metadata_path)
        for entry in metadata['files']:
            path = (ROOT / metadata_path).parent / entry['path']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256'], (family, entry['path'])
    native = compact['native_pytorch']
    assert native['status'] == 'VERIFIED_ALL_PRESPECIFIED_RUNS'
    assert native['runs'] == 180 and native['offered'] == 37800
    assert native['valid'] + native['failed'] == native['offered']
    assert len(native['cells']) == 36
    lifecycle = compact['elasticity']['lifecycle']
    assert lifecycle['records'] == 600 and lifecycle['repeats'] == 30
    for actual, reported in zip(lifecycle['totals'], [163.41, 61.21, 31.32]):
        assert abs(actual - reported) < 0.01
    wan = compact['wan']
    assert wan['deadline_ms'] == 135
    assert [c['pattern'] for c in wan['cases']] == ['gradual', 'burst']

    calibration = read_json(DATA / 'calibration/plot_data.json')
    assert len(calibration['grid']) == len(calibration['calibration']) == 68
    assert len(calibration['selection']) == 6
    assert all(row == [0, .9, .1] for row in
               (c['Weights'] for c in calibration['selection']))

    capacity = read_csv(DATA / 'capacity/cells.csv')
    rates = read_json(DATA / 'capacity/rates.json')
    assert len(capacity) == 360
    assert len({(r['kind'], r['level'], r['pattern'], r['system']) for r in capacity}) == 360
    assert all(int(r['n']) == 5 for r in capacity)
    assert len(rates['rates_rps']) == 3 and len(rates['rates_rps']['cpu']) == 10

    historical = load_renderer('historical')
    hdata = historical.load(DATA / 'historical')
    historical.validate(hdata)
    latency = hdata['latency']
    assert len(latency) == 6
    assert all([p['rate'] for p in series['points']] == list(range(5, 51, 5))
               for series in latency)
    assert abs(float(next(s for s in hdata['placement'] if s['platform'] == 'Tide'
                          and s['rate_rps_per_client'] == '5')['edge_percent']) - 98.05) < .01
    assert abs(float(next(s for s in hdata['placement'] if s['platform'] == 'Tide'
                          and s['rate_rps_per_client'] == '25')['edge_percent']) - 62.03) < .01
    assert abs(float(next(s for s in hdata['placement'] if s['platform'] == 'Tide'
                          and s['rate_rps_per_client'] == '50')['edge_percent']) - 35.86) < .01
    good = next(s for s in hdata['goodput'] if s['platform'] == 'Tide'
                and s['rate_rps_per_client'] == '50')
    assert abs(float(good['throughput_rps_from_pdf_vector']) - 144.16) < .01

    control = read_csv(DATA / 'control/management.csv')
    assert len(control) == 12
    assert {r['system'] for r in control} == {'tide', 'kubeedge', 'ray'}
    assert all(int(r['ddl_ms']) == 200 and int(r['n']) == 3 for r in control)

    counts = read_csv(DATA / 'domain/return-counts.csv')
    r_srv = read_csv(DATA / 'domain/r-srv.csv')
    assert len(counts) == len(r_srv) == 360
    assert all(len([r for r in counts if r['system'] == s
                    and r['aggregation'] == 'run']) == 90 for s in ('tide', 'kubeedge', 'ray'))
    first = read_json(DATA / 'domain/meta.json')['tide_first_B_timely_return_s']
    assert all(20 < t < 110 for t in first)
    assert abs(statistics.mean(first) - 47.5594) < .001


    admission = read_csv(DATA / 'tables/admission_results.csv')
    assert len(admission) == 120
    ratios = [int(r['heuristic_accepted']) / int(r['optimum_accepted']) for r in admission]
    assert abs(statistics.mean(ratios) - .9154) < .0001
    assert abs(statistics.median(ratios) - .9583) < .0001
    assert abs(min(ratios) - .5) < 1e-9
    assert sum(int(r['objective_gap']) == 0 for r in admission) == 60
    assert max(int(r['optimum_accepted']) - int(r['heuristic_accepted'])
               for r in admission) == 3

    mixed = read_csv(DATA / 'tables/mixed_cells.csv')
    assert len(mixed) == 24
    assert all(int(r['n']) == 5 for r in mixed)
    assert {(r['level'], int(r['deadline_ms']), r['system']) for r in mixed} == {
        (level, deadline, system) for level in ('low', 'high', 'overload')
        for deadline in (100, 500) for system in ('tide', 'bare', 'ray', 'kubeedge')}
    for level, system, expected in [('high', 'tide', 147.10), ('high', 'bare', 47.16),
                                    ('high', 'ray', 8.58), ('high', 'kubeedge', 47.10),
                                    ('overload', 'tide', 147.50), ('overload', 'bare', 63.34)]:
        row, = [r for r in mixed if r['level'] == level and r['system'] == system
                and r['deadline_ms'] == '500']
        assert abs(float(row['goodput_rps_mean']) - expected) < .01
    return compact, index


def draw(compact, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    native = load_renderer('native_pytorch')
    native.draw_all(compact['native_pytorch'], output)
    for extra in output.glob('supplement_p*_*'):
        extra.unlink()
    elasticity = load_renderer('elasticity')
    fig = elasticity.render_pair(compact['elasticity'])
    elasticity.save_pair(fig, output / 'elasticity_lifecycle_pair')
    plt.close(fig)
    wan = load_renderer('wan')
    fig = wan.render_three_pair_wan(compact['wan'])
    for extension in ('pdf', 'png', 'svg'):
        fig.savefig(output / ('wan_3plus3_65ms_tide_k8s.' + extension), dpi=300)
    plt.close(fig)

    calibration = load_renderer('calibration')
    calibration.draw(read_json(DATA / 'calibration/plot_data.json'), output)
    capacity = load_renderer('capacity')
    cells, rates = capacity.load(DATA / 'capacity/cells.csv', DATA / 'capacity/rates.json')
    capacity.draw(cells, rates, output)
    historical = load_renderer('historical')
    historical.draw_main(historical.load(DATA / 'historical'), output)
    control = load_renderer('control')
    control.draw(control.load(DATA / 'control/management.csv'), output)
    domain = load_renderer('domain')
    counts, r_srv, meta = domain.load(DATA / 'domain')
    domain.draw(counts, r_srv, meta, output)

    supplement = load_renderer('supplement')
    supplement.copy_fixed_assets(DATA / 'supplement_originals', output)
    (output / 'supplement_manifest.json').write_text(json.dumps({
        'S1': {'file': 'historical_ablation_original_style_embedded.pdf', 'method': 'unchanged source asset'},
        'S2': {'file': 'historical_allocation.pdf', 'method': 'redrawn from packaged allocation vertices'},
        'S3': {'file': 'historical_wan.pdf', 'method': 'unchanged source asset'},
        'S4': {'file': 'historical_cc_relative.pdf', 'method': 'redrawn from archived case-summary ratios'},
        'S5': {'file': 'historical_pi_relative.pdf', 'method': 'redrawn from archived case-summary ratios'},
        'S6': {'file': 'historical_ei_relative.pdf', 'method': 'redrawn from archived case-summary ratios'},
        'S7': {'file': 'historical_latency_original.pdf', 'method': 'unchanged source asset'},
        'S8': {'file': 'historical_placement_original.pdf', 'method': 'redrawn from 100 placement summaries'},
        'S9': {'file': 'historical_goodput_original.pdf', 'method': 'redrawn from 50 throughput/goodput summaries'}},
        indent=2) + '\n')
    supplement_out = output / 'motivation'
    supplement_out.mkdir(parents=True, exist_ok=True)
    for name in ('CPU-usage.pdf', 'HPA-analysis.pdf', 'resource_expansion_timeline.pdf'):
        (supplement_out / name).write_bytes((DATA / 'motivation' / name).read_bytes())


def tables(output):
    from build_tables import write
    summary, mixed_table = write(DATA / 'tables', output)
    return summary, mixed_table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['check', 'figures', 'tables'])
    parser.add_argument('--output', type=Path, default=ROOT / 'figures/rendered/current')
    args = parser.parse_args()
    compact, index = check_payloads()
    from aggregate_data import run as aggregate
    aggregation = aggregate(args.output if args.action == 'tables' else None)
    result = {'verified_files': len(index['files']), 'action': args.action,
              'live_experiments_executed': False, 'aggregation': aggregation}
    if args.action == 'figures':
        draw(compact, args.output)
    elif args.action == 'tables':
        summary, mixed_table = tables(args.output)
        result['admission_mean_ratio'] = summary['mean_ratio']
        result['mixed_rows'] = len(mixed_table)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
