"""TEST ONLY synthetic fixtures; never publication evidence."""
import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).parent
TARGET = HERE / 'fig7_cluster_mixed_audit.py'


def reference():
    buffer = bytearray((bytes((i * 17 + 3) % 256 for i in range(256))) * 256)
    for _ in range(100):
        result = hashlib.sha256(buffer).digest()
        buffer[:32] = result
    return {'bytes_processed': 6553600, 'checksum': int.from_bytes(result[:8], 'little')}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    return sha(path.read_bytes())


def quiescence_fixture(root, job, state, manifest_sha):
    nodes = {}
    identity_sources = []
    for index, node in enumerate(('nj1', 'nj2', 'nj3')):
        workers = [{'pid': index * 10 + slot + 1, 'start_ticks': str(slot + 100),
                    'cpu_ticks': 30, 'io': {'read_bytes': 0, 'write_bytes': 0},
                    'threads': [{'state': 'S'}]} for slot in range(2 if node == 'nj1' else 1)]
        identity_path = f'TEST_ONLY-identities-{node}.json'
        identity_sources.append({'path': identity_path, 'sha256': write(root / identity_path, {'processes': workers})})
        metrics = []
        for slot in range(len(workers)):
            for name in ('ray_serve_deployment_queued_queries', 'ray_serve_num_ongoing_requests_at_replicas',
                         'ray_serve_replica_processing_queries'):
                metrics.append({'name': name, 'value': 0, 'labels': {'application': 'tmc-fig6-ray',
                    'deployment': 'MatchedGoKernel', 'replica': f'{node}-replica-{slot}'}})
        nodes[node] = {'workers': workers, 'identities': workers, 'identity_errors': [],
                       'active_load': [], 'nonterminal_states': [], 'missing_target_states': [],
                       'sockets': [], 'locks_available': True, 'metrics': metrics}
    verified_ns = state['completed_ns'] + 3_000_000_000
    observation = {'criterion': 'bounded observed quiescence', 'minimum_age_s': 0,
        'minimum_observation_s': 2, 'minimum_samples': 3, 'passed': True,
        'accepted_first_sample': 0, 'last_window_errors': [], 'verified_ns': verified_ns,
        'identity_sources': identity_sources,
        'audit': {'job': job, 'state': copy.deepcopy(state), 'manifest_sha256': manifest_sha},
        'samples': [{'monotonic_ns': 100_000_000_000 + i * 1_100_000_000,
                     'finished_monotonic_ns': 100_000_000_001 + i * 1_100_000_000,
                     'time_ns': state['completed_ns'] + 1 + i * 1_100_000_000, 'nodes': copy.deepcopy(nodes)}
                    for i in range(3)]}
    path = f"formal/drain-observation/{job['id']}.json"
    clearance = {'job': job['id'], 'idle_verified': True, 'summary_sha256': state['summary_sha256'],
                 'criterion': 'bounded observed quiescence', 'verified_ns': verified_ns,
                 'evidence': [{'path': path, 'sha256': write(root / path, observation)}]}
    state['drain_clearance_sha256'] = write(root / f"formal/drain-clearance/{job['id']}.json", clearance)
    state['readiness'] = 'drain_verified'
    return observation


