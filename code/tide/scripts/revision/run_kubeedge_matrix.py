#!/usr/bin/env python3
"""Measure real-kernel HTTP requests in a KubeEdge EdgeCore-managed Pod.

Runs one Pod per cell, serially. The cluster is the isolated Kubernetes 1.32
kind testbed; each Pod is pinned to the KubeEdge 1.23.1 edge node. Pod startup
is outside the request window. This is an edge-container HTTP FCFS baseline,
not a native KubeEdge per-request scheduler or a physical multi-host test.
"""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import urllib.request


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kubectl(kubeconfig, *args, input=None):
    return subprocess.check_output(["kubectl", "--kubeconfig", str(kubeconfig), *args],
                                   input=input, text=True, stderr=subprocess.STDOUT)


def assert_edge_node(kubeconfig, name):
    node = json.loads(kubectl(kubeconfig, "get", "node", name, "-o", "json"))
    version = node["status"]["nodeInfo"]["kubeletVersion"]
    if "kubeedge-v1.23.1" not in version or not any(
            c["type"] == "Ready" and c["status"] == "True" for c in node["status"]["conditions"]):
        raise RuntimeError(f"not a Ready KubeEdge 1.23.1 node: {name} {version}")
    if "node-role.kubernetes.io/edge" not in node["metadata"]["labels"]:
        raise RuntimeError("edge-role label missing")
    return node, version


