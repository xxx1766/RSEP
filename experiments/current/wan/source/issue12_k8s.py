#!/usr/bin/env python3
"""Replay a frozen issue-12 trace against two prestarted Kubernetes HTTP Pods."""

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import queue
import sys
import threading
import time


def load_trace(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError("empty trace")
    ids = set()
    last = -1
    for row in rows:
        offset = row.get("scheduled_offset_ns")
        ident = row.get("id")
        if not isinstance(ident, str) or not ident or ident in ids or not isinstance(offset, int) or offset < last:
            raise ValueError("duplicate/missing ID or nonmonotonic/invalid scheduled offset")
        ids.add(ident)
        last = offset
    return [{"id": row["id"], "scheduled_offset_ns": row["scheduled_offset_ns"]} for row in rows]


def summarize(rows: list[dict], window_ms: int) -> dict:
    if window_ms <= 0 or not rows:
        raise ValueError("positive window and nonempty rows required")
    success = sum(row["status"] == "success" for row in rows)
    on_time = sum(row["status"] == "success" and row["latency_ms"] <= row.get("qos_ms", 135) for row in rows)
    return {"offered": len(rows), "success": success, "on_time": on_time,
            "yield_fraction": on_time / len(rows),
            "on_time_goodput_rps": on_time / (window_ms / 1000),
            "nominal_arrival_window_ms": window_ms}


class EndpointWorker:
    def __init__(self, endpoint: str, work: dict, expected_checksum: int, output: list, index: int):
        host, port = endpoint.rsplit(":", 1)
        self.endpoint, self.work, self.expected_checksum = endpoint, work, expected_checksum
        self.connection = http.client.HTTPConnection(host, int(port), timeout=3)
        self.body = json.dumps(work, separators=(",", ":")).encode()
        self.payload_padding = "0" * int(work["bytes"])
        self.headers = {"Content-Type": "application/json",
                        "X-Issue12-Payload": self.payload_padding}
        self.output, self.index = output, index
        self.queue = queue.Queue()
        self.ready = threading.Event()
        self.ready_error = None
        self.warmup = None
        self.thread = threading.Thread(target=self.run, name=f"issue12-http-{index}")

    def request(self) -> tuple[int, dict]:
        self.connection.request("POST", "/run", body=self.body, headers=self.headers)
        response = self.connection.getresponse()
        status = response.status
        body = json.loads(response.read())
        return status, body

    def validate(self, status: int, body: dict) -> tuple[str, str]:
        result = body.get("result", body.get("Result", {}))
        error = body.get("error", body.get("Error", ""))
        if status != 200 or error:
            return "http_error", f"HTTP {status}: {error}"
        if (result.get("checksum", result.get("Checksum")) != self.expected_checksum or
                result.get("bytes_processed", result.get("BytesProcessed")) != self.work["bytes"] * self.work["iterations"]):
            return "checksum_error", "worker result does not match frozen CPU kernel"
        return "success", ""

    def run(self) -> None:
        try:
            started = time.monotonic_ns()
            self.connection.request("GET", "/health")
            health = self.connection.getresponse()
            health_body = health.read()
            if health.status != 200 or b"ready" not in health_body:
                raise RuntimeError(f"{self.endpoint} health failed")
            status, body = self.request()
            outcome, error = self.validate(status, body)
            if outcome != "success":
                raise RuntimeError(f"{self.endpoint} warmup {outcome}: {error}")
            self.warmup = {"endpoint": self.endpoint, "elapsed_ms": (time.monotonic_ns() - started) / 1e6,
                           "status": outcome, "checksum": self.expected_checksum}
        except Exception as error:
            self.ready_error = f"{type(error).__name__}: {error}"
        finally:
            self.ready.set()
        if self.ready_error:
            self.connection.close()
            return
        available = True
        while True:
            job = self.queue.get()
            if job is None:
                break
            index, row = job
            status, error = "transport_unavailable", "execution channel unavailable after previous transport failure"
            service_ns = None
            checksum = None
            bytes_processed = None
            if available:
                try:
                    http_status, body = self.request()
                    status, error = self.validate(http_status, body)
                    service_ns = body.get("service_ns", body.get("ServiceNs"))
                    result = body.get("result", body.get("Result", {}))
                    checksum = result.get("checksum", result.get("Checksum"))
                    bytes_processed = result.get("bytes_processed", result.get("BytesProcessed"))
                except Exception as exc:
                    status, error = "transport_error", f"{type(exc).__name__}: {exc}"
                    available = False
            row.update(status=status, error=error, worker_service_ns=service_ns,
                       checksum=checksum, bytes_processed=bytes_processed,
                       reply_unix_ns=time.time_ns(), latency_ms=(time.monotonic_ns() - row["submit_monotonic_ns"]) / 1e6)
            row["on_time"] = status == "success" and row["latency_ms"] <= row["qos_ms"]
            row.pop("submit_monotonic_ns")
            self.output[index] = row
        self.connection.close()


def run(trace: list[dict], endpoints: list[str], work: dict, checksum: int, qos_ms: int, window_ms: int) -> tuple[list[dict], dict]:
    if len(endpoints) != 2 or qos_ms <= 0 or window_ms <= 0:
        raise ValueError("two endpoints and positive deadline/window required")
    rows = [None] * len(trace)
    workers = [EndpointWorker(endpoint, work, checksum, rows, i) for i, endpoint in enumerate(endpoints)]
    for worker in workers:
        worker.thread.start()
    for worker in workers:
        worker.ready.wait(timeout=10)
    errors = [worker.ready_error or ("readiness timed out" if not worker.ready.is_set() else "") for worker in workers]
    if any(errors):
        for worker in workers:
            if worker.ready.is_set() and not worker.ready_error:
                worker.queue.put(None)
        for worker in workers:
            worker.thread.join(timeout=5)
        raise RuntimeError(f"worker readiness failed: {errors}")
    warmups = [worker.warmup for worker in workers]
    start = time.monotonic_ns()
    start_unix = time.time_ns()
    for index, item in enumerate(trace):
        wait_ns = start + item["scheduled_offset_ns"] - time.monotonic_ns()
        if wait_ns > 0:
            time.sleep(wait_ns / 1e9)
        worker = workers[index % 2]
        row = dict(item, endpoint=worker.endpoint, qos_ms=qos_ms,
                   request_payload_bytes=len(worker.payload_padding),
                   submit_unix_ns=time.time_ns(), submit_monotonic_ns=time.monotonic_ns())
        worker.queue.put((index, row))
    for worker in workers:
        worker.queue.put(None)
    for worker in workers:
        worker.thread.join()
    if any(row is None for row in rows):
        raise RuntimeError("request accounting incomplete")
    end = time.monotonic_ns()
    summary = summarize(rows, window_ms)
    summary.update(warmups=warmups, start_unix_ns=start_unix,
                   request_payload_bytes=len(workers[0].payload_padding),
                   drain_duration_s=(end - start) / 1e9,
                   drain_inclusive_on_time_rps=summary["on_time"] / ((end - start) / 1e9))
    return rows, summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trace", type=Path, required=True)
    p.add_argument("--endpoints", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--expected-checksum", type=int, required=True)
    p.add_argument("--qos-ms", type=int, default=135)
    p.add_argument("--window-ms", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    trace = load_trace(a.trace)
    work = json.loads(a.work)
    a.out.mkdir(parents=True, exist_ok=False)
    rows, summary = run(trace, a.endpoints.split(","), work, a.expected_checksum, a.qos_ms, a.window_ms)
    summary.update(system="Kubernetes hostNetwork two-Pod HTTP FCFS", endpoints=a.endpoints.split(","),
                   workload=work, trace_sha256=hashlib.sha256(a.trace.read_bytes()).hexdigest(),
                   argv=sys.argv)
    (a.out / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (a.out / "run.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