def fixture(root):
    """Complete but small TEST ONLY synthetic campaign; 20 offers per treatment."""
    workload = {'kind': 'cpu', 'bytes': 65536, 'iterations': 100}
    expected = reference()
    config = {'qos_by_function_ms': {'cpu-qos100': 100, 'cpu-qos500': 500}}
    plan = {'campaign': 'TEST_ONLY_SYNTHETIC_NOT_PUBLICATION', 'test_only': True,
            'repetitions': 5, 'systems': ['bare', 'tide', 'ray', 'kubeedge'],
            'duration_s': 10, 'timeout_s': 10, 'deadline_classes_ms': [100, 500],
            'workloads': {'cpu': workload}, 'tide_configs': {'cpu': config},
            'rates_rps': {'cpu': {'low': 2, 'high': 3, 'overload': 4}},
            'load_factors': {'low': .3, 'high': .8, 'overload': 1.2},
            'source_sha256': {}, 'calibration_sha256': {}, 'traces': {}, 'jobs': [],
            'inter_treatment_drain_s': 1}
    traces = {}
    for level, rate in plan['rates_rps']['cpu'].items():
        count = rate * 10
        requests = [{'id': f'r{i:08d}', 'origin': 'k2' if i % 2 == 0 else 'k3',
                     'offset_ns': i * 10_000_000_000 // count,
                     'deadline_ms': (100, 500)[((i // 2) % 2) ^ (((i // 2) // 4) % 2) ^ (i % 2)]}
                    for i in range(count)]
        trace = {'schema_version': 2, 'deadline_classes_ms': [100, 500],
                 'qos_by_function_ms': config['qos_by_function_ms'],
                 'mixed_protocol': 'cpu65536x100-balanced-slot-orthogonal-v2', 'pattern': 'balanced', 'duration_ns': 10_000_000_000,
                 'requested_rate_rps': rate, 'actual_rate_rps': rate,
                 'workload': workload, 'expected_result': expected, 'requests': requests}
        name = f'cpu-{level}-balanced.json'
        path = root / 'frozen/traces' / name
        plan['traces'][name] = {'file_sha256': write(path, trace),
                                'sha256': sha(canonical(trace)), 'offered': count}
        traces[name] = trace
    for repeat in range(1, 6):
        for level in plan['rates_rps']['cpu']:
            for system in plan['systems']:
                sequence = len(plan['jobs']) + 1
                plan['jobs'].append({'id': f'j{sequence:03d}', 'sequence': sequence,
                    'block': f'r{repeat}-cpu-{level}-balanced', 'repeat': repeat,
                    'kind': 'cpu', 'pattern': 'balanced', 'level': level, 'system': system,
                    'rate_rps': plan['rates_rps']['cpu'][level], 'trace': f'cpu-{level}-balanced.json'})
    write(root / 'frozen/configs/tide-cpu.json', config)
    manifest_sha = write(root / 'frozen/manifest.json', plan)
    (root / 'frozen/manifest.sha256').write_text(manifest_sha + '\n')
    instances = {system: {'nj1': ['nj1-a', 'nj1-b'], 'nj2': ['nj2-a'], 'nj3': ['nj3-a']}
                 for system in plan['systems']}
    smoke = {'ok': True, 'test_only': True, 'native_configs': {node: config for node in ('nj1', 'nj2', 'nj3')},
             'evidence': [{'path': 'smoke-workers.json', 'sha256': write(root / 'smoke-workers.json',
                 [{'system': system, 'instances': nodes} for system, nodes in instances.items()])}]}
    smoke_sha = write(root / 'integrated_smoke.json', smoke)
    write(root / 'formal/readiness-used.json', {'marker_sha256': smoke_sha, 'marker': smoke,
                                             'manifest_sha256': manifest_sha})
    for job in plan['jobs']:
        trace = traces[job['trace']]
        start_unix = 1_800_000_000 + job['sequence'] * 30
        state = {'status': 'completed', 'job': job, 'manifest_sha256': manifest_sha,
                 'start_unix': start_unix, 'remote_terminal': {},
                 'pending_execution': [{'reason': 'mandatory_mixed_campaign_quiescence'}],
                 'summary_sha256': 'a' * 64,
                 'readiness': 'responses_drained', 'completed_ns': round((start_unix + 11) * 1e9)}
        for origin in ('k2', 'k3'):
            selected = [r for r in trace['requests'] if r['origin'] == origin]
            gateway = job['system'] in ('tide', 'ray')
            port = {'bare': 29810, 'tide': 29801, 'ray': 29870, 'kubeedge': 29890}[job['system']]
            urls = [f'http://127.0.0.1:{port}/run'] if gateway else [
                f'http://11.0.4.47:{port}/run', f'http://11.0.4.47:{port+1}/run',
                f'http://11.0.4.48:{port}/run', f'http://11.0.4.49:{port}/run']
            meta = {'schema_version': 2, 'mixed_protocol': 'cpu65536x100-balanced-slot-orthogonal-v2', 'complete': True, 'origin': origin, 'system': job['system'],
                    'route': 'gateway' if gateway else 'direct', 'urls': urls,
                    'trace_sha256': sha(canonical(trace)), 'workload': workload, 'expected_result': expected,
                    'qos_by_function_ms': config['qos_by_function_ms'],
                    'payload_sha256_by_deadline_ms': {str(d): sha(canonical({**workload, 'deadline_ms': d})) for d in (100, 500)},
                    'duration_ns': trace['duration_ns'], 'start_unix': start_unix, 'timeout_s': 10,
                    'window_start_monotonic_ns': 1_000_000_000,
                    'expected_ids': [r['id'] for r in selected],
                    'clock_sample': {'unix_ns': round((start_unix - 5) * 1e9), 'monotonic_ns': 1,
                                     'bracket_ns': 20}, 'clock_mapping_drift_ns': 0,
                    'collection_end_monotonic_ns': 12_000_000_000,
                    'collection_end_unix_ns': round((start_unix + 11) * 1e9)}
            records = []
            for ordinal, request in enumerate(selected):
                deadline = request['deadline_ms']
                sent = meta['window_start_monotonic_ns'] + request['offset_ns']
                node = ('nj1', 'nj1', 'nj2', 'nj3')[(ordinal + (2 if origin == 'k3' else 0)) % 4]
                body = {**workload, 'deadline_ms': deadline}
                response = {'node': node, 'worker_instance': instances[job['system']][node][0],
                            'result': expected, 'service_ns': 10_000_000,
                            'worker_start_ns': 1, 'worker_end_ns': 10_000_001}
                headers = {'x-tide-function': f'cpu-qos{deadline}', 'x-tide-deadline-ms': str(deadline),
                           'x-tide-effective-deadline-ms': str(deadline), 'x-tide-request-id': request['id']}
                records.append({**request, 'url': urls[0] if gateway else urls[(ordinal + (2 if origin == 'k3' else 0)) % 4],
                    'request': body, 'request_sha256': sha(canonical(body)),
                    'transmitted_deadline_ms': deadline, 'expected_function': f'cpu-qos{deadline}',
                    'planned_submit_monotonic_ns': sent,
                    'actual_submit_monotonic_ns': sent, 'return_monotonic_ns': sent + 20_000_000,
                    'http_status': 200, 'error': None, 'response': response,
                    'response_headers': headers if job['system'] == 'tide' else {},
                    'response_body': json.dumps(response)})
            prefix = root / 'formal' / f"{job['id']}-{origin}"
            raw = Path(str(prefix) + '.jsonl')
            raw.write_bytes(b''.join(json.dumps(r).encode() + b'\n' for r in records))
            meta.update(raw_file=raw.name, raw_sha256=sha(raw.read_bytes()))
            write(Path(str(prefix) + '.meta.json'), meta)
            state['remote_terminal'][origin] = {'status': 'completed', 'exit_code': 0,
                'job': job['id'], 'origin': origin, 'manifest_sha256': manifest_sha,
                'finished_ns': meta['collection_end_unix_ns'] + 10}
        quiescence_fixture(root, job, state, manifest_sha)
        write(root / 'formal/state' / f"{job['id']}.json", state)
    return plan


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TARGET.exists():
            spec = importlib.util.spec_from_file_location('mixed_audit', TARGET)
            cls.audit = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.audit)

    def setUp(self):
        self.assertTrue(TARGET.exists(), 'independent mixed audit implementation must exist')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'TEST_ONLY'
        self.plan = fixture(self.root)

    def mutate(self, job=1, origin='k2', change=lambda rows: None, rehash=True):
        prefix = self.root / 'formal' / f'j{job:03d}-{origin}'
        raw = Path(str(prefix) + '.jsonl')
        rows = [json.loads(line) for line in raw.read_text().splitlines()]
        change(rows)
        raw.write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows))
        if rehash:
            path = Path(str(prefix) + '.meta.json')
            meta = json.loads(path.read_text()); meta['raw_sha256'] = sha(raw.read_bytes()); write(path, meta)
        return rows

    def publish(self):
        return self.audit.publish(self.root, self.root / 'audit', HERE)

    def test_complete_campaign_has_120_class_rows_24_cells_and_real_run_ci(self):
        result = self.publish()
        self.assertEqual(result['status'], 'PASS')
        runs = list(csv.DictReader((self.root / 'audit/runs.csv').read_text().splitlines()))
        cells = list(csv.DictReader((self.root / 'audit/cells.csv').read_text().splitlines()))
        self.assertEqual(len(runs), 120); self.assertEqual(len(cells), 24)
        self.assertTrue(all(float(row['offered_rps']) == float(row['rate_rps']) / 2 for row in runs))
        self.assertTrue(all(int(row['n']) == 5 for row in cells))
        self.assertTrue(all(float(row['goodput_rps_ci95_halfwidth']) == 0 for row in cells))
        info = json.loads((self.root / 'audit/audit.json').read_text())
        self.assertTrue(info['test_only']); self.assertEqual(info['origin_runs'], 120)
        self.assertEqual(info['inference']['df'], 4)
        self.assertIn('TEST ONLY', (self.root / 'audit/README.md').read_text())

    def test_independent_kernel_reference(self):
        self.assertEqual(self.audit.cpu_reference(), {'bytes_processed': 6553600, 'checksum': 8843627163163507496})

    def test_missing_duplicate_and_extra_ids_fail_closed(self):
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                       lambda rows: rows[0].update(id='unoffered')):
            with self.subTest(change=change):
                with tempfile.TemporaryDirectory() as directory:
                    other = Path(directory); fixture(other)
                    original = self.root; self.root = other
                    self.mutate(change=change)
                    with self.assertRaisesRegex(ValueError, 'IDs|offer'):
                        self.publish()
                    self.assertFalse((other / 'audit').exists()); self.root = original

    def test_tampered_raw_hash_is_rejected(self):
        self.mutate(change=lambda rows: rows[0].update(error='timeout'), rehash=False)
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            self.publish()

    def test_wrong_class_body_and_missing_tide_headers_are_rejected(self):
        self.mutate(change=lambda rows: rows[0]['request'].update(deadline_ms=500))
        with self.assertRaisesRegex(ValueError, 'frozen offer'):
            self.publish()
        # Restore first cohort, then remove Tide admission evidence.
        fixture(self.root)
        self.mutate(job=2, change=lambda rows: rows[0].update(response_headers={}))
        with self.assertRaisesRegex(ValueError, 'Tide.*header'):
            self.publish()

    def test_deadline_inclusive_window_exclusive_timeout_denominator_and_bad_checksum(self):
        def change(rows):
            rows[0]['return_monotonic_ns'] = rows[0]['actual_submit_monotonic_ns'] + 100_000_000
            rows[2]['return_monotonic_ns'] = 11_000_000_000  # right window edge excluded
            rows[3].update(error='timeout', http_status=None, response=None, response_body=None)
            rows[9]['actual_submit_monotonic_ns'] = 10_500_000_000
            rows[9]['return_monotonic_ns'] = 11_000_000_000  # timely but right window edge
            rows[8]['response']['result'] = {'bytes_processed': 6553600, 'checksum': 0}
            rows[8]['response_body'] = json.dumps(rows[8]['response'])
        self.mutate(change=change)
        # Uncertain execution needs drain evidence, independently of metric denominators.
        state_path = self.root / 'formal/state/j001.json'
        state = json.loads(state_path.read_text())
        state['pending_execution'] = [{'reason': 'mandatory_mixed_campaign_quiescence'},
            {'origin': 'k2', 'id': 'r00000006', 'error': 'timeout', 'http_status': None}]
        quiescence_fixture(self.root, self.plan['jobs'][0], state, state['manifest_sha256'])
        write(state_path, state)
        self.publish()
        rows = list(csv.DictReader((self.root / 'audit/runs.csv').read_text().splitlines()))
        cls100 = next(r for r in rows if r['job_id'] == 'j001' and r['deadline_ms'] == '100')
        cls500 = next(r for r in rows if r['job_id'] == 'j001' and r['deadline_ms'] == '500')
        self.assertEqual(int(cls100['offered']), 10)
        self.assertEqual(int(cls100['failed']), 1)  # bad checksum is never success
        self.assertEqual(int(cls100['cohort_on_time']), 8)  # inclusive first, late window boundary
        self.assertEqual(int(cls100['on_time_in_window']), 8)
        self.assertEqual(float(cls100['cohort_yield']), .8)
        self.assertEqual(int(cls500['offered']), 10); self.assertEqual(int(cls500['failed']), 1)
        self.assertEqual(float(cls500['cohort_yield']), .9)
        self.assertEqual(int(cls500['on_time_in_window']), 8)

    def test_http_failure_with_valid_checksum_is_never_success(self):
        self.mutate(job=1, change=lambda rows: rows[0].update(http_status=400, error=None))
        self.publish()
        rows = list(csv.DictReader((self.root / 'audit/runs.csv').read_text().splitlines()))
        found = next(row for row in rows if row['job_id'] == 'j001' and row['deadline_ms'] == '100')
        self.assertEqual(int(found['failed']), 1)
        self.assertEqual(float(found['cohort_yield']), .9)

    def test_tide_error_response_also_requires_class_headers(self):
        self.mutate(job=2, change=lambda rows: rows[0].update(http_status=400, error='http_error', response_headers={}))
        with self.assertRaisesRegex(ValueError, 'Tide.*header'):
            self.publish()

    def test_quiescence_claim_requires_real_three_samples_spanning_two_seconds(self):
        path = self.root / 'formal/drain-observation/j001.json'
        observation = json.loads(path.read_text())
        observation['samples'] = observation['samples'][:2]
        clearance_path = self.root / 'formal/drain-clearance/j001.json'
        clearance = json.loads(clearance_path.read_text())
        clearance['evidence'][0]['sha256'] = write(path, observation)
        state_path = self.root / 'formal/state/j001.json'
        state = json.loads(state_path.read_text())
        state['drain_clearance_sha256'] = write(clearance_path, clearance); write(state_path, state)
        with self.assertRaisesRegex(ValueError, 'quiescence|Quiescence'):
            self.publish()

    def test_changed_worker_cpu_during_quiescence_is_rejected(self):
        path = self.root / 'formal/drain-observation/j001.json'
        observation = json.loads(path.read_text())
        observation['samples'][-1]['nodes']['nj1']['workers'][0]['cpu_ticks'] += 1
        clearance_path = self.root / 'formal/drain-clearance/j001.json'
        clearance = json.loads(clearance_path.read_text())
        clearance['evidence'][0]['sha256'] = write(path, observation)
        state_path = self.root / 'formal/state/j001.json'
        state = json.loads(state_path.read_text())
        state['drain_clearance_sha256'] = write(clearance_path, clearance); write(state_path, state)
        with self.assertRaisesRegex(ValueError, 'quiescence|Quiescence'):
            self.publish()

    def test_executing_auditor_must_match_frozen_hash_even_with_alternate_source_dir(self):
        source = self.root / 'TEST_ONLY-alternate-source'
        source.mkdir()
        raw = b'# TEST ONLY fake auditor\n'
        (source / TARGET.name).write_bytes(raw)
        self.plan['source_sha256'] = {TARGET.name: sha(raw)}
        wanted = write(self.root / 'frozen/manifest.json', self.plan)
        (self.root / 'frozen/manifest.sha256').write_text(wanted + '\n')
        with self.assertRaisesRegex(ValueError, 'Executing auditor'):
            self.audit.publish(self.root, self.root / 'audit', source)

    def test_all_three_readiness_nodes_must_have_frozen_class_config(self):
        path = self.root / 'integrated_smoke.json'
        marker = json.loads(path.read_text())
        marker['native_configs']['nj3']['qos_by_function_ms']['cpu-qos100'] = 500
        wanted = write(path, marker)
        used_path = self.root / 'formal/readiness-used.json'
        used = json.loads(used_path.read_text()); used.update(marker=marker, marker_sha256=wanted)
        write(used_path, used)
        with self.assertRaisesRegex(ValueError, 'native config'):
            self.publish()

    def test_ci_uses_five_run_values(self):
        result = self.audit.ci5([1, 2, 3, 4, 5])
        self.assertAlmostEqual(result['mean'], 3)
        self.assertAlmostEqual(result['ci95_halfwidth'], 2.7764451051977987 / 2 ** .5)
        with self.assertRaises(ValueError):
            self.audit.ci5([1, 2, 3, 4])


if __name__ == '__main__':
    unittest.main()
