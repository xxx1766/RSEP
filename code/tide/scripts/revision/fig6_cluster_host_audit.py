#!/usr/bin/env python3
"""Scope-checked native Tide limits and reusable read-only host evidence.

Only native-limits writes resource controls, and only after all matching
container/task/process/cgroup/mount identities have been validated. It never
moves a process, stops a container, changes a host controller, or runs kernels.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path("/root/tmc-fig6-cluster-20261014")
IMAGE = "docker.io/tide/fig6-cluster-20261014:amd64"
NODES = {"nj1": {"ip": "11.0.4.47", "slots": 2, "cpus": "0-3"},
         "nj2": {"ip": "11.0.4.48", "slots": 1, "cpus": "0-1"},
         "nj3": {"ip": "11.0.4.49", "slots": 1, "cpus": "0-1"}}
WORKER_SHA = "eba2aed92b54da72791824020f71e1911d0811cd3fc4b362fc3e2c16bff5a74c"
CGROUP_FILES = ("cgroup.procs", "cgroup.controllers", "cpu.max", "cpu.stat",
                "memory.max", "memory.swap.max", "memory.current", "memory.peak",
                "memory.events", "cpuset.cpus", "cpuset.cpus.effective",
                "cpuset.mems", "cpuset.mems.effective")


def run(command, timeout=15, required=False):
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        record = {"command": command, "returncode": result.returncode,
                  "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        record = {"command": command, "returncode": None, "error": str(exc)}
    if required and record["returncode"] != 0:
        raise RuntimeError(f"Required command failed: {record}")
    return record


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cpus(value):
    answer = set()
    for item in value.strip().split(","):
        if not item:
            continue
        ends = item.split("-")
        if len(ends) == 1:
            answer.add(int(item))
        elif len(ends) == 2 and int(ends[0]) <= int(ends[1]):
            answer.update(range(int(ends[0]), int(ends[1]) + 1))
        else:
            raise ValueError(f"Invalid CPU list: {value}")
    return answer


def stat_identity(path):
    info = Path(path).stat()
    return {"device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode}


def proc_identity(pid):
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text().split(") ", 1)[1].split()
    return {"pid": pid, "start_ticks": stat[19], "state": stat[0],
            "command": (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(),
            "exe": os.readlink(proc / "exe"), "exe_sha256": sha256(proc / "exe"),
            "cgroup": (proc / "cgroup").read_text(),
            "status": (proc / "status").read_text()}


def cgroup_snapshot(path):
    return {name: (path / name).read_text().strip()
            for name in CGROUP_FILES if (path / name).exists()}


def thread_snapshot(pid):
    threads = []
    # Go can retire an idle thread between enumeration and reading. Preserve
    # that fact while still auditing every thread present in this snapshot.
    for task in sorted((Path("/proc") / str(pid) / "task").iterdir(), key=lambda p: int(p.name)):
        try:
            status = (task / "status").read_text()
            allowed = next(line.split(":", 1)[1].strip() for line in status.splitlines()
                           if line.startswith("Cpus_allowed_list:"))
            threads.append({"tid": int(task.name), "affinity": sorted(os.sched_getaffinity(int(task.name))),
                            "cpus_allowed_list": allowed, "status": status})
        except (FileNotFoundError, ProcessLookupError):
            threads.append({"tid": int(task.name), "exited_during_snapshot": True})
    return threads


def native_inventory(node, expected_sha):
    containers_raw = run(["ctr", "-n", "tide", "containers", "list", "-q"], required=True)
    tasks_raw = run(["ctr", "-n", "tide", "tasks", "list"], required=True)
    tasks = {}
    for line in tasks_raw["stdout"].splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3:
            tasks[parts[0]] = {"pid": int(parts[1]), "status": parts[2]}
    matching, inactive, seen_cgroups = [], [], set()
    host_io = stat_identity(ROOT / "io-data")
    if sha256(ROOT / "worker") != expected_sha:
        raise RuntimeError("Host common worker does not match expected frozen SHA256")
    for container_id in containers_raw["stdout"].split():
        info = json.loads(run(["ctr", "-n", "tide", "containers", "info", container_id], required=True)["stdout"])
        if info.get("Image") != IMAGE:
            continue
        if container_id not in tasks or tasks[container_id]["status"] != "RUNNING":
            inactive.append({"container_id": container_id, "task": tasks.get(container_id), "container_info": info})
            continue
        task = tasks[container_id]
        process = proc_identity(task["pid"])
        spec = info["Spec"]
        args = spec["process"]["args"]
        if args[0] != "/worker" or f"--node={node}" not in args or "--tmpdir=/fig6-io" not in args:
            raise RuntimeError(f"Unexpected owned-image container command: {container_id}: {args}")
        if process["exe_sha256"] != expected_sha or process["state"] == "Z":
            raise RuntimeError(f"Container worker is not the frozen running executable: {container_id}")
        membership = [line[3:] for line in process["cgroup"].splitlines() if line.startswith("0::")]
        declared = spec["linux"]["cgroupsPath"]
        if membership != [declared] or not declared.startswith("/tide/") or len(Path(declared).parts) != 3:
            raise RuntimeError(f"Refusing unsafe or inconsistent cgroup scope: {container_id}: {membership}, {declared}")
        cgroup = Path("/sys/fs/cgroup") / declared.lstrip("/")
        if cgroup.is_symlink() or cgroup.resolve() != cgroup or str(cgroup) in seen_cgroups:
            raise RuntimeError(f"Refusing aliased/shared container cgroup: {cgroup}")
        seen_cgroups.add(str(cgroup))
        snapshot = cgroup_snapshot(cgroup)
        members = {int(value) for value in snapshot["cgroup.procs"].split()}
        if members != {task["pid"]}:
            raise RuntimeError(f"Container cgroup has unexpected processes: {cgroup}: {members}")
        expected_mount = [mount for mount in spec["mounts"] if mount["destination"] == "/fig6-io"]
        if len(expected_mount) != 1 or expected_mount[0]["source"] != str(ROOT / "io-data"):
            raise RuntimeError(f"Incorrect common I/O bind mount: {container_id}: {expected_mount}")
        container_io = stat_identity(Path("/proc") / str(task["pid"]) / "root/fig6-io")
        if (host_io["device"], host_io["inode"]) != (container_io["device"], container_io["inode"]):
            raise RuntimeError(f"Container I/O mount differs from common host directory: {container_id}")
        matching.append({"container_id": container_id, "image": info["Image"], "task": task,
                         "process": process, "container_info": info, "cgroup_path": str(cgroup),
                         "cgroup_stat": stat_identity(cgroup), "cgroup": snapshot,
                         "threads": thread_snapshot(task["pid"]),
                         "io_identity": {"host": host_io, "container": container_io, "same_device_and_inode": True},
                         "mountinfo": (Path("/proc") / str(task["pid"]) / "mountinfo").read_text()})
    if len(matching) != NODES[node]["slots"]:
        raise RuntimeError(f"Expected {NODES[node]['slots']} live owned slots on {node}, found {len(matching)}")
    return {"containers": matching, "inactive_matching_containers": inactive,
            "ctr_containers": containers_raw, "ctr_tasks": tasks_raw}


def validate_limits(inventory, node, require_bound):
    errors = []
    budget = cpus(NODES[node]["cpus"])
    for container in inventory["containers"]:
        name, group = container["container_id"], container["cgroup"]
        for key, value in {"cpu.max": "200000 100000", "memory.max": "536870912", "memory.swap.max": "0"}.items():
            if group.get(key) != value:
                errors.append(f"{name}: {key}={group.get(key)!r}, expected {value!r}")
        effective = cpus(group.get("cpuset.cpus.effective", ""))
        if not effective or not effective <= budget:
            errors.append(f"{name}: effective CPUs {sorted(effective)} outside application budget {sorted(budget)}")
        if require_bound and len(effective) != 2:
            errors.append(f"{name}: expected native two-CPU binding after execution, got {sorted(effective)}")
        live_threads = [thread for thread in container["threads"] if "affinity" in thread]
        if not live_threads:
            errors.append(f"{name}: no live threads in snapshot")
        for thread in live_threads:
            affinity = set(thread["affinity"])
            if not affinity or not affinity <= effective:
                errors.append(f"{name}: thread {thread['tid']} affinity {sorted(affinity)} outside cgroup {sorted(effective)}")
            if require_bound and len(affinity) != 2:
                errors.append(f"{name}: thread {thread['tid']} does not have a two-CPU native binding")
    return errors


def native(args, report):
    inventory = native_inventory(args.node, args.worker_sha256)
    report["before"] = inventory
    report["planned_controls"] = {"cpu.max": "200000 100000", "memory.max": "536870912",
                                   "memory.swap.max": "0", "cpuset.cpus": NODES[args.node]["cpus"]}
    if args.action == "native-limits" and not args.dry_run:
        report["mutations"] = []
        for container in inventory["containers"]:
            original = container["process"]
            current = proc_identity(original["pid"])
            cgroup = Path(container["cgroup_path"])
            if current["start_ticks"] != original["start_ticks"] or current["cgroup"] != original["cgroup"]:
                raise RuntimeError("Container restarted before limits; refusing to apply stale inventory")
            if stat_identity(cgroup) != container["cgroup_stat"]:
                raise RuntimeError("Container cgroup was replaced before limits")
            # No process migration or changes to parent controller enablement.
            controls = dict(report["planned_controls"])
            controls["cpuset.mems"] = (cgroup.parent / "cpuset.mems.effective").read_text().strip()
            for key in ("memory.max", "memory.swap.max", "cpu.max", "cpuset.mems", "cpuset.cpus"):
                target = cgroup / key
                if not target.exists():
                    raise RuntimeError(f"Required controller file missing; refusing parent modifications: {target}")
                target.write_text(controls[key])
                report["mutations"].append({"container_id": container["container_id"], "file": str(target), "value": controls[key]})
        report["after"] = native_inventory(args.node, args.worker_sha256)
        report["validation_errors"] = validate_limits(report["after"], args.node, args.require_bound)
    elif args.action == "native-audit":
        report["validation_errors"] = validate_limits(inventory, args.node, args.require_bound)
    else:
        report["validation_errors"] = []
        report["dry_run_note"] = "Scope/mount/executable/slot-count checks passed; no cgroup files were changed."
    report["status"] = "PASS" if not report["validation_errors"] else "FAIL"


def host(args, report):
    commands = {
        "cpu": ["lscpu", "--json"],
        "ip_addresses": ["ip", "-j", "address", "show"],
        "routes": ["ip", "-j", "route", "show"],
        "qdisc": ["tc", "-j", "-s", "qdisc", "show"],
        "io_filesystem": ["findmnt", "-J", "-T", str(ROOT / "io-data")],
        "ntp": ["timedatectl", "show", "-p", "NTPSynchronized", "-p", "NTP", "-p", "TimeUSec", "-p", "Timezone"],
        "timesync_status": ["timedatectl", "timesync-status"],
        "chrony_tracking": ["chronyc", "tracking"],
        "chrony_sources": ["chronyc", "sources", "-v"],
        "kernel": ["uname", "-a"],
    }
    report["commands"] = {key: run(value) for key, value in commands.items()}
    report["io_stat"] = stat_identity(ROOT / "io-data")
    statvfs = os.statvfs(ROOT / "io-data")
    report["io_statvfs"] = {name: getattr(statvfs, name) for name in ("f_bsize", "f_frsize", "f_blocks", "f_bfree", "f_bavail", "f_fsid")}
    report["kernel_files"] = {path: Path(path).read_text().strip() for path in (
        "/proc/loadavg", "/proc/meminfo", "/proc/uptime", "/sys/devices/system/cpu/online",
        "/sys/devices/system/clocksource/clocksource0/current_clocksource") if Path(path).exists()}
    report["clock_sample"] = {"realtime_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                              "note": "Host-local snapshot only; no cross-host queue delay inferred from these clocks."}
    if args.ping:
        report["ping_peers"] = {name: run(["ping", "-n", "-c", "5", "-i", "0.2", "-W", "1", values["ip"]], timeout=10)
                                for name, values in NODES.items() if name != args.node}
    errors = []
    for required in ("cpu", "ip_addresses", "routes", "qdisc", "io_filesystem", "ntp"):
        if report["commands"][required]["returncode"] != 0:
            errors.append(f"Required host evidence unavailable: {required}")
    if args.ping:
        errors += [f"Peer ping failed: {name}" for name, value in report["ping_peers"].items() if value["returncode"] != 0]
    report["validation_errors"] = errors
    report["status"] = "PASS" if not errors else "FAIL"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["native-limits", "native-audit", "host-audit"])
    parser.add_argument("--node", required=True, choices=list(NODES))
    parser.add_argument("--worker-sha256", default=WORKER_SHA)
    parser.add_argument("--dry-run", action="store_true", help="Only validate native-limits scope; do not write controls")
    parser.add_argument("--require-bound", action="store_true", help="Require every native worker/thread to have exactly two CPUs after execution")
    parser.add_argument("--ping", action="store_true", help="host-audit: five ICMP packets to each other experiment node")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.require_bound and args.action == "native-limits":
        parser.error("--require-bound is for read-only native-audit after execution; native-limits sets initial node budget")
    output = args.output or ROOT / "host_setup" / f"{args.action}-{args.node}-{time.time_ns()}.json"
    report = {"action": args.action, "node": args.node, "hostname": socket.gethostname(),
              "started_ns": time.time_ns(), "image_filter": IMAGE,
              "script_sha256": sha256(__file__), "expected_worker_sha256": args.worker_sha256,
              "output": str(output), "status": "FAIL"}
    try:
        if args.action == "host-audit":
            host(args, report)
        else:
            native(args, report)
    except Exception as exc:
        report["exception"] = f"{type(exc).__name__}: {exc}"
    report["ended_ns"] = time.time_ns()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": report["status"], "output": str(output),
                      "exception": report.get("exception"), "validation_errors": report.get("validation_errors", [])}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
