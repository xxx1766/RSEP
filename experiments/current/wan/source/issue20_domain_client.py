#!/usr/bin/env python3
"""Frozen image trace over preconnected concurrent HTTP channels to three Pods across a local domain and a remote path."""
import argparse
import os
import hashlib
import http.client
import json
from pathlib import Path
import queue
import threading
import time

from issue12_k8s import load_trace, summarize


class DomainPolicy:
    """Two local workers precede remote spill; counts include queued work."""

    def __init__(self, policy="rr", local_cap=2, endpoint_count=3):
        if policy not in ("rr", "local-first") or local_cap < 1 or endpoint_count != 3:
            raise ValueError("three endpoints, rr/local-first and positive local cap required")
        self.policy, self.local_cap = policy, local_cap
        self.inflight, self.cursor = [0, 0, 0], 0
        self.lock = threading.Lock()

    def reserve(self, index):
        with self.lock:
            if self.policy == "rr":
                node = index % 3
            else:
                available = [n for n in (0, 1) if self.inflight[n] < self.local_cap]
                if available:
                    node = min(available, key=lambda n: (self.inflight[n], (n-self.cursor) % 2))
                elif self.inflight[2] < self.local_cap:
                    node = 2
                else:
                    node = min(range(3), key=lambda n: self.inflight[n])
                if node < 2:
                    self.cursor = (node + 1) % 2
            self.inflight[node] += 1
            return node

    def release(self, node):
        with self.lock:
            self.inflight[node] -= 1


def validate_reply(status, row, expected):
    if (status != 200 or row.get("status") != "success" or row.get("finite") is not True or
            row.get("shape") != [1, 3549, 85] or any(row.get(k) != expected[k] for k in
            ("input_sha256", "output_sha256", "checksum", "bytes"))):
        raise ValueError("inference response differs from the frozen input/output reference")


