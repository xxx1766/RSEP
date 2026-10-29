import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def matrix():
    return importlib.import_module("fig6_cluster_matrix")


def calibrations():
    work = {kind: {"kind": kind, "bytes": 8, "iterations": 1} for kind in ("cpu", "memory", "io")}
    result = {}
    for node in ("nj1", "nj2", "nj3"):
        for system in ("bare", "tide"):
            result[f"{node}-{system}"] = {"node": node, "system": system, "workloads": work,
                "summary": {kind: {"median_service_ms": 100, "median_path_ms": 120.2, "samples": 20}
                            for kind in work}}
    return result


class MatrixTests(unittest.TestCase):
    def test_explicit_rates_keep_calibration_design_and_offer_exact_requested_load(self):
        m = matrix()
        requested = {'cpu': {'low': 100, 'high': 300}, 'memory': {'low': 20, 'high': 50},
                     'io': {'low': 40, 'high': 100}}
        inputs = calibrations()
        legacy, _ = m.build_plan(inputs, 'fixture')
        plan, traces = m.build_plan(inputs, 'fixture', rates_override=requested)
        self.assertEqual(plan['rates_rps'], requested)
        self.assertEqual(plan['rate_selection'], {'mode': 'explicit', 'rates_rps': requested})
        self.assertIn('rate_selection.rates_rps', plan['rate_rule'])
        self.assertNotIn('rate_quantization', plan)
        self.assertEqual(len(plan['jobs']), 360)
        self.assertEqual(len(traces), 18)
        self.assertEqual(sum(len(traces[j['trace']]['requests']) for j in plan['jobs']), 366000)
        for key in ('seed', 'order_rule', 'capacity_rps', 'tide_configs', 'workloads',
                    'duration_s', 'deadline_ms', 'timeout_s', 'systems', 'origins', 'slots'):
            self.assertEqual(plan[key], legacy[key], key)
        for old, new in zip(legacy['jobs'], plan['jobs']):
            self.assertEqual({k: v for k, v in old.items() if k != 'rate_rps'},
                             {k: v for k, v in new.items() if k != 'rate_rps'})
        for name, trace in traces.items():
            kind, level, _ = name.split('-')
            self.assertEqual(len(trace['requests']), 10 * requested[kind][level])
            self.assertTrue(all(0 <= r['offset_ns'] < 10_000_000_000 for r in trace['requests']))
        requested['cpu']['low'] = 1
        self.assertEqual(plan['rates_rps']['cpu']['low'], 100)
        self.assertEqual(plan['rate_selection']['rates_rps']['cpu']['low'], 100)

    def test_explicit_rates_reject_malformed_maps_and_quantization(self):
        m = matrix()
        valid = {'cpu': {'low': 100, 'high': 300}, 'memory': {'low': 20, 'high': 50},
                 'io': {'low': 40, 'high': 100}}
        invalid = [[], {}, {**valid, 'gpu': {'low': 1, 'high': 2}}]
        for mutate in (lambda p: p.pop('io'), lambda p: p['cpu'].pop('high'),
                       lambda p: p['cpu'].update(extra=1), lambda p: p.update(cpu=[])):
            changed = copy.deepcopy(valid); mutate(changed); invalid.append(changed)
        for value in (True, False, 100.0, '100', None, 0, -1, 300, 301):
            changed = copy.deepcopy(valid); changed['cpu']['low'] = value; invalid.append(changed)
        for value in (True, 300.0, 0, -1, 100, 99):
            changed = copy.deepcopy(valid); changed['cpu']['high'] = value; invalid.append(changed)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.build_plan(calibrations(), 'fixture', rates_override=value)
        with self.assertRaisesRegex(ValueError, 'mutually exclusive'):
            m.build_plan(calibrations(), 'fixture', rates_override=valid, rate_multiple=5)

    def test_rates_json_cli_is_freeze_only_and_excludes_nearest_five(self):
        script = Path(__file__).with_name('fig6_cluster_matrix.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'untouched'
            for options in (['run', '--rates-json', 'unused.json'],
                            ['collect', '--rates-json', 'unused.json'],
                            ['freeze', '--rates-json', 'unused.json', '--rate-multiple', '5']):
                result = subprocess.run([sys.executable, str(script), *options, '--root', str(root)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('only valid with freeze' if options[0] != 'freeze' else 'mutually exclusive', result.stderr)
                self.assertFalse(root.exists())

    def test_freeze_cli_reads_and_records_explicit_rates_json(self):
        script = Path(__file__).with_name('fig6_cluster_matrix.py')
        original = Path(__file__).resolve().parents[2] / 'results-fig6-cluster-20261015/frozen/calibration'
        requested = {'cpu': {'low': 100, 'high': 300}, 'memory': {'low': 20, 'high': 50},
                     'io': {'low': 40, 'high': 100}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'calibration').mkdir()
            for p in original.glob('*.json'): (root / 'calibration' / p.name).write_bytes(p.read_bytes())
            rates_path = root / 'requested.json'; rates_path.write_text(json.dumps(requested))
            result = subprocess.run([sys.executable, str(script), 'freeze', '--root', str(root),
                                     '--rates-json', str(rates_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan, _ = matrix().load_plan(root)
            self.assertEqual(plan['rates_rps'], requested)
            self.assertEqual(plan['rate_selection'], {'mode': 'explicit', 'rates_rps': requested})
            a = importlib.import_module('fig6_cluster_aggregate')
            verified, _, traces = a.frozen_plan(a.Evidence(root), script.parent)
            self.assertEqual(verified, plan)
            self.assertEqual(len(traces), 18)
            self.assertEqual(sum(len(traces[j['trace']]['requests']) for j in plan['jobs']), 366000)

    def test_rates_json_cli_rejects_null_malformed_or_missing_input_before_creating_root(self):
        script = Path(__file__).with_name('fig6_cluster_matrix.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'untouched'; rates_path = Path(directory) / 'rates.json'
            for raw in ('null', '[]', '{}', '{',
                        '{"cpu":{"low":true,"high":300},"memory":{"low":20,"high":50},"io":{"low":40,"high":100}}'):
                rates_path.write_text(raw)
                result = subprocess.run([sys.executable, str(script), 'freeze', '--root', str(root),
                                         '--rates-json', str(rates_path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('invalid --rates-json', result.stderr)
                self.assertFalse(root.exists())
            rates_path.unlink()
            result = subprocess.run([sys.executable, str(script), 'freeze', '--root', str(root),
                                     '--rates-json', str(rates_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('invalid --rates-json', result.stderr)
            self.assertFalse(root.exists())

    def test_freeze_records_exact_calibration_reuse_and_rejects_changed_input(self):
        m = matrix()
        old = Path(__file__).resolve().parents[2] / 'results-fig6-cluster-20261015/frozen'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'calibration').mkdir()
            for p in (old/'calibration').glob('*.json'): (root/'calibration'/p.name).write_bytes(p.read_bytes())
            m.freeze(root, rate_multiple=5, calibration_reuse_manifest=old/'manifest.json')
            plan, _ = m.load_plan(root)
            self.assertEqual(plan['calibration_reuse']['manifest_sha256'],m.digest_file(old/'manifest.json'))
            self.assertEqual(plan['calibration_reuse']['calibration_sha256'],plan['calibration_sha256'])
            other = root/'other';(other/'calibration').mkdir(parents=True)
            for p in (old/'calibration').glob('*.json'): (other/'calibration'/p.name).write_bytes(p.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'reused calibration'):
                m.freeze(other, rate_multiple=5, calibration_reuse_manifest=old/'manifest.json')
            self.assertFalse((other/'frozen').exists())

    def test_nearest_five_real_calibration_rates_counts_and_legacy_plan(self):
        m = matrix()
        old_root = Path(__file__).resolve().parents[2] / 'results-fig6-cluster-20261015'
        inputs = {p.stem: json.loads(p.read_text()) for p in (old_root / 'frozen/calibration').glob('*.json')}
        legacy = json.loads((old_root / 'frozen/manifest.json').read_text())
        original, original_traces = m.build_plan(inputs, legacy['campaign'])
        for key, value in original.items():
            self.assertEqual(value, legacy[key], key)
        explicit, _ = m.build_plan(inputs, legacy['campaign'], rate_multiple=1)
        self.assertEqual(original, explicit)
        rounded, traces = m.build_plan(inputs, legacy['campaign'], rate_multiple=5)
        self.assertEqual(rounded['rates_rps'], {'cpu': {'low': 115, 'high': 305},
                         'memory': {'low': 20, 'high': 45}, 'io': {'low': 40, 'high': 110}})
        self.assertEqual(rounded['rate_quantization'], {'multiple': 5, 'rounding': 'half_up',
                         'input': 'calibration_integer_rate', 'minimum': 5})
        self.assertIn('half_up', rounded['rate_rule'])
        self.assertEqual(len(rounded['jobs']), 360)
        self.assertEqual(len(traces), 18)
        self.assertEqual(sum(len(traces[j['trace']]['requests']) for j in rounded['jobs']), 381000)
        self.assertEqual(sum(len(original_traces[j['trace']]['requests']) for j in original['jobs']), 381600)
        for old, new in zip(original['jobs'], rounded['jobs']):
            self.assertEqual({k:v for k,v in old.items() if k!='rate_rps'},
                             {k:v for k,v in new.items() if k!='rate_rps'})
        for name, trace in traces.items():
            kind, level, _ = name.split('-')
            self.assertEqual(len(trace['requests']), 10 * rounded['rates_rps'][kind][level])
            self.assertTrue(all(0 <= r['offset_ns'] < 10_000_000_000 for r in trace['requests']))

    def test_rate_quantization_is_after_integer_selection_and_positive(self):
        m = matrix()
        inputs = calibrations()
        # Python round selects 12 from 12.5 before quantization to 10.
        # Quantizing the floating capacity product directly would give 15.
        for value in inputs.values():
            for summary in value['summary'].values():
                summary['median_service_ms'] = 256  # exact capacity 15.625; high factor gives exactly 12.5
        plan, _ = m.build_plan(inputs, 'fixture', rate_multiple=5)
        self.assertEqual(plan['rates_rps']['cpu']['high'], 10)
        for value in inputs.values():
            for summary in value['summary'].values(): summary['median_service_ms'] = 100000
        plan, _ = m.build_plan(inputs, 'fixture', rate_multiple=5)
        self.assertEqual(plan['rates_rps']['cpu'], {'low': 5, 'high': 5})
        for invalid in (0, 2, -5, True, 5.0):
            with self.assertRaises(ValueError): m.build_plan(inputs, 'fixture', rate_multiple=invalid)

    def test_uncertain_completion_requires_drain_but_late_success_does_not(self):
        m = matrix()
        records = [{"id": "timeout", "error": "timeout", "http_status": None},
                   {"id": "gateway", "error": "http_error", "http_status": 504},
                   {"id": "unavailable", "error": "http_error", "http_status": 503},
                   {"id": "reset", "error": "ConnectionResetError", "http_status": None},
                   {"id": "late", "error": None, "http_status": 200,
                    "actual_submit_monotonic_ns": 0, "return_monotonic_ns": 900000000},
                   {"id": "rejected-input", "error": "http_error", "http_status": 400}]
        risks = m.pending_execution_risks([({"origin": "k2"}, records)])
        self.assertEqual({item["id"] for item in risks}, {"timeout", "gateway", "unavailable", "reset"})

    def test_drain_gate_cannot_be_bypassed_by_resume_or_unsubstantiated_clearance(self):
        m = matrix()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = {"id": "fixture"}
            state = {"status": "completed", "completed_ns": 100, "summary_sha256": "summary",
                     "readiness": "drain_required", "pending_execution": [{"id": "timed-out"}]}
            with self.assertRaisesRegex(RuntimeError, "drain"):
                m.enforce_drain_gate(root, job, state)
            evidence = root / "idle-audit.json"
            evidence.write_text('{"owned_workers_idle":true}')
            clearance = {"job": "fixture", "summary_sha256": "summary", "idle_verified": True,
                         "verified_ns": 99, "evidence": [{"path": "idle-audit.json", "sha256": m.digest_file(evidence)}]}
            path = root / "formal" / "drain-clearance" / "fixture.json"
            m.write_json(path, clearance)
            with self.assertRaises(ValueError):
                m.enforce_drain_gate(root, job, state)
            clearance["verified_ns"] = 101
            m.write_json(path, clearance)
            m.enforce_drain_gate(root, job, state)
            self.assertEqual(state["readiness"], "drain_verified")
            evidence.write_text("changed")
            with self.assertRaises(ValueError):
                m.enforce_drain_gate(root, job, state)

    def test_freeze_calibration_raw_validation_and_trace_integrity(self):
        m = matrix()
        inputs = calibrations()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "calibration").mkdir()
            for key, value in inputs.items():
                value["rows"] = [{"kind": kind, "sample": sample, "elapsed_ns": 120200000,
                                  "response": {"service_ns": 100000000, "node": value["node"],
                                               "result": m.expected_result(value["workloads"][kind])}}
                                 for kind in ("cpu", "memory", "io") for sample in range(20)]
                (root / "calibration" / (key + ".json")).write_text(json.dumps(value))
            m.freeze(root)
            plan, _ = m.load_plan(root)
            self.assertEqual(len(plan["jobs"]), 360)
            with self.assertRaises(RuntimeError):
                m.freeze(root)
            path = next((root / "frozen" / "traces").glob("*.json"))
            path.write_text(path.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "trace changed"):
                m.load_plan(root)

    def test_remote_wrapper_records_terminal_status_and_never_relaunches(self):
        m = matrix()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace.json"
            trace.write_text("{}")
            marker = root / "execution_count"
            child = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
            spec = {"job": "fixture", "origin": "k2", "prefix": str(root / "job-k2"),
                    "manifest_sha256": "fixture-hash", "root": str(root),
                    "trace_path": str(trace), "trace_file_sha256": m.digest_file(trace),
                    "source_sha256": {}, "command": [sys.executable, "-c", child, str(marker)]}
            command = [sys.executable, "-c", m.REMOTE_RUN, json.dumps(spec)]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            state = json.loads((root / "job-k2.state.json").read_text())
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["exit_code"], 0)
            self.assertIn("driver_pid", state)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("never relaunch", second.stderr)
            self.assertEqual(marker.read_text(), "x")

    def test_frozen_matrix_counts_pairing_rates_and_determinism(self):
        m = matrix()
        plan, traces = m.build_plan(calibrations(), "fixture")
        again, again_traces = m.build_plan(calibrations(), "fixture")
        self.assertEqual(plan, again)
        self.assertEqual(traces, again_traces)
        self.assertEqual(len(plan["jobs"]), 360)
        self.assertEqual(len(traces), 18)
        self.assertEqual(plan["capacity_rps"]["cpu"], 40)
        self.assertEqual(plan["rates_rps"]["cpu"], {"low": 12, "high": 32})
        for index in range(0, 360, 4):
            block = plan["jobs"][index:index + 4]
            self.assertEqual({job["system"] for job in block}, {"bare", "tide", "ray", "kubeedge"})
            self.assertEqual(len({job["trace"] for job in block}), 1)
            self.assertEqual(len({job["block"] for job in block}), 1)
        self.assertEqual({job["repeat"] for job in plan["jobs"]}, {1, 2, 3, 4, 5})
        self.assertEqual(plan["tide_configs"]["cpu"]["node_execution_ms"]["nj1"], 121)

    def test_rates_depend_only_on_bare_service_calibration(self):
        m = matrix()
        inputs = calibrations()
        original, _ = m.build_plan(inputs, "fixture")
        for key, value in inputs.items():
            if key.endswith("tide"):
                for summary in value["summary"].values():
                    summary["median_service_ms"] = .001
                    summary["median_path_ms"] = 999
        changed, _ = m.build_plan(inputs, "fixture")
        self.assertEqual(original["rates_rps"], changed["rates_rps"])
        self.assertNotEqual(original["tide_configs"], changed["tide_configs"])

    def test_incomplete_or_ambiguous_remote_state_never_allows_relaunch(self):
        m = matrix()
        for remote in ({}, {"k2": {"status": "running"}},
                       {"k2": {"status": "completed", "exit_code": 0},
                        "k3": {"status": "running"}}):
            with self.assertRaises(RuntimeError):
                m.require_terminal_pair(remote)
        self.assertTrue(m.require_terminal_pair({origin: {"status": "completed", "exit_code": 0}
                                                for origin in ("k2", "k3")}))
        with self.assertRaises(RuntimeError):
            m.require_terminal_pair({origin: {"status": "completed", "exit_code": 1}
                                     for origin in ("k2", "k3")})

    def test_pair_audit_checks_exact_trace_not_just_self_reported_ids(self):
        m = matrix()
        plan, traces = m.build_plan(calibrations(), "fixture")
        job = plan["jobs"][0]
        trace = traces[job["trace"]]
        pairs = []
        for origin in ("k2", "k3"):
            requests = [r for r in trace["requests"] if r["origin"] == origin]
            start = 10000000000 if origin == "k2" else 20000000000
            meta = {"origin": origin, "system": job["system"], "start_unix": 1000,
                    "duration_ns": trace["duration_ns"], "trace_sha256": m.digest_json(trace),
                    "window_start_monotonic_ns": start, "expected_ids": [r["id"] for r in requests],
                    "expected_result": trace["expected_result"], "workload": trace["workload"]}
            records = [{"id": r["id"], "origin": origin, "request": trace["workload"],
                        "offset_ns": r["offset_ns"], "deadline_ms": r["deadline_ms"],
                        "planned_submit_monotonic_ns": start + r["offset_ns"],
                        "actual_submit_monotonic_ns": start + r["offset_ns"],
                        "return_monotonic_ns": start + r["offset_ns"] + 1,
                        "http_status": 200, "error": None, "response": {"result": trace["expected_result"]}}
                       for r in requests]
            pairs.append((meta, records))
        result = m.audit_pair(job, trace, pairs)
        self.assertEqual(result["offered"], len(trace["requests"]))
        changed = copy.deepcopy(pairs)
        changed[0][0]["expected_ids"].pop()
        changed[0][1].pop()
        with self.assertRaises(ValueError):
            m.audit_pair(job, trace, changed)


if __name__ == "__main__":
    unittest.main()
