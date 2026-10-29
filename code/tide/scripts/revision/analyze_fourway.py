#!/usr/bin/env python3
"""Audit Tide/Bare/Ray/KubeEdge request traces and summarize independent runs.

All four systems use the same calibrated Go kernels, offered request counts,
eight-second observation windows, and 500 ms request deadlines. This script
does not compare CPU/RSS measurements: their process/container scopes differ.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

if __package__:
    from . import metrics
    from .vector_pdf import plot_grid
else:
    import metrics
    from vector_pdf import plot_grid

SYSTEMS = ("tide", "bare", "ray", "kubeedge")
WORKLOADS = ("cpu", "memory", "io")
RATES = (5, 10, 20, 40)
METRICS = ("on_time_goodput", "throughput", "on_time_yield", "success_yield", "avg_ms", "p95_ms")
T_975 = {2: 12.7062047364, 3: 4.3026527299, 4: 3.1824463053, 5: 2.7764451052,
         6: 2.5705818356, 7: 2.4469118488, 8: 2.3646242516, 9: 2.3060041352,
         10: 2.2621571629}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mean_ci(values):
    vals = [float(v) for v in values if v is not None and math.isfinite(v)]
    require(len(vals) == len(values), "non-finite or missing run-level metric")
    n = len(vals)
    require(n in T_975 or n == 1, "unsupported repeat count for Student-t interval")
    mean = statistics.mean(vals)
    half = T_975[n] * statistics.stdev(vals) / math.sqrt(n) if n > 1 else None
    return {"n": n, "mean": mean, "ci95_low": mean-half if half is not None else None,
            "ci95_high": mean+half if half is not None else None}


def verify_outer(directory, manifest):
    directory = Path(directory)
    require(manifest.get("status") == "completed" and manifest.get("exit_code") == 0
            and manifest.get("timed_out") is False, f"{directory}: unsuccessful outer runner")
    files = manifest.get("files_sha256")
    require(isinstance(files, dict) and files, f"{directory}: missing hashed files")
    for name, digest in files.items():
        path = directory / name
        require(path.resolve().is_relative_to(directory.resolve()), f"{directory}: unsafe hashed path")
        require(path.is_file() and sha(path) == digest, f"{directory}: hash mismatch {name}")


def audit_system(system, directory):
    directory = Path(directory)
    matrix_path = directory / "matrix.json"
    matrix = json.loads(matrix_path.read_text())
    require(matrix.get("status") == "completed" and matrix.get("cpus") == 2
            and matrix.get("duration_s") == 8 and matrix.get("repeats") == 3,
            f"{directory}: incomplete or unmatched matrix")
    require(set(matrix["calibration"]) == set(WORKLOADS), f"{directory}: workload mismatch")
    binary = directory / ("revision-run" if system == "tide" else "revision-http-run")
    require(sha(binary) == matrix["binary_sha256"], f"{directory}: binary hash mismatch")
    if system in ("bare", "ray"):
        require(sha(directory / "revision-worker") == matrix["worker_sha256"],
                f"{directory}: worker hash mismatch")
    if system == "ray":
        require(sha(directory / "ray_serve_baseline.py") == matrix["ray_script_sha256"],
                f"{directory}: Ray adapter hash mismatch")
    if system == "kubeedge":
        require(sha(directory / "kubeedge_worker.json") == matrix["template_sha256"],
                f"{directory}: KubeEdge template hash mismatch")
        require("kubeedge-v1.23.1" in matrix["edge_node_version"], "not an EdgeCore node")
    expected = Counter((kind, rate, repeat) for kind in WORKLOADS for rate in RATES
                       for repeat in range(1, 4))
    selected_jobs = [job for job in matrix["jobs"] if job.get("system", "tide") == system]
    actual = Counter((job["workload"], job["rps"], job["repeat"]) for job in selected_jobs)
    require(actual == expected, f"{directory}: missing, repeated or unexpected job")
    rows, evidence = [], []
    for job in selected_jobs:
        kind, rate, repeat = job["workload"], job["rps"], job["repeat"]
        require(job.get("status") == "completed" and job.get("system", "tide") == system,
                f"{directory}: job not complete or system mismatch")
        root = (directory / kind / f"rps{rate}" / f"run{repeat}") if system == "tide" else directory / job["path"]
        require(root.resolve().is_relative_to(directory.resolve()), "unsafe job path")
        measurement = root if system == "tide" else root / "measurement"
        outer = json.loads((measurement / "manifest.json").read_text())
        verify_outer(measurement, outer)
        require({"run/raw.csv", "run/run.json"}.issubset(outer["files_sha256"]),
                f"{measurement}: raw or run manifest lacks hash")
        require(outer["binary_sha256"] == matrix["binary_sha256"], "run binary differs from matrix")
        run = json.loads((measurement / "run/run.json").read_text())
        request_count = rate * 8
        require(job["requests"] == run["requests"] == request_count and run["status"] == "completed",
                f"{measurement}: expected requests or completion mismatch")
        require(run["rps"] == rate and run["cpus"] == 2 and run["qos_ms"] == 500,
                f"{measurement}: rate, CPU, or QoS mismatch")
        require(run["workload"] == matrix["calibration"][kind]["workload"],
                f"{measurement}: kernel mismatch")
        require(run["measurement_end_ns"] - run["measurement_start_ns"] == 8_000_000_000,
                f"{measurement}: observation window mismatch")
        require(run["argv"] == outer["command"], f"{measurement}: command mismatch")
        records = metrics.read_records(measurement / "run/raw.csv", request_count)
        require({int(r["req_id"]) for r in records} == set(range(request_count)),
                f"{measurement}: missing or unexpected offered ID")
        require(all(float(r["qos_ms"]) == 500 for r in records), f"{measurement}: row QoS mismatch")
        require(sum(r["status"] == 200 for r in records) == job["successes"] == run["successes"],
                f"{measurement}: outcome count mismatch")
        stats = metrics.aggregate_records(records, manifest=run)
        require(stats["n_eligible"] == request_count and stats["window_source"] == "manifest",
                f"{measurement}: offered window incomplete")
        start = run["measurement_start_unix_ns"] + run["measurement_start_ns"]
        end = run["measurement_start_unix_ns"] + run["measurement_end_ns"]
        require(outer["started_unix_ns"] <= start < end <= outer["finished_unix_ns"],
                f"{measurement}: outer measurement did not cover window")
        if system != "tide":
            require(sha(root / "setup.json") == job["setup_sha256"], f"{root}: setup hash mismatch")
            setup = json.loads((root / "setup.json").read_text())
            if system in ("bare", "ray"):
                require(setup["gomaxprocs"] == 1 and all(v == [0, 1] for v in setup["threads_affinity"].values()),
                        f"{root}: worker CPU setting mismatch")
                if system == "ray":
                    identity = setup["ray_identity"]
                    require(identity["ray_version"] == "2.58.0"
                            and identity["worker_sha256"] == matrix["worker_sha256"]
                            and identity["worker_pid"] == setup["worker_pid"]
                            and identity["application_concurrency"] == 1,
                            f"{root}: Ray replica or Go kernel identity mismatch")
            elif system == "kubeedge":
                require(setup["edge_node_version"] == matrix["edge_node_version"]
                        and setup["pod"]["spec"]["nodeName"] == matrix["edge_node_name"]
                        and setup["pod"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] == "2"
                        and setup["pod"]["spec"]["containers"][0]["env"] == [{"name":"GOMAXPROCS","value":"1"}],
                        f"{root}: EdgeCore placement mismatch")
        rows.append({"system": system, "workload": kind, "rps": rate, "repeat": repeat,
                     "path": str(root), **stats})
        evidence.append({"system": system, "workload": kind, "rps": rate, "repeat": repeat,
                         "outer_sha256": sha(measurement / "manifest.json"),
                         "raw_sha256": outer["files_sha256"]["run/raw.csv"], "raw_rows": len(records)})
    return rows, {"matrix": str(matrix_path), "matrix_sha256": sha(matrix_path),
                  "jobs": len(rows), "raw_rows": sum(e["raw_rows"] for e in evidence), "runs": evidence,
                  "started_unix_ns": matrix["started_unix_ns"], "finished_unix_ns": matrix["finished_unix_ns"]}


def write_csv(path, rows):
    require(bool(rows), "empty output table")
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def plot_cells(path, cells):
    panels = []
    for metric, label, scale in (("on_time_goodput", "on-time goodput", 1),
                                 ("on_time_yield", "on-time yield (%)", 100)):
        for kind in WORKLOADS:
            series = {}
            for system in SYSTEMS:
                points = [next(c for c in cells if c["system"] == system and c["workload"] == kind
                               and c["rps"] == rate and c["metric"] == metric) for rate in RATES]
                series[system] = [{key: p[key] * scale for key in ("mean", "ci95_low", "ci95_high")}
                                  for p in points]
            panels.append({"title": f"{kind.upper()} / {label}", "rates": RATES, "series": series})
    plot_grid(path, panels, 3, "KubeEdge: isolated kind EdgeCore Pod. Ray: serial Serve replica with matched Go kernel.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for system in SYSTEMS:
        parser.add_argument("--" + system, type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    paths = {system: getattr(args, system) for system in SYSTEMS}
    runs, evidence = [], {}
    for system, directory in paths.items():
        rows, audit = audit_system(system, directory)
        runs.extend(rows)
        evidence[system] = audit
    calibrations = [json.loads((p / "matrix.json").read_text())["calibration"] for p in paths.values()]
    require(all(cal == calibrations[0] for cal in calibrations[1:]), "calibration differs across systems")
    require(len(runs) == 144, "four-system matrix requires 144 completed runs")
    cells = []
    for system in SYSTEMS:
        for kind in WORKLOADS:
            for rate in RATES:
                group = [r for r in runs if r["system"] == system and r["workload"] == kind and r["rps"] == rate]
                require({r["repeat"] for r in group} == {1, 2, 3}, "missing repeat")
                for metric in METRICS:
                    cells.append({"system": system, "workload": kind, "rps": rate, "metric": metric,
                                  **mean_ci([r[metric] for r in group])})
    if args.out.exists():
        require({p.name for p in args.out.iterdir()} <= {"per_run.csv", "per_cell.csv"},
                "output directory contains more than recoverable partial tables")
    else:
        args.out.mkdir(parents=True)
    fields = ["system", "workload", "rps", "repeat", "path", "n_eligible", "n_ok", "n_failure",
              "n_timeout", "n_ontime", "on_time_goodput", "throughput", "on_time_yield",
              "success_yield", "avg_ms", "p95_ms", "duration_s"]
    write_csv(args.out / "per_run.csv", [{key: r[key] for key in fields} for r in runs])
    write_csv(args.out / "per_cell.csv", cells)
    plot_cells(args.out / "fourway_goodput_yield.pdf", cells)
    (args.out / "audit.json").write_text(json.dumps({"status": "passed", "systems": evidence,
                                                     "runs": len(runs), "raw_rows": sum(v["raw_rows"] for v in evidence.values()),
                                                     "metrics": METRICS, "warmup_trim": 0}, indent=2) + "\n")
    print(json.dumps({"status": "passed", "runs": len(runs), "raw_rows": sum(v["raw_rows"] for v in evidence.values()),
                      "out": str(args.out)}))


if __name__ == "__main__":
    main()
