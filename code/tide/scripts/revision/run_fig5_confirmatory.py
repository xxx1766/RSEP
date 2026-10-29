#!/usr/bin/env python3
"""Run a prespecified, shuffled real-host ablation matrix and preserve evidence."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time

from audit_fig5_runtime import audit


def save(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def text(path):
    try:
        return Path(path).read_text().strip()
    except (FileNotFoundError, ProcessLookupError):
        return None


def snapshot():
    groups = []
    for path in Path("/sys/fs/cgroup/tide").glob("*/cgroup.procs"):
        pids = text(path)
        if not pids:
            continue
        group = path.parent
        groups.append({"path": str(group), "pids": pids.split(),
                       "cpus": text(group / "cpuset.cpus.effective"),
                       "quota": text(group / "cpu.max"),
                       "cpu_stat": text(group / "cpu.stat"),
                       "thread_affinities": sorted(set(
                           line.split(":", 1)[1].strip()
                           for pid in pids.split()
                           for status in Path(f"/proc/{pid}/task").glob("*/status")
                           for line in (text(status) or "").splitlines()
                           if line.startswith("Cpus_allowed_list:")))})
    return {"time": time.time(), "loadavg": text("/proc/loadavg"),
            "cpu": text("/proc/stat"), "groups": groups}


def verify_case(path, case, duration):
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["status"] == "completed", manifest
    rows = list(csv.DictReader((path / "raw.csv").open()))
    assert len(rows) == case["rps"] * duration, (path, len(rows))
    assert len({row["req_id"] for row in rows}) == len(rows)
    assert hashlib.sha256((path / "raw.csv").read_bytes()).hexdigest() == manifest["raw_sha256"]
    warm = list(csv.DictReader((path / "warmup.csv").open()))
    assert warm and all(row["status"] == "200" for row in warm), "warmup failed"
    result = {"case": case, "requests": len(rows),
              "successes": sum(row["status"] == "200" for row in rows)}
    runtime = path / "runtime.jsonl"
    if runtime.exists():
        result["runtime"] = audit(runtime)
        logs = [json.loads(line) for line in runtime.read_text().splitlines()]
        if case["cell"] in ["C3", "C4"]:
            assert not any(row.get("msg") == "Containerxv2: move a container" for row in logs), "PI-off unexpectedly moved into per-pair cgroup"
        for row in logs:
            if row.get("msg") == "StackPool: received one INIT event":
                eid = row["ExecutorId"]
                source = Path("/tmp/tide/exec-logs") / eid
                dest = path / "worker-logs" / eid
                dest.mkdir(parents=True, exist_ok=True)
                for name in ["stdout", "stderr"]:
                    if (source/name).exists():
                        (dest/name).write_bytes((source/name).read_bytes())
    save(path / "confirmatory-audit.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--duration", type=int, default=10)
    args = parser.parse_args()
    root = args.results.resolve()
    root.mkdir(parents=True, exist_ok=True)
    cases = [{"cell": f"C{cell}", "rps": rate, "repeat": repeat}
             for repeat in range(1, args.repeats+1)
             for cell in range(1, 7) for rate in [1, 2, 4, 8, 16, 32]]
    random.Random(20261004).shuffle(cases)
    plan = {"seed": 20261004, "duration_sec": args.duration, "deadline_sec": 90,
            "repeats": args.repeats, "cpus": "2-7", "driver_cpus": "0-1",
            "physical_cpu_pairs": [[2,3],[4,5],[6,7]],
            "slot_to_cpu": [2,4,3,5,6,7],
            "detections_per_request": 10, "cases": cases,
            "throughput_window": "zero_to_max_submission_duration_or_last_reply",
            "warmup": "one-second offered window at four times worker count; saved separately"}
    plan_path = root / "plan.json"
    if plan_path.exists():
        assert json.loads(plan_path.read_text()) == plan, "existing plan differs"
    else:
        save(plan_path, plan)
    state = {"pid": os.getpid(), "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "status": "running", "completed": 0, "total": len(cases)}
    save(root / "progress.json", state)
    env = os.environ.copy()
    env.update(REPO_ROOT=str(args.repo.resolve()), RESULTS_DIR=str(root),
               DURATION_SEC=str(args.duration), DEADLINE_SEC="90",
               TIDE_MQ_MAX_MESSAGES="10", TIDE_MQ_MESSAGE_BYTES="262144",
               TIDE_COMB_DEV_CPUNUM="16", TIDE_COMB_CPUIDS="2,4,3,5,6,7",
               WORKLOAD_CPUSET="2-7", TIDE_DETECTIONS_PER_REQ="10")
    try:
        for case in cases:
            path = root / case["cell"] / f'rps{case["rps"]}' / f'run{case["repeat"]}'
            state["current"] = case
            save(root / "progress.json", state)
            if (path / "confirmatory-audit.json").exists():
                verify_case(path, case, args.duration)
            else:
                assert not path.exists(), f"preserve incomplete case; inspect before resuming: {path}"
                path.mkdir(parents=True)
                env.update(CELLS=case["cell"], RPS_LIST=str(case["rps"]),
                           REPEAT_START=str(case["repeat"]), REPEATS=str(case["repeat"]))
                with (path/"runner.log").open("w") as log, (path/"host-samples.jsonl").open("w") as samples:
                    cmd = ["taskset", "-c", "0,1", "bash", str(args.repo.resolve()/"scripts/run-matrix.sh")]
                    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
                    while proc.poll() is None:
                        samples.write(json.dumps(snapshot()) + "\n")
                        samples.flush()
                        time.sleep(1)
                    assert proc.returncode == 0, f"runner failed: {path}, exit={proc.returncode}"
                verify_case(path, case, args.duration)
            state["completed"] += 1
            save(root/"progress.json", state)
            print(f'{state["completed"]}/{len(cases)} {case}', flush=True)
        state["status"] = "completed"
    except BaseException as exc:
        state.update(status="failed", error=repr(exc))
        raise
    finally:
        state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        save(root/"progress.json", state)


if __name__ == "__main__":
    main()
