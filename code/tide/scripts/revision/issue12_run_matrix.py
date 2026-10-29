#!/usr/bin/env python3
"""Run the frozen Beijing–Nanjing Tide/Kubernetes pairs without replacing failed cells."""

import argparse
import hashlib
import json
from pathlib import Path
import random
import shlex
import subprocess
import time

from issue12_audit import audit_cell


REMOTE = "/tmp/tide-issue12-20261007/formal-v2"
LOCAL = Path(__file__).resolve().parents[2] / "results-issue12-20261007" / "formal-v2"
NODES = {"nj2": ("NJ-k2", "11.0.4.48", "29722", "34683eb2-9e3b-4920-97df-038070131cfe"),
         "nj3": ("NJ-k3", "11.0.4.49", "29723", "420a21b3-1f90-4ed9-b6b5-089185fde01d")}
WORK = {"kind": "cpu", "bytes": 65536, "iterations": 30}
CHECKSUM = 1743019207182703820
POD_PIDS = {"nj2": 2424221, "nj3": 51282}
TIDE_SHA256 = "009f3deb29f3995853a2f1a9cba68ff457c0003f651f6bce29ea3ea3fb5d84fe"
CLIENT_SHA256 = "afde41adfe376d1668086a9f17768a754d2516a12ed1c43725f7557e1493bf72"
SSH_BASE = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3"]
NJ2_KEYS = ["-o", "UserKnownHostsFile=/private/tmp/tide-issue12-known-hosts",
            "-o", "StrictHostKeyChecking=yes"]


def make_plan(seed: int) -> list[dict]:
    rng = random.Random(seed)
    cells = [dict(id=f"{pattern}-p{producers}-r{repeat}", pattern=pattern,
                  producers=producers, repeat=repeat,
                  window_ms=270000 if pattern == "gradual" else 240000)
             for pattern in ("gradual", "burst") for producers in (1, 2) for repeat in (1, 2, 3)]
    rng.shuffle(cells)
    first = ["tide"] * 6 + ["k8s"] * 6
    rng.shuffle(first)
    for cell, initial in zip(cells, first):
        cell["order"] = [initial, "k8s" if initial == "tide" else "tide"]
    return cells


def origin_config(cell: dict) -> dict:
    name = cell["id"]
    return {"listen": "10.212.12.2:29582", "advertise_addr": "9.0.3.35:29582",
            "candidates": [{"node_id": node, "address": f"{NODES[node][1]}:{NODES[node][2]}",
                            "total_cpu": 4, "network_ms": 65} for node in ("nj2", "nj3")],
            "workload": WORK, "output_dir": f"{REMOTE}/{name}/tide-output",
            "resource_path": f"{REMOTE}/{name}/bj-origin-resources.jsonl",
            "qos_ms": 135, "warmup_qos_ms": 1000, "execution_ms": 2,
            "node_execution_ms": {"nj2": 2, "nj3": 2},
            "runs": [{"id": name, "rate": 1, "pattern": "steady", "repeat": cell["repeat"],
                      "seed": 20261007 + cell["repeat"], "duration_ms": cell["window_ms"],
                      "payload_bytes": 65536, "weight_name": "equal", "weights": [1, 1, 1],
                      "trace_path": f"{REMOTE}/{name}.jsonl"}]}


def worker_config(cell: dict, node: str) -> dict:
    name = cell["id"]
    return {"node_id": node, "listen": f"{NODES[node][1]}:{NODES[node][2]}",
            "origin_addr": "9.0.3.35:29582", "total_cpu": 4,
            "execution_ms": 2, "qos_ms": 135, "warmup_qos_ms": 1000,
            "workload": WORK, "ready_path": f"{REMOTE}/{name}/{node}-ready.json",
            "resource_path": f"{REMOTE}/{name}/{node}-resources.jsonl"}


def remote_options(host: str) -> list[str]:
    return SSH_BASE + (NJ2_KEYS if host == "NJ-k2" else [])


