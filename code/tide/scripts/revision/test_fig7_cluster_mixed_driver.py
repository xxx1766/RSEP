"""Focused mixed-class protocol tests; fixture HTTP only, no measured results."""
import asyncio
from collections import Counter
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import time
import unittest

WORK = {"kind": "cpu", "bytes": 65536, "iterations": 100}


def driver():
    return importlib.import_module("fig7_cluster_mixed_driver")


class MixedTraceTests(unittest.TestCase):
    def test_alternating_origins_and_classes_do_not_correlate(self):
        d = driver()
        trace = d.generate_mixed_trace("balanced", 10, 21, WORK)
        self.assertEqual(len(trace["requests"]), 210)
        self.assertEqual(trace, d.generate_mixed_trace("balanced", 10, 21, WORK))
        self.assertEqual(Counter(r["deadline_ms"] for r in trace["requests"]), {100: 105, 500: 105})
        for origin in ("k2", "k3"):
            rows = [r for r in trace["requests"] if r["origin"] == origin]
            self.assertLessEqual(abs(sum(r["deadline_ms"] == 100 for r in rows) - sum(r["deadline_ms"] == 500 for r in rows)), 1)
            for first in range(0, len(rows) - 7, 8):
                block = rows[first:first + 8]
                slots = {}
                for ordinal, row in enumerate(block, first):
                    slot = (ordinal + (2 if origin == "k3" else 0)) % 4
                    slots.setdefault(slot, []).append(row["deadline_ms"])
                self.assertEqual({slot: sorted(classes) for slot, classes in slots.items()},
                                 {slot: [100, 500] for slot in range(4)})
        self.assertEqual(d.validate_trace(trace), d.expected_result(WORK))

    def test_odd_trace_count_and_nonfixed_protocol_rejected(self):
        d = driver()
        for args in [("balanced", 1, 3, WORK), ("burst", 10, 20, WORK),
                     ("balanced", 10, 20, {**WORK, "bytes": 8}),
                     ("balanced", 10, 20, {**WORK, "kind": "memory"})]:
            with self.assertRaises(ValueError):
                d.generate_mixed_trace(*args)

    def test_missing_class_and_correlated_origin_class_rejected(self):
        d = driver()
        trace = d.generate_mixed_trace("balanced", 10, 20, WORK)
        del trace["requests"][0]["deadline_ms"]
        with self.assertRaises(ValueError):
            d.validate_trace(trace)
        trace = d.generate_mixed_trace("balanced", 10, 20, WORK)
        for row in trace["requests"]:
            row["deadline_ms"] = 100 if row["origin"] == "k2" else 500
        with self.assertRaises(ValueError):
            d.validate_trace(trace)


class MixedHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_transmission_payload_hash_and_headers_for_all_frameworks(self):
        d = driver()
        bodies = []
        async def server(reader, writer):
            headers = await reader.readuntil(b"\r\n\r\n")
            length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:")))
            raw = await reader.readexactly(length)
            bodies.append(raw)
            deadline = json.loads(raw)["deadline_ms"]
            payload = d.canonical({"result": d.expected_result(WORK)})
            writer.write((f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\nX-Tide-Function: cpu-qos{deadline}\r\nX-Tide-Deadline-Ms: {deadline}\r\nX-Tide-Effective-Deadline-Ms: {deadline}\r\nConnection: close\r\n\r\n").encode() + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        service = await asyncio.start_server(server, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{service.sockets[0].getsockname()[1]}/run"
        try:
            for system in ("tide", "bare", "ray", "kubeedge"):
                trace = d.generate_mixed_trace("balanced", .01, 400, WORK)
                meta, records = await d.collect(trace, "k2", system, "gateway", [url], time.time() + .02, 2)
                self.assertEqual({r["transmitted_deadline_ms"] for r in records}, {100, 500})
                self.assertEqual(meta["workload"], WORK)
                self.assertEqual(meta["qos_by_function_ms"], {"cpu-qos100": 100, "cpu-qos500": 500})
                self.assertEqual(meta["payload_sha256_by_deadline_ms"], {
                    str(deadline): hashlib.sha256(d.canonical({**WORK, "deadline_ms": deadline})).hexdigest()
                    for deadline in (100, 500)})
                with tempfile.TemporaryDirectory() as directory:
                    prefix = Path(directory) / "run"
                    d.write_run(prefix, meta, records)
                    committed = json.loads(Path(str(prefix) + ".meta.json").read_text())
                    self.assertEqual(committed["raw_sha256"], hashlib.sha256(Path(str(prefix) + ".jsonl").read_bytes()).hexdigest())
                    summary = json.loads(Path(str(prefix) + ".summary.json").read_text())
                    self.assertEqual(set(summary["per_class"]), {"100", "500"})
                    self.assertEqual(summary["per_class"]["100"]["offered"], 1)
                for record in records:
                    payload = d.canonical({**WORK, "deadline_ms": record["deadline_ms"]})
                    self.assertIn(payload, bodies)
                    self.assertEqual(record["request"], json.loads(payload))
                    self.assertEqual(record["request_sha256"], hashlib.sha256(payload).hexdigest())
                    self.assertEqual(record["expected_function"], f'cpu-qos{record["deadline_ms"]}')
                    self.assertIsNone(record["error"])
        finally:
            service.close()
            await service.wait_closed()

    async def test_tide_missing_or_wrong_effective_class_is_audited_as_failure(self):
        d = driver()
        async def server(reader, writer):
            headers = await reader.readuntil(b"\r\n\r\n")
            length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:")))
            await reader.readexactly(length)
            payload = d.canonical({"result": d.expected_result(WORK)})
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        service = await asyncio.start_server(server, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{service.sockets[0].getsockname()[1]}/run"
        try:
            trace = d.generate_mixed_trace("balanced", .01, 400, WORK)
            _, rows = await d.collect(trace, "k2", "tide", "gateway", [url], time.time() + .02, 2)
            self.assertTrue(all(r["error"] == "tide_class_mapping_mismatch" for r in rows))
        finally:
            service.close()
            await service.wait_closed()

    async def test_native_failure_headers_and_timeout_preserve_attempted_cohort(self):
        d = driver()
        async def server(reader, writer):
            headers = await reader.readuntil(b"\r\n\r\n")
            length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:")))
            deadline = json.loads(await reader.readexactly(length))["deadline_ms"]
            if deadline == 100:
                payload = d.canonical({"error": "native admission failure"})
                writer.write((f"HTTP/1.1 503 Unavailable\r\nContent-Length: {len(payload)}\r\nX-Tide-Function: cpu-qos100\r\nX-Tide-Deadline-Ms: 100\r\nX-Tide-Effective-Deadline-Ms: 100\r\nConnection: close\r\n\r\n").encode() + payload)
                await writer.drain()
            else:
                await reader.read()  # Collector's timeout closes the connection.
            writer.close()
            await writer.wait_closed()
        service = await asyncio.start_server(server, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{service.sockets[0].getsockname()[1]}/run"
        try:
            trace = d.generate_mixed_trace("balanced", .01, 400, WORK)
            meta, rows = await d.collect(trace, "k2", "tide", "gateway", [url], time.time() + .02, .03)
            by_class = {r["deadline_ms"]: r for r in rows}
            self.assertEqual(by_class[100]["error"], "http_error")
            self.assertTrue(by_class[100]["tide_class_mapping_valid"])
            self.assertEqual(by_class[500]["error"], "timeout")
            report = d.summarize_mixed_runs([(meta, rows)])
            self.assertEqual(report["offered"], 2)
            self.assertEqual(report["failed"], 2)
            self.assertEqual(report["per_class"]["500"]["cohort_yield"], 0)
        finally:
            service.close()
            await service.wait_closed()


if __name__ == "__main__":
    unittest.main()
