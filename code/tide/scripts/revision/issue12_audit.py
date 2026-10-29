#!/usr/bin/env python3
"""Independently audit every offered request in a frozen issue-12 paired cell."""

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(fraction * len(ordered)) - 1]


def audit_rows(trace: list[dict], rows: list[dict], window_ms: int,
               expected_checksum: int, qos_ms: int) -> dict:
    if window_ms <= 0 or qos_ms <= 0 or not trace:
        raise ValueError("invalid trace/window/deadline")
    expected = {item["id"]: item["scheduled_offset_ns"] for item in trace}
    if len(expected) != len(trace) or len(rows) != len(trace):
        raise ValueError("duplicate or missing trace/result rows")
    found = set()
    for row in rows:
        ident = row.get("id")
        if ident not in expected or ident in found or row.get("scheduled_offset_ns") != expected[ident]:
            raise ValueError(f"unmatched or duplicate request {ident!r}")
        found.add(ident)
        latency = row.get("latency_ms")
        if not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise ValueError(f"invalid latency for {ident}")
        if row.get("reply_unix_ns", 0) < row.get("submit_unix_ns", 0) or not row.get("submit_unix_ns"):
            raise ValueError(f"invalid timestamps for {ident}")
        success = row.get("status") == "success"
        if success and str(row.get("checksum")) != str(expected_checksum):
            raise ValueError(f"wrong checksum for {ident}")
        on_time = success and latency <= qos_ms
        if row.get("on_time") is not on_time:
            raise ValueError(f"incorrect on_time flag for {ident}")
    if len(found) != len(expected):
        raise ValueError("incomplete request ID set")
    statuses = Counter(row["status"] for row in rows)
    success = statuses["success"]
    on_time = sum(row["on_time"] for row in rows)
    successes = [row["latency_ms"] for row in rows if row["status"] == "success"]
    return {"offered": len(rows), "success": success, "on_time": on_time,
            "late_success": success - on_time, "statuses": dict(statuses),
            "yield_fraction": on_time / len(rows),
            "on_time_goodput_rps": on_time * 1000 / window_ms,
            "success_latency_median_ms": statistics.median(successes) if successes else None,
            "success_latency_p95_ms": percentile(successes, 0.95),
            "nominal_arrival_window_ms": window_ms}


def validate_payload_scope(summary: dict, rows: list[dict], expected_bytes: int) -> None:
    if summary.get("request_payload_bytes") != expected_bytes or any(
            row.get("request_payload_bytes") != expected_bytes for row in rows):
        raise ValueError("Kubernetes request payload does not match native Tide payload bytes")


def audit_cell(base: Path, cell: dict, checksum: int, qos_ms: int) -> dict:
    trace_path = base / "traces" / f"{cell['id']}.jsonl"
    trace = jsonl(trace_path)
    raw = base / "raw" / cell["id"]
    output = {"cell": cell, "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
              "systems": {}}
    for system, dirname in (("tide", raw / "tide-output" / cell["id"]),
                            ("k8s", raw / "k8s-output")):
        records = jsonl(dirname / "requests.jsonl")
        summary = json.loads((dirname / "run.json").read_text())
        if system == "k8s":
            validate_payload_scope(summary, records, 65536)
        elif summary["spec"]["payload_bytes"] != 65536:
            raise ValueError("native Tide payload bytes drift")
        checked = audit_rows(trace, records, cell["window_ms"], checksum, qos_ms)
        reported = summary.get("submitted", summary.get("offered"))
        if (reported != checked["offered"] or summary["success"] != checked["success"] or
                summary["on_time"] != checked["on_time"] or
                not math.isclose(summary["on_time_goodput_rps"], checked["on_time_goodput_rps"], rel_tol=1e-9)):
            raise ValueError(f"{system} summary disagrees with per-request records")
        if summary.get("trace_sha256") != output["trace_sha256"]:
            raise ValueError(f"{system} did not use the frozen trace")
        drain_seconds = (summary["end_unix_ns"] - summary["start_unix_ns"]) / 1e9 if system == "tide" else summary["drain_duration_s"]
        checked["drain_inclusive_on_time_rps"] = checked["on_time"] / drain_seconds
        checked["drain_duration_s"] = drain_seconds
        output["systems"][system] = checked
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--cell", required=True)
    args = parser.parse_args()
    plan = json.loads((args.base / "plan.json").read_text())
    cells = [cell for cell in plan["cells"] if cell["id"] == args.cell]
    if len(cells) != 1:
        parser.error("cell absent from frozen plan")
    result = audit_cell(args.base, cells[0], plan["checksum"], plan["qos_ms"])
    path = args.base / "raw" / args.cell / "audit.json"
    with path.open("x") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
