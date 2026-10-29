#!/usr/bin/env python3
"""One serial Ray Serve replica executing the revision's persistent Go kernel.

The Serve replica owns the same revision-worker binary used by Bare/Kubernetes.
The extra local HTTP hop is explicit; this is a Ray Serve request-path baseline,
not a measurement of a pure Python kernel or a native Ray task submission path.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.request

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import Response
import ray
from ray import serve


APP = FastAPI()


@serve.deployment(
    name="MatchedGoKernel", num_replicas=1, max_ongoing_requests=1,
    ray_actor_options={"num_cpus": 1},
)
@serve.ingress(APP)
class MatchedGoKernel:
    def __init__(self, worker: str, worker_port: int, cpus: str, state_dir: str):
        self.worker = Path(worker).resolve()
        self.worker_port = worker_port
        self.log = (Path(state_dir) / "worker.log").open("w")
        self.proc = subprocess.Popen(
            ["taskset", "-c", cpus, str(self.worker), "--mode=http",
             f"--listen=127.0.0.1:{worker_port}"],
            env=dict(os.environ, GOMAXPROCS="1"), stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Go worker exited: {self.log.name}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{worker_port}/health", timeout=.5) as reply:
                    if reply.status == 200:
                        break
            except OSError:
                time.sleep(.05)
        else:
            self.stop_worker()
            raise RuntimeError("Go worker did not become ready")
        identity = {
            "ray_version": ray.__version__, "ray_replica_pid": os.getpid(),
            "worker_pid": self.proc.pid, "worker_sha256": hashlib.sha256(self.worker.read_bytes()).hexdigest(),
            "worker_command": self.proc.args, "cpus": cpus, "application_concurrency": 1,
            "kernel_execution": "persistent matched Go revision-worker, owned by Ray Serve replica",
        }
        (Path(state_dir) / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")

    @APP.get("/health")
    async def health(self):
        if self.proc.poll() is not None:
            return Response(status_code=503)
        return {"status": "ready"}

    @APP.post("/run")
    async def run(self, request: Request):
        if self.proc.poll() is not None:
            return Response(status_code=503)
        data = await request.body()
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(f"http://127.0.0.1:{self.worker_port}/run", data=data,
                                   headers={"Content-Type": "application/json"}) as reply:
                return Response(content=await reply.read(), status_code=reply.status,
                                media_type="application/json")

    @APP.post("/stop-worker")
    async def stop(self):
        self.stop_worker()
        return {"status": "stopped"}

    def stop_worker(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if not self.log.closed:
            self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--http-port", type=int, default=19280)
    parser.add_argument("--worker-port", type=int, default=19281)
    parser.add_argument("--cpus", default="0,1")
    args = parser.parse_args()
    if not args.worker.is_file() or not 0 < args.http_port < 65536 or not 0 < args.worker_port < 65536:
        parser.error("valid worker and ports required")
    if args.http_port == args.worker_port:
        parser.error("HTTP and worker ports must differ")
    args.state_dir.mkdir(parents=True, exist_ok=False)
    stopping = threading.Event()
    # Ray's UNIX sockets have a 107-byte path limit; experiment result paths
    # are intentionally descriptive and can exceed it.
    ray_temp_dir = Path("/tmp") / ("tmc-ray-" + hashlib.sha256(str(args.state_dir.resolve()).encode()).hexdigest()[:12])
    ray.init(num_cpus=2, include_dashboard=False, _temp_dir=str(ray_temp_dir))
    # Ray initialization installs native signal handlers; register graceful
    # shutdown afterward so SIGTERM does not abort the launcher.
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    try:
        serve.start(proxy_location="HeadOnly", http_options={"host": "127.0.0.1", "port": args.http_port})
        serve.run(MatchedGoKernel.bind(str(args.worker.resolve()), args.worker_port,
                                       args.cpus, str(args.state_dir.resolve())),
                  name="tmc-ray", route_prefix="/")
        print(json.dumps({"status": "ready", "ray_version": ray.__version__,
                          "http_port": args.http_port, "state_dir": str(args.state_dir),
                          "ray_temp_dir": str(ray_temp_dir)}), flush=True)
        stopping.wait()
    finally:
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{args.http_port}/stop-worker",
                                             data=b"", method="POST")
            with urllib.request.urlopen(request, timeout=8):
                pass
        except OSError:
            pass
        serve.shutdown()
        ray.shutdown()


if __name__ == "__main__":
    main()
