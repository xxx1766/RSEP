#!/usr/bin/env python3
"""Independently recompute and hash an issue-5 native placement matrix."""

import argparse
import collections
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def p95(values):
    if not values:
        return None
    values = sorted(values)
    return values[math.ceil(0.95 * len(values)) - 1]


def audit(root, expected_runs, expected_weights):
    plan = json.loads((root / "plan.json").read_text())
    qos = plan["qos_ms"]
    candidates = {node["node_id"] for node in plan["candidates"]}
    expected_receivers = {"issue5-worker-" + node for node in candidates}
    runs = plan["runs"]
    assert len(runs) == expected_runs, (len(runs), expected_runs)
    assert len({run["id"] for run in runs}) == expected_runs
    assert {run["weight_name"] for run in runs} == expected_weights
    run_dirs = {path.name for path in root.iterdir() if path.is_dir()}
    assert run_dirs == {run["id"] for run in runs}, (run_dirs - {run["id"] for run in runs})
    hashes = {}
    rows_out = []
    paired = collections.defaultdict(dict)
    for spec in runs:
        run_dir = root / spec["id"]
        meta_path = run_dir / "run.json"
        raw_path = run_dir / "requests.jsonl"
        meta = json.loads(meta_path.read_text())
        assert meta["spec"] == spec
        assert meta["candidates"] == plan["candidates"]
        assert meta["configuration"]["weights"] == spec["weights"]
        assert meta["configuration"]["qos_ms"] == qos
        assert meta["configuration"]["ttl_ms"] == 3600000
        assert meta["configuration"]["node_execution_ms"] == plan["node_execution_ms"] | {"issue5-origin": qos + 1}
        assert meta["configuration"]["network_ms"] == {node["node_id"]: node["network_ms"] for node in plan["candidates"]}
        latency_scale = meta["configuration"]["latency_scale_ms"]
        raw = [json.loads(line) for line in raw_path.open()]
        expected = spec["rate"] * spec["duration_ms"] // 1000
        assert len(raw) == expected == meta["submitted"], spec["id"]
        assert len({row["id"] for row in raw}) == expected
        assert all(row["id"] == f"{spec['id']}-request-{i:06d}" for i, row in enumerate(raw))
        assert all(len(row["candidates"]) == 2 for row in raw)
        assert all({item["node_id"] for item in row["candidates"]} == candidates for row in raw)
        for row in raw:
            assert row["reply_unix_ns"] >= row["submit_unix_ns"]
            observed_latency = (row["reply_unix_ns"] - row["submit_unix_ns"]) / 1e6
            assert abs(row["latency_ms"] - observed_latency) < 1e-7
            assert row["on_time"] == (row["status"] == "success" and row["latency_ms"] <= qos)
            if row.get("receiver"):
                assert row["receiver"] in expected_receivers
            if row["status"] == "success":
                assert row.get("checksum") == "1743019207182703820"
            for candidate in row["candidates"]:
                if candidate["eligible"]:
                    weights = spec["weights"]
                    score = (weights[0] * candidate["free_fraction"]
                             + weights[1] / (1 + candidate["predicted_latency_ms"] / latency_scale)
                             + weights[2]) / sum(weights)
                    assert abs(candidate["score"] - score) < 1e-9
        success = [row for row in raw if row["status"] == "success"]
        on_time = sum(row["on_time"] for row in raw)
        failures = collections.Counter(row["status"] for row in raw if row["status"] != "success")
        placements = collections.Counter(row.get("receiver", "") for row in raw)
        assert meta["success"] == len(success)
        assert meta["on_time"] == on_time
        assert meta["no_receiver_timeout"] == failures["no_receiver_timeout"]
        assert abs(meta["on_time_goodput_rps"] - on_time * 1000 / spec["duration_ms"]) < 1e-9
        key = (spec["rate"], spec["pattern"], spec["repeat"])
        paired[key][spec["weight_name"]] = (spec["seed"], spec["duration_ms"], spec["payload_bytes"],
                                              [row["scheduled_offset_ns"] for row in raw])
        rows_out.append({
            "run_id": spec["id"], "rate": spec["rate"], "pattern": spec["pattern"],
            "repeat": spec["repeat"], "weight": spec["weight_name"],
            "submitted": expected, "success": len(success), "on_time": on_time,
            "goodput_rps": on_time * 1000 / spec["duration_ms"],
            "cohort_on_time_pct": 100 * on_time / expected,
            "success_latency_mean_ms": statistics.mean(row["latency_ms"] for row in success) if success else None,
            "success_latency_p95_ms": p95([row["latency_ms"] for row in success]),
            "failure_statuses": dict(failures), "placements": dict(placements),
        })
        hashes[str(meta_path.relative_to(root))] = digest(meta_path)
        hashes[str(raw_path.relative_to(root))] = digest(raw_path)
    assert len(paired) == expected_runs // len(expected_weights)
    for key, group in paired.items():
        assert set(group) == expected_weights, key
        assert all(value == group["equal"] for value in group.values()), key
    warmups = json.loads((root / "warmups.json").read_text())
    assert len(warmups) == 2
    assert {row["receiver"] for row in warmups} == expected_receivers
    assert all(row["status"] == "success" for row in warmups)
    resource_files = sorted(root.glob("*resources.jsonl"))
    assert len(resource_files) == 3, [path.name for path in resource_files]
    for path in resource_files:
        samples = [json.loads(line) for line in path.open()]
        assert len(samples) >= 2 and all(row["rss_bytes"] > 0 for row in samples)
        hashes[path.name] = digest(path)
    for path in (root / "plan.json", root / "warmups.json"):
        hashes[path.name] = digest(path)
    return rows_out, hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--expected-runs", type=int, default=90)
    parser.add_argument("--expected-weights", default="equal,latency,neighbor")
    args = parser.parse_args()
    rows, hashes = audit(args.root, args.expected_runs, set(args.expected_weights.split(",")))
    with (args.root / "audited_runs.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
                             for key, value in row.items()})
    (args.root / "sha256_manifest.json").write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"runs": len(rows), "blocks": len(rows) // len(set(args.expected_weights.split(","))),
                      "requests": sum(row["submitted"] for row in rows),
                      "success": sum(row["success"] for row in rows),
                      "on_time": sum(row["on_time"] for row in rows),
                      "files_hashed": len(hashes)}, indent=2))


if __name__ == "__main__":
    main()
