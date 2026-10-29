#!/usr/bin/env python3
"""Owned four-slot Bare HTTP deployment, executed separately on each NJ host.

start/audit/stop --node nj1|nj2|nj3. nj1 has slots on ports 29810/29811;
nj2/nj3 each have port 29810. self-test is safe on a development machine.
Requires existing cgroup-v2 cpu, cpuset and memory controllers. Never enables
global controllers or signals processes without saved PID/command ownership.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import unittest
import urllib.request


ROOT = Path("/root/tmc-fig6-cluster-20261014")
STATE = ROOT / "bare"
CGROUP_ROOT = Path("/sys/fs/cgroup")
NODES = {"nj1": "11.0.4.47", "nj2": "11.0.4.48", "nj3": "11.0.4.49"}
LIMITS = {"cpu.max": "200000 100000", "memory.max": "536870912", "memory.swap.max": "0"}


def slots_for(node):
    if node not in NODES:
        raise ValueError("unknown physical node")
    return [{"slot": i, "port": 29810 + i, "cpus": [2 * i, 2 * i + 1],
             "cgroup": str(CGROUP_ROOT / f"tmc-fig6-bare-slot{i}")}
            for i in range(2 if node == "nj1" else 1)]


def write_json(path, value):
    temporary = Path(str(path) + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def require_controllers(enabled):
    missing = {"cpu", "cpuset", "memory"} - set(enabled.split())
    if missing:
        raise RuntimeError("required parent controllers not already enabled: " + ",".join(sorted(missing)))


def process_record(pid):
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
    return {"pid": pid, "start_ticks": stat[19], "state": stat[0],
            "argv": [x.decode() for x in (proc / "cmdline").read_bytes().split(b"\0") if x],
            "cgroup": (proc / "cgroup").read_text(), "affinity": sorted(os.sched_getaffinity(pid))}


def matches_identity(identity, current):
    saved = identity["process"]
    relative = str(Path(identity["cgroup"]).relative_to(CGROUP_ROOT))
    return (current["pid"] == saved["pid"] and current["start_ticks"] == saved["start_ticks"]
            and current["state"] != "Z" and current["argv"] == identity["command"]
            and "0::/" + relative in current["cgroup"].splitlines())


def current_if_same_pid(identity):
    try:
        current = process_record(identity["process"]["pid"])
    except (FileNotFoundError, ProcessLookupError):
        return None
    if current["start_ticks"] != identity["process"]["start_ticks"] or current["state"] == "Z":
        return None
    return current


def manifest(node):
    saved = json.loads((STATE / "node.json").read_text())
    if saved["node"] != node:
        raise RuntimeError("saved manifest belongs to another node")
    expected = slots_for(node)
    if len(saved["slots"]) > len(expected):
        raise RuntimeError("unexpected slot inventory")
    for identity, slot in zip(saved["slots"], expected):
        if any(identity[key] != slot[key] for key in ("slot", "port", "cpus", "cgroup")):
            raise RuntimeError("saved slot configuration differs from owned inventory")
        if identity["command"] != worker_command(node, slot):
            raise RuntimeError("saved command differs from owned worker command")
    return saved


def worker_command(node, slot):
    return [str(ROOT / "worker"), "--mode=http", f"--node={node}",
            f"--tmpdir={ROOT / 'io-data'}", "--cpu-list=" + ",".join(map(str, slot["cpus"])),
            f"--listen=0.0.0.0:{slot['port']}"]


def thread_affinities(pid):
    threads = {}
    for path in (Path("/proc") / str(pid) / "task").iterdir():
        try:
            threads[path.name] = sorted(os.sched_getaffinity(int(path.name)))
        except ProcessLookupError:
            continue
    return threads


def inspect_slot(identity):
    cgroup = Path(identity["cgroup"])
    current = current_if_same_pid(identity)
    owned = current is not None and matches_identity(identity, current)
    controls = {name: (cgroup / name).read_text().strip() for name in
                (*LIMITS, "cpuset.cpus", "cpuset.cpus.effective", "cpuset.mems.effective",
                 "memory.current", "memory.events", "cgroup.procs") if (cgroup / name).exists()}
    threads = thread_affinities(current["pid"]) if owned else {}
    want = identity["cpus"]
    effective = controls.get("cpuset.cpus.effective", "")
    # Linux normally prints the two adjacent CPUs as a range.
    cpuset_ok = effective in (",".join(map(str, want)), f"{want[0]}-{want[1]}")
    checks = {"owned_process": owned,
              "limits": all(controls.get(k) == v for k, v in LIMITS.items()),
              "hard_cpuset": cpuset_ok,
              "all_threads_pinned": bool(threads) and all(mask == want for mask in threads.values()),
              "exclusive_cgroup": controls.get("cgroup.procs", "").split() == [str(identity["process"]["pid"])],
              "binary_hash": hashlib.sha256((ROOT / "worker").read_bytes()).hexdigest() == identity["worker_sha256"]}
    return {"identity": identity, "process": current, "threads": threads, "controls": controls,
            "checks": checks, "ok": all(checks.values())}


def audit(node):
    saved = manifest(node)
    slots = [inspect_slot(identity) for identity in saved["slots"]]
    result = {"node": node, "time_ns": time.time_ns(), "slots": slots,
              "ok": len(slots) == len(slots_for(node)) and all(s["ok"] for s in slots)}
    write_json(STATE / "audit.json", result)
    print(json.dumps(result), flush=True)
    if not result["ok"]:
        raise RuntimeError("Bare slot audit failed; inspect bare/audit.json")
    return result


def stop(node):
    if not (STATE / "node.json").exists():
        print(json.dumps({"node": node, "actions": [], "reason": "no owned manifest"}))
        return
    saved = manifest(node)
    actions = []
    for identity in saved["slots"]:
        current = current_if_same_pid(identity)
        if current is not None:
            if not matches_identity(identity, current):
                raise RuntimeError("refusing to signal a process with changed command or cgroup")
            os.kill(current["pid"], signal.SIGTERM)
            actions.append({"pid": current["pid"], "signal": "TERM"})
            deadline = time.monotonic() + 10
            while current_if_same_pid(identity) is not None and time.monotonic() < deadline:
                time.sleep(.1)
            current = current_if_same_pid(identity)
            if current is not None:
                if not matches_identity(identity, current):
                    raise RuntimeError("ownership changed while waiting for worker exit")
                os.kill(current["pid"], signal.SIGKILL)
                actions.append({"pid": current["pid"], "signal": "KILL"})
                deadline = time.monotonic() + 3
                while current_if_same_pid(identity) is not None and time.monotonic() < deadline:
                    time.sleep(.1)
        cgroup = Path(identity["cgroup"])
        if cgroup.exists():
            if (cgroup / "cgroup.procs").read_text().strip():
                raise RuntimeError("refusing to remove a populated cgroup")
            cgroup.rmdir()
            actions.append({"removed_cgroup": str(cgroup)})
    saved["stopped_ns"] = time.time_ns()
    write_json(STATE / "node.json", saved)
    write_json(STATE / "cleanup.json", {"node": node, "time_ns": time.time_ns(), "actions": actions})
    print(json.dumps({"node": node, "actions": actions}), flush=True)


def start(node):
    require_controllers((CGROUP_ROOT / "cgroup.subtree_control").read_text())
    # Refuse a mistyped --node before creating any workers or cgroups.
    with socket.socket() as node_probe:
        node_probe.bind((NODES[node], 0))
    worker = ROOT / "worker"
    if not worker.is_file() or not os.access(worker, os.X_OK):
        raise RuntimeError("owned executable worker is missing")
    if (STATE / "node.json").exists():
        previous = manifest(node)
        if any(current_if_same_pid(identity) is not None for identity in previous["slots"]):
            raise RuntimeError("a prior owned worker is still live; stop before starting")
        write_json(STATE / f"node-{previous['created_ns']}.json", previous)
    (ROOT / "io-data").mkdir(exist_ok=True)
    run_id = time.time_ns()
    run_dir = STATE / str(run_id)
    run_dir.mkdir()
    saved = {"node": node, "ip": NODES[node], "created_ns": run_id, "slots": [],
             "run_dir": str(run_dir), "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "parent_controllers": (CGROUP_ROOT / "cgroup.subtree_control").read_text().strip()}
    write_json(STATE / "node.json", saved)
    spawned, created_cgroups = [], []
    try:
        for slot in slots_for(node):
            with socket.socket() as probe:
                probe.bind(("0.0.0.0", slot["port"]))
            cgroup = Path(slot["cgroup"])
            cgroup.mkdir(exist_ok=True)
            if (cgroup / "cgroup.procs").read_text().strip():
                raise RuntimeError("owned slot cgroup already contains processes")
            created_cgroups.append(cgroup)
            for name in (*LIMITS, "cpuset.cpus", "cpuset.mems"):
                if not (cgroup / name).exists():
                    raise RuntimeError("required cgroup controller file is absent: " + name)
            (cgroup / "cpuset.mems").write_text((CGROUP_ROOT / "cpuset.mems.effective").read_text().strip())
            (cgroup / "cpuset.cpus").write_text(",".join(map(str, slot["cpus"])))
            for name, value in LIMITS.items():
                (cgroup / name).write_text(value)
            command = worker_command(node, slot)
            # Enter the cgroup before exec, so every Go runtime thread inherits it.
            launch = [sys.executable, "-c",
                      "import os,sys; open(sys.argv[1],'w').write(str(os.getpid())); os.execvpe(sys.argv[2],sys.argv[2:],os.environ)",
                      str(cgroup / "cgroup.procs"), *command]
            with (run_dir / f"slot{slot['slot']}.log").open("x") as log:
                process = subprocess.Popen(launch, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                           env=dict(os.environ, GOMAXPROCS="1", TMPDIR=str(ROOT / "io-data")))
            spawned.append(process)
            identity = {**slot, "node": node, "command": command, "process": process_record(process.pid),
                        "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
                        "GOMAXPROCS": 1, "application_concurrency": 1, "limits": dict(LIMITS),
                        "endpoint": f"http://{NODES[node]}:{slot['port']}/run", "log": str(run_dir / f"slot{slot['slot']}.log")}
            saved["slots"].append(identity)
            write_json(STATE / "node.json", saved)
            (run_dir / f"slot{slot['slot']}.pid").write_text(str(process.pid) + "\n")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("worker exited during startup; see " + identity["log"])
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{slot['port']}/health", timeout=.5) as reply:
                        if reply.status == 200:
                            break
                except OSError:
                    time.sleep(.05)
            else:
                raise RuntimeError("worker did not become healthy")
            identity["process"] = process_record(process.pid)
            if not inspect_slot(identity)["ok"]:
                raise RuntimeError("worker resource audit failed")
            write_json(run_dir / f"slot{slot['slot']}-identity.json", identity)
            write_json(STATE / "node.json", saved)
        saved["ready_ns"] = time.time_ns()
        write_json(STATE / "node.json", saved)
    except BaseException:
        # These are live direct-child Popen handles, not discovered arbitrary PIDs.
        for process in spawned:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for cgroup in created_cgroups:
            if cgroup.exists() and not (cgroup / "cgroup.procs").read_text().strip():
                cgroup.rmdir()
        saved["startup_failed_ns"] = time.time_ns()
        write_json(STATE / "node.json", saved)
        raise
    return audit(node)


class SafetyTests(unittest.TestCase):
    def test_fixed_inventory_and_argument_validation(self):
        self.assertEqual([(s["port"], s["cpus"]) for s in slots_for("nj1")],
                         [(29810, [0, 1]), (29811, [2, 3])])
        self.assertEqual(len(slots_for("nj2")), 1)
        self.assertEqual(len(slots_for("nj3")), 1)
        with self.assertRaises(ValueError):
            slots_for("arbitrary-host")

    def test_signal_guard_rejects_reused_pid_changed_command_and_cgroup(self):
        identity = {"process": {"pid": 123, "start_ticks": "456"},
                    "command": ["/owned/worker", "--node=nj1"], "cgroup": "/sys/fs/cgroup/owned"}
        current = {"pid": 123, "start_ticks": "456", "state": "S",
                   "argv": identity["command"], "cgroup": "0::/owned\n"}
        self.assertTrue(matches_identity(identity, current))
        for key, replacement in (("start_ticks", "457"), ("argv", ["unrelated"]),
                                 ("cgroup", "0::/unrelated\n"), ("state", "Z")):
            self.assertFalse(matches_identity(identity, {**current, key: replacement}))

    def test_controller_validation_does_not_enable_controllers(self):
        require_controllers("cpu cpuset memory")
        with self.assertRaisesRegex(RuntimeError, "cpuset"):
            require_controllers("cpu memory")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "audit", "self-test"))
    parser.add_argument("--node", choices=tuple(NODES))
    args = parser.parse_args()
    if args.action == "self-test":
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SafetyTests))
        return 0 if result.wasSuccessful() else 1
    if not args.node:
        parser.error("start, stop and audit require --node")
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("deployment actions require root on the intended Linux node")
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "deployment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        {"start": start, "stop": stop, "audit": audit}[args.action](args.node)
    return 0


if __name__ == "__main__":
    sys.exit(main())
