#!/usr/bin/env python3
"""Measure the single-host Tide workload/load matrix with frozen calibration.

This supplies Tide observations for workload/usage panels, not missing baseline,
multi-node, mixed-QoS, or fault results. Jobs run serially to avoid interference.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.duration < 1 or args.repeats < 1:
        parser.error("duration and repeats must be positive")
    args.out.mkdir(parents=True, exist_ok=False)
    binary = args.out.resolve() / "revision-run"
    shutil.copy2(args.binary, binary)
    calibration = json.loads((args.calibration / "summary.json").read_text())
    configs = args.out / "configs"
    configs.mkdir()
    for kind in calibration:
        shutil.copy2(args.calibration / f"{kind}_config.json", configs / f"{kind}.json")
    jobs = [dict(workload=kind, rps=rps, repeat=rep, requests=rps * args.duration)
            for kind in calibration for rps in (5, 10, 20, 40) for rep in range(1, args.repeats + 1)]
    random.Random(20260928).shuffle(jobs)  # randomizes execution order, never outcomes
    manifest = dict(status="running", started_unix_ns=time.time_ns(), jobs=jobs,
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    calibration=calibration, duration_s=args.duration, repeats=args.repeats,
                    cpus=2, order_seed=20260928,
                    image_listing=subprocess.check_output(["ctr", "-n", "tide", "images", "ls"], text=True),
                    scope="single-host Tide normal SubmitRoutine; all offered requests retained")
    path = args.out / "matrix.json"
    def save():
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    save()
    try:
        for index, job in enumerate(jobs):
            kind = job["workload"]
            out = args.out / kind / f"rps{job['rps']}" / f"run{job['repeat']}"
            command = [sys.executable, str(Path(__file__).with_name("run_checked.py")),
                       "--out", str(out), "--timeout", str(args.duration + 30), "--", str(binary),
                       "--requests", str(job["requests"]), "--rps", str(job["rps"]), "--cpus", "2",
                       "--config", str(configs / f"{kind}.json"),
                       "--work", json.dumps(calibration[kind]["workload"]), "--out", str(out / "run")]
            job.update(status="running", command=command)
            save()
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            job["runner_output"] = result.stdout
            if result.returncode:
                raise RuntimeError(f"run failed: {out}; evidence retained")
            with (out / "run/raw.csv").open() as f:
                rows = list(csv.DictReader(f))
            if len(rows) != job["requests"] or len({r["req_id"] for r in rows}) != job["requests"]:
                raise RuntimeError(f"incomplete trace: {out}")
            job.update(status="completed", successes=sum(int(r["status"]) == 200 for r in rows))
            save()
            print(f"{index + 1}/{len(jobs)} {kind} rps={job['rps']} repeat={job['repeat']} success={job['successes']}/{job['requests']}", flush=True)
    except Exception as error:
        manifest.update(status="failed", error=str(error), finished_unix_ns=time.time_ns())
        save()
        raise
    manifest.update(status="completed", finished_unix_ns=time.time_ns())
    save()


if __name__ == "__main__":
    main()
