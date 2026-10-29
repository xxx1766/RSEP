import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('analyze', SCRIPTS / 'analyze.py')
analyze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyze)


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'raw.csv'

    def write(self, statuses, latencies=None, qos=None):
        latencies = latencies or [100] * len(statuses)
        import csv
        with self.path.open('w') as f:
            w = csv.writer(f)
            w.writerow(['req_id', 't_submit_ns', 't_reply_ns', 'latency_ms', 'status'] + (['qos_ms'] if qos else []))
            for i, (status, latency) in enumerate(zip(statuses, latencies)):
                w.writerow([i + 1, i * 10**9, i * 10**9 + int(latency * 10**6), latency, status] + ([qos[i]] if qos else []))

    def test_warmup_is_selected_before_outcomes(self):
        # One warmup success; remaining: six successes, one timeout, two failures.
        self.write([200, 200, 500, 200, 504, 200, 200, 500, 200, 200],
                   [100, 100, 100, 800, 100, 100, 900, 100, 100, 100])
        a = analyze._agg_one_csv(self.path, .1)
        self.assertAlmostEqual(a['drop_pct'], 100 * 3 / 9)
        self.assertEqual(a['n_eligible'], 9)
        self.assertEqual(a['n_ok'], 6)
        self.assertAlmostEqual(a['throughput'], 6 / 8.1)
        self.assertTrue(math.isnan(a['ontime_goodput']))

    def test_manifest_and_explicit_qos(self):
        self.write([200, 200, 500, 200, 504, 200, 200, 500, 200, 200],
                   [100, 100, 100, 800, 100, 100, 900, 100, 100, 100])
        (self.path.parent / 'manifest.json').write_text(json.dumps({
            'measurement_start_ns': 10**9, 'measurement_end_ns': 10 * 10**9}))
        a = analyze._agg_one_csv(self.path, 0, qos_ms=500)
        self.assertEqual((a['n_eligible'], a['n_ontime'], a['n_late'], a['n_timeout'], a['n_failure']), (9, 4, 2, 1, 2))
        self.assertAlmostEqual(a['throughput'], 6 / 9)
        self.assertAlmostEqual(a['ontime_goodput'], 4 / 9)
        self.assertEqual(a['window_source'], 'manifest')

    def test_failed_tail_stays_in_duration(self):
        self.write([200, 500], [100, 4000])
        a = analyze._agg_one_csv(self.path, 0)
        self.assertAlmostEqual(a['throughput'], 1 / 5)

    def test_per_row_qos(self):
        self.write([200, 200], [100, 200], qos=[100, 100])
        a = analyze._agg_one_csv(self.path, 0)
        self.assertEqual((a['n_ontime'], a['n_late']), (1, 1))
        self.assertAlmostEqual(a['ontime_goodput'], 1 / 1.2)

    def test_empty_single_and_failed(self):
        self.write([])
        a = analyze._agg_one_csv(self.path, 0)
        self.assertEqual(a['n_eligible'], 0)
        self.assertTrue(math.isnan(a['throughput']))
        self.write([200], [0])
        self.assertTrue(math.isnan(analyze._agg_one_csv(self.path, 0)['throughput']))
        self.write([500, 504])
        a = analyze._agg_one_csv(self.path, 0)
        self.assertEqual(a['throughput'], 0)
        self.assertEqual(a['drop_pct'], 100)
        self.assertTrue(math.isnan(a['avg_ms']))

    def test_manifest_rates_exclude_drained_successes(self):
        self.write([200, 200], [100, 1500], qos=[2000, 2000])
        (self.path.parent / 'manifest.json').write_text(json.dumps({
            'measurement_start_ns': 0, 'measurement_end_ns': 2 * 10**9}))
        a = analyze._agg_one_csv(self.path, 0)
        self.assertEqual(a['n_ok'], 2)
        self.assertEqual(a['n_ontime'], 2)
        self.assertEqual(a['drained_successes'], 1)
        self.assertEqual(a['throughput'], .5)
        self.assertEqual(a['ontime_goodput'], .5)
        self.assertEqual(a['success_yield'], 1)
        self.assertEqual(a['on_time_yield'], 1)

    def test_status_zero_is_not_assumed_timeout(self):
        self.write([0, 504])
        a = analyze._agg_one_csv(self.path, 0)
        self.assertEqual((a['n_timeout'], a['n_failure']), (1, 1))

    def test_completed_manifest_integrity_checked_during_analysis(self):
        import hashlib
        self.write([200, 200])
        manifest = self.path.parent / 'manifest.json'
        manifest.write_text(json.dumps(dict(schema_version=1, status='completed',
            exit_code=0, expected_count=2, raw_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest())))
        self.assertIsNotNone(analyze._agg_one_csv(self.path, 0))
        self.write([200])
        self.assertIsNone(analyze._agg_one_csv(self.path, 0))

    def test_invalid_schema_and_duplicate_ids_rejected(self):
        self.path.write_text('req_id,status\n1,200\n')
        self.assertIsNone(analyze._agg_one_csv(self.path, 0))
        self.write([200, 200])
        self.path.write_text(self.path.read_text().replace('2,1000000000', '1,1000000000'))
        self.assertIsNone(analyze._agg_one_csv(self.path, 0))


