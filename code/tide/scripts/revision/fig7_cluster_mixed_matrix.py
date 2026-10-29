#!/usr/bin/env python3
"""Freeze, execute, and collect the paired three-node Fig. 7 mixed-deadline matrix.

freeze --root LOCAL reads calibration/{nj1,nj2,nj3}-{bare,tide}.json and
creates an immutable frozen/manifest.json. run requires integrated_smoke.json
with ok=true. collect only recovers completed attempts; neither command ever
relaunches an ambiguous or failed attempt. Every remote command and exit code
is archived. Performance treatments execute sequentially, with two origins
sharing one future start time and one fixed observation window.
"""
import argparse
import fcntl
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import time

from fig7_cluster_mixed_driver import canonical, expected_result, generate_mixed_trace
from fig6_cluster_analyze import load_run, summarize_runs

from fig7_cluster_mixed_matrix_transport import (REMOTE, HOSTS, REMOTE_RUN, write_json, ssh_options,
                                           ssh_command, logged_command, remote_locks)

ORIGINS = {"k2": "nj2", "k3": "nj3"}
SYSTEMS = ("bare", "tide", "ray", "kubeedge")
KINDS = ("cpu",)
PATTERNS = ("balanced",)
SEED = 20261016
LOAD_FACTORS = {"low": .30, "high": .80, "overload": 1.20}
WORKLOAD = {"kind": "cpu", "bytes": 65536, "iterations": 100}
QOS_BY_FUNCTION_MS = {"cpu-qos100": 100, "cpu-qos500": 500}
SOURCES = ("fig7_cluster_mixed_driver.py", "fig7_cluster_mixed_calibrate.py",
           "fig7_cluster_mixed_matrix.py", "fig7_cluster_mixed_matrix_transport.py", "fig7_cluster_mixed_audit.py",
           "fig6_cluster_driver.py", "fig6_cluster_analyze.py")


