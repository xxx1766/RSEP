"""Independent boundary/count fixtures for publication aggregation."""
import copy
import importlib
import math
import json
import itertools
import csv
from pathlib import Path
import tempfile
import unittest


def subject():
    return importlib.import_module("fig6_cluster_aggregate")


def fixture():
    expected = {"checksum": 116, "bytes_processed": 16}
    pairs = []
    for origin, epoch, events in [
        ("k2", 10_000_000_000, [(0, 0, .5, 200), (.1, .1, .8, 200), (.8, .9, 1.1, 200)]),
        ("k3", 99_000_000_000, [(0, 0, .1, 200), (.1, .1, .2, 503), (.5, .5, 1., 200)])]:
        rows = []
        for i, (planned, submitted, returned, status) in enumerate(events):
            rows.append({"id": origin + str(i), "origin": origin, "deadline_ms": 500,
                         "planned_submit_monotonic_ns": epoch + round(planned * 1e9),
                         "actual_submit_monotonic_ns": epoch + round(submitted * 1e9),
                         "return_monotonic_ns": epoch + round(returned * 1e9),
                         "http_status": status, "error": None if status == 200 else "http_error",
                         "response": {"result": dict(expected), "node": "nj2" if origin == "k2" else "nj3",
                                      "worker_instance": origin + "-worker", "worker_pid": 123,
                                      "worker_start_ns": 1000000, "worker_end_ns": 2000100,
                                      "service_ns": 1000000}})
        pairs.append(({"origin": origin, "system": "bare", "duration_ns": 1000000000,
                       "start_unix": 123.5, "trace_sha256": "fixture", "expected_result": expected,
                       "window_start_monotonic_ns": epoch, "expected_ids": [r["id"] for r in rows],
                       "clock_sample": {"bracket_ns": 100}, "clock_mapping_drift_ns": 120}, rows))
    return pairs


