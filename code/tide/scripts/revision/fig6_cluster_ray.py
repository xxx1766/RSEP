#!/usr/bin/env python3
"""Isolated, fixed-capacity three-host Ray Serve baseline for Fig. 6.

Run node-start on nj1/nj2/nj3, then deploy on nj1. Serve uses its native
request router and four replicas; CPU resources force placement 2/1/1.
No client-side placement policy is implemented here.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path("/root/tmc-fig6-cluster-20261014")
STATE = ROOT / "ray"
TEMP = Path("/tmp/tmc-f6ray-1014")
NODES = {"nj1": ("11.0.4.47", 4), "nj2": ("11.0.4.48", 2), "nj3": ("11.0.4.49", 2)}
PYTHON = STATE / "venv/bin/python"
CONTROL_CPUS = set(range(8, 16))


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def process_record(pid):
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text().split(") ", 1)[1].split()
    return {"pid": pid, "start_ticks": stat[19], "state": stat[0], "process_group": int(stat[2]),
            "command": (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(),
            "affinity": sorted(os.sched_getaffinity(pid))}


def same_process(record):
    try:
        current = process_record(record["pid"])
        return current["start_ticks"] == record["start_ticks"] and current["state"] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def ensure_nofile():
    """Reserve Ray process FD headroom before offering any requests; never alter hard limits."""
    minimum = 65536
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard < minimum:
        raise RuntimeError(f"RLIMIT_NOFILE hard limit {hard} is below required {minimum}")
    target = soft if soft == resource.RLIM_INFINITY else max(soft, minimum)
    if target != soft:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    actual, actual_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    return {"soft_before": soft, "soft": actual, "hard": actual_hard, "minimum": minimum}


def node_start(node):
    nofile = ensure_nofile()
    ip, cpus = NODES[node]
    STATE.mkdir(parents=True, exist_ok=True)
    if (STATE / "node.json").exists() and same_process(json.loads((STATE / "node.json").read_text())["process"]):
        raise RuntimeError("Owned Ray node already running")
    (STATE / "tmp").mkdir(exist_ok=True)
    if TEMP.is_symlink():
        if TEMP.resolve() != (STATE / "tmp").resolve():
            raise RuntimeError("Short Ray socket path belongs to another task")
    elif TEMP.exists():
        raise RuntimeError("Short Ray socket path already exists")
    else:
        TEMP.symlink_to(STATE / "tmp")
    command = [str(PYTHON), "-m", "ray.scripts.scripts", "start", "--block",
               f"--node-ip-address={ip}", f"--num-cpus={cpus}", "--num-gpus=0",
               "--object-store-memory=268435456", f"--temp-dir={TEMP}",
               "--node-manager-port=29823", "--object-manager-port=29824",
               "--dashboard-agent-listen-port=29825", "--dashboard-agent-grpc-port=29826",
               "--runtime-env-agent-port=29827", "--metrics-export-port=29828",
               "--min-worker-port=29920", "--max-worker-port=29949", "--disable-usage-stats"]
    if node == "nj1":
        command += ["--head", "--port=29820", "--ray-client-server-port=29821",
                    "--include-dashboard=false", "--dashboard-port=29822"]
    else:
        command += ["--address=11.0.4.47:29820"]
    os.sched_setaffinity(0, CONTROL_CPUS)
    with (STATE / "node.log").open("a") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True,
                                   env=dict(os.environ, RAY_USAGE_STATS_ENABLED="0"))
    record = {"node": node, "ip": ip, "logical_cpus": cpus,
              "control_cpus": sorted(CONTROL_CPUS), "command": command,
              "process": process_record(process.pid), "temp_dir": str(TEMP),
              "created_ns": time.time_ns(), "rlimit_nofile": nofile}
    write_json(STATE / "node.json", record)
    print(json.dumps(record), flush=True)


def deploy():
    import aiohttp
    from fastapi import FastAPI, Request
    from fastapi.responses import Response
    import ray
    from ray import serve

    app = FastAPI()

    @serve.deployment(name="MatchedGoKernel", num_replicas=4,
                      max_ongoing_requests=1, max_queued_requests=-1,
                      ray_actor_options={"num_cpus": 2},
                      graceful_shutdown_timeout_s=35)
    @serve.ingress(app)
    class MatchedGoKernel:
        def __init__(self):
            self.proc = None
            self.slot_lock = None
            self.log = None
            ip = ray.util.get_node_ip_address()
            self.node = next(n for n, values in NODES.items() if values[0] == ip)
            for slot in range(NODES[self.node][1] // 2):
                lock = (STATE / f"slot{slot}.lock").open("a")
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.slot, self.slot_lock = slot, lock
                    break
                except BlockingIOError:
                    lock.close()
            else:
                raise RuntimeError("No free physical execution slot")
            self.port = 29871 + self.slot
            self.cpus = f"{self.slot * 2},{self.slot * 2 + 1}"
            cgroup = Path(f"/sys/fs/cgroup/tmc-fig6-ray-slot{self.slot}")
            cgroup.mkdir(exist_ok=True)
            if (cgroup / "cgroup.procs").read_text().strip():
                raise RuntimeError("Owned slot cgroup still has processes")
            (cgroup / "memory.max").write_text("536870912")
            (cgroup / "memory.swap.max").write_text("0")
            (cgroup / "cpu.max").write_text("200000 100000")
            (cgroup / "cpuset.mems").write_text((cgroup.parent / "cpuset.mems.effective").read_text())
            (cgroup / "cpuset.cpus").write_text(self.cpus)
            worker = ROOT / "worker"
            command = ["taskset", "-c", self.cpus, str(worker), "--mode=http",
                       f"--listen=127.0.0.1:{self.port}", f"--node={self.node}",
                       f"--tmpdir={ROOT / 'io-data'}"]
            # A small exec launcher enters the cgroup before the Go process starts.
            # Avoid preexec_fn in Ray's multi-threaded Python actor process.
            launch = [sys.executable, "-c",
                      "import os,sys; open(sys.argv[1], 'w').write(str(os.getpid())); os.execvpe(sys.argv[2],sys.argv[2:],os.environ)",
                      str(cgroup / "cgroup.procs"), *command]
            self.log = (STATE / f"slot{self.slot}.log").open("a")
            self.proc = subprocess.Popen(launch, stdout=self.log, stderr=subprocess.STDOUT,
                                         env=dict(os.environ, GOMAXPROCS="1", TMPDIR=str(ROOT / "io-data")))
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if self.proc.poll() is not None:
                        raise RuntimeError("Owned Go worker exited")
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=.5) as reply:
                            if reply.status == 200:
                                break
                    except OSError:
                        time.sleep(.05)
                else:
                    raise RuntimeError("Owned Go worker did not become healthy")
                self.identity = {"node": self.node, "ip": ip, "slot": self.slot,
                                 "ray_version": ray.__version__, "ray_node_id": ray.get_runtime_context().get_node_id(),
                                 "ray_actor_id": ray.get_runtime_context().get_actor_id(),
                                 "replica_process": process_record(os.getpid()),
                                 "worker_process": process_record(self.proc.pid),
                                 "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
                                 "worker_command": command, "cpus": self.cpus, "GOMAXPROCS": 1,
                                 "memory_max": (cgroup / "memory.max").read_text().strip(),
                                 "memory_swap_max": (cgroup / "memory.swap.max").read_text().strip(),
                                 "cpu_max": (cgroup / "cpu.max").read_text().strip(),
                                 "cpuset_cpus_effective": (cgroup / "cpuset.cpus.effective").read_text().strip(),
                                 "cgroup": str(cgroup), "tmpdir": str(ROOT / "io-data"),
                                 "application_concurrency": 1, "ray_num_cpus": 2}
                write_json(STATE / f"slot{self.slot}-identity.json", self.identity)
            except Exception:
                self.stop_worker()
                raise

        @app.get("/health")
        async def health(self):
            if self.proc.poll() is not None:
                return Response(status_code=503)
            return self.identity

        @app.post("/run")
        async def run(self, request: Request):
            if self.proc.poll() is not None:
                return Response(status_code=503)
            data = await request.body()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
                async with client.post(f"http://127.0.0.1:{self.port}/run", data=data,
                                       headers={"Content-Type": "application/json"}) as reply:
                    return Response(content=await reply.read(), status_code=reply.status,
                                    media_type="application/json",
                                    headers={"X-Ray-Node": self.node, "X-Ray-Slot": str(self.slot)})

        def stop_worker(self):
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            if self.log is not None:
                self.log.close()
            if self.slot_lock is not None:
                self.slot_lock.close()

        def __del__(self):
            self.stop_worker()

    os.sched_setaffinity(0, CONTROL_CPUS)
    ray.init(address="11.0.4.47:29820", namespace="tmc-fig6-20261014")
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    write_json(STATE / "deploy-process.json", process_record(os.getpid()))
    try:
        serve.start(proxy_location="EveryNode", http_options={"host": "0.0.0.0", "port": 29870,
                                                              "request_timeout_s": 30})
        serve.run(MatchedGoKernel.bind(), name="tmc-fig6-ray", route_prefix="/")
        write_json(STATE / "deployment.json", {
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "ray_version": ray.__version__, "nodes": ray.nodes(),
            "serve_status": str(serve.status()), "cluster_resources": ray.cluster_resources(),
            "available_resources": ray.available_resources(), "replicas": 4,
            "max_ongoing_requests": 1, "max_queued_requests": -1, "ray_num_cpus_per_replica": 2,
            "proxy_location": "EveryNode", "http_port": 29870,
            "routing": "Ray Serve native PowerOfTwoChoicesRequestRouter; HTTP proxy local-node preference (Ray default)",
            "request_timeout_s": 30, "created_ns": time.time_ns()})
        print("READY: three-node Ray Serve baseline", flush=True)
        stopping.wait()
    finally:
        serve.shutdown()
        ray.shutdown()


def stop_owned(serve_only=False):
    actions = []
    targets = [("deploy-process.json", 45)]
    if not serve_only:
        targets.append(("node.json", 15))
    for filename, timeout in targets:
        path = STATE / filename
        if not path.exists():
            continue
        record = json.loads(path.read_text())
        record = record.get("process", record)
        if same_process(record):
            if filename == "node.json":
                # node-start creates a new session. Only this owned process group
                # is terminated; never use ray stop, which scans unrelated Ray jobs.
                group = os.getpgid(record["pid"])
                if group != record["pid"]:
                    raise RuntimeError("Owned Ray launcher is not its process-group leader")
                os.killpg(group, signal.SIGTERM)
                actions.append({"process_group": group, "signal": "TERM", "record": filename})
            else:
                os.kill(record["pid"], signal.SIGTERM)
                actions.append({"pid": record["pid"], "signal": "TERM", "record": filename})
            deadline = time.monotonic() + timeout
            while same_process(record) and time.monotonic() < deadline:
                time.sleep(.2)
            if same_process(record):
                raise RuntimeError(f"Owned process {record['pid']} did not stop after SIGTERM")
    if serve_only:
        write_json(STATE / "serve-cleanup.json", {"actions": actions, "time_ns": time.time_ns()})
        print(json.dumps(actions), flush=True)
        return
    # Explicitly owned worker identities guard against orphaned subprocesses.
    for path in STATE.glob("slot*-identity.json"):
        identity = json.loads(path.read_text())
        record = identity["worker_process"]
        if same_process(record):
            os.kill(record["pid"], signal.SIGTERM)
            actions.append({"pid": record["pid"], "signal": "TERM", "record": path.name})
        cgroup = Path(identity["cgroup"])
        if cgroup.exists() and not (cgroup / "cgroup.procs").read_text().strip():
            cgroup.rmdir()
            actions.append({"removed_cgroup": str(cgroup)})
    write_json(STATE / "cleanup.json", {"actions": actions, "time_ns": time.time_ns()})
    print(json.dumps(actions), flush=True)


def audit():
    import ray
    slots = []
    for path in sorted(STATE.glob("slot*-identity.json")):
        identity = json.loads(path.read_text())
        worker = identity["worker_process"]
        cgroup = Path(identity["cgroup"])
        slot = {"identity": identity, "worker_still_same_process": same_process(worker)}
        if slot["worker_still_same_process"]:
            slot["current_process"] = process_record(worker["pid"])
            slot["kernel_cgroup_membership"] = (Path("/proc") / str(worker["pid"]) / "cgroup").read_text()
        slot["cgroup"] = {name: (cgroup / name).read_text().strip()
                          for name in ("cgroup.procs", "memory.max", "memory.swap.max", "memory.current", "memory.events",
                                       "cpu.max", "cpu.stat", "cpuset.cpus.effective", "cpuset.mems.effective")
                          if (cgroup / name).exists()}
        slots.append(slot)
    report = {"time_ns": time.time_ns(), "ray_version": ray.__version__,
              "python_version": sys.version, "slots": slots,
              "worker_sha256": hashlib.sha256((ROOT / "worker").read_bytes()).hexdigest(),
              "environment_archive_sha256": hashlib.sha256((STATE / "venv.tar.gz").read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(STATE / "audit.json", report)
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["node-start", "deploy", "audit", "stop-serve", "stop"])
    parser.add_argument("--node", choices=list(NODES))
    args = parser.parse_args()
    if args.action == "node-start":
        if not args.node:
            parser.error("node-start requires --node")
        node_start(args.node)
    elif args.action == "deploy":
        deploy()
    elif args.action == "audit":
        audit()
    else:
        stop_owned(serve_only=args.action == "stop-serve")


if __name__ == "__main__":
    main()
