#!/usr/bin/env python3
"""Freeze, execute, and collect the paired three-node Fig. 6 matrix.

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

from fig6_cluster_driver import canonical, expected_result, generate_trace
from fig6_cluster_analyze import load_run, summarize_runs

from fig6_cluster_matrix_transport import (REMOTE, HOSTS, REMOTE_RUN, write_json, ssh_options,
                                           ssh_command, logged_command, remote_locks)

ORIGINS = {"k2": "nj2", "k3": "nj3"}
SYSTEMS = ("bare", "tide", "ray", "kubeedge")
KINDS = ("cpu", "memory", "io")
PATTERNS = ("balanced", "hotspot", "burst")
SEED = 20261014
SOURCES = ("fig6_cluster_driver.py", "fig6_cluster_analyze.py", "fig6_cluster_calibrate.py", "fig6_cluster_matrix.py", "fig6_cluster_matrix_transport.py")


def digest_json(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_rates_override(rates):
    if not isinstance(rates, dict) or set(rates) != set(KINDS):
        raise ValueError("explicit rates must contain exactly cpu, memory, and io")
    copied = {}
    for kind in KINDS:
        levels = rates[kind]
        if (not isinstance(levels, dict) or set(levels) != {"low", "high"}
                or any(type(value) is not int or value <= 0 for value in levels.values())
                or levels["low"] >= levels["high"]):
            raise ValueError("explicit rates require positive integer low < high for " + kind)
        copied[kind] = dict(levels)
    return copied


def build_plan(calibrations, campaign, rate_multiple=1, rates_override=None):
    if type(rate_multiple) is not int or rate_multiple not in (1, 5):
        raise ValueError("rate_multiple must be 1 (legacy) or 5 (nearest-five)")
    if rates_override is not None:
        if rate_multiple != 1:
            raise ValueError("explicit rates and rate quantization are mutually exclusive")
        rates_override = validate_rates_override(rates_override)
    workloads = calibrations["nj1-bare"]["workloads"]
    capacities, rates, configs, traces = {}, {}, {}, {}
    for kind in KINDS:
        capacities[kind] = sum((2 if node == "nj1" else 1) * 1000 /
                               calibrations[f"{node}-bare"]["summary"][kind]["median_service_ms"]
                               for node in HOSTS)
        rates[kind] = {level: round(max(2, capacities[kind] * factor))
                       for level, factor in (("low", .30), ("high", .80))}
        if rate_multiple == 5:
            rates[kind] = {level: max(5, 5 * ((2 * rate + 5) // 10)) for level, rate in rates[kind].items()}
        if rates_override is not None:
            rates[kind] = dict(rates_override[kind])
        estimates = {node: math.ceil(calibrations[f"{node}-tide"]["summary"][kind]["median_path_ms"])
                     for node in HOSTS}
        configs[kind] = {"execution_ms": max(estimates.values()), "node_execution_ms": estimates,
                         "network_ms": {node: 1 for node in HOSTS}, "qos_ms": 500,
                         "ttl_ms": 86400000, "weights": [1, 1, 1], "latency_scale_ms": 100,
                         "state_max_age_ms": 5000}
        for level, rate in rates[kind].items():
            for pattern in PATTERNS:
                name = f"{kind}-{level}-{pattern}.json"
                traces[name] = generate_trace(pattern, 10, rate, workloads[kind], 500)
    rng = random.Random(SEED)
    cells = [(kind, level, pattern) for kind in KINDS for level in ("low", "high") for pattern in PATTERNS]
    jobs = []
    for repeat in range(1, 6):
        ordered_cells = list(cells)
        rng.shuffle(ordered_cells)
        for kind, level, pattern in ordered_cells:
            treatments = list(SYSTEMS)
            rng.shuffle(treatments)
            block = f"r{repeat}-{kind}-{level}-{pattern}"
            for system in treatments:
                jobs.append({"id": f"{campaign}-j{len(jobs) + 1:03d}", "sequence": len(jobs) + 1,
                             "block": block, "repeat": repeat, "kind": kind, "level": level,
                             "pattern": pattern, "system": system, "rate_rps": rates[kind][level],
                             "trace": f"{kind}-{level}-{pattern}.json"})
    plan = {"schema_version": 1, "campaign": campaign, "seed": SEED, "jobs": jobs,
            "capacity_rps": capacities, "rates_rps": rates, "workloads": workloads,
            "tide_configs": configs, "duration_s": 10, "deadline_ms": 500, "timeout_s": 10,
            "repetitions": 5, "systems": list(SYSTEMS), "origins": ORIGINS,
            "slots": {"nj1": 2, "nj2": 1, "nj3": 1}, "inter_treatment_drain_s": 1,
            "rate_rule": "round(max(2, sum(node_slots * 1000 / bare median service ms) * factor)); factors=0.30,0.80; Python round",
            "order_rule": "seeded shuffle of 18 cells within each repeat; seeded shuffle of four systems within each cell",
            "timing_rule": "both origins start at NJ-k1 time.time()+5; clocks validated in integrated smoke"}
    if rate_multiple == 5:
        plan["rate_quantization"] = {"multiple": 5, "rounding": "half_up", "input": "calibration_integer_rate", "minimum": 5}
        plan["rate_rule"] += "; then nearest positive multiple of 5, half_up: max(5, 5 * ((2 * integer_rate + 5) // 10))"
    if rates_override is not None:
        plan["rate_selection"] = {"mode": "explicit", "rates_rps": rates_override}
        plan["rate_rule"] = "explicit positive integer requests per second from rate_selection.rates_rps; low < high"
    return plan, traces


def read_calibrations(root):
    inputs = {}
    for node in HOSTS:
        for system in ("bare", "tide"):
            key = f"{node}-{system}"
            obj = json.loads((root / "calibration" / f"{key}.json").read_text())
            if obj["node"] != node or obj["system"] != system or set(obj["workloads"]) != set(KINDS):
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


def freeze(root, rate_multiple=1, calibration_reuse_manifest=None, rates_override=None):
    frozen = root / "frozen"
    if frozen.exists() or any((root / "formal").glob("*-k*.meta.json")):
        raise RuntimeError("freeze destination or formal outcomes already exist; refusing to replace the manifest")
    inputs = read_calibrations(root)
    created = time.time_ns()
    plan, traces = build_plan(inputs, f"f6-{created}", rate_multiple=rate_multiple, rates_override=rates_override)
    if calibration_reuse_manifest is not None:
        prior_path = Path(calibration_reuse_manifest)
        prior_sha = digest_file(prior_path)
        if prior_sha != prior_path.with_suffix('.sha256').read_text().strip():
            raise ValueError("reused calibration manifest hash changed")
        prior = json.loads(prior_path.read_text())
        hashes = {key + '.json': digest_file(root/'calibration'/(key + '.json')) for key in inputs}
        if hashes != prior['calibration_sha256']:
            raise ValueError("reused calibration files differ from original manifest")
        plan['calibration_reuse'] = {'campaign': prior['campaign'], 'manifest_sha256': prior_sha,
                                     'calibration_sha256': hashes}
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
            if (request is None or record["request"] != trace["workload"]
                    or record["offset_ns"] != request["offset_ns"] or record["deadline_ms"] != request["deadline_ms"]
                    or record["planned_submit_monotonic_ns"] != meta["window_start_monotonic_ns"] + request["offset_ns"]):
                raise ValueError("offered record differs from frozen request")
    summary = summarize_runs(pairs)
    if summary["offered"] != len(trace["requests"]):
        raise ValueError("pair omitted offered requests")
    return {**summary, "job": job}


def state_path(root, job):
    return root / "formal" / "state" / (job["id"] + ".json")


def load_job_state(root, job):
    path = state_path(root, job)
    return json.loads(path.read_text()) if path.exists() else {"job": job, "status": "planned", "launches": {}}


def pending_execution_risks(pairs):
    risks = []
    for meta, records in pairs:
        for record in records:
            status, error = record.get("http_status"), record.get("error")
            if error == "timeout" or status == 408 or (status is not None and status >= 500) or (status is None and error):
                risks.append({"origin": meta["origin"], "id": record["id"], "error": error, "http_status": status})
    return risks


def enforce_drain_gate(root, job, state):
    if not state.get("pending_execution"):
        return
    path = root / "formal" / "drain-clearance" / (job["id"] + ".json")
    if not path.exists():
        raise RuntimeError("drain_required after " + job["id"] + "; verify workers idle and archive evidence before continuing")
    clearance = json.loads(path.read_text())
    if (clearance.get("job") != job["id"] or clearance.get("summary_sha256") != state["summary_sha256"]
            or clearance.get("idle_verified") is not True or clearance.get("verified_ns", 0) <= state["completed_ns"]
            or not clearance.get("evidence")):
        raise ValueError("drain clearance must identify this completed treatment and a subsequent idle audit")
    for evidence in clearance["evidence"]:
        if digest_file(root / evidence["path"]) != evidence["sha256"]:
            raise ValueError("drain clearance evidence hash mismatch")
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
            command = ["taskset", "-c", "8-15", "python3", REMOTE + "/fig6_cluster_driver.py", "run",
                       "--trace", trace_path, "--origin", origin, "--system", job["system"], "--route", route,
                       "--urls", *urls, "--start-unix", repr(start_unix), "--timeout-s", str(plan["timeout_s"]), "--output", prefix]
            spec = {"job": job["id"], "origin": origin, "prefix": prefix, "root": REMOTE,
                    "manifest_sha256": manifest_sha, "command": command, "trace_path": trace_path,
                    "trace_file_sha256": plan["traces"][job["trace"]]["file_sha256"],
                    "source_sha256": {name: plan["source_sha256"][name] for name in ("fig6_cluster_driver.py", "fig6_cluster_analyze.py")}}
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


def run(root, collect_only=False):
    plan, manifest_sha = load_plan(root)
    if not collect_only:
        ready = json.loads((root / "integrated_smoke.json").read_text())
        if ready.get("ok") is not True:
            raise RuntimeError("integrated_smoke.json has no successful readiness marker")
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
    parser.add_argument("--rate-multiple", type=int, choices=(1, 5), default=1,
                        help="freeze only: quantize selected integer rates to this multiple (default: legacy 1)")
    parser.add_argument("--rates-json", type=Path,
                        help="freeze only: explicit positive integer low/high rates for exactly cpu, memory, io")
    parser.add_argument("--calibration-reuse-manifest", type=Path, help="freeze only: original manifest binding reused calibration files")
    args = parser.parse_args()
    if args.action != "freeze" and (args.rate_multiple != 1 or args.calibration_reuse_manifest or args.rates_json):
        parser.error("rate/calibration selection options are only valid with freeze")
    if args.rates_json is not None and args.rate_multiple != 1:
        parser.error("--rates-json and nondefault --rate-multiple are mutually exclusive")
    rates_override = None
    if args.rates_json is not None:
        try:
            rates_override = validate_rates_override(json.loads(args.rates_json.read_text()))
        except (OSError, ValueError) as exc:
            parser.error("invalid --rates-json: " + str(exc))
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "matrix.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "freeze":
            freeze(root, rate_multiple=args.rate_multiple, calibration_reuse_manifest=args.calibration_reuse_manifest,
                   rates_override=rates_override)
        else:
            run(root, collect_only=args.action == "collect")


if __name__ == "__main__":
    main()
