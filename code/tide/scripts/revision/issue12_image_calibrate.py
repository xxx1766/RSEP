#!/usr/bin/env python3
"""Low-rate image-path calibration; retain every inference response."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import statistics
import time


def calibrate(endpoint, count):
    host, port = endpoint.rsplit(":", 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.request("GET", "/manifest")
    manifest = json.loads(conn.getresponse().read())
    bodies = []
    for image in manifest["inputs"]:
        conn.request("GET", f'/input/{image["index"]}')
        body = conn.getresponse().read()
        if hashlib.sha256(body).hexdigest() != image["input_sha256"]:
            raise RuntimeError("input checksum mismatch")
        bodies.append(body)
    rows = []
    for index in range(count):
        image = manifest["inputs"][index % len(bodies)]
        ident = f"calibration-{host}-{time.time_ns()}-{index}"
        started = time.monotonic_ns()
        conn.request("POST", "/infer", bodies[index % len(bodies)],
                     {"Content-Type": "image/jpeg", "X-Request-ID": ident})
        sent = time.monotonic_ns()
        response = conn.getresponse()
        headers = time.monotonic_ns()
        row = json.loads(response.read())
        row["send_ms"] = (sent - started) / 1e6
        row["headers_ms"] = (headers - started) / 1e6
        row["body_after_headers_ms"] = (time.monotonic_ns() - headers) / 1e6
        row["latency_ms"] = (time.monotonic_ns() - started) / 1e6
        if (response.status != 200 or row.get("status") != "success" or
                row["input_sha256"] != image["input_sha256"] or
                row["output_sha256"] != image["output_sha256"] or not row["finite"]):
            raise RuntimeError(f"inference verification failed: {row}")
        rows.append(row)
        time.sleep(0.1)
    conn.close()
    return dict(endpoint=endpoint, manifest=manifest, rows=rows,
                summary=dict(count=len(rows), latency_median_ms=statistics.median(r["latency_ms"] for r in rows),
                             service_median_ms=statistics.median(r["service_ns"] / 1e6 for r in rows),
                             on_time_135ms=sum(r["latency_ms"] <= 135 for r in rows)))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoints", required=True)
    p.add_argument("--count", type=int, default=40)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if not 4 <= a.count <= 100 or a.out.exists():
        p.error("count 4--100 and new output path required")
    results = [calibrate(endpoint, a.count) for endpoint in a.endpoints.split(",")]
    a.out.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps([dict(endpoint=r["endpoint"], summary=r["summary"]) for r in results]))
