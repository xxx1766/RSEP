#!/usr/bin/env python3
"""Run paired measured-kernel HTTP faults on two isolated-affinity Linux workers.

This adapter does not exercise Tide's native distributed event-return path.
It creates no containers, changes no host network rules, and kills only its own
worker processes. Run serially after other experiment matrices finish.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time
import urllib.error
import urllib.request


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fresh_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def state(url):
    with urllib.request.urlopen(url + "/state", timeout=1) as response:
        return json.load(response)


def affinity(pid):
    return {p.name: sorted(os.sched_getaffinity(int(p.name)))
            for p in Path(f"/proc/{pid}/task").iterdir()}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--local-cpus", default="0,1")
    parser.add_argument("--remote-cpus", default="2,3")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--phase-seconds", type=float, default=3)
    parser.add_argument("--rps", type=float, default=70)
    parser.add_argument("--estimate-ms", type=float, default=22)
    parser.add_argument("--qos-ms", type=float, default=200)
    args = parser.parse_args()
    if not hasattr(os, "sched_getaffinity"):
        raise RuntimeError("formal matrix requires Linux CPU affinity")
    local_cpus, remote_cpus = [int(c) for c in args.local_cpus.split(",")], [int(c) for c in args.remote_cpus.split(",")]
    allowed = os.sched_getaffinity(0)
    if len(set(local_cpus)) != 2 or len(set(remote_cpus)) != 2 or set(local_cpus) & set(remote_cpus) or not set(local_cpus + remote_cpus) <= allowed:
        raise ValueError("need two disjoint, allowed 2-CPU worker affinity sets")
    if args.repetitions < 1 or args.phase_seconds <= 0 or args.rps <= 0:
        raise ValueError("invalid experiment size")
    args.binary = args.binary.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    original_binary = args.binary
    shutil.copy2(original_binary, args.out / "revision-fault")
    args.binary = (args.out / "revision-fault").resolve()
    sources = list((args.source_root / "cmd/revision-fault").glob("*.go"))
    sources += [args.source_root / "cmd/revision-fault/run_matrix.py", args.source_root / "pkg/schedpolicy/score.go", args.source_root / "pkg/revisionworkload/workload.go"]
    manifest = {
        "evidence_type": "measured-kernel HTTP fault adapter, NOT native Tide distributed recovery",
        "binary_sha256": digest(args.binary),
        "original_binary_path": str(original_binary),
        "frozen_binary_path": "revision-fault",
        "source_sha256": {str(p.relative_to(args.source_root)): digest(p) for p in sources},
        "command": os.sys.argv,
        "host_uname": list(os.uname()),
        "controller_allowed_cpus": sorted(allowed),
        "worker_affinity": [local_cpus, remote_cpus],
        "worker_gomaxprocs": 1,
        "worker_concurrency": 1,
        "cpu_scope": "two CPU affinity budget per worker; no cgroup quota or exclusivity claimed",
        "repetitions": args.repetitions,
        "runs": [],
        "status": "running",
    }
    write_json(args.out / "manifest.json", manifest)
    for repetition in range(args.repetitions):
        seed = 2026092800 + repetition
        policies = [True, False] if repetition % 2 == 0 else [False, True]
        paired_trace = None
        for eligible in policies:
            run_id = f"seed{seed}-local{'on' if eligible else 'off'}"
            parent = args.out / run_id
            parent.mkdir()
            processes = []
            logs = []
            setup = []
            try:
                for label, cpus in (("local", local_cpus), ("remote", remote_cpus)):
                    port = fresh_port()
                    url = f"http://127.0.0.1:{port}"
                    worker_log = parent / f"{label}-worker.jsonl"
                    stderr = (parent / f"{label}-process.log").open("w")
                    logs.append(stderr)
                    command = ["taskset", "-c", ",".join(map(str, cpus)), str(args.binary), "-mode=worker", f"-listen=127.0.0.1:{port}", f"-worker-log={worker_log}"]
                    process = subprocess.Popen(command, env=dict(os.environ, GOMAXPROCS="1"), stdout=stderr, stderr=subprocess.STDOUT)
                    processes.append(process)
                    limit = time.monotonic() + 10
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError(f"{label} worker exited during startup")
                        try:
                            ready = state(url)
                            if ready["pid"] != process.pid:
                                raise RuntimeError("endpoint belongs to another process")
                            break
                        except (OSError, urllib.error.URLError):
                            if time.monotonic() > limit:
                                raise RuntimeError("worker readiness timeout")
                            time.sleep(.05)
                    observed = affinity(process.pid)
                    if any(mask != sorted(cpus) for mask in observed.values()):
                        raise RuntimeError("worker affinity mismatch")
                    observed_binary = digest(Path(f"/proc/{process.pid}/exe"))
                    if observed_binary != manifest["binary_sha256"]:
                        raise RuntimeError("worker binary identity mismatch")
                    setup.append({"node": label, "pid": process.pid, "url": url, "command": command, "threads_affinity_before": observed, "binary_sha256": observed_binary, "state_before": ready})
                write_json(parent / "setup.json", setup)
                command = [str(args.binary), f"-out={parent / 'run'}", f"-run-id={run_id}", f"-local-url={setup[0]['url']}", f"-remote-url={setup[1]['url']}", f"-local-eligible={str(eligible).lower()}", f"-seed={seed}", f"-phase={args.phase_seconds}s", f"-rps={args.rps}", f"-estimate={args.estimate_ms}ms", f"-qos={args.qos_ms}ms"]
                if paired_trace is not None:
                    command.append(f"-trace={paired_trace}")
                with (parent / "controller.log").open("w") as log:
                    completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=3 * args.phase_seconds + 20)
                if completed.returncode != 0:
                    raise RuntimeError(f"controller failed with exit {completed.returncode}")
                trace = parent / "run/trace.json"
                if paired_trace is not None and digest(trace) != digest(paired_trace):
                    raise RuntimeError("paired offered traces differ")
                paired_trace = trace
                # Remote response cancellation does not cancel the kernel. Wait
                # for actual worker idleness before terminating owned processes.
                for item, process in zip(setup, processes):
                    limit = time.monotonic() + 5
                    while state(item["url"])["active"]:
                        if time.monotonic() > limit:
                            raise RuntimeError("actual worker execution did not drain")
                        time.sleep(.02)
                    item["state_after"] = state(item["url"])
                    if item["state_after"]["pid"] != process.pid:
                        raise RuntimeError("worker endpoint identity changed")
                    item["threads_affinity_after"] = affinity(process.pid)
                    expected = local_cpus if item["node"] == "local" else remote_cpus
                    if any(mask != sorted(expected) for mask in item["threads_affinity_after"].values()):
                        raise RuntimeError("worker affinity changed")
                write_json(parent / "setup.json", setup)
                manifest["runs"].append({"run_id": run_id, "seed": seed, "local_eligible": eligible, "exit_status": 0, "trace_sha256": digest(trace), "command": command})
                write_json(args.out / "manifest.json", manifest)
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                for log in logs:
                    log.close()
    manifest["status"] = "completed"
    manifest["artifact_sha256"] = {str(p.relative_to(args.out)): digest(p) for p in args.out.rglob("*") if p.is_file() and p != args.out / "manifest.json"}
    write_json(args.out / "manifest.json", manifest)
    print(json.dumps({"status": "completed", "runs": len(manifest["runs"]), "out": str(args.out)}))


if __name__ == "__main__":
    main()
