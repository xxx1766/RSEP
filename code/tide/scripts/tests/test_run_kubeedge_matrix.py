"""The KubeEdge edge pod must produce a complete measured request trace."""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "revision" / "run_kubeedge_matrix.py"
REQUIRED = ("REVISION_HTTP_RUN", "REVISION_CALIBRATION", "KUBEEDGE_KUBECONFIG", "KUBEEDGE_WORKER_TEMPLATE")


@unittest.skipUnless(all(os.environ.get(k) for k in REQUIRED), "set remote KubeEdge matrix paths")
class KubeEdgeMatrixTest(unittest.TestCase):
    def test_one_cpu_cell_has_complete_trace_and_edgecore_identity(self):
        with tempfile.TemporaryDirectory(prefix="tmc-ke-matrix-test-") as directory:
            out = Path(directory) / "matrix"
            command = [sys.executable, str(RUNNER),
                       "--binary", os.environ["REVISION_HTTP_RUN"],
                       "--calibration", os.environ["REVISION_CALIBRATION"],
                       "--kubeconfig", os.environ["KUBEEDGE_KUBECONFIG"],
                       "--template", os.environ["KUBEEDGE_WORKER_TEMPLATE"],
                       "--workloads", "cpu", "--rps", "5", "--duration", "2",
                       "--repeats", "1", "--out", str(out)]
            done = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=120)
            self.assertEqual(done.returncode, 0, done.stdout)
            matrix = json.loads((out / "matrix.json").read_text())
            self.assertEqual(matrix["status"], "completed")
            self.assertEqual(len(matrix["jobs"]), 1)
            job = matrix["jobs"][0]
            measured = out / job["path"] / "measurement"
            with (measured / "run" / "raw.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 10)
            self.assertEqual({int(r["req_id"]) for r in rows}, set(range(10)))
            setup = json.loads((out / job["path"] / "setup.json").read_text())
            self.assertIn("kubeedge-v1.23.1", setup["edge_node_version"])
            self.assertEqual(setup["pod"]["spec"]["nodeName"], "tmc-ke-worker")


if __name__ == "__main__":
    unittest.main()
