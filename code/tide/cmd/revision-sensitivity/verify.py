#!/usr/bin/env python3
"""Independently replay all recorded FIFO/score decisions from saved inputs."""
import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path


def verify(root, output):
    manifest = json.loads((output / "manifest.json").read_text())
    for name, expected in manifest["source_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
    for name, expected in manifest["artifact_sha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected, name
    traces = {t["id"]: t for t in json.loads((output / "traces.json").read_text())}
    runs = {r["run_id"]: r for r in map(json.loads, (output / "runs.jsonl").read_text().splitlines())}
    summaries = {r["run_id"]: r for r in json.loads((output / "summary.json").read_text())}
    grid = {tuple(round(w * 10) for w in row) for row in manifest["weight_grid"]}
    assert len(grid) == 66 and all(sum(w) == 10 for w in grid)
    configurations = set()
    for run in runs.values():
        key = (run["trace_id"], run["network_profile"], tuple(round(w * 10) for w in run["weights_resource_latency_device"]))
        assert key not in configurations
        configurations.add(key)
    for trace_id in traces:
        for network in ("low", "high"):
            assert {w for tid, net, w in configurations if tid == trace_id and net == network} == grid
    total = 0
    previous = None
    seen = set()
    current = None

    def finish_run():
        if current is None:
            return
        run, trace, finish, counts, latencies = current
        metrics = summaries[run["run_id"]]["metrics"]
        assert counts["arrivals"] == len(trace["requests"])
        for key, count in counts.items():
            assert metrics[key] == count, (run["run_id"], key)
        seconds = trace["duration_ticks"] * trace["tick_ms"] / 1000
        assert abs(metrics["window_goodput_rps"] - counts["completed_within_window"] / seconds) < 1e-10
        assert abs(metrics["actual_arrival_rps"] - counts["arrivals"] / seconds) < 1e-10
        expected_yield = counts["accepted"] / counts["arrivals"] if counts["arrivals"] else 0
        assert abs(metrics["cohort_yield"] - expected_yield) < 1e-10
        if latencies:
            assert abs(metrics["mean_accepted_latency_ms"] - sum(latencies) / len(latencies)) < 1e-9
            latencies.sort()
            assert abs(metrics["p95_accepted_latency_ms"] - latencies[math.ceil(.95 * len(latencies)) - 1]) < 1e-9

    with gzip.open(output / "requests.csv.gz", "rt", newline="") as stream:
        for row in csv.DictReader(stream):
            run_id = row["run_id"]
            if run_id != previous:
                finish_run()
                assert run_id not in seen
                seen.add(run_id)
                run = runs[run_id]
                trace = traces[run["trace_id"]]
                current = (run, trace, [0] * len(run["nodes"]), dict.fromkeys(("arrivals", "accepted", "rejected", "completed_within_window", "drained_after_window"), 0), [])
                previous = run_id
            run, trace, finish, counts, latencies = current
            request = trace["requests"][counts["arrivals"]]
            assert row["trace_id"] == trace["id"] and int(row["request_id"]) == request["id"]
            assert row["kind"] == request["kind"]
            for key in ("arrival_ticks", "decision_ticks", "deadline_ticks"):
                assert int(row[key]) == request[key]
            best = None
            if request["decision_ticks"] < request["deadline_ticks"]:
                for node_index, node in enumerate(run["nodes"]):
                    start = max(request["decision_ticks"], finish[node_index])
                    end = start + request["service_ticks_by_node"][node_index]
                    response = end + node["network_response_ticks"]
                    if response > request["deadline_ticks"]:
                        continue
                    latency = (response - request["arrival_ticks"]) * trace["tick_ms"]
                    wr, wl, wd = run["weights_resource_latency_device"]
                    score = (wr * node["fixed_free_cpu_fraction"] + wl / (1 + latency / run["latency_scale_ms"]) + wd * node["device_weight"]) / (wr + wl + wd)
                    if best is None or score > best[0]:
                        best = (score, node_index, start, end, response, latency)
            counts["arrivals"] += 1
            if best is None:
                assert row["accepted"] == "false" and int(row["node_index"]) == -1
                assert row["service_ticks"] == row["latency_ms"] == ""
                assert all(int(row[k]) == -1 for k in ("executor_start_ticks", "executor_finish_ticks", "response_ticks"))
                assert row["status"] == ("expired" if request["decision_ticks"] >= request["deadline_ticks"] else "infeasible")
                counts["rejected"] += 1
            else:
                score, node_index, start, end, response, latency = best
                assert row["accepted"] == "true" and int(row["node_index"]) == node_index
                assert int(row["service_ticks"]) == request["service_ticks_by_node"][node_index]
                assert int(row["executor_start_ticks"]) == start and int(row["executor_finish_ticks"]) == end and int(row["response_ticks"]) == response
                assert abs(float(row["latency_ms"]) - latency) < 1e-9
                assert abs(float(row["score"]) - score) < 1e-12
                within = response < trace["duration_ticks"]
                assert row["completed_within_window"] == str(within).lower()
                assert row["status"] == "completed_on_time"
                counts["accepted"] += 1
                counts["completed_within_window" if within else "drained_after_window"] += 1
                finish[node_index] = end
                latencies.append(latency)
            total += 1
    finish_run()
    # A zero-arrival trace has summary rows but no CSV decisions.
    assert seen == {rid for rid, run in runs.items() if traces[run["trace_id"]]["requests"]}
    assert total == manifest["request_decision_count"]
    report = {
        "verified_decisions": total,
        "verified_runs": len(runs),
        "shared_input_traces": len(traces),
        "full_66_point_grid_for_every_trace_and_network": True,
        "source_and_artifact_hashes_match": True,
        "independent_fifo_score_replay": "PASS",
        "summary_count_rate_and_latency_checks": "PASS",
        "validator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--results", type=Path, default=Path("results-revision-20260928/weights-02"))
    args = parser.parse_args()
    verify(args.source_root, args.results)