def run(trace, endpoints, input_dir, qos_ms, window_ms, channels, policy="rr", local_cap=2):
    selection = DomainPolicy(policy, local_cap, len(endpoints))
    inputs = [(input_dir / f"image-{i}.jpg").read_bytes() for i in range(4)]
    manifests = []
    for endpoint in endpoints:
        host, port = endpoint.split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=6)
        conn.request("GET", "/manifest")
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError("manifest endpoint failed")
        manifest = json.loads(response.read())
        conn.close()
        for body, meta in zip(inputs, manifest["inputs"]):
            if hashlib.sha256(body).hexdigest() != meta["input_sha256"] or len(body) != meta["bytes"]:
                raise RuntimeError("input dataset mismatch")
        if not manifest.get("tcp_nodelay") or manifest["compute_concurrency"] != 1:
            raise RuntimeError("worker configuration drift")
        manifests.append(manifest)
    queues, rows, ready, errors = [queue.Queue() for _ in endpoints], [None] * len(trace), [], []
    pools = [queue.LifoQueue() for _ in endpoints]
    warmups = []

    def worker(node, channel, event):
        host, port = endpoints[node].split(":")
        conn = http.client.HTTPConnection(host, int(port), timeout=6)
        try:
            conn.request("GET", "/health")
            health = conn.getresponse()
            if health.status != 200 or not json.loads(health.read()).get("ready"):
                raise RuntimeError("worker not ready")
        except Exception as error:
            errors.append(str(error))
            event.set()
            conn.close()
            return
        pools[node].put(conn)
        event.set()
        while True:
            job = queues[node].get()
            if job is None:
                break
            index, row, submitted = job
            conn = pools[node].get()
            image_index = (index // 2) % 4
            expected = manifests[node]["inputs"][image_index]
            row.update(endpoint=endpoints[node], channel=channel, image_index=image_index,
                       input_sha256=expected["input_sha256"], input_bytes=len(inputs[image_index]))
            row.update(dispatch_unix_ns=time.time_ns(),
                       client_queue_ms=(time.monotonic_ns()-submitted)/1e6)
            try:
                conn.request("POST", "/infer", inputs[image_index],
                             {"Content-Type":"image/jpeg", "X-Request-ID":row["id"]})
                response = conn.getresponse()
                body = json.loads(response.read())
                validate_reply(response.status, body, expected)
                if body["request_id"] != row["id"]:
                    raise ValueError("response request identity mismatch")
                row.update(status="success", checksum=body["checksum"],
                           output_sha256=body["output_sha256"], worker_response=body,
                           connection_local_port=conn.sock.getsockname()[1])
            except Exception as error:
                row.update(status="request_error", error=f"{type(error).__name__}: {error}")
                conn.close()
                conn = http.client.HTTPConnection(host, int(port), timeout=6)
            row.update(reply_unix_ns=time.time_ns(), latency_ms=(time.monotonic_ns()-submitted)/1e6)
            row["on_time"] = row["status"] == "success" and row["latency_ms"] <= qos_ms
            selection.release(node)
            rows[index] = row
            pools[node].put(conn)

    threads = []
    for node in range(len(endpoints)):
        for channel in range(channels):
            event = threading.Event()
            ready.append(event)
            thread = threading.Thread(target=worker, args=(node, channel, event), daemon=True)
            thread.start()
            threads.append(thread)
    for event in ready:
        if not event.wait(15):
            errors.append("connection readiness timed out")
    if errors:
        raise RuntimeError(errors)
    for node in range(len(endpoints)):
        conn = pools[node].get()
        for index in range(2):
            started_warm = time.monotonic_ns()
            conn.request("POST", "/infer", inputs[3],
                         {"Content-Type":"image/jpeg", "X-Request-ID":f"k8s-warmup-{node}-{index}"})
            response = conn.getresponse()
            body = json.loads(response.read())
            validate_reply(response.status, body, manifests[node]["inputs"][3])
            warmups.append(dict(endpoint=endpoints[node], response=body,
                                latency_ms=(time.monotonic_ns()-started_warm)/1e6))
        pools[node].put(conn)
    started, started_unix = time.monotonic_ns(), time.time_ns()
    for index, item in enumerate(trace):
        pause = (started + item["scheduled_offset_ns"] - time.monotonic_ns()) / 1e9
        if pause > 0:
            time.sleep(pause)
        submitted = time.monotonic_ns()
        row = dict(item, submit_unix_ns=time.time_ns(), qos_ms=qos_ms)
        node = selection.reserve(index)
        row.update(endpoint=endpoints[node], endpoint_index=node)
        queues[node].put((index, row, submitted))
    for node in range(len(endpoints)):
        for _ in range(channels):
            queues[node].put(None)
    for thread in threads:
        thread.join()
    for pool in pools:
        while not pool.empty():
            pool.get().close()
    if any(row is None for row in rows):
        raise RuntimeError("incomplete request accounting")
    summary = summarize(rows, window_ms)
    summary.update(start_unix_ns=started_unix, end_unix_ns=time.time_ns(),
                   drain_duration_s=(time.monotonic_ns()-started)/1e9,
                   warmups=warmups, worker_manifests=manifests,
                   channels_per_endpoint=channels, local_endpoints=endpoints[:2], remote_endpoints=endpoints[2:],
                   policy=policy, local_cap=local_cap, final_inflight=selection.inflight,
                   connection_reuse="LIFO pool; use active connections at low load and expand for bursts",
                   image_order="(request_index // 2) % 4; both endpoints see each input size")
    return rows, summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trace", type=Path, required=True)
    p.add_argument("--endpoints", default=os.environ.get("RSEP_WAN_DOMAIN_ENDPOINTS","198.51.100.47:19383,198.51.100.48:19383,198.51.100.49:19383"))
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--qos-ms", type=int, default=135)
    p.add_argument("--window-ms", type=int, required=True)
    p.add_argument("--channels", type=int, default=16)
    p.add_argument("--policy", choices=("rr", "local-first"), default="rr")
    p.add_argument("--local-cap", type=int, default=2)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    endpoints = a.endpoints.split(",")
    if len(endpoints) != 3 or not 1 <= a.channels <= 64 or a.qos_ms <= 0 or a.window_ms <= 0 or a.local_cap < 1:
        p.error("three endpoints and bounded positive controls required")
    a.out.mkdir(parents=True, exist_ok=False)
    rows, summary = run(load_trace(a.trace), endpoints, a.inputs, a.qos_ms, a.window_ms, a.channels,
                        a.policy, a.local_cap)
    summary["trace_sha256"] = hashlib.sha256(a.trace.read_bytes()).hexdigest()
    (a.out / "requests.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows))
    (a.out / "run.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps({k:summary[k] for k in ("offered","success","on_time","on_time_goodput_rps","yield_fraction")}))