def full_campaign(root):
    """360 tiny treatments with hand-known 4 successes, 2 window returns each."""
    a = subject()
    def save(relative, obj):
        path = root / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, sort_keys=True) + "\n")
        return a.digest(path.read_bytes())
    (root / 'sources').mkdir(); (root / 'sources/frozen.py').write_text('fixture\n')
    plan = {'campaign': 'fixture', 'repetitions': 5, 'systems': list(a.SYSTEMS), 'duration_s': 1,
            'deadline_ms': 500, 'timeout_s': 10, 'inter_treatment_drain_s': 1, 'source_sha256': {'frozen.py': a.digest(b'fixture\n')},
            'calibration_sha256': {}, 'tide_configs': {}, 'rates_rps': {}, 'workloads': {}, 'jobs': [], 'traces': {}}
    traces = {}
    for kind, level, pattern in itertools.product(a.KINDS, a.LEVELS, a.PATTERNS):
        work = {'kind': kind, 'bytes': 8, 'iterations': 2}; plan['workloads'][kind] = work
        plan['rates_rps'][kind] = {'low': 4, 'high': 4}
        trace = {'pattern': pattern, 'duration_ns': 1_000_000_000, 'workload': work,
                 'expected_result': {'checksum': 116, 'bytes_processed': 16}, 'requests': [
                     {'id': origin + str(i), 'origin': origin, 'offset_ns': i*500_000_000, 'deadline_ms': 500}
                     for origin in a.ORIGINS for i in range(2)]}
        name = f'{kind}-{level}-{pattern}.json'; traces[name] = trace
        plan['traces'][name] = {'file_sha256': save('frozen/traces/'+name, trace), 'sha256': a.digest(a.canonical(trace)), 'offered': 4}
    for repeat, kind, level, pattern, system in itertools.product(range(1, 6), a.KINDS, a.LEVELS, a.PATTERNS, a.SYSTEMS):
        sequence = len(plan['jobs'])+1
        plan['jobs'].append({'id': f'fixture-j{sequence:03d}', 'sequence': sequence, 'block': f'r{repeat}-{kind}-{level}-{pattern}',
                             'repeat': repeat, 'kind': kind, 'level': level, 'pattern': pattern, 'system': system, 'rate_rps': 4, 'trace': f'{kind}-{level}-{pattern}.json'})
    manifest_sha = save('frozen/manifest.json', plan); (root/'frozen/manifest.sha256').write_text(manifest_sha+'\n')
    identities = ['nj1-1-11','nj1-1-12','nj2-1-13','nj3-1-14']
    proof = {'results': [{'system': system, 'instances': {'endpoints': identities} if system == 'kubeedge' else identities} for system in a.SYSTEMS]}
    proof_sha = save('preformal.json', proof)
    marker = {'ok': True, 'evidence': [{'path':'preformal.json','sha256':proof_sha}]}
    marker_sha = save('integrated_smoke.json', marker)
    save('formal/readiness-used.json', {'marker':marker,'marker_sha256':marker_sha,'manifest_sha256':manifest_sha})
    for job in plan['jobs']:
        trace = traces[job['trace']]; trace_sha = a.digest(a.canonical(trace)); start = 1000 + job['sequence']*3
        remote = {}; origins = {}; job_id = job['id']
        for origin_index, origin in enumerate(a.ORIGINS):
            epoch = (10 if origin == 'k2' else 99)*1_000_000_000
            selected = [r for r in trace['requests'] if r['origin'] == origin]
            gateway = job['system'] in ('ray','tide'); port = {'ray':29870,'tide':29801,'bare':29810,'kubeedge':29890}[job['system']]
            urls = [f'http://127.0.0.1:{port}/run'] if gateway else [f'http://11.0.4.47:{port}/run',f'http://11.0.4.47:{port+1}/run',f'http://11.0.4.48:{port}/run',f'http://11.0.4.49:{port}/run']
            records = []
            for i, request in enumerate(selected):
                instance = identities[origin_index*2+i]; node = instance.split('-')[0]
                records.append({**request,'request':trace['workload'],'url':urls[0] if gateway else urls[(i+origin_index*2)%4],
                                'planned_submit_monotonic_ns':epoch+request['offset_ns'],'actual_submit_monotonic_ns':epoch+request['offset_ns'],
                                'return_monotonic_ns':epoch+(100_000_000 if i==0 else 1_000_000_000),'http_status':200,'error':None,
                                'response':{'node':node,'worker_instance':instance,'worker_pid':1,'result':trace['expected_result'],
                                            'service_ns':1_000_000,'worker_start_ns':1000000,'worker_end_ns':2000100}})
            prefix = f'{job_id}-{origin}'; raw = root/'formal'/f'{prefix}.jsonl'
            raw.write_text(''.join(json.dumps(record)+'\n' for record in records))
            meta = {'complete':True,'raw_file':raw.name,'raw_sha256':a.digest(raw.read_bytes()),'origin':origin,'system':job['system'],
                    'route':'gateway' if gateway else 'direct','urls':urls,'start_unix':start,'duration_ns':1_000_000_000,
                    'trace_sha256':trace_sha,'payload_sha256':a.digest(a.canonical(trace['workload'])),'workload':trace['workload'],
                    'expected_result':trace['expected_result'],'expected_ids':[r['id'] for r in selected],'timeout_s':10,
                    'window_start_monotonic_ns':epoch,'clock_mapping_drift_ns':100,'clock_sample':{'bracket_ns':20},
                    'collection_end_unix_ns':round((start+1.01)*1e9),'collection_end_monotonic_ns':epoch+1_010_000_000}
            save(f'formal/{prefix}.meta.json',meta)
            remote[origin] = {'status':'completed','exit_code':0,'job':job_id,'origin':origin,'manifest_sha256':manifest_sha,'finished_ns':round((start+1.02)*1e9)}
            origins[origin] = dict(offered=2,cohort_successful=2,cohort_on_time=2,failed=0,completed_in_window=1,on_time_in_window=1)
        summary = {'job':job,'system':job['system'],'trace_sha256':trace_sha,'start_unix':start,'duration_s':1,
                   'offered':4,'cohort_successful':4,'cohort_on_time':4,'failed':0,'completed_in_window':2,'on_time_in_window':2,
                   'throughput_rps':2,'goodput_rps':2,'offered_rps':4,'cohort_yield':1,'cohort_success_fraction':1,
                   'failure_reasons':{},'destinations':{'nj1':2,'nj2':1,'nj3':1},'origins':origins}
        latency = {'count':4,'mean':300,'p50':100,'p95':500,'p99':500,'max':500}
        summary.update(successful_latency_ms=latency,successful_planned_to_return_ms=latency,
                       replay_lateness_ms={'count':4,'mean':0,'p50':0,'p95':0,'p99':0,'max':0},
                       kernel_service_ms={'count':4,'mean':1,'p50':1,'p95':1,'p99':1,'max':1})
        summary_sha = save(f'formal/{job_id}.summary.json',summary)
        save(f'formal/state/{job_id}.json',{'status':'completed','job':job,'manifest_sha256':manifest_sha,'start_unix':start,
                                         'remote_terminal':remote,'pending_execution':[],'summary_sha256':summary_sha,'readiness':'responses_drained'})
    return plan


