#!/usr/bin/env python3
"""Bounded mechanism diagnosis with unchanged native binary in a private netns.

Not a performance replication: all three processes share one Linux host.
Only the private namespace's loopback qdisc is modified. Raw records/logs
are retained; child processes and the namespace are removed in finally.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def command(argv):
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--namespace", default="t12-waitdiag")
    a = p.parse_args()
    binary_sha = hashlib.sha256(a.binary.read_bytes()).hexdigest()
    if binary_sha != "009f3deb29f3995853a2f1a9cba68ff457c0003f651f6bce29ea3ea3fb5d84fe":
        raise RuntimeError("requires the exact formal-v2 binary")
    if not a.namespace.startswith("t12-waitdiag") or not a.namespace.replace("-", "").isalnum():
        raise ValueError("diagnostic namespace required")
    if any(line.split()[0] == a.namespace for line in command(["ip", "netns", "list"]).splitlines()):
        raise RuntimeError("namespace already exists")
    a.output.mkdir(parents=True, exist_ok=False)
    cases = [("direct64", 0, 65536, 12, 300),
             ("delay35-small32", 35, 32768, 12, 300),
             ("delay35-original64", 35, 65536, 12, 300),
             ("delay80-expired64", 80, 65536, 12, 300),
             ("delay35-burst64", 35, 65536, 200, 0)]
    save(a.output / "manifest.json", {
        "binary": str(a.binary), "binary_sha256": binary_sha,
        "namespace": a.namespace, "cases": cases, "qos_ms": 135,
        "note": "same-host isolated loopback; per-packet egress delay in both directions; no jitter/loss",
    })
    created = False
    processes, handles = [], []
    try:
        command(["ip", "netns", "add", a.namespace])
        created = True
        prefix = ["ip", "netns", "exec", a.namespace]
        command(prefix + ["ip", "link", "set", "lo", "up"])
        for name, delay, payload, count, gap in cases:
            folder = a.output / name
            folder.mkdir()
            command(prefix + ["tc", "qdisc", "replace", "dev", "lo", "root", "netem", "delay", f"{delay}ms"])
            work = {"kind": "cpu", "bytes": 65536, "iterations": 30}
            env = dict(os.environ, GOMAXPROCS="1", TMC_ISSUE12_DEBUG="1", TMC_ISSUE12_PRECONNECT="1")
            def launch(config, role, label):
                path = folder / (label + ".json")
                save(path, config)
                log = (folder / (label + ".log")).open("w")
                handles.append(log)
                child_env = dict(env)
                child_env["TMC_ISSUE5_" + role.upper() + "_CONFIG"] = str(path)
                proc = subprocess.Popen(prefix + [str(a.binary), "-test.run", "^TestIssue5" + role.title() + "Process$", "-test.v", "-test.timeout", "45s"], env=child_env, stdout=log, stderr=subprocess.STDOUT)
                processes.append(proc)
                return proc
            workers = []
            for index in (2, 3):
                node = f"nj{index}"
                config = {"node_id": node, "listen": f"127.0.0.1:2988{index}",
                          "origin_addr": "127.0.0.1:29881", "total_cpu": 4,
                          "execution_ms": 2, "qos_ms": 135, "warmup_qos_ms": 1000,
                          "workload": work, "ready_path": str(folder / (node + "-ready.json"))}
                workers.append(launch(config, "worker", node))
            deadline = time.monotonic() + 5
            while not all((folder / (f"nj{i}-ready.json")).exists() for i in (2, 3)):
                if time.monotonic() >= deadline or any(w.poll() is not None for w in workers):
                    raise RuntimeError("worker startup failed")
                time.sleep(.05)
            trace = folder / "trace.jsonl"
            trace.write_text("".join(json.dumps({"id": f"{name}-{i:04}", "scheduled_offset_ns": i * gap * 1000000}) + "\n" for i in range(count)))
            origin = {"listen": "127.0.0.1:29881", "candidates": [
                {"node_id": f"nj{i}", "address": f"127.0.0.1:2988{i}", "total_cpu": 4, "network_ms": 65} for i in (2, 3)],
                "workload": work, "output_dir": str(folder / "output"), "qos_ms": 135,
                "warmup_qos_ms": 1000, "execution_ms": 2,
                "runs": [{"id": name, "rate": 1, "pattern": "steady", "repeat": 1,
                          "seed": 20261008, "duration_ms": max(count * gap, 1000),
                          "payload_bytes": payload, "weight_name": "equal", "weights": [1, 1, 1],
                          "trace_path": str(trace)}]}
            process = launch(origin, "origin", "origin")
            rc = process.wait(timeout=40)
            for worker in workers:
                worker.terminate()
                worker.wait(timeout=5)
            save(folder / "exit.json", {"origin": rc, "workers": [w.returncode for w in workers],
                                       "qdisc": command(prefix + ["tc", "-s", "qdisc", "show", "dev", "lo"])})
            print(json.dumps({"case": name, "exit": rc}), flush=True)
            if rc:
                raise RuntimeError("origin failed; inspect preserved log")
            rows = [json.loads(line) for line in (folder / "output" / name / "requests.jsonl").read_text().splitlines()]
            expected = [json.loads(line) for line in trace.read_text().splitlines()]
            if len(rows) != count or [(r["id"], r["scheduled_offset_ns"]) for r in rows] != [(r["id"], r["scheduled_offset_ns"]) for r in expected]:
                raise RuntimeError("frozen trace IDs/count/offsets mismatch")
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        for handle in handles:
            handle.close()
        if created:
            command(["ip", "netns", "delete", a.namespace])
        save(a.output / "cleanup.json", {"namespace_absent": a.namespace not in command(["ip", "netns", "list"]),
                                         "child_returncodes": [p.returncode for p in processes]})


if __name__ == "__main__":
    main()
