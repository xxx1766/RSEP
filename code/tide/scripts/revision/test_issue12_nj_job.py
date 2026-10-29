import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

try:
    from issue12_nj_job import run_job, job_status, stop_job
except ImportError:
    run_job = None


class DetachedJobTests(unittest.TestCase):
    def test_nonzero_exit_is_preserved_and_job_cannot_be_relaunched(self):
        self.assertIsNotNone(run_job, "detached job recorder missing")
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'spec.json').write_text(json.dumps(dict(argv=[sys.executable,'-c','print("evidence"); raise SystemExit(7)'], env={})))
            run_job(p)
            state = job_status(p)
            self.assertEqual(state['phase'], 'finished')
            self.assertEqual(state['exit_code'], 7)
            self.assertIn('evidence', (p/'process.log').read_text())
            with self.assertRaises(FileExistsError):
                run_job(p)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux process identity check')
    def test_stop_refuses_pid_reuse(self):
        self.assertIsNotNone(run_job)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'state.json').write_text(json.dumps(dict(pid=os.getpid(), start_ticks='wrong')))
            with self.assertRaises(RuntimeError):
                stop_job(p)


if __name__ == '__main__':
    unittest.main()
