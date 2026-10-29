"""Independent count fixtures and real local HTTP tests for Fig. 6 collection."""
import asyncio
import importlib
import json
from pathlib import Path
import tempfile
import time
import unittest


def modules():
    return (importlib.import_module("fig6_cluster_driver"),
            importlib.import_module("fig6_cluster_analyze"))


class AccountingTests(unittest.TestCase):
    def fixture(self, origin, start, rows):
        expected = {"bytes_processed": 16, "checksum": 12345678901234567890}
        meta = {"schema_version": 1, "origin": origin, "system": "fixture",
                "trace_sha256": "same", "start_unix": 1000,
                "window_start_monotonic_ns": start,
                "duration_ns": 1_000_000_000, "expected_result": expected,
                "expected_ids": [origin + str(i) for i in range(len(rows))]}
        records = []
        for i, (submit, returned, status) in enumerate(rows):
            records.append({"id": origin + str(i), "origin": origin,
                            "planned_submit_monotonic_ns": start + int(submit * 1e9),
                            "actual_submit_monotonic_ns": start + int(submit * 1e9),
                            "return_monotonic_ns": start + int(returned * 1e9),
                            "deadline_ms": 500, "http_status": status,
                            "error": None if status == 200 else "http_error",
                            "response": {"result": dict(expected), "node": "nj1",
                                         "service_ns": 1_000_000}})
        return meta, records

    def test_window_cohort_failures_boundary_and_origin_sum(self):
        _, analyze = modules()
        # k2: tie accepted; one late inside; one on-time outside; one late outside.
        k2 = self.fixture("k2", 10_000_000_000,
                          [(0, .5, 200), (.1, .8, 200), (.9, 1.1, 200), (.8, 1.5, 200)])
        # k3's unrelated monotonic epoch must not extend the shared duration.
        k3 = self.fixture("k3", 99_000_000_000, [(0, .1, 200), (.1, .2, 503)])
        report = analyze.summarize_runs([k2, k3])
        self.assertEqual(report["offered"], 6)
        self.assertEqual(report["cohort_successful"], 5)
        self.assertEqual(report["cohort_on_time"], 3)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["completed_in_window"], 3)
        self.assertEqual(report["on_time_in_window"], 2)
        self.assertEqual(report["throughput_rps"], 3)
        self.assertEqual(report["goodput_rps"], 2)
        self.assertEqual(report["cohort_yield"], .5)
        self.assertEqual(report["origins"]["k2"]["cohort_on_time"], 2)
        self.assertEqual(report["origins"]["k3"]["offered"], 2)

    def test_checksum_error_is_failure_even_when_transport_succeeded(self):
        _, analyze = modules()
        run = self.fixture("k2", 10_000, [(0, .1, 200)])
        run[1][0]["response"]["result"]["checksum"] += 1
        result = analyze.summarize_runs([run])
        self.assertEqual(result["cohort_successful"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["failure_reasons"]["result_mismatch"], 1)

    def test_window_end_excluded_and_replay_lateness_reported(self):
        _, analyze = modules()
        run = self.fixture("k2", 1_000_000_000, [(.5, 1, 200)])
        run[1][0]["planned_submit_monotonic_ns"] -= 100_000_000
        result = analyze.summarize_runs([run])
        self.assertEqual(result["cohort_on_time"], 1)
        self.assertEqual(result["on_time_in_window"], 0)
        self.assertEqual(result["replay_lateness_ms"]["p99"], 100)
        self.assertEqual(result["successful_latency_ms"]["p50"], 500)

    def test_raw_artifact_tampering_detected(self):
        driver, analyze = modules()
        meta, records = self.fixture("k2", 1_000_000_000, [(0, .1, 200)])
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "run"
            driver.write_run(prefix, meta, records)
            raw = Path(str(prefix) + ".jsonl")
            raw.write_text(raw.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "hash"):
                analyze.load_run(str(prefix) + ".meta.json")

    def test_missing_duplicate_and_incompatible_windows_rejected(self):
        _, analyze = modules()
        one = self.fixture("k2", 10000, [(0, .1, 200)])
        with self.assertRaises(ValueError):
            analyze.summarize_runs([(one[0], [])])
        with self.assertRaises(ValueError):
            analyze.summarize_runs([(one[0], one[1] * 2)])
        two = self.fixture("k3", 10000, [(0, .1, 200)])
        two[0]["duration_ns"] *= 2
        with self.assertRaises(ValueError):
            analyze.summarize_runs([one, two])


class TraceTests(unittest.TestCase):
    def test_all_patterns_deterministic_equal_count_and_window_bounds(self):
        driver, _ = modules()
        traces = []
        for pattern in ("balanced", "hotspot", "burst"):
            trace = driver.generate_trace(pattern, 10, 20, {"kind": "cpu", "bytes": 8, "iterations": 1})
            self.assertEqual(trace, driver.generate_trace(pattern, 10, 20, trace["workload"]))
            self.assertEqual(len(trace["requests"]), 200)
            self.assertEqual(len({r["id"] for r in trace["requests"]}), 200)
            self.assertTrue(all(0 <= r["offset_ns"] < trace["duration_ns"] for r in trace["requests"]))
            traces.append(trace)
        self.assertEqual(sum(r["origin"] == "k2" for r in traces[0]["requests"]), 100)
        self.assertEqual(sum(r["origin"] == "k2" for r in traces[1]["requests"]), 180)
        first_burst = [r for r in traces[2]["requests"] if r["offset_ns"] == 0]
        self.assertEqual({r["origin"] for r in first_burst}, {"k2", "k3"})
        self.assertGreater(len(first_burst), 2)

    def test_small_independent_reference_checksums(self):
        driver, _ = modules()
        # Memory hand calculation: [1..8] + [3,5,..17] = 36 + 80.
        self.assertEqual(driver.expected_result({"kind": "memory", "bytes": 8, "iterations": 2}),
                         {"bytes_processed": 16, "checksum": 116})
        # Golden outputs independently captured with the actual Go worker's
        # --mode once, including CPU copy truncation for a buffer shorter than SHA256.
        self.assertEqual(driver.expected_result({"kind": "cpu", "bytes": 8, "iterations": 2}),
                         {"bytes_processed": 16, "checksum": 10626351018065338775})
        self.assertEqual(driver.expected_result({"kind": "io", "bytes": 261, "iterations": 3}),
                         {"bytes_processed": 783, "checksum": 2207777631})

    def test_memory_reference_spans_full_iteration_period(self):
        driver, _ = modules()
        # Slow direct byte kernel, independent of the production algebraic shortcut.
        buffer = bytearray(261)
        checksum = 0
        for iteration in range(1027):
            for i in range(len(buffer)):
                buffer[i] = (buffer[i] + i + iteration + 1) % 256
                checksum += buffer[i]
        self.assertEqual(driver.expected_result({"kind": "memory", "bytes": 261, "iterations": 1027}),
                         {"bytes_processed": 261 * 1027, "checksum": checksum})


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_loop_concurrency_payload_envelope_and_atomic_artifacts(self):
        driver, analyze = modules()
        arrived, bodies = [], []
        all_arrived = asyncio.Event()
        workload = {"kind": "memory", "bytes": 8, "iterations": 2}
        response = {"result": {"bytes_processed": 16, "checksum": 116},
                    "service_ns": 120, "node": "nj2", "worker_pid": 123,
                    "start_unix_ns": 1234, "end_unix_ns": 1354, "extra": "preserve"}

        async def server(reader, writer):
            headers = await reader.readuntil(b"\r\n\r\n")
            length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n")
                              if line.lower().startswith(b"content-length:")))
            bodies.append(json.loads(await reader.readexactly(length)))
            arrived.append(time.monotonic())
            if len(arrived) == 6:
                all_arrived.set()
            await asyncio.wait_for(all_arrived.wait(), 2)
            payload = json.dumps(response).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(payload)).encode() +
                         b"\r\nConnection: close\r\n\r\n" + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        service = await asyncio.start_server(server, "127.0.0.1", 0)
        port = service.sockets[0].getsockname()[1]
        trace = driver.generate_trace("balanced", .1, 120, workload)
        for request in trace["requests"]:
            request["offset_ns"] = 0
        try:
            meta, records = await driver.collect(trace, "k2", "test", "gateway",
                                                 [f"http://127.0.0.1:{port}/run"],
                                                 time.time() + .05, 2)
        finally:
            service.close()
            await service.wait_closed()
        self.assertEqual(len(records), 6)
        self.assertEqual(bodies, [workload] * 6)
        self.assertTrue(all(r["response"] == response for r in records))
        self.assertEqual(analyze.summarize_runs([(meta, records)])["cohort_successful"], 6)
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "run"
            driver.write_run(prefix, meta, records)
            loaded = analyze.load_run(str(prefix) + ".meta.json")
            self.assertEqual(loaded[0]["expected_ids"], meta["expected_ids"])
            self.assertEqual(len(loaded[1]), 6)
            self.assertFalse(list(Path(directory).glob("*.partial")))

    async def test_connection_failure_and_timeout_retained(self):
        driver, analyze = modules()
        async def server(reader, writer):
            await reader.read()
            writer.close()
            await writer.wait_closed()
        service = await asyncio.start_server(server, "127.0.0.1", 0)
        port = service.sockets[0].getsockname()[1]
        trace = driver.generate_trace("hotspot", .1, 20, {"kind": "memory", "bytes": 8, "iterations": 1})
        try:
            run = await driver.collect(trace, "k2", "test", "gateway",
                                       [f"http://127.0.0.1:{port}/run"], time.time() + .02, .02)
        finally:
            service.close()
            await service.wait_closed()
        self.assertEqual(len(run[1]), 2)
        self.assertTrue(all(r["error"] == "timeout" for r in run[1]))
        self.assertEqual(analyze.summarize_runs([run])["failed"], 2)
        failed = await driver.collect(trace, "k2", "test", "gateway",
                                      [f"http://127.0.0.1:{port}/run"], time.time() + .02, .02)
        self.assertEqual(analyze.summarize_runs([failed])["failed"], 2)


if __name__ == "__main__":
    unittest.main()