def ready_http(url):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.1)
    raise RuntimeError("edge worker HTTP endpoint did not become reachable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workloads", default="cpu,memory,io")
    parser.add_argument("--rps", default="5,10,20,40")
    parser.add_argument("--duration", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--qos-classes-ms", default="")
    args = parser.parse_args()
    if any(not p.is_file() for p in (args.binary, args.kubeconfig, args.template)):
        parser.error("binary, kubeconfig and worker template must exist")
    calibration_all = json.loads((args.calibration / "summary.json").read_text())
    kinds, rates = args.workloads.split(","), [int(v) for v in args.rps.split(",")]
    classes = [int(v) for v in args.qos_classes_ms.split(",")] if args.qos_classes_ms else []
    if (not kinds or len(kinds) != len(set(kinds)) or any(k not in calibration_all for k in kinds)
            or min(rates + [args.duration, args.repeats]) <= 0
            or (classes and (len(classes) < 2 or min(classes) <= 0 or len(set(classes)) != len(classes)))):
        parser.error("invalid matrix configuration")
    template = json.loads(args.template.read_text())
    namespace, edge_name = template["metadata"]["namespace"], template["spec"]["nodeName"]
    node, version = assert_edge_node(args.kubeconfig, edge_name)
    addresses = [entry["address"] for entry in node["status"]["addresses"]
                 if entry["type"] == "InternalIP"]
    if len(addresses) != 1:
        parser.error("expected one EdgeCore InternalIP")
    if not template["spec"]["hostNetwork"] or template["spec"]["containers"][0]["resources"]["limits"]["cpu"] != "2":
        parser.error("expected hostNetwork and two-CPU worker limit")
    args.out.mkdir(parents=True, exist_ok=False)
    binary = args.out.resolve() / "revision-http-run"
    frozen_template = args.out.resolve() / "kubeedge_worker.json"
    shutil.copy2(args.binary, binary)
    shutil.copy2(args.template, frozen_template)
    calibration = {k: calibration_all[k] for k in kinds}
    jobs = [dict(system="kubeedge", workload=kind, rps=rate, repeat=rep,
                 requests=rate * args.duration)
            for kind in kinds for rate in rates for rep in range(1, args.repeats + 1)]
    random.Random(20261001).shuffle(jobs)
    matrix = dict(status="running", system="kubeedge", jobs=jobs, duration_s=args.duration,
                  repeats=args.repeats, cpus=2, order_seed=20261001,
                  calibration=calibration, binary_sha256=sha(binary),
                  qos_classes_ms=classes or None,
                  template_sha256=sha(frozen_template), kubeconfig_sha256=sha(args.kubeconfig),
                  edge_node_name=edge_name, edge_node_version=version,
                  edge_node_uid=node["metadata"]["uid"],
                  endpoint=f"http://{addresses[0]}:19280", started_unix_ns=time.time_ns(),
                  queue_policy="serial FCFS in matched HTTP driver; queue-inclusive external timeout; no predictive QoS admission",
                  scope="KubeEdge EdgeCore-managed Pod in isolated kind Kubernetes 1.32; hostNetwork within edge container; not a physical multi-host comparison",
                  worker_cpu_scope="not sampled by this runner; host /proc PID is not the nested edge-container PID")
    path = args.out / "matrix.json"

    def save():
        path.write_text(json.dumps(matrix, indent=2) + "\n")

    save()
    job = None
    try:
        for index, job in enumerate(jobs):
            root = args.out / "kubeedge" / job["workload"] / f"rps{job['rps']}" / f"run{job['repeat']}"
            root.mkdir(parents=True)
            pod = json.loads(frozen_template.read_text())
            name = f"tmc-ke-run-{index:03d}"
            pod["metadata"]["name"] = name
            uid = None
            job.update(status="running", path=str(root.relative_to(args.out)), pod=name)
            save()
            try:
                created = json.loads(kubectl(args.kubeconfig, "create", "-f", "-", "-o", "json",
                                               input=json.dumps(pod)))
                uid = created["metadata"]["uid"]
                start_ns = time.monotonic_ns()
                kubectl(args.kubeconfig, "wait", "-n", namespace, "--for=condition=Ready",
                        f"pod/{name}", "--timeout=90s")
                ready_http(matrix["endpoint"])
                observed = json.loads(kubectl(args.kubeconfig, "get", "pod", "-n", namespace,
                                              name, "-o", "json"))
                if observed["metadata"]["uid"] != uid or observed["spec"]["nodeName"] != edge_name:
                    raise RuntimeError("edge pod identity or placement changed")
                setup = dict(system="kubeedge", pod=observed, edge_node_version=version,
                             edge_node_uid=node["metadata"]["uid"],
                             pod_ready_observed_ns=time.monotonic_ns() - start_ns,
                             endpoint=matrix["endpoint"], startup_semantics="Pod creation/readiness excluded from request window")
                setup_path = root / "setup.json"
                setup_path.write_text(json.dumps(setup, indent=2) + "\n")
                measured = root / "measurement"
                command = [sys.executable, str(Path(__file__).with_name("run_checked.py")),
                           "--out", str(measured), "--timeout", str(args.duration + 30),
                           "--", str(binary), "--requests", str(job["requests"]),
                           "--rps", str(job["rps"]), "--url", matrix["endpoint"] + "/run",
                           "--system", "KubeEdge 1.23.1 EdgeCore HTTP FCFS", "--qos-ms", "500",
                           "--work", json.dumps(calibration[job["workload"]]["workload"]),
                           "--out", str(measured / "run")]
                if classes:
                    command.extend(["--qos-classes-ms", ",".join(str(value) for value in classes)])
                job["command"] = command
                save()
                result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)
                job["runner_output"] = result.stdout
                if result.returncode:
                    raise RuntimeError(f"measurement failed: {root}")
                with (measured / "run" / "raw.csv").open() as stream:
                    rows = list(csv.DictReader(stream))
                if len(rows) != job["requests"] or {int(r["req_id"]) for r in rows} != set(range(job["requests"])):
                    raise RuntimeError(f"incomplete offered trace: {root}")
                if classes:
                    kind = calibration[job["workload"]]["workload"]["kind"]
                    if any(float(row["qos_ms"]) != classes[int(row["req_id"]) % len(classes)]
                           or row["fn_name"] != f"{kind}-qos{classes[int(row['req_id']) % len(classes)]}"
                           for row in rows):
                        raise RuntimeError(f"deadline class mismatch: {root}")
                job.update(status="completed", successes=sum(int(r["status"]) == 200 for r in rows),
                           setup_sha256=sha(setup_path))
            finally:
                if uid is not None:
                    current = json.loads(kubectl(args.kubeconfig, "get", "pod", "-n", namespace,
                                                 name, "-o", "json"))
                    if current["metadata"]["uid"] != uid:
                        raise RuntimeError("pod identity changed; refusing cleanup")
                    kubectl(args.kubeconfig, "delete", "pod", "-n", namespace, name,
                            "--wait=true", "--timeout=30s")
            save()
            print(f"{index+1}/{len(jobs)} kubeedge {job['workload']} rps={job['rps']} "
                  f"repeat={job['repeat']} successes={job['successes']}/{job['requests']}", flush=True)
    except BaseException as error:
        if job and job.get("status") == "running":
            job.update(status="failed", error=str(error))
        matrix.update(status="failed", error=str(error), finished_unix_ns=time.time_ns())
        save()
        raise
    matrix.update(status="completed", finished_unix_ns=time.time_ns())
    save()


if __name__ == "__main__":
    main()
