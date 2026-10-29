#!/usr/bin/env python3
"""Collect real service-time calibration through the normal Tide executor path."""
import argparse
import csv
import json
import math
from pathlib import Path
import subprocess
import sys


WORKLOADS = {
    "cpu": {"kind": "cpu", "bytes": 65536, "iterations": 100},
    "memory": {"kind": "memory", "bytes": 16777216, "iterations": 4},
    "io": {"kind": "io", "bytes": 4194304, "iterations": 4},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=30)
    args = parser.parse_args()
    if args.requests < 20:
        parser.error("at least 20 calibration requests required")
    args.out.mkdir(parents=True, exist_ok=False)
    config = args.out / "calibration_config.json"
    config.write_text(json.dumps(dict(execution_ms=500, qos_ms=5000, ttl_ms=2000)))
    summaries = {}
    for kind, workload in WORKLOADS.items():
        out = args.out / kind
        command = [sys.executable, str(Path(__file__).with_name("run_checked.py")),
                   "--out", str(out), "--timeout", "60", "--", str(args.binary.resolve()),
                   "--requests", str(args.requests), "--rps", "5", "--cpus", "2",
                   "--config", str(config), "--work", json.dumps(workload), "--out", str(out / "run")]
        subprocess.run(command, check=True)
        with (out / "run/raw.csv").open() as f:
            rows = list(csv.DictReader(f))
        if len(rows) != args.requests or any(int(r["status"]) != 200 for r in rows):
            raise RuntimeError(f"{kind}: calibration incomplete or failed; raw data retained")
        # First request is separately retained as cold/warm-up observation.
        service = sorted(int(r["worker_service_ns"]) / 1e6 for r in rows if int(r["req_id"]) != 0)
        if not service or min(service) <= 0:
            raise RuntimeError("missing measured worker service time")
        p95 = service[math.ceil(.95 * len(service)) - 1]
        summaries[kind] = dict(workload=workload, n_service=len(service),
                               service_min_ms=min(service), service_max_ms=max(service),
                               service_mean_ms=sum(service) / len(service), service_p95_ms=p95,
                               execution_estimate_ms=math.ceil(p95),
                               estimate_rule="ceil nearest-rank p95 of warm worker service milliseconds",
                               raw=str(out / "run/raw.csv"))
        (args.out / f"{kind}_config.json").write_text(json.dumps(dict(
            execution_ms=max(1, math.ceil(p95)), qos_ms=500, ttl_ms=2000,
            weights=[1, 1, 1], latency_scale_ms=100, state_max_age_ms=5000), indent=2) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
