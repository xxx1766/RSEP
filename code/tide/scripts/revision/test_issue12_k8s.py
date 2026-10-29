import json
import tempfile
import unittest
from pathlib import Path

from issue12_k8s import EndpointWorker, load_trace, summarize


class K8sTraceTests(unittest.TestCase):
    def test_request_carries_same_payload_bytes_as_native_tide(self):
        worker = EndpointWorker("127.0.0.1:19282", {"kind": "cpu", "bytes": 65536, "iterations": 30}, 1, [], 0)
        self.assertEqual(len(worker.headers["X-Issue12-Payload"]), 65536)
        self.assertLess(len(worker.body), 8192)

    def test_rejects_missing_or_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "trace.jsonl"
            path.write_text('{"id":"a","scheduled_offset_ns":0}\n{"id":"a","scheduled_offset_ns":1}\n')
            with self.assertRaises(ValueError):
                load_trace(path)
            path.write_text('{"id":"a","scheduled_offset_ns":0}\n{"id":"b","scheduled_offset_ns":-1}\n')
            with self.assertRaises(ValueError):
                load_trace(path)

    def test_failed_and_late_requests_reduce_goodput(self):
        rows = [{"id": "a", "status": "success", "latency_ms": 100},
                {"id": "b", "status": "success", "latency_ms": 140},
                {"id": "c", "status": "transport_error", "latency_ms": 50}]
        got = summarize(rows, window_ms=2000)
        self.assertEqual(got["offered"], 3)
        self.assertEqual(got["success"], 2)
        self.assertEqual(got["on_time"], 1)
        self.assertAlmostEqual(got["yield_fraction"], 1 / 3)
        self.assertAlmostEqual(got["on_time_goodput_rps"], 0.5)


if __name__ == "__main__":
    unittest.main()