def digest_json(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_plan(calibrations, campaign):
    for node in HOSTS:
        for system in ("bare", "tide"):
            obj = calibrations[f"{node}-{system}"]
            if obj["workloads"] != {"cpu": WORKLOAD}:
                raise ValueError("mixed campaign requires CPU 65536 bytes and 100 iterations")
            for field in ("median_service_ms", "median_path_ms"):
                value = obj["summary"]["cpu"][field]
                if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    raise ValueError("calibration service/path medians must be finite and positive")
    capacity = sum((2 if node == "nj1" else 1) * 1000 /
                   calibrations[f"{node}-bare"]["summary"]["cpu"]["median_service_ms"]
                   for node in HOSTS)
    rates = {level: round(max(2, capacity * factor)) for level, factor in LOAD_FACTORS.items()}
    estimates = {node: math.ceil(calibrations[f"{node}-tide"]["summary"]["cpu"]["median_path_ms"])
                 for node in HOSTS}
    config = {"execution_ms": max(estimates.values()), "node_execution_ms": estimates,
              "network_ms": {node: 1 for node in HOSTS}, "qos_ms": 500,
              "qos_by_function_ms": dict(QOS_BY_FUNCTION_MS),
              "ttl_ms": 86400000, "weights": [1, 1, 1], "latency_scale_ms": 100,
              "state_max_age_ms": 5000}
    traces = {f"cpu-{level}-balanced.json": generate_mixed_trace("balanced", 10, rate, WORKLOAD)
              for level, rate in rates.items()}
    rng, jobs = random.Random(SEED), []
    for repeat in range(1, 6):
        levels = list(LOAD_FACTORS)
        rng.shuffle(levels)
        for level in levels:
            treatments = list(SYSTEMS)
            rng.shuffle(treatments)
            for system in treatments:
                jobs.append({"id": f"{campaign}-j{len(jobs) + 1:03d}", "sequence": len(jobs) + 1,
                             "block": f"r{repeat}-cpu-{level}-balanced", "repeat": repeat,
                             "kind": "cpu", "level": level, "pattern": "balanced", "system": system,
                             "load_factor": LOAD_FACTORS[level], "rate_rps": rates[level],
                             "trace": f"cpu-{level}-balanced.json"})
    plan = {"schema_version": 2, "campaign": campaign, "seed": SEED, "jobs": jobs,
            "capacity_rps": {"cpu": capacity}, "rates_rps": {"cpu": rates},
            "load_factors": dict(LOAD_FACTORS), "workloads": {"cpu": dict(WORKLOAD)},
            "tide_configs": {"cpu": config}, "duration_s": 10, "deadline_classes_ms": [100, 500],
            "timeout_s": 10, "repetitions": 5, "systems": list(SYSTEMS), "origins": ORIGINS,
            "slots": {"nj1": 2, "nj2": 1, "nj3": 1}, "inter_treatment_drain_s": 1,
            "quiescence_rule": "after every treatment: at least 3 quiet samples spanning at least 2 seconds",
            "rate_rule": "round(max(2, sum(node_slots * 1000 / bare median service ms) * factor)); factors=0.30,0.80,1.20; Python round",
            "order_rule": "seeded shuffle of 3 load cells within each repeat; seeded shuffle of four systems within each cell",
            "timing_rule": "both origins start at NJ-k1 time.time()+5; clocks validated in integrated smoke"}
    return plan, traces


def read_calibrations(root):
    inputs = {}
    for node in HOSTS:
        for system in ("bare", "tide"):
            key = f"{node}-{system}"
            obj = json.loads((root / "calibration" / f"{key}.json").read_text())
            if obj["node"] != node or obj["system"] != system or obj["workloads"] != {"cpu": WORKLOAD}:
                raise ValueError("calibration identity or workload mismatch: " + key)
            for kind in KINDS:
                reference = expected_result(obj["workloads"][kind])
                rows = [row for row in obj["rows"] if row["kind"] == kind]
                summary = obj["summary"][kind]
                if not rows or len(rows) != summary["samples"] or len({r["sample"] for r in rows}) != len(rows):
                    raise ValueError("calibration sample count mismatch: " + key)
                if any(r["response"]["result"] != reference or r["response"]["node"] != node
                       or r["elapsed_ns"] <= 0 or r["response"]["service_ns"] <= 0 for r in rows):
                    raise ValueError("invalid calibration result: " + key)
                measured = {"median_service_ms": statistics.median(r["response"]["service_ns"] / 1e6 for r in rows),
                            "median_path_ms": statistics.median(r["elapsed_ns"] / 1e6 for r in rows)}
                if any(not math.isclose(summary[k], v, rel_tol=1e-10) for k, v in measured.items()):
                    raise ValueError("calibration summary differs from raw records: " + key)
            inputs[key] = obj
    first = next(iter(inputs.values()))["workloads"]
    if any(obj["workloads"] != first for obj in inputs.values()):
        raise ValueError("calibrations used different fixed kernels")
    return inputs


def freeze(root):
    frozen = root / "frozen"
    formal = root / "formal"
    attempted = any(path.is_file() for pattern in ("*-k*.meta.json", "*-k*.jsonl", "*-k*.jsonl.partial", "state/*.json")
                    for path in formal.glob(pattern))
    if frozen.exists() or attempted:
        raise RuntimeError("freeze destination or formal attempts already exist; refusing to replace the manifest")
    inputs = read_calibrations(root)
    created = time.time_ns()
    plan, traces = build_plan(inputs, f"f7-{created}")
    plan["created_ns"] = created
    plan["source_sha256"] = {name: digest_file(Path(__file__).parent / name) for name in SOURCES}
    staging = root / f".frozen-{created}.partial"
    staging.mkdir()
    try:
        plan["calibration_sha256"] = {}
        for key in inputs:
            name = f"{key}.json"
            raw = (root / "calibration" / name).read_bytes()
            (staging / "calibration").mkdir(exist_ok=True)
            (staging / "calibration" / name).write_bytes(raw)
            plan["calibration_sha256"][name] = hashlib.sha256(raw).hexdigest()
        plan["traces"] = {}
        for name, trace in traces.items():
            write_json(staging / "traces" / name, trace)
            plan["traces"][name] = {"sha256": digest_json(trace),
                                     "file_sha256": digest_file(staging / "traces" / name),
                                     "offered": len(trace["requests"])}
        for kind, config in plan["tide_configs"].items():
            write_json(staging / "configs" / f"tide-{kind}.json", config)
        write_json(staging / "manifest.json", plan)
        (staging / "manifest.sha256").write_text(digest_file(staging / "manifest.json") + "\n")
        staging.rename(frozen)
    except BaseException:
        # Preserve incomplete freeze evidence; never silently delete or replace it.
        raise
    print(json.dumps({"event": "frozen", "manifest": str(frozen / "manifest.json"),
                      "sha256": digest_file(frozen / "manifest.json"), "treatments": len(plan["jobs"]),
                      "capacity_rps": plan["capacity_rps"], "rates_rps": plan["rates_rps"]}), flush=True)


def load_plan(root):
    frozen = root / "frozen"
    manifest_path = frozen / "manifest.json"
    manifest_sha = digest_file(manifest_path)
    if manifest_sha != (frozen / "manifest.sha256").read_text().strip():
        raise ValueError("frozen manifest hash changed")
    plan = json.loads(manifest_path.read_text())
    for name, info in plan["traces"].items():
        path = frozen / "traces" / name
        if digest_file(path) != info["file_sha256"] or digest_json(json.loads(path.read_text())) != info["sha256"]:
            raise ValueError("frozen trace changed: " + name)
    for name, wanted in plan["calibration_sha256"].items():
        if digest_file(frozen / "calibration" / name) != wanted:
            raise ValueError("frozen calibration changed: " + name)
    for kind, config in plan["tide_configs"].items():
        if json.loads((frozen / "configs" / f"tide-{kind}.json").read_text()) != config:
            raise ValueError("frozen Tide config changed")
    return plan, manifest_sha


def remote_state(root, job, origin):
    node = ORIGINS[origin]
    path = f"{REMOTE}/formal/{job['id']}-{origin}.state.json"
    code = "import pathlib,json,sys; p=pathlib.Path(sys.argv[1]); print(p.read_text() if p.exists() else 'null')"
    return json.loads(logged_command(root, f"state-{job['id']}-{origin}", ssh_command(node, ["python3", "-c", code, path])))


def require_terminal_pair(states):
    for origin in ORIGINS:
        state = states.get(origin)
        if not state or state.get("status") != "completed":
            raise RuntimeError("ambiguous or active prior attempt for " + origin + "; refusing to relaunch")
        if state.get("exit_code") != 0:
            raise RuntimeError("prior attempt failed for " + origin + "; preserve it and investigate without rerun")
    return True


def routes(system, origin):
    if system in ("tide", "ray"):
        return "gateway", [f"http://127.0.0.1:{29801 if system == 'tide' else 29870}/run"]
    port = 29810 if system == "bare" else 29890
    return "direct", [f"http://11.0.4.47:{port}/run", f"http://11.0.4.47:{port + 1}/run",
                      f"http://11.0.4.48:{port}/run", f"http://11.0.4.49:{port}/run"]


def audit_pair(job, trace, pairs):
    if {meta["origin"] for meta, _ in pairs} != set(ORIGINS) or len(pairs) != 2:
        raise ValueError("both distinct origins are required")
    for meta, records in pairs:
        origin = meta["origin"]
        expected = {r["id"]: r for r in trace["requests"] if r["origin"] == origin}
        if (set(meta["expected_ids"]) != set(expected) or meta["system"] != job["system"]
                or meta["trace_sha256"] != digest_json(trace) or meta["duration_ns"] != trace["duration_ns"]
                or meta["expected_result"] != trace["expected_result"] or meta["workload"] != trace["workload"]):
            raise ValueError("metadata differs from the frozen trace or treatment")
        for record in records:
            request = expected.get(record["id"])
            if (request is None or record["request"] != {**trace["workload"], "deadline_ms": request["deadline_ms"]}
                    or record["offset_ns"] != request["offset_ns"] or record["deadline_ms"] != request["deadline_ms"]
                    or record["planned_submit_monotonic_ns"] != meta["window_start_monotonic_ns"] + request["offset_ns"]):
                raise ValueError("offered record differs from frozen request")
            function = f"cpu-qos{request['deadline_ms']}"
            if (record.get("transmitted_deadline_ms") != request["deadline_ms"]
                    or record.get("expected_function") != function
                    or record.get("request_sha256") != digest_json(record["request"])):
                raise ValueError("class-bearing request differs from frozen deadline")
            if job["system"] == "tide" and record.get("http_status") == 200 and not record.get("error"):
                headers = record.get("response_headers", {})
                if (headers.get("x-tide-function") != function
                        or headers.get("x-tide-deadline-ms") != str(request["deadline_ms"])
                        or headers.get("x-tide-effective-deadline-ms") != str(request["deadline_ms"])
                        or not headers.get("x-tide-request-id")):
                    raise ValueError("successful response omitted or mismatched the requested class")

    summary = summarize_runs(pairs)
    if summary["offered"] != len(trace["requests"]):
        raise ValueError("pair omitted offered requests")
    return {**summary, "job": job}


def state_path(root, job):
    return root / "formal" / "state" / (job["id"] + ".json")


def load_job_state(root, job):
    path = state_path(root, job)
    state = json.loads(path.read_text()) if path.exists() else {"job": job, "status": "planned", "launches": {}}
    if state.get("job") != job:
        raise ValueError("job state differs from the frozen treatment")
    return state


def pending_execution_risks(pairs):
    risks = [{"reason": "mandatory_mixed_campaign_quiescence"}]
    for meta, records in pairs:
        for record in records:
            status, error = record.get("http_status"), record.get("error")
            if error == "timeout" or status == 408 or (status is not None and status >= 500) or (status is None and error):
                risks.append({"origin": meta["origin"], "id": record["id"], "error": error, "http_status": status})
    return risks


def validate_quiescence_observation(observed, job, state, verified_ns):
    """Validate the accepted raw probe window, not an operator's idle assertion."""
    def require(condition):
        if not condition:
            raise ValueError("quiescence evidence lacks a passed, identity-bound quiet observation")
    audit = observed.get("audit", {})
    audited_state = audit.get("state", {})
    require(observed.get("criterion") == "bounded observed quiescence"
            and observed.get("passed") is True and observed.get("verified_ns") == verified_ns
            and observed.get("last_window_errors") == [] and audit.get("job") == job)
    require(all(audited_state.get(field) == state.get(field)
                for field in ("job", "summary_sha256", "completed_ns", "pending_execution")))
    samples = observed.get("samples", [])
    first = observed.get("accepted_first_sample")
    require(type(first) is int and 0 <= first <= len(samples) - 3)
    accepted = samples[first:]
    require(accepted[-1]["monotonic_ns"] - accepted[0]["finished_monotonic_ns"] >= 2 * 10**9)
    baseline = {}
    for ordinal, sample in enumerate(accepted):
        require(state["completed_ns"] < sample["time_ns"] < verified_ns
                and sample["finished_monotonic_ns"] >= sample["monotonic_ns"]
                and set(sample["nodes"]) == set(HOSTS))
        if ordinal:
            require(0 < sample["monotonic_ns"] - accepted[ordinal - 1]["monotonic_ns"] <= 5 * 10**9)
        replicas = set()
        for node, row in sample["nodes"].items():
            require(not row.get("error") and row.get("locks_available") is True)
            require(all(row.get(field) == [] for field in
                        ("identity_errors", "active_load", "nonterminal_states", "missing_target_states", "sockets")))
            workers = row.get("workers", [])
            identities = {(w["pid"], str(w["start_ticks"])) for w in workers}
            slots = 2 if node == "nj1" else 1
            require(len(workers) == len(identities) == slots)
            require(baseline.setdefault((node, "identities"), identities) == identities)
            for worker in workers:
                require(bool(worker.get("threads")) and all(t.get("state") in ("S", "I") for t in worker["threads"]))
                counters = (worker["cpu_ticks"], worker["io"])
                key = (node, worker["pid"])
                require(baseline.setdefault(key, counters) == counters)
            if job["system"] == "ray":
                metrics = [r for r in row.get("metrics", []) if r["labels"].get("application") == "tmc-fig6-ray"
                           and r["labels"].get("deployment") == "MatchedGoKernel"]
                gauges = ("ray_serve_deployment_queued_queries", "ray_serve_num_ongoing_requests_at_replicas",
                          "ray_serve_replica_processing_queries")
                required_gauges = gauges if node != "nj1" else (gauges[2],)
                for metric in required_gauges:
                    series = [r for r in metrics if r["name"] == metric]
                    require(bool(series) and all(r["value"] == 0 for r in series))
                ids = [r["labels"].get("replica") for r in metrics if r["name"] == "ray_serve_replica_processing_queries"]
                require(len(ids) == len(set(ids)) == slots and None not in ids and not replicas.intersection(ids))
                replicas.update(ids)
        if job["system"] == "ray":
            require(baseline.setdefault("replicas", replicas) == replicas)


def validate_drain_clearance(root, job, state):
    path = root / "formal" / "drain-clearance" / (job["id"] + ".json")
    if not path.exists():
        raise RuntimeError("drain_required after " + job["id"] + "; verify workers idle and archive evidence before continuing")
    clearance = json.loads(path.read_text())
    if (clearance.get("job") != job["id"] or clearance.get("summary_sha256") != state["summary_sha256"]
            or clearance.get("idle_verified") is not True or clearance.get("verified_ns", 0) <= state["completed_ns"]
            or not clearance.get("evidence")):
        raise ValueError("drain clearance must identify this completed treatment and a subsequent idle audit")
    for evidence in clearance["evidence"]:
        relative = Path(evidence["path"])
        if relative.is_absolute() or ".." in relative.parts or digest_file(root / relative) != evidence["sha256"]:
            raise ValueError("drain clearance evidence hash or path mismatch")
        observed = json.loads((root / relative).read_text())
        try:
            validate_quiescence_observation(observed, job, state, clearance["verified_ns"])
        except (KeyError, TypeError, IndexError) as error:
            raise ValueError("malformed quiescence observation") from error
    return path


def enforce_drain_gate(root, job, state):
    if not state.get("pending_execution"):
        return
    path = validate_drain_clearance(root, job, state)
    state.update(readiness="drain_verified", drain_clearance_sha256=digest_file(path))
    write_json(state_path(root, job), state)


def update_progress(root, plan):
    counts, drain_required = {}, []
    for job in plan["jobs"]:
        state = load_job_state(root, job)
        status = state["status"]
        counts[status] = counts.get(status, 0) + 1
        if state.get("readiness") == "drain_required":
            drain_required.append(job["id"])
    write_json(root / "progress.json", {"campaign": plan["campaign"], "time_ns": time.time_ns(),
                                        "total": len(plan["jobs"]), "statuses": counts, "drain_required": drain_required})


def collect_job(root, plan, job, state):
    terminal = {origin: remote_state(root, job, origin) for origin in ORIGINS}
    require_terminal_pair(terminal)
    for origin, remote in terminal.items():
        if (remote.get("job") != job["id"] or remote.get("origin") != origin
                or remote.get("manifest_sha256") != digest_file(root / "frozen" / "manifest.json")):
            raise ValueError("remote terminal state identity mismatch")
    state["remote_terminal"] = terminal
    state["status"] = "collecting"
    write_json(state_path(root, job), state)
    formal = root / "formal"
    for origin, node in ORIGINS.items():
        prefix = f"{job['id']}-{origin}"
        staging = formal / "downloads" / f"{prefix}-{time.time_ns()}"
        staging.mkdir(parents=True)
        sources = [f"{HOSTS[node]}:{REMOTE}/formal/{prefix}{suffix}" for suffix in (".meta.json", ".jsonl")]
        logged_command(root, "fetch-" + prefix, ["scp", *ssh_options(node), *sources, str(staging)])
        # Verify the downloaded pair before promoting it to its final local name.
        load_run(staging / (prefix + ".meta.json"))
        for suffix in (".meta.json", ".jsonl"):
            source, destination = staging / (prefix + suffix), formal / (prefix + suffix)
            if destination.exists() and digest_file(destination) != digest_file(source):
                raise ValueError("collected artifact would replace different evidence: " + str(destination))
            source.replace(destination)
    trace = json.loads((root / "frozen" / "traces" / job["trace"]).read_text())
    pairs = [load_run(formal / f"{job['id']}-{origin}.meta.json") for origin in ORIGINS]
    summary = audit_pair(job, trace, pairs)
    if state.get("start_unix") is not None and summary["start_unix"] != state["start_unix"]:
        raise ValueError("collected start time differs from coordinator target")
    write_json(formal / (job["id"] + ".summary.json"), summary)
    state["status"] = "completed"
    state["completed_ns"] = time.time_ns()
    state["summary_sha256"] = digest_file(formal / (job["id"] + ".summary.json"))
    state["pending_execution"] = pending_execution_risks(pairs)
    state["readiness"] = "drain_required" if state["pending_execution"] else "responses_drained"
    write_json(state_path(root, job), state)
    update_progress(root, plan)
    print(json.dumps({"event": "completed", "job": job["id"], "sequence": job["sequence"],
                      "total": len(plan["jobs"]), "system": job["system"], "block": job["block"],
                      "offered": summary["offered"], "failed": summary["failed"],
                      "readiness": state["readiness"],
                      "throughput_rps": summary["throughput_rps"], "goodput_rps": summary["goodput_rps"]}), flush=True)


def prepare_remote(root, plan):
    # Never overlap an earlier attempt whose wrapper still reports active state.
    scan = ("import json,pathlib; p=pathlib.Path(" + repr(REMOTE + "/formal") + "); "
            "print(json.dumps([{'path':str(f),'state':json.loads(f.read_text())} for f in p.glob('*.state.json') "
            "if json.loads(f.read_text()).get('status')!='completed']))")
    for node in HOSTS:
        active = json.loads(logged_command(root, "active-" + node, ssh_command(node, ["python3", "-c", scan])))
        if active:
            raise RuntimeError("prior remote load state is active or ambiguous on " + node + ": " + json.dumps(active))
        logged_command(root, "mkdir-" + node, ssh_command(node, ["mkdir", "-p", REMOTE + "/traces", REMOTE + "/configs", REMOTE + "/formal"]))
        configs = [str(path) for path in sorted((root / "frozen" / "configs").glob("*.json"))]
        logged_command(root, "configs-" + node, ["scp", *ssh_options(node), *configs, f"{HOSTS[node]}:{REMOTE}/configs/"])
        if node in ORIGINS.values():
            traces = [str(path) for path in sorted((root / "frozen" / "traces").glob("*.json"))]
            logged_command(root, "traces-" + node, ["scp", *ssh_options(node), *traces, f"{HOSTS[node]}:{REMOTE}/traces/"])


def configure_tide(root, plan, job):
    config = plan["tide_configs"][job["kind"]]
    code = ("import json,sys,urllib.request; data=json.dumps(json.loads(sys.argv[1])).encode(); "
            "req=urllib.request.Request('http://127.0.0.1:29801/config',data,{'Content-Type':'application/json'}); "
            "print(urllib.request.urlopen(req,timeout=10).read().decode())")
    for node in HOSTS:
        actual = json.loads(logged_command(root, f"config-{job['id']}-{node}",
                                           ssh_command(node, ["python3", "-c", code, json.dumps(config)])))
        if any(actual.get(key) != value for key, value in config.items()):
            raise ValueError("native Tide did not apply frozen configuration on " + node)


def execute_job(root, plan, manifest_sha, job, state):
    if state.get("launches") or state["status"] not in ("planned", "preparing"):
        raise RuntimeError("refusing to execute a previously attempted treatment")
    state["status"] = "preparing"
    write_json(state_path(root, job), state)
    if job["system"] == "tide":
        configure_tide(root, plan, job)
    start_unix = float(logged_command(root, "clock-" + job["id"], ssh_command("nj1", ["python3", "-c", "import time;print(time.time()+5)"])))
    state.update(start_unix=start_unix, manifest_sha256=manifest_sha, status="launching")
    write_json(state_path(root, job), state)
    processes = []
    try:
        for origin, node in ORIGINS.items():
            route, urls = routes(job["system"], origin)
            prefix = f"{REMOTE}/formal/{job['id']}-{origin}"
            trace_path = f"{REMOTE}/traces/{job['trace']}"
            command = ["taskset", "-c", "8-15", "python3", REMOTE + "/fig7_cluster_mixed_driver.py", "run",
                       "--trace", trace_path, "--origin", origin, "--system", job["system"], "--route", route,
                       "--urls", *urls, "--start-unix", repr(start_unix), "--timeout-s", str(plan["timeout_s"]), "--output", prefix]
            spec = {"job": job["id"], "origin": origin, "prefix": prefix, "root": REMOTE,
                    "manifest_sha256": manifest_sha, "command": command, "trace_path": trace_path,
                    "trace_file_sha256": plan["traces"][job["trace"]]["file_sha256"],
                    "source_sha256": {name: plan["source_sha256"][name] for name in ("fig7_cluster_mixed_driver.py", "fig6_cluster_driver.py", "fig6_cluster_analyze.py")}}
            argv = ssh_command(node, ["python3", "-c", REMOTE_RUN, json.dumps(spec)])
            stdout_path = root / "formal" / "logs" / f"{job['id']}-{origin}.ssh.stdout.log"
            stderr_path = root / "formal" / "logs" / f"{job['id']}-{origin}.ssh.stderr.log"
            state["launches"][origin] = {"intent_ns": time.time_ns(), "argv": argv,
                                          "stdout": str(stdout_path), "stderr": str(stderr_path)}
            # Intent is durable before Popen; a crash in between is ambiguous, never auto-retried.
            write_json(state_path(root, job), state)
            out, err = stdout_path.open("xb"), stderr_path.open("xb")
            process = subprocess.Popen(argv, stdout=out, stderr=err)
            processes.append((origin, process, out, err))
            state["launches"][origin]["local_ssh_pid"] = process.pid
            write_json(state_path(root, job), state)
        state["status"] = "running"
        write_json(state_path(root, job), state)
        update_progress(root, plan)
        for origin, process, _, _ in processes:
            state["launches"][origin]["ssh_exit_code"] = process.wait(timeout=120)
            state["launches"][origin]["ssh_finished_ns"] = time.time_ns()
            write_json(state_path(root, job), state)
    finally:
        for _, _, out, err in processes:
            out.close()
            err.close()
    # Terminal remote state, not SSH success alone, determines whether an attempt completed.
    collect_job(root, plan, job, state)


def verify_completed(root, job):
    trace = json.loads((root / "frozen" / "traces" / job["trace"]).read_text())
    pairs = [load_run(root / "formal" / f"{job['id']}-{origin}.meta.json") for origin in ORIGINS]
    summary = audit_pair(job, trace, pairs)
    path = root / "formal" / (job["id"] + ".summary.json")
    if json.loads(path.read_text()) != summary:
        raise ValueError("completed summary differs from independently reconstructed metrics")


def validate_readiness(ready, plan):
    if ready.get("ok") is not True:
        raise RuntimeError("integrated_smoke.json has no successful readiness marker")
    configs = ready.get("native_configs")
    if (not isinstance(configs, dict) or set(configs) != set(HOSTS)
            or any(configs[node].get("qos_by_function_ms") != QOS_BY_FUNCTION_MS for node in HOSTS)):
        raise ValueError("readiness native_configs must prove both deadline classes on all three nodes")


def run(root, collect_only=False):
    plan, manifest_sha = load_plan(root)
    if not collect_only:
        ready = json.loads((root / "integrated_smoke.json").read_text())
        validate_readiness(ready, plan)
        for name, wanted in plan["source_sha256"].items():
            if digest_file(Path(__file__).parent / name) != wanted:
                raise RuntimeError("execution source changed since freeze: " + name)
        readiness_path = root / "formal" / "readiness-used.json"
        readiness = {"marker_sha256": digest_file(root / "integrated_smoke.json"),
                     "marker": ready, "manifest_sha256": manifest_sha}
        if readiness_path.exists() and json.loads(readiness_path.read_text()) != readiness:
            raise RuntimeError("readiness marker changed after the campaign started")
        write_json(readiness_path, readiness)
    # Recover existing attempts first. A missing terminal record stops the campaign.
    for job in plan["jobs"]:
        state = load_job_state(root, job)
        if state["status"] == "completed":
            verify_completed(root, job)
        elif state["status"] not in ("planned", "preparing") or state.get("launches"):
            collect_job(root, plan, job, state)
    if collect_only:
        update_progress(root, plan)
        return
    for job in plan["jobs"]:
        enforce_drain_gate(root, job, load_job_state(root, job))
    update_progress(root, plan)
    with remote_locks(root, plan["campaign"]) as holders:
        prepare_remote(root, plan)
        for job in plan["jobs"]:
            state = load_job_state(root, job)
            if state["status"] == "completed":
                continue
            if any(process.poll() is not None for _, process, _ in holders):
                raise RuntimeError("a remote campaign lock connection ended; refusing to start another treatment")
            try:
                execute_job(root, plan, manifest_sha, job, state)
                enforce_drain_gate(root, job, state)
            except BaseException as error:
                state["last_error"] = {"type": type(error).__name__, "message": str(error), "time_ns": time.time_ns()}
                write_json(state_path(root, job), state)
                update_progress(root, plan)
                raise
            time.sleep(plan["inter_treatment_drain_s"])
    update_progress(root, plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "collect"))
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "matrix.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "freeze":
            freeze(root)
        else:
            run(root, collect_only=args.action == "collect")


if __name__ == "__main__":
    main()
