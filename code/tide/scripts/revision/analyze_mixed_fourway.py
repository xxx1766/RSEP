#!/usr/bin/env python3
"""Audit four-system alternating 100/500 ms deadline traces, by offered class."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path

if __package__:
    from . import metrics
    from .analyze_fourway import mean_ci, require, sha, verify_outer, write_csv
    from .vector_pdf import plot_grid
else:
    import metrics
    from analyze_fourway import mean_ci, require, sha, verify_outer, write_csv
    from vector_pdf import plot_grid

SYSTEMS = ("tide", "bare", "ray", "kubeedge")
RATES = (20, 40, 80)
CLASSES = (100, 500)
MEASURES = ("on_time_goodput", "on_time_yield", "success_yield", "avg_ms", "p95_ms")


def audit(system, directory):
    directory = Path(directory)
    matrix_path = directory / "matrix.json"
    matrix = json.loads(matrix_path.read_text())
    require(matrix["status"] == "completed" and matrix["cpus"] == 2
            and matrix["duration_s"] == 6 and matrix["repeats"] == 3,
            f"{directory}: matrix incomplete or unmatched")
    kubeedge = system == "kubeedge"
    if kubeedge:
        require(matrix["qos_classes_ms"] == list(CLASSES), "KubeEdge classes mismatch")
        calibration = matrix["calibration"]["cpu"]
        binary_hash = matrix["binary_sha256"]
        require(sha(directory / "revision-http-run") == binary_hash, "KubeEdge binary mismatch")
        require(sha(directory / "kubeedge_worker.json") == matrix["template_sha256"],
                "KubeEdge pod template mismatch")
        require("kubeedge-v1.23.1" in matrix["edge_node_version"], "not a KubeEdge node")
    else:
        require(matrix["classes_ms"] == list(CLASSES), f"{directory}: classes mismatch")
        calibration = matrix["calibration"]
        binary_hash = matrix["binary_sha256"]["tide" if system == "tide" else "http"]
        for name, digest in matrix["binary_sha256"].items():
            require(sha(directory / "bin" / name) == digest, f"{directory}: {name} binary mismatch")
        require(sha(directory / "cpu_config.json") == matrix["config_sha256"],
                f"{directory}: Tide configuration mismatch")
        if system == "ray":
            require(sha(directory / "bin/ray_serve_baseline.py") == matrix["ray_script_sha256"],
                    "Ray adapter mismatch")
    jobs = [j for j in matrix["jobs"] if j["system"] == system]
    expected = Counter((rate, repeat) for rate in RATES for repeat in (1, 2, 3))
    require(Counter((j["rps"], j["repeat"]) for j in jobs) == expected,
            f"{directory}: missing, duplicated or unexpected jobs")
    rows, evidence = [], []
    for job in jobs:
        rate, repeat = job["rps"], job["repeat"]
        require(job["status"] == "completed" and job["requests"] == rate * 6,
                f"{directory}: unfinished job or request count mismatch")
        root = directory / job["path"]
        require(root.resolve().is_relative_to(directory.resolve()), "unsafe job path")
        measured = root / "measurement"
        outer = json.loads((measured / "manifest.json").read_text())
        verify_outer(measured, outer)
        require(outer["binary_sha256"] == binary_hash, "mixed binary identity mismatch")
        run = json.loads((measured / "run/run.json").read_text())
        require(run["status"] == "completed" and run["requests"] == rate * 6
                and run["rps"] == rate and run["cpus"] == 2
                and run["workload"] == calibration["workload"],
                "mixed run configuration mismatch")
        require(run["measurement_end_ns"] - run["measurement_start_ns"] == 6_000_000_000
                and run["qos_classes_ms"] == list(CLASSES), "mixed observation window/classes mismatch")
        require(run["argv"] == outer["command"], "mixed command mismatch")
        records = metrics.read_records(measured / "run/raw.csv", rate * 6)
        require({int(r["req_id"]) for r in records} == set(range(rate * 6)), "mixed offered IDs mismatch")
        require(sum(r["status"] == 200 for r in records) == run["successes"] == job["successes"],
                "mixed outcome count mismatch")
        for record in records:
            qos = CLASSES[int(record["req_id"]) % 2]
            require(float(record["qos_ms"]) == qos and record["fn_name"] == f"cpu-qos{qos}",
                    "mixed offered deadline class mismatch")
        start = run["measurement_start_unix_ns"] + run["measurement_start_ns"]
        end = run["measurement_start_unix_ns"] + run["measurement_end_ns"]
        require(outer["started_unix_ns"] <= start < end <= outer["finished_unix_ns"],
                "mixed outer runner does not cover window")
        if system != "tide":
            require(sha(root / "setup.json") == job["setup_sha256"], "mixed setup mismatch")
            setup = json.loads((root / "setup.json").read_text())
            if system in ("bare", "ray"):
                require(setup["gomaxprocs"] == 1
                        and all(mask == [0, 1] for mask in setup["threads_affinity"].values()),
                        "mixed worker CPU setting mismatch")
            if system == "ray":
                require(setup["ray_identity"]["ray_version"] == "2.58.0"
                        and setup["ray_identity"]["worker_sha256"] == matrix["binary_sha256"]["worker"]
                        and setup["ray_identity"]["worker_pid"] == setup["worker_pid"],
                        "mixed Ray identity mismatch")
            if kubeedge:
                require(setup["edge_node_version"] == matrix["edge_node_version"]
                        and setup["pod"]["spec"]["nodeName"] == matrix["edge_node_name"]
                        and setup["pod"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] == "2",
                        "mixed EdgeCore placement mismatch")
        else:
            expected_config = json.loads((directory / "cpu_config.json").read_text())
            require(all(run["effective_config"].get(k) == v for k, v in expected_config.items()),
                    "mixed Tide effective configuration mismatch")
        overall = metrics.aggregate_records(records, manifest=run)
        require(overall["n_eligible"] == rate * 6, "mixed offered cohort mismatch")
        for qos in CLASSES:
            selected = [r for r in records if float(r["qos_ms"]) == qos]
            stats = metrics.aggregate_records(selected, manifest=run)
            require(stats["n_eligible"] == rate * 3, "mixed class count mismatch")
            rows.append({"system": system, "rps": rate, "repeat": repeat, "qos_ms": qos,
                         "path": str(root), **stats})
        evidence.append({"system": system, "rps": rate, "repeat": repeat,
                         "raw_rows": len(records), "raw_sha256": outer["files_sha256"]["run/raw.csv"],
                         "outer_sha256": sha(measured / "manifest.json")})
    return rows, {"matrix_sha256": sha(matrix_path), "jobs": len(jobs),
                  "raw_rows": sum(e["raw_rows"] for e in evidence), "runs": evidence}


def plot_summary(path, summary):
    panels = []
    for qos in CLASSES:
        series = {}
        for system in SYSTEMS:
            series[system] = [next(row for row in summary if row["system"] == system
                                   and row["rps"] == rate and row["qos_ms"] == qos
                                   and row["metric"] == "on_time_goodput") for rate in RATES]
        panels.append({"title": f"{qos} ms / class on-time goodput (req/s)",
                       "rates": RATES, "series": series})
    plot_grid(path, panels, 2, "Same CPU kernel; deadlines alternate by offered request ID; no priority queue.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for system in SYSTEMS:
        parser.add_argument("--" + system, type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows, evidence = [], {}
    for system in SYSTEMS:
        group, meta = audit(system, getattr(args, system))
        rows.extend(group)
        evidence[system] = meta
    require(len(rows) == 72 and sum(v["raw_rows"] for v in evidence.values()) == 10080,
            "mixed four-system matrix incomplete")
    summary = []
    for system in SYSTEMS:
        for rate in RATES:
            for qos in CLASSES:
                group = [r for r in rows if r["system"] == system and r["rps"] == rate and r["qos_ms"] == qos]
                require({r["repeat"] for r in group} == {1, 2, 3}, "mixed repeat mismatch")
                for metric in MEASURES:
                    summary.append({"system": system, "rps": rate, "qos_ms": qos, "metric": metric,
                                    **mean_ci([r[metric] for r in group])})
    if args.out.exists():
        require({p.name for p in args.out.iterdir()} <= {"mixed_per_run.csv", "mixed_per_cell.csv"},
                "output directory contains more than recoverable partial tables")
    else:
        args.out.mkdir(parents=True)
    fields = ["system", "rps", "repeat", "qos_ms", "path", "n_eligible", "n_ok", "n_ontime",
              "on_time_goodput", "on_time_yield", "success_yield", "avg_ms", "p95_ms"]
    write_csv(args.out / "mixed_per_run.csv", [{k: r[k] for k in fields} for r in rows])
    write_csv(args.out / "mixed_per_cell.csv", summary)
    plot_summary(args.out / "mixed_class_goodput.pdf", summary)
    (args.out / "mixed_audit.json").write_text(json.dumps({"status": "passed", "systems": evidence,
                                                           "runs": 36, "raw_rows": 10080,
                                                           "classes_ms": CLASSES, "warmup_trim": 0}, indent=2) + "\n")
    print(json.dumps({"status": "passed", "runs": 36, "raw_rows": 10080, "out": str(args.out)}))


if __name__ == "__main__":
    main()
