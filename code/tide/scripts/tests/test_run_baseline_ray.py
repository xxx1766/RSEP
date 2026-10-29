"""A measured Ray matrix cell must retain the same raw-request contract."""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "revision" / "run_baseline_matrix.py"
REQUIRED = ("REVISION_HTTP_RUN", "REVISION_WORKER", "REVISION_CALIBRATION", "RAY_PYTHON")


@unittest.skipUnless(all(os.environ.get(k) for k in REQUIRED), "set remote matrix paths")
class RayMatrixTest(unittest.TestCase):
    def test_one_cpu_cell_has_complete_raw_trace(self):
        with tempfile.TemporaryDirectory(prefix="tmc-ray-matrix-test-") as directory:
            out = Path(directory) / "matrix"
            command = [sys.executable, str(RUNNER),
                       "--binary", os.environ["REVISION_HTTP_RUN"],
                       "--worker", os.environ["REVISION_WORKER"],
                       "--calibration", os.environ["REVISION_CALIBRATION"],
                       "--ray-python", os.environ["RAY_PYTHON"],
                       "--systems", "ray", "--workloads", "cpu",
                       "--rps", "5", "--duration", "2", "--repeats", "1",
                       "--out", str(out)]
            done = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=120)
            self.assertEqual(done.returncode, 0, done.stdout)
            manifest = json.loads((out / "matrix.json").read_text())
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(len(manifest["jobs"]), 1)
            path = out / manifest["jobs"][0]["path"]
            with (path / "measurement" / "run" / "raw.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 10)
            self.assertEqual({int(r["req_id"]) for r in rows}, set(range(10)))
            self.assertEqual(json.loads((path / "setup.json").read_text())["system"], "ray")


if __name__ == "__main__":
    unittest.main()
