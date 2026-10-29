#!/usr/bin/env python3
"""Measure local used-stub TTL with real kernels and observed reservations.

Nine isolated processes by default: TTL 100/500/2000 ms, three repeats each,
with the same three post-burst gaps (250/1000/2500 ms) in every run.
The app executor persists across stub expiration; this is not container eviction
or distributed cold-start evidence. Run serially after other experiment matrices.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time


def validate_run(root, expected_requests, bursts):
    meta = json.loads((root / "run.json").read_text())
    if meta.get("status") != "completed":
        raise ValueError("TTL driver did not complete successfully")
    with (root / "raw.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != expected_requests or {int(r["req_id"]) for r in rows} != set(range(expected_requests)):
        raise ValueError("missing or duplicate request outcomes")
    events = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]
    created = [e for e in events if e["kind"] == "stub_created"]
    stopped = [e for e in events if e["kind"] == "stub_stop_handled"]
    if not 1 <= meta["stub_creations"] <= bursts or len(created) != meta["stub_creations"] or len(stopped) != len(created):
        raise ValueError("every actually created stub must expire through the runtime stop handler")
    if {e["detail"]["stub_id"] for e in created} != {e["detail"]["stub_id"] for e in stopped}:
        raise ValueError("created/stopped stub identities differ")
    gaps, ttl = meta["idle_gaps_ns"], meta["effective_config"]["ttl_ms"] * 1000000
    margin = meta["observation_margin_ns"]
    if all(abs(gap - ttl) > margin for gap in gaps[:-1]):
        expected_creations = 1 + sum(gap > ttl + margin for gap in gaps[:-1])
        if len(created) != expected_creations:
            raise ValueError("observed recreation count disagrees with separated retention/expiration gaps")
    outcomes = [e for e in events if e["kind"] == "burst_outcomes_recorded"]
    if len(outcomes) != bursts or any(e["detail"]["successes"] < 1 for e in outcomes):
        raise ValueError("each measured idle interval must follow a successfully used stub")
    inventories = [e["detail"]["owned_container_pids"] for e in events if e["kind"] == "container_inventory"]
    if len(inventories) < bursts + len(created) or any(item != inventories[0] for item in inventories):
        raise ValueError("missing evidence of unchanged executor/container identity")
    resources = [json.loads(line) for line in (root / "resources.jsonl").read_text().splitlines()]
    if not resources or resources[-1]["remaining_cpu"] != 2 or resources[-1]["alive_stubs"] != 0:
        raise ValueError("last measured state does not show released reservations")
    if meta["allocation"]["idle_reserved_cpu_ns"] <= 0:
        raise ValueError("no observed idle allocation interval")
    return meta, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ttls-ms", default="100,500,2000")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--burst-requests", type=int, default=4)
    parser.add_argument("--burst-rps", type=int, default=20)
    parser.add_argument("--idle-gaps-ms", default="250,1000,2500")
    args = parser.parse_args()
    ttls = [int(value) for value in args.ttls_ms.split(",")]
    gaps = [int(value) for value in args.idle_gaps_ms.split(",")]
    if len(set(ttls)) != len(ttls) or min(ttls) < 20 or len(gaps) != 3 or min(gaps) <= 0 or gaps[-1] <= max(ttls) + 50:
        parser.error("unique TTLs >=20ms and three positive gaps, final gap greater than max TTL +50ms required")
    if args.repeats < 1 or args.burst_requests < 1 or args.burst_rps < 1:
        parser.error("positive repeats/requests/rate required")
    calibration = json.loads((args.calibration / "summary.json").read_text())["cpu"]
    config = json.loads((args.calibration / "cpu_config.json").read_text())
    if config["qos_ms"] != 500:
        parser.error("this matrix requires the frozen CPU calibration with QoS 500ms")
    if config["execution_ms"] != max(1, calibration["execution_estimate_ms"]):
        parser.error("CPU configuration estimate does not match frozen calibration summary")
    args.out.mkdir(parents=True, exist_ok=False)
    binary = args.out.resolve() / "revision-ttl"
    shutil.copy2(args.binary, binary)
    config_path = args.out.resolve() / "calibrated_cpu_config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    jobs = [dict(ttl_ms=ttl, repeat=repeat) for ttl in ttls for repeat in range(1, args.repeats + 1)]
    random.Random(20260930).shuffle(jobs)
    manifest = dict(status="running", jobs=jobs, started_unix_ns=time.time_ns(),
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                    calibration=calibration, cpus=2, bursts=len(gaps),
                    requests_per_burst=args.burst_requests, burst_rps=args.burst_rps,
                    idle_gaps_ms=gaps, observation_margin_ms=50, order_seed=20260930,
                    scope="local used function-stub expiration/recreation; retained one-worker app pool",
                    allocation_scope="observed logical CPU reservation integral, not utilization or reclaimed container memory")
    path = args.out / "matrix.json"
    def save():
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    save()
    job = None
    try:
        for index, job in enumerate(jobs):
            root = args.out / f"ttl{job['ttl_ms']}" / f"run{job['repeat']}"
            deadline = int(sum(gaps)/1000 + len(gaps) * (args.burst_requests / args.burst_rps + 5) + 30)
            command = [sys.executable, str(Path(__file__).with_name("run_checked.py")),
                       "--out", str(root), "--timeout", str(deadline), "--", str(binary),
                       "--config", str(config_path), "--ttl-ms", str(job["ttl_ms"]),
                       "--burst-requests", str(args.burst_requests),
                       "--burst-rps", str(args.burst_rps), "--idle-gaps-ms", args.idle_gaps_ms,
                       "--work", json.dumps(calibration["workload"]), "--out", str(root / "run")]
            job.update(status="running", path=str(root.relative_to(args.out)), command=command)
            save()
            completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            job["runner_output"] = completed.stdout
            if completed.returncode:
                raise RuntimeError(f"TTL run failed; raw evidence retained in {root}")
            meta, rows = validate_run(root / "run", len(gaps) * args.burst_requests, len(gaps))
            job.update(status="completed", requests=len(rows), successes=meta["successes"],
                       allocation=meta["allocation"], stub_creations=meta["stub_creations"])
            save()
            print(f"{index+1}/{len(jobs)} TTL={job['ttl_ms']}ms repeat={job['repeat']} "
                  f"success={meta['successes']}/{len(rows)} idle_reserved_cpu_s="
                  f"{meta['allocation']['idle_reserved_cpu_ns']/1e9:.6f}", flush=True)
    except BaseException as error:
        if job is not None and job.get("status") == "running":
            job.update(status="failed", error=str(error))
        manifest.update(status="failed", error=str(error), finished_unix_ns=time.time_ns())
        save()
        raise
    manifest.update(status="completed", finished_unix_ns=time.time_ns())
    save()


if __name__ == "__main__":
    main()