class MatrixTests(unittest.TestCase):
    def test_failed_command_and_truncated_success_are_not_completed(self):
        for mode, expected_status in [('fail', 'failed'), ('short', 'invalid'), ('ok', 'completed')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                binary = root / 'fake-client'
                binary.write_text("#!/bin/bash\nfor x in \"$@\"; do case $x in -outfile=*) out=${x#*=};; esac; done\n"
                                  + ("exit 7\n" if mode == 'fail' else
                                     "echo 'req_id,t_submit_ns,t_reply_ns,latency_ms,status' > \"$out\"\n"
                                     + ("echo '1,0,1000000,1,200' >> \"$out\"\n" if mode == 'ok' else '')))
                binary.chmod(0o755)
                env = dict(os.environ, RESULTS_DIR=str(root / 'results'), HTTPBASE_BIN=str(binary),
                           PYTHON=os.sys.executable, CELLS='C1', RPS_LIST='1', REPEATS='1', DURATION_SEC='1')
                cmd = ['bash', '-c', 'source "$1"; check_prereq() { :; }; cleanup_run() { :; }; main',
                       'test', str(SCRIPTS / 'run-matrix.sh')]
                result = subprocess.run(cmd, env=env, capture_output=True, text=True)
                manifest = root / 'results/C1/rps1/run1/manifest.json'
                self.assertTrue(manifest.exists(), result.stderr)
                data = json.loads(manifest.read_text())
                self.assertEqual(data['status'], expected_status)
                self.assertEqual(result.returncode == 0, mode == 'ok', result.stderr)
                self.assertEqual(data['command'][0], str(binary))
                if mode == 'ok':
                    again = subprocess.run(cmd, env=env, capture_output=True, text=True)
                    self.assertEqual(again.returncode, 0, again.stderr)
                    self.assertIn('1 skipped', again.stdout)
                    for key, value in [('PAPER_CPUSET', '0-1'), ('TIDE_COMB_CPUIDS', '0,1'), ('TIDE_COMB_DEV_CPUNUM', '8')]:
                        changed_env = dict(env, **{key: value})
                        changed = subprocess.run(cmd, env=changed_env, capture_output=True, text=True)
                        self.assertNotEqual(changed.returncode, 0, f'changed {key} must reject resume')
                    raw = manifest.parent / 'raw.csv'
                    raw.write_text(raw.read_text().splitlines()[0] + '\n')
                    broken = subprocess.run(cmd, env=env, capture_output=True, text=True)
                    self.assertNotEqual(broken.returncode, 0)
                    self.assertNotIn('1 skipped', broken.stdout)

    def test_no_global_cleanup_or_swallowed_run_failure(self):
        s = (SCRIPTS / 'run-matrix.sh').read_text()
        self.assertNotIn('rm -f /dev/mqueue/*', s)
        self.assertNotIn('"$out" || true', s)
        self.assertIn('manifest.json', s)
        self.assertIn('validate', s)


if __name__ == '__main__':
    unittest.main()
