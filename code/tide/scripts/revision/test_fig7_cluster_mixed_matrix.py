import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest


def module():
    return importlib.import_module('fig7_cluster_mixed_matrix')


def calibrations():
    workload = {'cpu': {'kind': 'cpu', 'bytes': 65536, 'iterations': 100}}
    return {f'{node}-{system}': {'node': node, 'system': system, 'workloads': copy.deepcopy(workload),
        'summary': {'cpu': {'median_service_ms': 100, 'median_path_ms': 120.2, 'samples': 1}}}
        for node in ('nj1', 'nj2', 'nj3') for system in ('bare', 'tide')}


class MixedMatrixTests(unittest.TestCase):
    def test_frozen_design_has_60_paired_jobs_and_three_capacity_rates(self):
        m = module(); plan, traces = m.build_plan(calibrations(), 'f7-fixture')
        self.assertEqual(plan['seed'], 20261016)
        self.assertEqual(plan['capacity_rps'], {'cpu': 40})
        self.assertEqual(plan['rates_rps'], {'cpu': {'low': 12, 'high': 32, 'overload': 48}})
        self.assertEqual(plan['load_factors'], {'low': .3, 'high': .8, 'overload': 1.2})
        self.assertEqual(len(plan['jobs']), 60)
        self.assertEqual(len(traces), 3)
        self.assertEqual(plan['slots'], {'nj1': 2, 'nj2': 1, 'nj3': 1})
        self.assertEqual(plan['tide_configs']['cpu']['qos_by_function_ms'], {'cpu-qos100': 100, 'cpu-qos500': 500})
        for start in range(0, 60, 4):
            block = plan['jobs'][start:start + 4]
            self.assertEqual({j['system'] for j in block}, set(m.SYSTEMS))
            self.assertEqual(len({j['block'] for j in block}), 1)
        self.assertEqual(m.build_plan(calibrations(), 'f7-fixture'), (plan, traces))
        for trace in traces.values():
            self.assertEqual(trace['duration_ns'], 10_000_000_000)
            for origin in m.ORIGINS:
                cohort = [r for r in trace['requests'] if r['origin'] == origin]
                self.assertLessEqual(abs(sum(r['deadline_ms'] == 100 for r in cohort) - sum(r['deadline_ms'] == 500 for r in cohort)), 1)

    def test_rejects_non_cpu_kernel_and_invalid_service_measurements(self):
        for mutation in (lambda p: p['nj1-bare']['workloads']['cpu'].update(bytes=8),
                         lambda p: p['nj2-bare']['summary']['cpu'].update(median_service_ms=0),
                         lambda p: p['nj3-tide']['summary']['cpu'].update(median_path_ms=float('nan'))):
            inputs = calibrations(); mutation(inputs)
            with self.assertRaises(ValueError): module().build_plan(inputs, 'f7-fixture')

    def test_ambiguous_attempts_never_relaunch_and_every_completed_job_needs_quiescence(self):
        m = module()
        for states in ({}, {'k2': {'status': 'running'}},
                       {'k2': {'status': 'completed', 'exit_code': 0}, 'k3': {'status': 'completed', 'exit_code': 1}}):
            with self.assertRaises(RuntimeError): m.require_terminal_pair(states)
        self.assertTrue(m.require_terminal_pair({o: {'status': 'completed', 'exit_code': 0} for o in m.ORIGINS}))
        self.assertEqual(m.pending_execution_risks([])[0]['reason'], 'mandatory_mixed_campaign_quiescence')
        with tempfile.TemporaryDirectory() as directory:
            job = {'id': 'f7-fixture-j001'}
            with self.assertRaisesRegex(RuntimeError, 'drain_required'):
                m.enforce_drain_gate(Path(directory), job, {'pending_execution': m.pending_execution_risks([])})

    def test_run_rejects_readiness_without_both_classes_on_every_node(self):
        m = module(); plan, _ = m.build_plan(calibrations(), 'f7-fixture')
        with self.assertRaisesRegex(ValueError, 'native_configs'):
            m.validate_readiness({'ok': True}, plan)
        ready = {'ok': True, 'native_configs': {n: copy.deepcopy(plan['tide_configs']['cpu']) for n in m.HOSTS}}
        m.validate_readiness(ready, plan)
        ready['native_configs']['nj3']['qos_by_function_ms'].pop('cpu-qos100')
        with self.assertRaisesRegex(ValueError, 'native_configs'):
            m.validate_readiness(ready, plan)

