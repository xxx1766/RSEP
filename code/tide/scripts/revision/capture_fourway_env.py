#!/usr/bin/env python3
"""Record nonsecret NJ-k1 software and hardware identity for four-way runs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def command_json(*args):
    return json.loads(subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--ray-python", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                      if "=" in line)
    meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
                   if ":" in line)
    node = command_json("kubectl", "--kubeconfig", str(args.kubeconfig),
                        "get", "node", "tmc-ke-worker", "-o", "json")
    pods = command_json("kubectl", "--kubeconfig", str(args.kubeconfig),
                        "get", "pods", "-A", "-o", "json")
    cloudcore = [p for p in pods["items"] if "cloudcore" in p["metadata"]["name"]]
    result = {
        "captured_unix_ns": time.time_ns(),
        "host": platform.node(), "machine": platform.machine(),
        "kernel": platform.release(),
        "os_pretty_name": os_release.get("PRETTY_NAME", "").strip('"'),
        "cpu_model": next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                           if line.startswith("model name")), None),
        "logical_cpu_count": os.cpu_count(), "allowed_cpus": sorted(os.sched_getaffinity(0)),
        "memory_total_kib": int(meminfo["MemTotal"].split()[0]),
        "containerd_version": command("containerd", "--version"),
        "host_kubernetes": command_json("kubectl", "version", "-o", "json"),
        "isolated_kubernetes": command_json("kubectl", "--kubeconfig", str(args.kubeconfig),
                                             "version", "-o", "json"),
        "kubeconfig_sha256": sha(args.kubeconfig),
        "edge_node_uid": node["metadata"]["uid"],
        "edge_node_version": node["status"]["nodeInfo"]["kubeletVersion"],
        "edge_node_addresses": node["status"]["addresses"],
        "edge_node_ready": any(c["type"] == "Ready" and c["status"] == "True"
                               for c in node["status"]["conditions"]),
        "cloudcore_pods": [{"name": p["metadata"]["name"], "uid": p["metadata"]["uid"],
                            "phase": p["status"]["phase"]} for p in cloudcore],
        "ray_version": command(str(args.ray_python), "-c", "import ray; print(ray.__version__)"),
        "binaries_sha256": {name: sha(args.root / name) for name in
                            ("revision-run", "revision-http-run", "revision-worker")},
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
