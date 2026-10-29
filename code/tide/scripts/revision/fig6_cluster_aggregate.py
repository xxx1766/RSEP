#!/usr/bin/env python3
"""Independent, fail-closed publication audit for the complete 360-treatment matrix.

No measurement-module imports. All 720 origin cohorts must be committed and
hash-valid before a new output directory containing runs.csv, cells.csv, and
audit.json is atomically published. Five run-level replicates are the unit of
inference; missing latency estimates never reduce the CI denominator silently.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
import sys
import time

SYSTEMS = ("bare", "tide", "ray", "kubeedge")
KINDS = ("cpu", "memory", "io")
LEVELS = ("low", "high")
PATTERNS = ("balanced", "hotspot", "burst")
ORIGINS = ("k2", "k3")
NODES = ("nj1", "nj2", "nj3")
COUNTS = ("offered", "cohort_successful", "cohort_on_time", "failed", "completed_in_window", "on_time_in_window")
DISTRIBUTIONS = ("successful_latency_ms", "successful_planned_to_return_ms", "replay_lateness_ms",
                 "kernel_service_ms", "worker_clock_span_ms")
STATS = ("count", "mean", "p50", "p95", "p99", "max")
T4 = 2.7764451051977987
BASE = ("job_id", "sequence", "block", "repeat", "kind", "level", "pattern", "system",
        "rate_rps", "trace_sha256", "start_unix", "duration_s")
METRICS = (*COUNTS, "throughput_rps", "goodput_rps", "offered_rps", "cohort_yield", "cohort_success_fraction",
           "failure_fraction", "successful_outside_window", "successful_deadline_misses",
           *[f"{group}_{stat}" for group in DISTRIBUTIONS for stat in STATS],
           *[f"destination_{node}_{suffix}" for node in NODES for suffix in ("successes", "fraction")],
           *[f"origin_{origin}_{count}" for origin in ORIGINS for count in COUNTS],
           "worker_instance_count", "clock_mapping_drift_abs_ms_max", "clock_bracket_ms_max")
CELL_COLUMNS = ("kind", "level", "pattern", "system", "metric", "n", "mean", "sd", "se",
                "ci95_halfwidth", "ci95_low", "ci95_high")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def ci5(values):
    require(len(values) == 5, "Exactly five paired repetitions required")
    valid = [value for value in values if value is not None]
    result = dict.fromkeys(CELL_COLUMNS[5:])
    result["n"] = len(valid)
    if len(valid) == 5:
        require(all(math.isfinite(value) for value in valid), "Nonfinite metric")
        mean, sd = statistics.mean(valid), statistics.stdev(valid)
        se = sd / math.sqrt(5)
        result.update(mean=mean, sd=sd, se=se, ci95_halfwidth=T4 * se,
                      ci95_low=mean - T4 * se, ci95_high=mean + T4 * se)
    return result


def describe(values):
    values = sorted(values)
    if not values:
        return {key: 0 if key == "count" else None for key in STATS}
    return {"count": len(values), "mean": math.fsum(values) / len(values), "max": values[-1],
            **{key: values[math.ceil(len(values) * quantile) - 1]
               for key, quantile in (("p50", .5), ("p95", .95), ("p99", .99))}}


def failure(record, expected):
    if record.get("error"):
        return str(record["error"])
    if record.get("http_status") != 200:
        return "http_error"
    body = record.get("response")
    if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
        return "invalid_response"
    return "result_mismatch" if any(type(body["result"].get(k)) is not int or body["result"][k] != v
                                    for k, v in expected.items()) else None


def recompute(pairs):
    require(len(pairs) == 2 and {meta["origin"] for meta, _ in pairs} == set(ORIGINS), "Both distinct origins required")
    first = pairs[0][0]; total = Counter({key: 0 for key in COUNTS})
    samples = {key: [] for key in DISTRIBUTIONS}; reasons = Counter(); destinations = Counter()
    origins = {}; instances = Counter(); statuses = Counter(); failed_destinations = Counter()
    for meta, records in pairs:
        require(all(meta[key] == first[key] for key in ("duration_ns", "start_unix", "system", "trace_sha256", "expected_result")), "Incompatible origin windows")
        ids = [r["id"] for r in records]; expected_ids = meta["expected_ids"]
        require(len(ids) == len(set(ids)) == len(expected_ids) == len(set(expected_ids)) and set(ids) == set(expected_ids), "Incomplete or duplicate offered IDs")
        start, duration = meta["window_start_monotonic_ns"], meta["duration_ns"]
        require(duration > 0, "Nonpositive window")
        count = Counter({key: 0 for key in COUNTS}); count["offered"] = len(ids)
        for row in records:
            planned, sent, end = (row[key] for key in ("planned_submit_monotonic_ns", "actual_submit_monotonic_ns", "return_monotonic_ns"))
            require(row["origin"] == meta["origin"] and start <= planned < start + duration and planned <= sent <= end, "Invalid origin/client timestamps")
            samples["replay_lateness_ms"].append((sent - planned) / 1e6)
            statuses[str(row.get("http_status"))] += 1
            reason = failure(row, meta["expected_result"])
            body = row.get("response") or {}
            if reason:
                count["failed"] += 1; reasons[reason] += 1
                failed_destinations[str(body.get("node", "unknown")) if isinstance(body, dict) else "unknown"] += 1
                continue
            node, instance = body.get("node"), body.get("worker_instance")
            require(node in NODES and isinstance(instance, str) and instance, "Successful response lacks worker identity")
            require(type(body.get("service_ns")) is int and body["service_ns"] > 0, "Successful response lacks positive service time")
            require(type(body.get("worker_start_ns")) is int and type(body.get("worker_end_ns")) is int
                    and body["worker_end_ns"] >= body["worker_start_ns"], "Invalid worker-local clock span")
            timely, inside = end - sent <= round(row["deadline_ms"] * 1e6), start <= end < start + duration
            count.update(cohort_successful=1, cohort_on_time=int(timely), completed_in_window=int(inside), on_time_in_window=int(timely and inside))
            destinations[node] += 1; instances[instance] += 1
            for key, value in (("successful_latency_ms", end - sent), ("successful_planned_to_return_ms", end - planned),
                               ("kernel_service_ms", body["service_ns"]), ("worker_clock_span_ms", body["worker_end_ns"] - body["worker_start_ns"])):
                samples[key].append(value / 1e6)
        origins[meta["origin"]] = dict(count); total.update(count)
    seconds = first["duration_ns"] / 1e9; offered = total["offered"]; successes = total["cohort_successful"]
    require(offered > 0 and successes + total["failed"] == offered, "Count conservation failed")
    result = {**dict(total), "throughput_rps": total["completed_in_window"] / seconds,
              "goodput_rps": total["on_time_in_window"] / seconds, "offered_rps": offered / seconds,
              "cohort_yield": total["cohort_on_time"] / offered, "cohort_success_fraction": successes / offered,
              "failure_fraction": total["failed"] / offered, "successful_outside_window": successes - total["completed_in_window"],
              "successful_deadline_misses": successes - total["cohort_on_time"], "worker_instance_count": len(instances),
              "clock_mapping_drift_abs_ms_max": max(abs(m["clock_mapping_drift_ns"]) / 1e6 for m, _ in pairs),
              "clock_bracket_ms_max": max(m["clock_sample"]["bracket_ns"] / 1e6 for m, _ in pairs)}
    for key, values in samples.items():
        result.update({f"{key}_{stat}": value for stat, value in describe(values).items()})
    for node in NODES:
        result.update({f"destination_{node}_successes": destinations[node], f"destination_{node}_fraction": destinations[node] / successes if successes else None})
    for origin, counts in origins.items():
        result.update({f"origin_{origin}_{key}": value for key, value in counts.items()})
    return result, {"failure_reasons": dict(reasons), "destinations": dict(destinations), "origins": origins,
                    "worker_instances": dict(instances), "http_statuses": dict(statuses), "failed_destinations": dict(failed_destinations)}


class Evidence:
    def __init__(self, root):
        self.root, self.hashes = root.resolve(), {}

    def read(self, relative, wanted=None, jsonl=False, parse=True):
        path = (self.root / relative).resolve()
        require(path.is_relative_to(self.root), "Evidence path escapes root")
        raw = path.read_bytes(); actual = digest(raw)
        require(wanted is None or actual == wanted, f"Hash mismatch: {relative}")
        self.hashes[str(path.relative_to(self.root))] = actual
        if not parse:
            return raw
        return [json.loads(line) for line in raw.splitlines() if line.strip()] if jsonl else json.loads(raw)


def verify_rate_rule(plan, calibrations):
    """Independently replay declared rate selection; older minimal fixtures have no rule."""
    if "rate_rule" not in plan:
        require("rate_quantization" not in plan and "rate_selection" not in plan
                and "capacity_rps" not in plan and not calibrations,
                "Calibration/rate selection without a declared rate rule")
        return
    rule = "round(max(2, sum(node_slots * 1000 / bare median service ms) * factor)); factors=0.30,0.80; Python round"
    quantized = "rate_quantization" in plan
    explicit = "rate_selection" in plan
    require(not (quantized and explicit), "Explicit rates and quantization are mutually exclusive")
    if quantized:
        require(plan["rate_quantization"] == {"multiple": 5, "rounding": "half_up", "input": "calibration_integer_rate", "minimum": 5}, "Unsupported rate quantization")
        rule += "; then nearest positive multiple of 5, half_up: max(5, 5 * ((2 * integer_rate + 5) // 10))"
    if explicit:
        selection = plan["rate_selection"]
        require(isinstance(selection, dict) and set(selection) == {"mode", "rates_rps"}
                and selection["mode"] == "explicit", "Unsupported explicit rate selection")
        rule = "explicit positive integer requests per second from rate_selection.rates_rps; low < high"
    require(plan["rate_rule"] == rule, "Unsupported rate rule")
    rate_maps = [plan.get("rates_rps")]
    if explicit:
        rate_maps.append(selection["rates_rps"])
    for rates in rate_maps:
        require(isinstance(rates, dict) and set(rates) == set(KINDS), "Invalid rate kinds")
        for kind in KINDS:
            levels = rates[kind]
            require(isinstance(levels, dict) and set(levels) == set(LEVELS)
                    and all(type(rate) is int and rate > 0 for rate in levels.values()),
                    "Rates must be positive integers for exactly low/high")
            if explicit:
                require(levels["low"] < levels["high"], "Explicit rates require low < high")
    if explicit:
        require(selection["rates_rps"] == plan["rates_rps"], "Explicit rate selection mismatch")
    capacities = plan.get("capacity_rps")
    require(isinstance(capacities, dict) and set(capacities) == set(KINDS), "Invalid calibration capacities")
    for kind in KINDS:
        require(type(capacities[kind]) in (int, float) and math.isfinite(capacities[kind])
                and capacities[kind] > 0, "Invalid calibration capacity")
        capacity = sum((2 if node == "nj1" else 1) * 1000 / calibrations[f"{node}-bare"]["summary"][kind]["median_service_ms"] for node in NODES)
        require(math.isclose(capacity, capacities[kind], rel_tol=1e-12), "Calibration capacity mismatch")
        for level, factor in (("low", .30), ("high", .80)):
            if explicit:
                continue
            rate = round(max(2, capacity * factor))
            if quantized:
                quotient, remainder = divmod(rate, 5)
                rate = max(1, quotient + int(remainder >= 3)) * 5
            require(plan["rates_rps"][kind][level] == rate, "Calibration rate mismatch")
    trace_names = {f"{kind}-{level}-{pattern}.json" for kind, level, pattern in itertools.product(KINDS, LEVELS, PATTERNS)}
    require(isinstance(plan.get("traces"), dict) and set(plan["traces"]) == trace_names, "Invalid frozen trace set")
    duration = plan.get("duration_s")
    require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0, "Invalid offered-load duration")
    for name in trace_names:
        kind, level, _ = name.split("-")
        offered = plan["traces"][name].get("offered")
        require(type(offered) is int and offered == round(plan["rates_rps"][kind][level] * duration),
                "Rate/trace offered-count mismatch")
    for job in plan["jobs"]:
        require(job.get("kind") in KINDS and job.get("level") in LEVELS and job.get("pattern") in PATTERNS,
                "Invalid rate selection job")
        rate = plan["rates_rps"][job["kind"]][job["level"]]
        require(type(job.get("rate_rps")) is int and job["rate_rps"] == rate
                and job.get("trace") == f"{job['kind']}-{job['level']}-{job['pattern']}.json",
                "Rate/trace offered-count mismatch")


def frozen_plan(evidence, source_dir):
    root = evidence.root
    manifest_sha = (root / "frozen/manifest.sha256").read_text().strip()
    plan = evidence.read("frozen/manifest.json", manifest_sha)
    require(plan["repetitions"] == 5 and set(plan["systems"]) == set(SYSTEMS), "Unexpected repetition/system design")
    wanted = set(itertools.product(KINDS, LEVELS, PATTERNS, SYSTEMS, range(1, 6)))
    keys = [(j["kind"], j["level"], j["pattern"], j["system"], j["repeat"]) for j in plan["jobs"]]
    require(len(keys) == 360 and set(keys) == wanted and len({j['id'] for j in plan['jobs']}) == 360, "Matrix is not the complete 360-treatment factorial")
    require([j["sequence"] for j in plan["jobs"]] == list(range(1, 361)), "Invalid treatment sequence")
    for name, sha in plan["source_sha256"].items():
        require(Path(name).name == name and digest((source_dir / name).read_bytes()) == sha, f"Frozen measurement source changed: {name}")
    calibrations = {Path(name).stem: evidence.read("frozen/calibration/" + name, sha)
                    for name, sha in plan["calibration_sha256"].items()}
    verify_rate_rule(plan, calibrations)
    for kind, value in plan["tide_configs"].items():
        require(evidence.read(f"frozen/configs/tide-{kind}.json") == value, "Frozen Tide configuration changed")
    traces = {}
    for name, info in plan["traces"].items():
        trace = evidence.read("frozen/traces/" + name, info["file_sha256"])
        require(digest(canonical(trace)) == info["sha256"] and len(trace["requests"]) == info["offered"], "Trace digest/count mismatch")
        require(len({r['id'] for r in trace['requests']}) == len(trace['requests']), "Duplicate frozen trace ID")
        require(all(r['origin'] in ORIGINS and 0 <= r['offset_ns'] < trace['duration_ns'] and r['deadline_ms'] == plan['deadline_ms'] for r in trace['requests']), "Invalid frozen request")
        traces[name] = trace
    return plan, manifest_sha, traces


def proven_instances(evidence, plan, manifest_sha):
    used = evidence.read("formal/readiness-used.json")
    marker = evidence.read("integrated_smoke.json", used["marker_sha256"])
    require(used["manifest_sha256"] == manifest_sha and used["marker"] == marker and marker["ok"] is True, "Readiness marker changed")
    instances = {system: defaultdict(set) for system in SYSTEMS}
    def visit(obj, system=None):
        if isinstance(obj, list):
            for value in obj: visit(value, system)
        elif isinstance(obj, dict):
            system = obj.get("system", system)
            if system in instances:
                values = obj.get("instances", [])
                if isinstance(values, dict):
                    values = [instance for group in values.values() for instance in group]
                values = values + ([obj["worker_instance"]] if "worker_instance" in obj else [])
                for value in values:
                    node = value.split("-", 1)[0]
                    require(node in NODES, "Invalid preformal worker instance")
                    instances[system][node].add(value)
            for value in obj.values():
                if isinstance(value, (dict, list)): visit(value, system)
    for item in marker["evidence"]:
        visit(evidence.read(item["path"], item["sha256"]))
    reuse = plan.get("calibration_reuse")
    if reuse is not None:
        require(isinstance(reuse, dict) and isinstance(reuse.get("campaign"), str) and reuse["campaign"]
                and isinstance(reuse.get("manifest_sha256"), str) and len(reuse["manifest_sha256"]) == 64
                and all(c in "0123456789abcdef" for c in reuse["manifest_sha256"])
                and reuse.get("calibration_sha256") == plan["calibration_sha256"], "Invalid calibration reuse provenance")
        # Historical service measurements cannot establish current worker identity.
    else:
        for name, sha in plan["calibration_sha256"].items():
            visit(evidence.read("frozen/calibration/" + name, sha))
    for system in SYSTEMS:
        require({node: len(instances[system][node]) for node in NODES} == {"nj1": 2, "nj2": 1, "nj3": 1}, f"Preformal instances not exactly 2/1/1 for {system}")
    return {system: {node: sorted(values) for node, values in nodes.items()} for system, nodes in instances.items()}


def check_summary(summary, metrics, detail, job, trace, start):
    require(summary["job"] == job and summary["system"] == job["system"] and summary["duration_s"] == trace["duration_ns"] / 1e9
            and summary["trace_sha256"] == digest(canonical(trace)) and summary["start_unix"] == start, "Stored summary identity mismatch")
    for key in (*COUNTS, "throughput_rps", "goodput_rps", "offered_rps", "cohort_yield", "cohort_success_fraction"):
        require(math.isclose(summary[key], metrics[key], rel_tol=1e-10, abs_tol=1e-10), f"Independent summary mismatch: {key}")
    for group in DISTRIBUTIONS[:-1]:
        for stat in STATS:
            a, b = summary[group][stat], metrics[f"{group}_{stat}"]
            require(a == b if a is None or b is None else math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10), f"Independent distribution mismatch: {group}.{stat}")
    for key in ("failure_reasons", "destinations", "origins"):
        require(summary[key] == detail[key], f"Independent count mismatch: {key}")


def audit_job(evidence, plan, manifest_sha, traces, proven, job):
    trace = traces[job["trace"]]; job_id = job["id"]
    state = evidence.read(f"formal/state/{job_id}.json")
    require(state["status"] == "completed" and state["job"] == job and state["manifest_sha256"] == manifest_sha, f"Unfinished/mismatched state: {job_id}")
    require(job["block"] == f"r{job['repeat']}-{job['kind']}-{job['level']}-{job['pattern']}" and job["rate_rps"] == plan["rates_rps"][job["kind"]][job["level"]], "Unpaired treatment block/rate")
    require(trace["pattern"] == job["pattern"] and trace["workload"] == plan["workloads"][job["kind"]] and trace["duration_ns"] == round(plan["duration_s"] * 1e9), "Trace differs from factorial cell")
    pairs, pending = [], []
    timing = {"collection_end_unix_ns": {}, "remote_finished_ns": {}, "drain_clearance_verified_ns": None}
    for origin in ORIGINS:
        remote = state["remote_terminal"][origin]
        require(remote["status"] == "completed" and remote["exit_code"] == 0 and remote["job"] == job_id and remote["origin"] == origin and remote["manifest_sha256"] == manifest_sha, "Origin attempt not terminal/successful")
        prefix = f"{job_id}-{origin}"
        meta = evidence.read(f"formal/{prefix}.meta.json")
        require(meta["complete"] is True and meta["raw_file"] == prefix + ".jsonl", "Missing origin commit marker/invalid raw path")
        records = evidence.read("formal/" + meta["raw_file"], meta["raw_sha256"], jsonl=True)
        require(type(meta["collection_end_unix_ns"]) is int and type(remote["finished_ns"]) is int
                and remote["finished_ns"] >= meta["collection_end_unix_ns"]
                and meta["collection_end_monotonic_ns"] >= max((r["return_monotonic_ns"] for r in records), default=0), "Collection/remote completion timestamp ordering invalid")
        timing["collection_end_unix_ns"][origin] = meta["collection_end_unix_ns"]
        timing["remote_finished_ns"][origin] = remote["finished_ns"]
        selected = sorted((r for r in trace["requests"] if r["origin"] == origin), key=lambda r: r["offset_ns"])
        requests = {r["id"]: r for r in selected}
        gateway = job["system"] in ("tide", "ray")
        port = {"tide": 29801, "ray": 29870, "bare": 29810, "kubeedge": 29890}[job["system"]]
        urls = [f"http://127.0.0.1:{port}/run"] if gateway else [f"http://11.0.4.47:{port}/run", f"http://11.0.4.47:{port+1}/run", f"http://11.0.4.48:{port}/run", f"http://11.0.4.49:{port}/run"]
        routes = {r["id"]: urls[0] if gateway else urls[(ordinal + (2 if origin == "k3" else 0)) % 4] for ordinal, r in enumerate(selected)}
        require(meta["route"] == ("gateway" if gateway else "direct") and meta["urls"] == urls, "Origin routing policy changed")
        require(meta["origin"] == origin and meta["system"] == job["system"] and meta["start_unix"] == state["start_unix"]
                and meta["trace_sha256"] == digest(canonical(trace)) and meta["duration_ns"] == trace["duration_ns"]
                and meta["workload"] == trace["workload"] and meta["expected_result"] == trace["expected_result"]
                and meta["payload_sha256"] == digest(canonical(trace["workload"])) and meta["timeout_s"] == plan["timeout_s"]
                and meta["expected_ids"] == list(requests), "Origin metadata differs from frozen offered trace")
        for row in records:
            request = requests.get(row["id"])
            require(request is not None and row["url"] == routes[row["id"]] and row["request"] == trace["workload"] and row["offset_ns"] == request["offset_ns"]
                    and row["deadline_ms"] == request["deadline_ms"] and row["planned_submit_monotonic_ns"] == meta["window_start_monotonic_ns"] + request["offset_ns"], "Record differs from frozen offer")
            body = row.get("response")
            if isinstance(body, dict) and body.get("worker_instance"):
                require(body["worker_instance"] in proven[job["system"]].get(body.get("node"), []), "Unexpected worker instance/restart: " + str(body.get("worker_instance")))
            status, error = row.get("http_status"), row.get("error")
            if error == "timeout" or status == 408 or (status is not None and status >= 500) or (status is None and error):
                pending.append({"origin": origin, "id": row["id"], "error": error, "http_status": status})
        pairs.append((meta, records))
    require(pending == state["pending_execution"], "Pending-execution accounting mismatch")
    if pending:
        clearance = evidence.read(f"formal/drain-clearance/{job_id}.json", state.get("drain_clearance_sha256"))
        require(state["readiness"] == "drain_verified" and clearance["idle_verified"] is True and clearance["job"] == job_id
                and clearance["summary_sha256"] == state["summary_sha256"] and clearance["verified_ns"] > state["completed_ns"] and clearance["evidence"], "Unverified drain after uncertain execution")
        for item in clearance["evidence"]: evidence.read(item["path"], item["sha256"], parse=False)
        timing["drain_clearance_verified_ns"] = clearance["verified_ns"]
    metrics, detail = recompute(pairs)
    require(metrics["offered"] == len(trace["requests"]), "Omitted offered requests")
    summary = evidence.read(f"formal/{job_id}.summary.json", state["summary_sha256"])
    check_summary(summary, metrics, detail, job, trace, state["start_unix"])
    detail["timing"] = timing
    row = {key: job[key] for key in BASE if key in job}
    row.update(job_id=job_id, trace_sha256=digest(canonical(trace)), start_unix=state["start_unix"], duration_s=plan["duration_s"], **metrics)
    return row, detail


def verify_sequence(rows, details, drain_s):
    transitions = []
    for previous, following in zip(rows, rows[1:]):
        timing = details[previous["job_id"]]["timing"]
        collection_end = max(timing["collection_end_unix_ns"].values())
        remote_end = max(timing["remote_finished_ns"].values())
        clearance_end = timing["drain_clearance_verified_ns"] or 0
        latest = max(collection_end, remote_end, clearance_end,
                     round((previous["start_unix"] + previous["duration_s"]) * 1e9))
        next_start = round(following["start_unix"] * 1e9)
        require(next_start >= latest + round(drain_s * 1e9),
                "Next treatment overlaps prior collection/remote completion/drain clearance or nominal drain margin: " + following["job_id"])
        transitions.append({"previous_job": previous["job_id"], "next_job": following["job_id"],
                            "next_start_unix_ns": next_start, "latest_collection_end_unix_ns": collection_end,
                            "latest_remote_finished_ns": remote_end, "drain_clearance_verified_ns": clearance_end or None,
                            "latest_prior_evidence_end_unix_ns": latest, "nominal_gap_s": (next_start - latest) / 1e9})
    return transitions


def publish(root, output, source_dir):
    require(not output.exists(), "Publication output already exists; choose a new directory")
    evidence = Evidence(root); plan, manifest_sha, traces = frozen_plan(evidence, source_dir)
    # Check the entire commit inventory before computing any publication metrics.
    expected_meta = {f"{job['id']}-{origin}.meta.json" for job in plan["jobs"] for origin in ORIGINS}
    require({p.name for p in (root / "formal").glob("*.meta.json")} == expected_meta, "Incomplete or unexpected origin commit inventory (720 required)")
    require({p.name for p in (root / "formal").glob("*.jsonl")} == {name.replace(".meta.json", ".jsonl") for name in expected_meta}, "Unplanned/missing raw artifacts")
    require(not list((root / "formal").glob("*.partial")), "Uncommitted partial artifacts present")
    proven = proven_instances(evidence, plan, manifest_sha)
    rows, details = [], {}
    for job in plan["jobs"]:
        row, detail = audit_job(evidence, plan, manifest_sha, traces, proven, job)
        rows.append(row); details[job["id"]] = detail
    transitions = verify_sequence(rows, details, plan["inter_treatment_drain_s"])
    clock_evidence = {node: evidence.read(f"host_setup/{node}/clock-formal-start.json") for node in NODES
                      if (root / f"host_setup/{node}/clock-formal-start.json").exists()}
    cells = []; grouped = defaultdict(list)
    for row in rows: grouped[tuple(row[k] for k in CELL_COLUMNS[:4])].append(row)
    for key, group in sorted(grouped.items()):
        require({r["repeat"] for r in group} == set(range(1, 6)) and len(group) == 5, "Cell lacks five paired repeats")
        for metric in METRICS:
            cells.append({**dict(zip(CELL_COLUMNS[:4], key)), "metric": metric, **ci5([r[metric] for r in group])})
    require(len(grouped) == 72, "Expected 72 factorial cells")
    observed = {system: sorted({instance for row in rows if row['system'] == system for instance in details[row['job_id']]['worker_instances']}) for system in SYSTEMS}
    for relative, wanted in evidence.hashes.items():
        require(digest((root / relative).read_bytes()) == wanted, "Evidence changed during audit: " + relative)
    audit = {"status": "PASS", "created_ns": time.time_ns(), "manifest_sha256": manifest_sha,
             "campaign": plan["campaign"], "treatments": 360, "origin_runs": 720, "cells": 72, "repetitions": 5,
             "offered_total": sum(r["offered"] for r in rows), "failed_total": sum(r["failed"] for r in rows),
             "source_sha256": plan["source_sha256"], "aggregator_sha256": digest(Path(__file__).read_bytes()),
             "proven_instances": proven, "observed_instances": observed, "job_details": details,
             "sequential_treatment_audit": {"transitions": transitions, "nominal_drain_margin_s": plan["inter_treatment_drain_s"],
                 "clock_evidence": clock_evidence, "interpretation": "Cross-host wall clocks check treatment overlap only. The nominal 1-second drain margin is not a precise physical clock bound; NTP offset/jitter evidence is retained. No latency or queue time is inferred from cross-host subtraction."},
             "evidence_sha256": evidence.hashes, "runs_columns": list(BASE + METRICS), "cells_columns": list(CELL_COLUMNS),
             "inference": {"confidence": .95, "distribution": "Student t", "df": 4, "critical_value": T4,
                           "unit": "five run-level metrics; percentiles are averaged across runs, never pooled",
                           "missing": "If any run has no successful observations, that latency cell has n<5 and all estimates/CI blank",
                           "bounds": "Unclipped symmetric intervals; rates/fractions can have limits outside their physical range"},
             "timing": "Client monotonic latency and half-open completion windows within each origin; deadline inclusive. Worker clock spans are descriptive only; no cross-clock queue subtraction.",
             "destination_denominator": "checksum-valid successful returns; failed destinations separately retained"}
    staging = output.with_name(output.name + f".partial-{time.time_ns()}")
    staging.mkdir(parents=True)
    for name, columns, data in (("runs.csv", BASE + METRICS, rows), ("cells.csv", CELL_COLUMNS, cells)):
        with (staging / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns); writer.writeheader(); writer.writerows(data)
    audit["export_sha256"] = {name: digest((staging / name).read_bytes()) for name in ("runs.csv", "cells.csv")}
    (staging / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    staging.rename(output)
    return {"status": "PASS", "output": str(output), "treatments": len(rows), "cells": len(grouped), "metric_rows": len(cells)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--schema", action="store_true", help="Print exact CSV columns and metric names without reading a campaign")
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    if args.schema:
        print(json.dumps({"runs_columns": list(BASE + METRICS), "cells_columns": list(CELL_COLUMNS), "metrics": list(METRICS)}, indent=2))
        return 0
    if args.root is None or args.output is None:
        parser.error("--root and --output are required unless --schema is used")
    try:
        print(json.dumps(publish(args.root.resolve(), args.output.resolve(), args.source_dir.resolve())))
    except (ValueError, KeyError, OSError, TypeError) as error:
        print(json.dumps({"status": "NOT_PUBLISHED", "error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