def call(argv: list[str], *, log: Path | None = None, timeout: int = 60) -> str:
    print("RUN", " ".join(shlex.quote(arg) for arg in argv), flush=True)
    if log:
        with log.open("xb") as stream:
            result = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"exit {result.returncode}: {argv}; see {log}")
        return ""
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"exit {result.returncode}: {argv}: {result.stderr}")
    return result.stdout.strip()


def ssh(host: str, command: str, **kwargs) -> str:
    return call(["ssh", *remote_options(host), host, command], **kwargs)


def scp(source: str, destination: str, host: str) -> None:
    call(["scp", *remote_options(host), source, f"{host}:{destination}"], timeout=120)


def pull(host: str, remote: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            call(["scp", *remote_options(host), "-r", f"{host}:{remote}", str(destination)], timeout=120)
            return
        except RuntimeError:
            if attempt == 2:
                raise
            time.sleep(2)


def cleanup_ssh(host: str, command: str) -> None:
    """Retry only idempotent, PID-checked cleanup after transient SSH closure."""
    for attempt in range(3):
        try:
            ssh(host, command)
            return
        except RuntimeError:
            if attempt == 2:
                raise
            time.sleep(2)


def preflight(cell: dict, folder: Path) -> None:
    pods = json.loads(ssh("NJ-k1", "kubectl -n tmc-revision get pods tmc-issue12-nj2 tmc-issue12-nj3 -o json", timeout=40))
    for pod in pods["items"]:
        node = pod["metadata"]["name"].removeprefix("tmc-issue12-")
        if node not in NODES or pod["metadata"]["uid"] != NODES[node][3] or pod["status"]["containerStatuses"][0]["restartCount"] != 0 or not pod["status"]["containerStatuses"][0]["ready"]:
            raise RuntimeError(f"Pod identity/readiness drift: {pod['metadata']['name']}")
    if len(pods["items"]) != 2:
        raise RuntimeError("not exactly two experiment Pods")
    qdisc = ssh("BJ-k1", "ip netns exec t12-formal13 tc qdisc show dev n12formal13; tc qdisc show dev eth0")
    if "delay 44ms" not in qdisc or "loss 0.1%" not in qdisc or "fq_codel" not in qdisc:
        raise RuntimeError(f"network configuration drift: {qdisc}")
    (folder / "preflight-pods.json").write_text(json.dumps(pods, indent=2) + "\n")
    (folder / "preflight-qdisc.txt").write_text(qdisc + "\n")
    (folder / "preflight-qdisc-stats.txt").write_text(
        ssh("BJ-k1", "ip netns exec t12-formal13 tc -s qdisc show dev n12formal13") + "\n")
    (folder / "preflight-route.txt").write_text(
        ssh("BJ-k1", "ip netns exec t12-formal13 ip route get 11.0.4.48; ip netns exec t12-formal13 ip route get 11.0.4.49") + "\n")
    binary_hash = TIDE_SHA256
    client_hash = CLIENT_SHA256
    for host in ("BJ-k1", "NJ-k2", "NJ-k3"):
        if ssh(host, f"sha256sum {REMOTE}/tide-test").split()[0] != binary_hash:
            raise RuntimeError(f"Tide binary drift on {host}")
    if ssh("BJ-k1", f"sha256sum {REMOTE}/issue12_k8s.py").split()[0] != client_hash:
        raise RuntimeError("Kubernetes client drift")
    expected = hashlib.sha256((LOCAL / "traces" / f"{cell['id']}.jsonl").read_bytes()).hexdigest()
    remote = ssh("BJ-k1", f"sha256sum {shlex.quote(REMOTE + '/' + cell['id'] + '.jsonl')}").split()[0]
    if remote != expected:
        raise RuntimeError("trace SHA-256 drift")
    (folder / "trace-sha256.txt").write_text(expected + "\n")


def start_worker(cell: dict, node: str, local_config: Path) -> int:
    host = NODES[node][0]
    remote_dir = f"{REMOTE}/{cell['id']}"
    scp(str(local_config), f"{remote_dir}/{node}-worker.json", host)
    command = (f"env TMC_ISSUE5_WORKER_CONFIG={shlex.quote(remote_dir + '/' + node + '-worker.json')} "
               f"GOMAXPROCS=1 taskset -c 8,9 {REMOTE}/tide-test -test.run '^TestIssue5WorkerProcess$' "
               f"-test.timeout=12m -test.v > {remote_dir}/{node}-worker.log 2>&1 < /dev/null & echo $!")
    pid = int(ssh(host, command))
    for _ in range(30):
        if ssh(host, f"test -s {remote_dir}/{node}-ready.json && echo ready || true") == "ready":
            return pid
        time.sleep(1)
    raise RuntimeError(f"{node} worker did not become ready, pid={pid}")


def stop_worker(host: str, pid: int) -> None:
    command = f"ps -o command= -p {pid} | grep -F {shlex.quote(REMOTE + '/tide-test')} >/dev/null && kill -TERM {pid} || true"
    cleanup_ssh(host, command)


def run_tide(cell: dict, folder: Path) -> None:
    remote_dir = f"{REMOTE}/{cell['id']}"
    scp(str(folder / "origin.json"), f"{remote_dir}/origin.json", "BJ-k1")
    started = []
    try:
        for node in ("nj2", "nj3"):
            pid = start_worker(cell, node, folder / f"{node}-worker.json")
            started.append((NODES[node][0], pid))
        (folder / "worker-pids.json").write_text(json.dumps(started) + "\n")
        command = (f"ip netns exec t12-formal13 env TMC_ISSUE5_ORIGIN_CONFIG={remote_dir}/origin.json "
                   f"TMC_ISSUE12_PRECONNECT=1 GOMAXPROCS=2 taskset -c 12,13 "
                   f"{REMOTE}/tide-test -test.run '^TestIssue5OriginProcess$' -test.timeout=12m -test.v")
        ssh("BJ-k1", command, log=folder / "origin-ssh.log", timeout=750)
    finally:
        cleanup_errors = []
        for host, pid in reversed(started):
            try:
                stop_worker(host, pid)
            except RuntimeError as error:
                cleanup_errors.append(str(error))
        for node in ("nj2", "nj3"):
            try:
                pull(NODES[node][0], f"{remote_dir}/{node}-worker.log", folder)
                pull(NODES[node][0], f"{remote_dir}/{node}-resources.jsonl", folder)
            except RuntimeError as error:
                print("PULL WARNING", error, flush=True)
        try:
            pull("BJ-k1", f"{remote_dir}/tide-output", folder)
            pull("BJ-k1", f"{remote_dir}/bj-origin-resources.jsonl", folder)
        except RuntimeError as error:
            print("PULL WARNING", error, flush=True)
        if cleanup_errors:
            raise RuntimeError("Tide worker cleanup incomplete: " + "; ".join(cleanup_errors))


def run_k8s(cell: dict, folder: Path) -> None:
    name = cell["id"]
    started = []
    for node in ("nj2", "nj3"):
        host = NODES[node][0]
        pid = POD_PIDS[node]
        if "worker" not in ssh(host, f"ps -o command= -p {pid}"):
            raise RuntimeError(f"Pod process identity drift on {node}")
        remote_dir = f"{REMOTE}/{name}"
        command = (f"python3 {REMOTE}/issue12_sample_process.py --pid {pid} --seconds 360 "
                   f"--out {remote_dir}/{node}-k8s-resources.jsonl > {remote_dir}/{node}-k8s-sampler.log 2>&1 < /dev/null & echo $!")
        started.append((host, int(ssh(host, command)), node))
    (folder / "k8s-sampler-pids.json").write_text(json.dumps(started) + "\n")
    command = (f"ip netns exec t12-formal13 python3 {REMOTE}/issue12_k8s.py "
               f"--trace {REMOTE}/{name}.jsonl --endpoints 11.0.4.48:19282,11.0.4.49:19282 "
               f"--work {shlex.quote(json.dumps(WORK, separators=(',', ':')))} "
               f"--expected-checksum {CHECKSUM} --qos-ms 135 --window-ms {cell['window_ms']} "
               f"--out {REMOTE}/{name}/k8s-output")
    try:
        ssh("BJ-k1", command, log=folder / "k8s-ssh.log", timeout=750)
    finally:
        cleanup_errors = []
        for host, pid, node in started:
            try:
                cleanup_ssh(host, f"ps -o command= -p {pid} | grep -F issue12_sample_process.py >/dev/null && kill -TERM {pid} || true")
                pull(host, f"{REMOTE}/{name}/{node}-k8s-resources.jsonl", folder)
            except RuntimeError as error:
                cleanup_errors.append(str(error))
        try:
            pull("BJ-k1", f"{REMOTE}/{name}/k8s-output", folder)
        except RuntimeError as error:
            print("PULL WARNING", error, flush=True)
        if cleanup_errors:
            raise RuntimeError("K8s sampler cleanup incomplete: " + "; ".join(cleanup_errors))


def run_one(cell: dict) -> None:
    folder = LOCAL / "raw" / cell["id"]
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "cell.json").write_text(json.dumps(cell, indent=2) + "\n")
    (folder / "origin.json").write_text(json.dumps(origin_config(cell), indent=2) + "\n")
    for node in ("nj2", "nj3"):
        (folder / f"{node}-worker.json").write_text(json.dumps(worker_config(cell, node), indent=2) + "\n")
    preflight(cell, folder)
    for host in ("BJ-k1", "NJ-k2", "NJ-k3"):
        ssh(host, f"mkdir {shlex.quote(REMOTE + '/' + cell['id'])}")
    for system in cell["order"]:
        (folder / f"{system}-start-utc.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")
        if system == "tide":
            run_tide(cell, folder)
        else:
            run_k8s(cell, folder)
        (folder / f"{system}-post-qdisc-stats.txt").write_text(
            ssh("BJ-k1", "ip netns exec t12-formal13 tc -s qdisc show dev n12formal13") + "\n")
        (folder / f"{system}-end-utc.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")
        print("CELL SYSTEM DONE", cell["id"], system, flush=True)
    result = audit_cell(LOCAL, cell, CHECKSUM, 135)
    with (folder / "audit.json").open("x") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print("PAIR DONE", cell["id"], flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "run-one", "run-all"])
    p.add_argument("--cell")
    args = p.parse_args()
    plan_path = LOCAL / "plan.json"
    if args.action == "prepare":
        cells = make_plan(20261007)
        manifest = {"seed": 20261007, "cells": cells, "workload": WORK, "checksum": CHECKSUM,
                    "qos_ms": 135, "warmup_qos_ms": 1000, "request_payload_bytes": 65536,
                    "tide_binary_sha256": TIDE_SHA256,
                    "k8s_client_sha256": CLIENT_SHA256,
                    "network": "BJ isolated namespace t12-formal13; netem 44ms 5ms loss 0.1%; shared eth0 unmodified",
                    "systems": ["native Tide with two NJ executors", "Kubernetes two prestarted hostNetwork HTTP FCFS Pods"]}
        with plan_path.open("x") as file:
            json.dump(manifest, file, indent=2)
            file.write("\n")
        print(json.dumps(manifest, indent=2))
        return
    cells = json.loads(plan_path.read_text())["cells"]
    if args.action == "run-one":
        matches = [cell for cell in cells if cell["id"] == args.cell]
        if len(matches) != 1:
            p.error("--cell must name one frozen cell")
        run_one(matches[0])
    else:
        for cell in cells:
            existing = LOCAL / "raw" / cell["id"]
            if existing.exists() and not (existing / "audit.json").exists():
                raise RuntimeError(f"incomplete prior cell requires explicit recovery: {cell['id']}")
            if existing.exists():
                continue
            run_one(cell)


if __name__ == "__main__":
    main()