class MixedEvidenceTests(unittest.TestCase):
    def pair(self, system='bare'):
        m = module()
        from fig7_cluster_mixed_driver import generate_mixed_trace
        trace = generate_mixed_trace('balanced', 10, 2, m.WORKLOAD)
        pairs = []
        for origin in m.ORIGINS:
            requests = [r for r in trace['requests'] if r['origin'] == origin]
            meta = {'origin': origin, 'system': system, 'expected_ids': [r['id'] for r in requests],
                    'trace_sha256': m.digest_json(trace), 'duration_ns': trace['duration_ns'],
                    'expected_result': trace['expected_result'], 'workload': trace['workload'],
                    'start_unix': 1, 'window_start_monotonic_ns': 100000000000}
            records = []
            for req in requests:
                body = {**trace['workload'], 'deadline_ms': req['deadline_ms']}
                planned = meta['window_start_monotonic_ns'] + req['offset_ns']
                headers = {'x-tide-function': f"cpu-qos{req['deadline_ms']}",
                           'x-tide-deadline-ms': str(req['deadline_ms']),
                           'x-tide-effective-deadline-ms': str(req['deadline_ms']), 'x-tide-request-id': req['id']}
                records.append({**req, 'request': body, 'request_sha256': m.digest_json(body),
                    'transmitted_deadline_ms': req['deadline_ms'], 'expected_function': f"cpu-qos{req['deadline_ms']}",
                    'planned_submit_monotonic_ns': planned, 'actual_submit_monotonic_ns': planned,
                    'return_monotonic_ns': planned + 1000000, 'http_status': 200, 'error': None,
                    'response': {'result': trace['expected_result'], 'node': 'nj1', 'service_ns': 500000},
                    'response_headers': headers if system == 'tide' else {}})
            pairs.append((meta, records))
        return {'system': system}, trace, pairs

    def test_pair_checks_class_bearing_payload_and_native_headers_only_for_tide(self):
        m = module()
        for system in ('bare', 'tide', 'ray', 'kubeedge'):
            job, trace, pairs = self.pair(system)
            self.assertEqual(m.audit_pair(job, trace, pairs)['offered'], 20)
        job, trace, pairs = self.pair('tide')
        pairs[0][1][0]['response_headers']['x-tide-effective-deadline-ms'] = '500'
        with self.assertRaisesRegex(ValueError, 'class'): m.audit_pair(job, trace, pairs)

    def test_saved_job_state_must_identify_frozen_job(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); job = {'id': 'f7-fixture-j001', 'system': 'tide'}
            m.write_json(m.state_path(root, job), {'job': {**job, 'system': 'bare'}, 'status': 'planned', 'launches': {}})
            with self.assertRaisesRegex(ValueError, 'frozen'): m.load_job_state(root, job)

    def test_freeze_refuses_any_existing_formal_attempt_even_without_completion(self):
        m = module()
        for relative in ('formal/f7-fixture-j001-k2.jsonl.partial', 'formal/state/f7-fixture-j001.json'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); path = root/relative; path.parent.mkdir(parents=True)
                path.write_text('{}')
                with self.assertRaisesRegex(RuntimeError, 'formal'): m.freeze(root)

    def test_execute_refuses_prior_launch_intent_without_touching_transport(self):
        with self.assertRaisesRegex(RuntimeError, 'previously attempted'):
            module().execute_job(Path('/unused'), {}, 'unused', {'id':'f7-fixture-j001'},
                {'status': 'planned', 'launches': {'k2': {'intent_ns':1}}})

    def test_drain_gate_requires_real_passed_samples_and_worker_quiescence(self):
        m = module(); job = {'id': 'f7-fixture-j001', 'system': 'tide'}
        state = {'job': job, 'status': 'completed', 'completed_ns': 10, 'summary_sha256': 'summary',
                 'pending_execution': m.pending_execution_risks([]), 'readiness': 'drain_required'}
        samples = []
        for second in range(3):
            nodes = {}
            for node, count in (('nj1',2), ('nj2',1), ('nj3',1)):
                workers = [{'pid': i+1, 'start_ticks': '1', 'cpu_ticks': 0,
                    'io': {'read_bytes': 0, 'write_bytes': 0}, 'threads': [{'state':'S'}]} for i in range(count)]
                nodes[node] = {'workers': workers, 'identity_errors': [], 'active_load': [],
                    'nonterminal_states': [], 'missing_target_states': [], 'sockets': [], 'locks_available': True}
            samples.append({'monotonic_ns': (second+1)*1000000000, 'finished_monotonic_ns': (second+1)*1000000000,
                            'time_ns': 20+second, 'nodes': nodes})
        observation = {'criterion': 'bounded observed quiescence', 'passed': True, 'verified_ns': 30,
            'accepted_first_sample': 0, 'samples': samples, 'last_window_errors': [],
            'audit': {'job': job, 'state': state}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); evidence = root/'formal/drain-observation/quiet.json'
            clearance = {'job': job['id'], 'summary_sha256': 'summary', 'idle_verified': True, 'verified_ns': 30,
                         'evidence': [{'path': str(evidence.relative_to(root)), 'sha256': ''}]}
            def publish(value):
                m.write_json(evidence, value); clearance['evidence'][0]['sha256'] = m.digest_file(evidence)
                m.write_json(root/'formal/drain-clearance'/(job['id']+'.json'), clearance)
            for broken in ({'note':'operator believes idle'}, {**observation, 'passed':False},
                           {**observation, 'samples':samples[:2]}):
                publish(broken)
                with self.assertRaisesRegex(ValueError, 'quiescence'): m.enforce_drain_gate(root, job, copy.deepcopy(state))
            busy = copy.deepcopy(observation); busy['samples'][-1]['nodes']['nj1']['workers'][0]['cpu_ticks'] = 1
            publish(busy)
            with self.assertRaisesRegex(ValueError, 'quiescence'): m.enforce_drain_gate(root, job, copy.deepcopy(state))
            publish(observation); current = copy.deepcopy(state); m.enforce_drain_gate(root, job, current)
            self.assertEqual(current['readiness'], 'drain_verified')

    def test_freeze_validates_cpu_raw_calibration_and_hash_binds_frozen_inputs(self):
        m = module(); inputs = calibrations()
        for obj in inputs.values():
            response = {'result': m.expected_result(m.WORKLOAD), 'node': obj['node'], 'service_ns': 100000000}
            obj['rows'] = [{'kind': 'cpu', 'sample': 0, 'response': response, 'elapsed_ns': 120200000}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, obj in inputs.items(): m.write_json(root/'calibration'/(name+'.json'), obj)
            m.freeze(root); plan, _ = m.load_plan(root)
            self.assertTrue(plan['campaign'].startswith('f7-'))
            self.assertEqual(set(plan['source_sha256']), set(m.SOURCES))
            self.assertEqual(len(plan['jobs']), 60)
            with self.assertRaises(RuntimeError): m.freeze(root)
            path = root/'frozen/traces'/plan['jobs'][0]['trace']; path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError, 'trace changed'): m.load_plan(root)

if __name__ == '__main__': unittest.main()
