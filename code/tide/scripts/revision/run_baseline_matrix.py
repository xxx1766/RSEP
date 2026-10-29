#!/usr/bin/env python3
"""Run serial FCFS Bare/Kubernetes/Ray baselines with the same compiled kernels.

Kubernetes uses hostNetwork because the existing CNI is unhealthy. This tests
one application deployment, not KubeEdge, HPA, or cluster placement behavior.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, **kwargs)


def ready(timeout=15):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        try:
            with urllib.request.urlopen("http://127.0.0.1:19280/health", timeout=.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(.05)
    raise RuntimeError("worker did not become ready")


class Worker:
    def __init__(self, system, worker, template, name, root, ray_python=None, ray_script=None):
        self.system, self.worker, self.template, self.name, self.root = system, worker, template, name, root
        self.ray_python, self.ray_script = ray_python, ray_script
        self.proc = self.uid = self.log = None

    def start(self):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", 19280))  # fail rather than use someone else's listener
        started = time.monotonic_ns()
        setup = {"system": self.system, "worker_start_unix_ns": time.time_ns()}
        if self.system == "bare":
            self.log = (self.root / "worker.log").open("w")
            args = ["taskset", "-c", "0,1", str(self.worker), "--mode=http", "--listen=127.0.0.1:19280"]
            self.proc = subprocess.Popen(args, env=dict(os.environ, GOMAXPROCS="1"), stdout=self.log, stderr=subprocess.STDOUT)
            pid = self.proc.pid
            setup["worker_command"] = args
        elif self.system == "ray":
            self.log = (self.root / "ray.log").open("w")
            args = [str(self.ray_python), str(self.ray_script), "--worker", str(self.worker),
                    "--http-port", "19280", "--worker-port", "19281",
                    "--state-dir", str(self.root / "ray-state")]
            self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT)
            ready(timeout=90)
            identity = json.loads((self.root / "ray-state" / "identity.json").read_text())
            pid = int(identity["worker_pid"])
            if identity["worker_sha256"] != hashlib.sha256(self.worker.read_bytes()).hexdigest():
                raise RuntimeError("Ray replica launched a different Go worker binary")
            setup.update(worker_command=args, ray_identity=identity,
                         ray_scope="one serial Ray Serve replica owning the matched persistent Go worker")
        else:
            pod = json.loads(self.template.read_text())
            pod["metadata"]["name"] = self.name
            pod["spec"]["automountServiceAccountToken"] = False
            created = json.loads(command(["kubectl", "create", "-f", "-", "-o", "json"], input=json.dumps(pod)))
            self.uid = created["metadata"]["uid"]
            command(["kubectl", "wait", "-n", "tmc-revision", "--for=condition=Ready", f"pod/{self.name}", "--timeout=60s"])
            observed = json.loads(command(["kubectl", "get", "pod", "-n", "tmc-revision", self.name, "-o", "json"]))
            container = observed["status"]["containerStatuses"][0]["containerID"].split("://", 1)[1]
            tasks = command(["ctr", "-n", "k8s.io", "tasks", "ls"])
            matches = [line.split() for line in tasks.splitlines()[1:] if line.split()[0] == container]
            if len(matches) != 1:
                raise RuntimeError("cannot locate this pod's worker PID")
            pid = int(matches[0][1])
            if b"/worker\0" not in Path(f"/proc/{pid}/cmdline").read_bytes():
                raise RuntimeError("container task is not the expected worker")
            setup.update(pod=observed, container_id=container,
                         affinity_command=command(["taskset", "-apc", "0,1", str(pid)]))
        if self.system != "ray":
            ready()
        if self.proc is not None and self.proc.poll() is not None:
            raise RuntimeError("worker exited during readiness")
        affinity = {p.name: sorted(os.sched_getaffinity(int(p.name))) for p in Path(f"/proc/{pid}/task").iterdir()}
        if any(mask != [0, 1] for mask in affinity.values()):
            raise RuntimeError(f"unmatched worker CPU affinity: {affinity}")
        setup.update(worker_pid=pid, threads_affinity=affinity, gomaxprocs=1,
                     worker_cgroup=Path(f"/proc/{pid}/cgroup").read_text(),
                     worker_ready_observed_ns=time.monotonic_ns()-started,
                     startup_semantics="launcher invocation to readiness observation; outside request window")
        (self.root / "setup.json").write_text(json.dumps(setup, indent=2) + "\n")
        return pid

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=30 if self.system == "ray" else 10)
                except subprocess.TimeoutExpired:
                    self.proc.kill(); self.proc.wait()
            self.log.close()
        if self.uid is not None:
            observed = json.loads(command(["kubectl", "get", "pod", "-n", "tmc-revision", self.name, "-o", "json"]))
            if observed["metadata"]["uid"] != self.uid:
                raise RuntimeError("pod identity changed; refusing cleanup")
            command(["kubectl", "delete", "pod", "-n", "tmc-revision", self.name, "--wait=true", "--timeout=30s"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--template", type=Path, default=Path(__file__).with_name("kubernetes_worker.json"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--systems", default="bare,kubernetes")
    parser.add_argument("--workloads", default="cpu,memory,io")
    parser.add_argument("--ray-python", type=Path)
    parser.add_argument("--ray-script", type=Path, default=Path(__file__).with_name("ray_serve_baseline.py"))
    parser.add_argument("--rps", default="5,10,20,40")
    parser.add_argument("--duration", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    systems = args.systems.split(","); rates = [int(v) for v in args.rps.split(",")]
    if (any(s not in ("bare", "kubernetes", "ray") for s in systems)
            or min(rates + [args.duration, args.repeats]) < 1):
        parser.error("invalid matrix configuration")
    if "ray" in systems and (args.ray_python is None or not args.ray_python.is_file() or not args.ray_script.is_file()):
        parser.error("Ray requires --ray-python and an existing --ray-script")
    args.out.mkdir(parents=True, exist_ok=False)
    binary, worker = args.out.resolve() / "revision-http-run", args.out.resolve() / "revision-worker"
    shutil.copy2(args.binary, binary); shutil.copy2(args.worker, worker)
    calibration_all = json.loads((args.calibration / "summary.json").read_text())
    kinds = args.workloads.split(",")
    if len(set(kinds)) != len(kinds) or not kinds or any(k not in calibration_all for k in kinds):
        parser.error("invalid --workloads selection")
    calibration = {k: calibration_all[k] for k in kinds}
    ray_script = None
    if "ray" in systems:
        ray_script = args.out.resolve() / "ray_serve_baseline.py"
        shutil.copy2(args.ray_script, ray_script)
    jobs = [dict(system=system, workload=kind, rps=rps, repeat=rep, requests=rps * args.duration)
            for system in systems for kind in calibration for rps in rates for rep in range(1, args.repeats + 1)]
    random.Random(20260929).shuffle(jobs)
    manifest = dict(status="running", jobs=jobs, duration_s=args.duration, repeats=args.repeats,
                    started_unix_ns=time.time_ns(), cpus=2, application_concurrency=1,
                    queue_policy="serial FCFS; queue-inclusive external timeout; no predictive QoS admission",
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest(), calibration=calibration,
                    kubernetes_scope="single-node hostNetwork Pod; CNI unhealthy; no HPA or KubeEdge claims",
                    ray_scope="Ray Serve 2.58.0 one serial replica, same persistent Go kernel through local HTTP; Ray controller CPU not included in worker PID samples" if "ray" in systems else None,
                    ray_script_sha256=hashlib.sha256(ray_script.read_bytes()).hexdigest() if ray_script else None,
                    io_scope="same fsync/readback kernel; Bare host /tmp, containers writable overlay /tmp")
    path = args.out / "matrix.json"
    def save(): path.write_text(json.dumps(manifest, indent=2) + "\n")
    save()
    try:
        for index, job in enumerate(jobs):
            root = args.out / job["system"] / job["workload"] / f"rps{job['rps']}" / f"run{job['repeat']}"
            root.mkdir(parents=True)
            instance = Worker(job["system"], worker, args.template,
                              f"revision-{args.out.name}-{index:03d}", root,
                              args.ray_python, ray_script)
            job.update(status="running", path=str(root.relative_to(args.out))); save()
            try:
                pid = instance.start()
                measured = root / "measurement"
                cmd = [sys.executable, str(Path(__file__).with_name("run_checked.py")), "--out", str(measured),
                       "--workload-pid", str(pid), "--timeout", str(args.duration + 30), "--", str(binary),
                       "--requests", str(job["requests"]), "--rps", str(job["rps"]),
                       "--system", job["system"] + " HTTP FCFS", "--qos-ms", "500",
                       "--work", json.dumps(calibration[job["workload"]]["workload"]), "--out", str(measured / "run")]
                job["command"] = cmd; save()
                result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                job["runner_output"] = result.stdout
                if result.returncode: raise RuntimeError(f"measurement failed: {root}")
                with (measured / "run/raw.csv").open() as f: rows = list(csv.DictReader(f))
                if len(rows) != job["requests"] or len({r["req_id"] for r in rows}) != len(rows):
                    raise RuntimeError(f"incomplete trace: {root}")
                job.update(status="completed", successes=sum(int(r["status"]) == 200 for r in rows),
                           setup_sha256=hashlib.sha256((root / "setup.json").read_bytes()).hexdigest())
            finally:
                instance.stop()
            save()
            print(f"{index+1}/{len(jobs)} {job['system']} {job['workload']} rps={job['rps']} repeat={job['repeat']} success={job['successes']}/{job['requests']}", flush=True)
    except BaseException as error:
        if job.get("status") == "running": job.update(status="failed", error=str(error))
        manifest.update(status="failed", error=str(error), finished_unix_ns=time.time_ns()); save(); raise
    manifest.update(status="completed", finished_unix_ns=time.time_ns()); save()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    main()