class AggregateTests(unittest.TestCase):
    def test_explicit_rate_rule_is_independent_and_rejects_tampered_design(self):
        a = subject()
        # Hand-authored manifest: validation must not depend on the measurement planner.
        requested = {'cpu': {'low': 100, 'high': 300}, 'memory': {'low': 20, 'high': 50},
                     'io': {'low': 40, 'high': 100}}
        inputs = {f'{node}-bare': {'summary': {kind: {'median_service_ms': 100} for kind in a.KINDS}}
                  for node in a.NODES}
        plan = {'rates_rps': copy.deepcopy(requested), 'capacity_rps': {kind: 40.0 for kind in a.KINDS},
                'rate_rule': 'explicit positive integer requests per second from rate_selection.rates_rps; low < high',
                'rate_selection': {'mode': 'explicit', 'rates_rps': copy.deepcopy(requested)},
                'duration_s': 10, 'jobs': [], 'traces': {}}
        for kind, level, pattern in itertools.product(a.KINDS, a.LEVELS, a.PATTERNS):
            name = f'{kind}-{level}-{pattern}.json'; rate = requested[kind][level]
            plan['traces'][name] = {'offered': 10 * rate}
            plan['jobs'].append({'kind': kind, 'level': level, 'pattern': pattern, 'rate_rps': rate, 'trace': name})
        a.verify_rate_rule(plan, inputs)
        mutations = [lambda p: p.pop('rate_selection'), lambda p: p.pop('rate_rule'),
                     lambda p: p.update(rate_rule='unrecognized'),
                     lambda p: p['rate_selection'].update(mode='calibration'),
                     lambda p: p['rate_selection'].update(extra=True),
                     lambda p: p['rate_selection']['rates_rps']['cpu'].update(low=99),
                     lambda p: p.update(rate_quantization={'multiple': 5, 'rounding': 'half_up',
                                                          'input': 'calibration_integer_rate', 'minimum': 5}),
                     lambda p: p['capacity_rps'].update(cpu=41),
                     lambda p: p['capacity_rps'].update(cpu=True),
                     lambda p: p['capacity_rps'].update(cpu=float('nan')),
                     lambda p: p['capacity_rps'].pop('cpu'),
                     lambda p: p['jobs'][0].update(rate_rps=999),
                     lambda p: p['jobs'][0].update(rate_rps=100.0),
                     lambda p: p['jobs'][0].update(trace='cpu-high-balanced.json'),
                     lambda p: p['traces']['cpu-low-balanced.json'].update(offered=999),
                     lambda p: p['traces']['cpu-low-balanced.json'].update(offered=1000.0),
                     lambda p: p['traces'].pop('cpu-low-balanced.json')]
        for value in (True, 100.0, 0, -1, '100', None, 300):
            def change_both(p, value=value):
                p['rates_rps']['cpu']['low'] = value
                p['rate_selection']['rates_rps']['cpu']['low'] = value
            mutations.append(change_both)
        mutations.extend((lambda p: p['rates_rps'].update(gpu={'low': 1, 'high': 2}),
                          lambda p: p['rates_rps']['cpu'].update(extra=1)))
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(plan); mutate(changed)
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                a.verify_rate_rule(changed, inputs)
        legacy = copy.deepcopy(plan)
        legacy['rate_rule'] = 'round(max(2, sum(node_slots * 1000 / bare median service ms) * factor)); factors=0.30,0.80; Python round'
        with self.assertRaises(ValueError): a.verify_rate_rule(legacy, inputs)

    def test_reused_calibration_never_supplies_current_worker_identity(self):
        a = subject()
        current = ['nj1-new-1', 'nj1-new-2', 'nj2-new-1', 'nj3-new-1']
        old = ['nj1-old-1', 'nj1-old-2', 'nj2-old-1', 'nj3-old-1']
        marker = {'ok': True, 'evidence': [{'path':'current.json','sha256':'current'}]}
        proof = {'results':[{'system':s, 'instances':current[:]} for s in a.SYSTEMS]}
        plan = {'calibration_sha256':{'old.json':'old'}, 'calibration_reuse':{
            'campaign':'old-campaign','manifest_sha256':'a'*64,'calibration_sha256':{'old.json':'old'}}}
        objects = {'formal/readiness-used.json':{'marker':marker,'manifest_sha256':'new-manifest','marker_sha256':'marker'},
                   'integrated_smoke.json':marker, 'current.json':proof,
                   'frozen/calibration/old.json':{'results':[{'system':s,'instances':old} for s in a.SYSTEMS]}}
        class Evidence:
            def read(self, path, wanted=None): return objects[path]
        proven = a.proven_instances(Evidence(), plan, 'new-manifest')
        self.assertEqual(proven['tide']['nj1'],current[:2])
        proof['results'][1]['instances'].remove('nj1-new-2')
        with self.assertRaisesRegex(ValueError,'2/1/1'):
            a.proven_instances(Evidence(), plan, 'new-manifest')

    def test_declared_rate_rules_replay_legacy_and_nearest_five(self):
        a = subject()
        root = Path(__file__).resolve().parents[2] / 'results-fig6-cluster-20261015'
        legacy = json.loads((root / 'frozen/manifest.json').read_text())
        inputs = {p.stem: json.loads(p.read_text()) for p in (root/'frozen/calibration').glob('*.json')}
        a.verify_rate_rule(legacy, inputs)
        m = importlib.import_module('fig6_cluster_matrix')
        plan, traces = m.build_plan(inputs, 'fixture', rate_multiple=5)
        plan['traces'] = {name: {'offered': len(t['requests'])} for name,t in traces.items()}
        a.verify_rate_rule(plan, inputs)
        for mutate in (lambda p:p['rates_rps']['cpu'].update(low=110),
                       lambda p:p['rate_quantization'].update(rounding='bankers'),
                       lambda p:p.pop('rate_rule'),
                       lambda p:p.update(rate_rule='unrecognized'),
                       lambda p:p['traces']['cpu-low-balanced.json'].update(offered=1140),
                       lambda p:p['jobs'][0].update(rate_rps=999)):
            changed = copy.deepcopy(plan); mutate(changed)
            with self.assertRaises(ValueError): a.verify_rate_rule(changed, inputs)

    def test_t_df4_interval_hand_calculation_and_missing_run(self):
        a = subject(); result = a.ci5([1, 2, 3, 4, 5])
        self.assertEqual(result["n"], 5)
        self.assertEqual(result["mean"], 3)
        self.assertAlmostEqual(result["sd"], math.sqrt(2.5))
        self.assertAlmostEqual(result["se"], math.sqrt(.5))
        self.assertAlmostEqual(result["ci95_halfwidth"], 1.963243161477561, places=10)
        self.assertEqual(a.ci5([2] * 5)["ci95_halfwidth"], 0)
        self.assertIsNone(a.ci5([1, 2, None, 4, 5])["mean"])
        with self.assertRaises(ValueError):
            a.ci5([1, 2, 3, 4])

    def test_independent_window_deadline_origin_and_failure_counts(self):
        result, detail = subject().recompute(fixture())
        self.assertEqual(result["offered"], 6)
        self.assertEqual(result["cohort_successful"], 5)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["cohort_on_time"], 4)
        self.assertEqual(result["completed_in_window"], 3)
        self.assertEqual(result["on_time_in_window"], 2)
        self.assertEqual(result["throughput_rps"], 3)
        self.assertEqual(result["goodput_rps"], 2)
        self.assertAlmostEqual(result["cohort_yield"], 4 / 6)
        self.assertEqual(result["replay_lateness_ms_p95"], 100)
        self.assertEqual(result["destination_nj2_successes"], 3)
        self.assertEqual(result["destination_nj3_successes"], 2)
        self.assertEqual(detail["failure_reasons"], {"http_error": 1})
        self.assertEqual(result["kernel_service_ms_mean"], 1)

    def test_checksum_mismatch_remains_in_offered_denominator(self):
        pair = fixture(); pair[0][1][0]["response"]["result"]["checksum"] += 1
        result, detail = subject().recompute(pair)
        self.assertEqual(result["offered"], 6)
        self.assertEqual(result["failed"], 2)
        self.assertEqual(detail["failure_reasons"]["result_mismatch"], 1)

    def test_missing_duplicate_ids_and_different_windows_rejected(self):
        for mutate in (lambda p: p[0][1].pop(), lambda p: p[0][1].append(p[0][1][0]),
                       lambda p: p[1][0].update(duration_ns=2_000_000_000),
                       lambda p: p[0][1][0].update(return_monotonic_ns=0)):
            pair = fixture(); mutate(pair)
            with self.assertRaises(ValueError):
                subject().recompute(pair)

    def test_no_success_has_zero_goodput_but_no_latency_estimate(self):
        pair = fixture()
        for _, rows in pair:
            for row in rows:
                row["error"] = "timeout"
        result, _ = subject().recompute(pair)
        self.assertEqual(result["offered"], 6)
        self.assertEqual(result["failed"], 6)
        self.assertEqual(result["goodput_rps"], 0)
        self.assertEqual(result["cohort_yield"], 0)
        self.assertIsNone(result["successful_latency_ms_mean"])

    def test_partial_campaign_never_creates_publication_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / "publication"
            with self.assertRaises((ValueError, FileNotFoundError)):
                subject().publish(root, output, Path(__file__).parent)
            self.assertFalse(output.exists())

    def test_sequence_waits_for_both_origins_remote_completion_and_clearance(self):
        a=subject(); rows=[{'job_id':'a','start_unix':1.,'duration_s':1.},{'job_id':'b','start_unix':3.5,'duration_s':1.}]
        timing={'collection_end_unix_ns':{'k2':2_100_000_000,'k3':2_200_000_000},
                'remote_finished_ns':{'k2':2_300_000_000,'k3':2_400_000_000},'drain_clearance_verified_ns':None}
        detail={'a':{'timing':timing}}
        self.assertEqual(a.verify_sequence(rows,detail,1)[0]['nominal_gap_s'],1.1)
        timing['collection_end_unix_ns']['k3']=2_600_000_000
        with self.assertRaisesRegex(ValueError,'overlaps'):
            a.verify_sequence(rows,detail,1)
        timing['collection_end_unix_ns']['k3']=2_200_000_000
        timing['remote_finished_ns']['k2']=2_600_000_000
        with self.assertRaisesRegex(ValueError,'overlaps'):
            a.verify_sequence(rows,detail,1)
        timing['remote_finished_ns']['k2']=2_300_000_000
        timing['drain_clearance_verified_ns']=2_600_000_000
        with self.assertRaisesRegex(ValueError,'overlaps'):
            a.verify_sequence(rows,detail,1)
        rows[1]['start_unix']=3.6
        self.assertEqual(a.verify_sequence(rows,detail,1)[0]['nominal_gap_s'],1.)

    def test_complete_matrix_publishes_counts_ci_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan = full_campaign(root); a = subject()
            result = a.publish(root,root/'publication',root/'sources')
            self.assertEqual(result['treatments'],360); self.assertEqual(result['cells'],72)
            rows = list(csv.DictReader((root/'publication/runs.csv').read_text().splitlines()))
            self.assertEqual(len(rows),360); self.assertTrue(all(r['offered']=='4' and r['goodput_rps']=='2.0' for r in rows))
            cells = list(csv.DictReader((root/'publication/cells.csv').read_text().splitlines()))
            yields = [r for r in cells if r['metric']=='cohort_yield']
            self.assertEqual(len(yields),72); self.assertTrue(all(float(r['mean'])==1 and float(r['ci95_halfwidth'])==0 and r['n']=='5' for r in yields))
            audit=json.loads((root/'publication/audit.json').read_text()); self.assertEqual(audit['offered_total'],1440)
            raw=root/'formal/fixture-j001-k2.jsonl'; raw.write_text(raw.read_text()+'\n')
            with self.assertRaisesRegex(ValueError,'Hash mismatch'):
                a.publish(root,root/'tampered',root/'sources')
            self.assertFalse((root/'tampered').exists())

    def test_rehashed_missing_offer_and_restarted_worker_still_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); full_campaign(root); a=subject()
            raw=root/'formal/fixture-j001-k2.jsonl'; meta_path=root/'formal/fixture-j001-k2.meta.json'
            original=[json.loads(line) for line in raw.read_text().splitlines()]
            def rewrite(records):
                raw.write_text(''.join(json.dumps(record)+'\n' for record in records))
                meta=json.loads(meta_path.read_text()); meta['raw_sha256']=a.digest(raw.read_bytes())
                meta_path.write_text(json.dumps(meta)+'\n')
            rewrite(original[:1])
            with self.assertRaisesRegex(ValueError,'Incomplete or duplicate offered IDs'):
                a.publish(root,root/'omitted',root/'sources')
            self.assertFalse((root/'omitted').exists())
            changed=copy.deepcopy(original); changed[0]['response']['worker_instance']='nj1-1-RESTARTED'
            rewrite(changed)
            with self.assertRaisesRegex(ValueError,'Unexpected worker instance/restart'):
                a.publish(root,root/'restarted',root/'sources')
            self.assertFalse((root/'restarted').exists())


if __name__ == "__main__":
    unittest.main()
