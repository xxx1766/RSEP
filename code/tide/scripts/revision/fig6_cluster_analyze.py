#!/usr/bin/env python3
"""Audit offered cohorts and fixed-window metrics from raw Fig. 6 records.

Example: python3 fig6_cluster_analyze.py k2.meta.json k3.meta.json --output pair.json
Completion windows are half-open; the deadline boundary is inclusive. Monotonic
timestamps are compared only within an origin. Concurrent origins share one
wall-clock duration, so aggregate rates sum counts and divide by that duration.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None, "max": None}
    return {"count": len(values), "mean": sum(values) / len(values),
            **{name: values[max(0, math.ceil(p * len(values)) - 1)]
               for name, p in (("p50", .5), ("p95", .95), ("p99", .99))}, "max": values[-1]}


def outcome(record, expected):
    if record.get("error"):
        return str(record["error"])
    if record.get("http_status") != 200:
        return "http_error"
    response = record.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        return "invalid_response"
    result = response["result"]
    if any(type(result.get(k)) is not int or result[k] != v for k, v in expected.items()):
        return "result_mismatch"
    return None


def summarize_runs(runs):
    if not runs:
        raise ValueError("at least one origin run is required")
    first = runs[0][0]
    duration = first["duration_ns"]
    if duration <= 0:
        raise ValueError("duration must be positive")
    totals = Counter()
    reasons, nodes = Counter(), Counter()
    successful_ms, replay_ms, offered_ms, service_ms = [], [], [], []
    origins = {}
    for meta, records in runs:
        for field in ("duration_ns", "start_unix", "system", "trace_sha256", "expected_result"):
            if meta[field] != first[field]:
                raise ValueError("cannot combine runs with different " + field)
        origin = meta["origin"]
        if origin in origins:
            raise ValueError("duplicate origin " + origin)
        ids = [r["id"] for r in records]
        expected_ids = meta["expected_ids"]
        if (len(ids) != len(set(ids)) or len(expected_ids) != len(set(expected_ids))
                or set(ids) != set(expected_ids)):
            raise ValueError("offered cohort incomplete or duplicated for " + origin)
        counts = Counter(offered=len(expected_ids), cohort_successful=0, cohort_on_time=0,
                         failed=0, completed_in_window=0, on_time_in_window=0)
        window_start = meta["window_start_monotonic_ns"]
        for record in records:
            if record["origin"] != origin:
                raise ValueError("record origin mismatch")
            planned = record["planned_submit_monotonic_ns"]
            submitted = record["actual_submit_monotonic_ns"]
            returned = record["return_monotonic_ns"]
            if not (window_start <= planned < window_start + duration and returned >= submitted >= planned):
                raise ValueError("invalid client timestamps for " + record["id"])
            replay_ms.append((submitted - planned) / 1e6)
            reason = outcome(record, meta["expected_result"])
            if reason:
                counts["failed"] += 1
                reasons[reason] += 1
                continue
            elapsed = returned - submitted
            on_time = elapsed <= round(record["deadline_ms"] * 1e6)
            in_window = window_start <= returned < window_start + duration
            counts["cohort_successful"] += 1
            counts["cohort_on_time"] += int(on_time)
            counts["completed_in_window"] += int(in_window)
            counts["on_time_in_window"] += int(in_window and on_time)
            successful_ms.append(elapsed / 1e6)
            offered_ms.append((returned - planned) / 1e6)
            response = record["response"]
            nodes[str(response.get("node", "unknown"))] += 1
            if isinstance(response.get("service_ns"), (int, float)):
                service_ms.append(response["service_ns"] / 1e6)
        totals.update(counts)
        origins[origin] = dict(counts)
    seconds = duration / 1e9
    return {"schema_version": 1, "system": first["system"], "trace_sha256": first["trace_sha256"],
            "start_unix": first["start_unix"], "duration_s": seconds, **dict(totals),
            "throughput_rps": totals["completed_in_window"] / seconds,
            "goodput_rps": totals["on_time_in_window"] / seconds,
            "offered_rps": totals["offered"] / seconds,
            "cohort_yield": totals["cohort_on_time"] / totals["offered"] if totals["offered"] else None,
            "cohort_success_fraction": totals["cohort_successful"] / totals["offered"] if totals["offered"] else None,
            "successful_latency_ms": distribution(successful_ms),
            "successful_planned_to_return_ms": distribution(offered_ms),
            "replay_lateness_ms": distribution(replay_ms),
            "kernel_service_ms": distribution(service_ms),
            "failure_reasons": dict(reasons), "destinations": dict(nodes), "origins": origins,
            "definitions": {"window": "[start_unix, start_unix + duration_s)",
                            "deadline": "return_monotonic_ns - actual_submit_monotonic_ns <= deadline_ms",
                            "throughput": "checksum-valid successful returns in window / shared duration_s",
                            "goodput": "checksum-valid on-time successful returns in window / shared duration_s",
                            "cohort_yield": "all on-time successes, including returns after window / all offered"}}


def load_run(meta_path):
    meta_path = Path(meta_path)
    meta = json.loads(meta_path.read_text())
    if meta.get("complete") is not True:
        raise ValueError("run has no complete commit marker")
    raw = meta_path.parent / meta["raw_file"]
    content = raw.read_bytes()
    if hashlib.sha256(content).hexdigest() != meta["raw_sha256"]:
        raise ValueError("raw artifact hash mismatch")
    records = [json.loads(line) for line in content.splitlines() if line.strip()]
    return meta, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("meta", nargs="+")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = summarize_runs([load_run(path) for path in args.meta])
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        path = Path(args.output)
        partial = path.with_name(path.name + ".partial")
        partial.write_text(rendered)
        partial.replace(path)
    print(rendered, end="")


if __name__ == "__main__":
    main()
