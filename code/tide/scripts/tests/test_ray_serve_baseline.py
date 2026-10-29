"""End-to-end Ray Serve baseline contract, run in the Ray virtual environment."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


SCRIPT = Path(__file__).resolve().parents[1] / "revision" / "ray_serve_baseline.py"
WORKER = Path(os.environ.get("REVISION_WORKER", ""))


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(WORKER.is_file(), "set REVISION_WORKER to compiled Go worker")
class RayServeBaselineTest(unittest.TestCase):
    def test_real_go_kernel_runs_through_ray_serve_and_stops(self):
        http_port, worker_port = free_port(), free_port()
        with tempfile.TemporaryDirectory(prefix="tmc-ray-test-") as directory:
            root = Path(directory)
            log = (root / "server.log").open("w")
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), "--worker", str(WORKER),
                 "--http-port", str(http_port), "--worker-port", str(worker_port),
                 "--state-dir", str(root / "state")],
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    self.assertIsNone(proc.poll(), (root / "server.log").read_text())
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (OSError, urllib.error.URLError):
                        time.sleep(0.2)
                else:
                    self.fail("Ray Serve did not become ready")
                payload = {"kind": "cpu", "bytes": 1024, "iterations": 3}
                request = urllib.request.Request(
                    f"http://127.0.0.1:{http_port}/run", data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=15) as response:
                        result = json.load(response)
                        self.assertEqual(response.status, 200)
                except urllib.error.HTTPError as error:
                    self.fail(f"HTTP {error.code}: {error.read().decode()}; log: {(root / 'server.log').read_text()}")
                self.assertEqual(result["result"]["bytes_processed"], 3072)
                self.assertGreater(result["service_ns"], 0)
                identity = json.loads((root / "state" / "identity.json").read_text())
                self.assertEqual(identity["ray_version"], "2.58.0")
                self.assertEqual(identity["worker_sha256"], __import__("hashlib").sha256(WORKER.read_bytes()).hexdigest())
                worker_pid = int(identity["worker_pid"])
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                log.close()
            if "worker_pid" in locals():
                for _ in range(30):
                    if not Path(f"/proc/{worker_pid}/cmdline").exists():
                        break
                    time.sleep(0.1)
                self.assertFalse(Path(f"/proc/{worker_pid}/cmdline").exists(), "Go worker remains after shutdown")


if __name__ == "__main__":
    unittest.main()
