#!/usr/bin/env python3
"""Audit and plot measured HTTP adapter faults; never synthesize outcomes."""
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

PHASES = ("connected", "disconnected", "recovered")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def phase_at(timestamp, events):
    return max(i for i, event in enumerate(events) if timestamp >= event["transition_completed_ns"])


def reaggregate(rows, events, end, qos):
    result = []
    for i, event in enumerate(events):
        start = event["transition_completed_ns"]
        stop = events[i + 1]["transition_completed_ns"] if i + 1 < len(events) else end
        assert stop > start
        result.append(dict(phase=event["phase"], start_ns=start, end_ns=stop, arrivals=0,
                           local_routes=0, remote_routes=0, admission_rejected=0,
                           cohort_successes=0, cohort_ontime=0, cohort_failures=0,
                           window_ontime_completions=0, cohort_successes_drained_after_full_window=0))
    for row in rows:
        submit, reply, status = int(row["t_submit_ns"]), int(row["t_reply_ns"]), int(row["status"])
        cohort = result[phase_at(submit, events)]
        cohort["arrivals"] += 1
        cohort[{"local": "local_routes", "remote": "remote_routes", "rejected": "admission_rejected"}[row["route"]]] += 1
        if status == 200:
            cohort["cohort_successes"] += 1
            if reply >= end:
                cohort["cohort_successes_drained_after_full_window"] += 1
            if reply - submit <= qos:
                cohort["cohort_ontime"] += 1
                if reply < end:
                    result[phase_at(reply, events)]["window_ontime_completions"] += 1
        else:
            cohort["cohort_failures"] += 1
    for phase in result:
        phase["cohort_ontime_yield"] = phase["cohort_ontime"] / phase["arrivals"] if phase["arrivals"] else 0
        phase["window_ontime_goodput_rps"] = phase["window_ontime_completions"] / ((phase["end_ns"] - phase["start_ns"]) / 1e9)
    return result


def load_json(path):
    return json.loads(path.read_text())


def audit(root, source_root):
    manifest = load_json(root / "manifest.json")
    assert manifest["status"] == "completed"
    assert len(manifest["runs"]) == 2 * manifest["repetitions"]
    for name, expected in manifest["source_sha256"].items():
        assert digest(source_root / name) == expected, f"source changed: {name}"
    for name, expected in manifest["artifact_sha256"].items():
        assert digest(root / name) == expected, f"artifact changed: {name}"
    assert digest(root / manifest["frozen_binary_path"]) == manifest["binary_sha256"]
    paired = collections.defaultdict(list)
    all_ids, checksums = set(), set()
    run_phases = []
    diagnostics = []
    for entry in manifest["runs"]:
        run_id = entry["run_id"]
        directory = root / run_id
        config = load_json(directory / "run/config.json")
        metadata = load_json(directory / "run/run.json")
        events = load_json(directory / "run/events.json")
        trace = load_json(directory / "run/trace.json")
        setup = load_json(directory / "setup.json")
        assert len(setup) == 2
        end, qos = 3 * config["phase_ns"], config["qos_ns"]
        assert entry["exit_status"] == 0 and metadata["status"] == "completed"
        assert config["run_id"] == run_id
        assert entry["local_eligible"] == config["local_eligible"] == metadata["local_eligible"]
        assert metadata["binary_sha256"] == manifest["binary_sha256"]
        assert config["offered_trace_ns"] == trace
        assert digest(directory / "run/trace.json") == entry["trace_sha256"]
        paired[entry["seed"]].append((entry["local_eligible"], entry["trace_sha256"], config["workload"]))
        assert [event["phase"] for event in events] == list(PHASES)
        assert events[0]["transition_completed_ns"] == 0
        for event in events:
            assert 0 <= event["transition_started_ns"] <= event["transition_completed_ns"] < end
        assert events[0]["transition_completed_ns"] < events[1]["transition_completed_ns"] < events[2]["transition_completed_ns"]
        targeted = [rid for event in events for rid in event["affected_inflight_request_ids"]]
        assert len(targeted) == len(set(targeted))
        with (directory / "run/raw.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == len(trace) == metadata["request_count"]
        assert [int(row["req_id"]) for row in rows] == list(range(len(trace)))
        by_id = {}
        for i, row in enumerate(rows):
            rid = row["request_id"]
            assert rid == f"{run_id}-{i}" and rid not in all_ids
            all_ids.add(rid)
            by_id[rid] = row
            assert int(row["planned_submit_ns"]) == trace[i]
            submit, reply, status = int(row["t_submit_ns"]), int(row["t_reply_ns"]), int(row["status"])
            assert reply >= submit >= trace[i]
            assert row["phase_submit"] == PHASES[phase_at(submit, events)]
            assert row["phase_reply"] == PHASES[phase_at(reply, events)]
            assert row["on_time"] == str(status == 200 and reply - submit <= qos).lower()
            assert row["success_within_full_window"] == str(status == 200 and reply < end).lower()
            assert row["fault_cancel_target"] == str(rid in targeted).lower()
            assert math.isclose(float(row["latency_ms"]), (reply - submit) / 1e6, rel_tol=1e-12, abs_tol=1e-9)
            if row["route"] == "rejected":
                assert status == 429
            else:
                assert row["route"] in ("local", "remote")
                assert int(row["t_dispatch_ns"]) >= submit
            if not config["local_eligible"]:
                assert row["route"] != "local"
        assert set(targeted) <= set(by_id)
        for rid in targeted:
            row = by_id[rid]
            assert row["route"] == "remote"
            assert int(row["t_dispatch_ns"]) <= events[1]["transition_completed_ns"]
            assert int(row["t_reply_ns"]) >= events[1]["transition_started_ns"]
        completions = {}
        cancelled_kernel_ids = []
        for node, worker in zip(("local", "remote"), setup):
            assert worker["node"] == node
            assert worker["binary_sha256"] == manifest["binary_sha256"]
            assert worker["state_before"]["pid"] == worker["pid"] == worker["state_after"]["pid"]
            assert worker["state_before"]["active"] == worker["state_after"]["active"] == 0
            assert worker["state_before"]["completed"] == 0
            affinity = manifest["worker_affinity"][0 if node == "local" else 1]
            for key in ("threads_affinity_before", "threads_affinity_after"):
                assert worker[key] and all(mask == sorted(affinity) for mask in worker[key].values())
            kernels = []
            for line in (directory / f"{node}-worker.jsonl").read_text().splitlines():
                event = json.loads(line)
                if "service_ns" in event:
                    kernels.append(event)
                else:
                    assert "response_write_error" in event and event["request_id"] in by_id
            assert len(kernels) == worker["state_after"]["completed"]
            previous_finish = 0
            for kernel in kernels:
                rid = kernel["request_id"]
                assert rid in by_id and rid not in completions
                assert by_id[rid]["route"] == node
                assert kernel["service_ns"] > 0
                assert kernel["finish_unix_ns"] >= kernel["start_unix_ns"] >= previous_finish
                previous_finish = kernel["finish_unix_ns"]
                assert kernel["result"]["bytes_processed"] == config["workload"]["bytes"] * config["workload"]["iterations"]
                assert not kernel.get("error")
                checksums.add(kernel["result"]["checksum"])
                completions[rid] = kernel
                if kernel["client_cancelled_at_kernel_finish"]:
                    cancelled_kernel_ids.append(rid)
        for rid, row in by_id.items():
            if int(row["status"]) == 200:
                assert rid in completions, f"success without actual kernel: {rid}"
                kernel = completions[rid]
                assert int(row["worker_service_ns"]) == kernel["service_ns"]
                assert int(row["worker_start_unix_ns"]) == kernel["start_unix_ns"]
                assert int(row["worker_finish_unix_ns"]) == kernel["finish_unix_ns"]
        lost_completions = [rid for rid in completions if int(by_id[rid]["status"]) != 200]
        recomputed = reaggregate(rows, events, end, qos)
        saved = load_json(directory / "run/summary.json")
        assert len(saved) == len(recomputed) == 3
        for actual, expected in zip(recomputed, saved):
            assert actual.keys() == expected.keys()
            for key, value in actual.items():
                if isinstance(value, float):
                    assert math.isclose(value, expected[key], rel_tol=1e-12, abs_tol=1e-12), (run_id, key)
                else:
                    assert value == expected[key], (run_id, key)
            run_phases.append(dict(run_id=run_id, seed=entry["seed"], policy="on" if config["local_eligible"] else "off", **actual))
        diagnostics.append(dict(run_id=run_id, offered_requests=len(rows), actual_kernel_completions=len(completions),
                                fault_target_ids=targeted, kernels_completed_without_client_success=lost_completions,
                                client_cancelled_at_kernel_finish_ids=cancelled_kernel_ids,
                                actual_submissions_at_or_after_window_end=sum(int(r["t_submit_ns"]) >= end for r in rows)))
    assert len(paired) == manifest["repetitions"]
    for seed, pairs in paired.items():
        assert len(pairs) == 2 and {p[0] for p in pairs} == {False, True}
        assert pairs[0][1:] == pairs[1][1:], f"paired inputs differ: {seed}"
    assert len(checksums) == 1, "fixed CPU kernel checksum differs across workers or runs"
    validation = dict(status="PASS", evidence_type="measured real HTTP scheduling adapter; not native Tide distributed recovery",
                      verified_runs=len(manifest["runs"]), verified_requests=len(all_ids), paired_traces_verified=len(paired),
                      source_artifact_and_frozen_binary_hashes_match=True, client_worker_identity_and_serial_execution_verified=True,
                      all_actual_workers_drained=True, half_open_phase_and_full_windows=True,
                      independently_reaggregated_cohort_and_completion_metrics=True, diagnostics=diagnostics,
                      script_sha256=digest(Path(__file__)))
    (root / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    return run_phases, validation


def summarize(run_phases):
    result = []
    metrics = ("window_ontime_goodput_rps", "cohort_ontime_yield", "arrivals", "local_routes", "remote_routes",
               "admission_rejected", "cohort_successes", "cohort_ontime", "cohort_failures",
               "window_ontime_completions", "cohort_successes_drained_after_full_window")
    for policy in ("on", "off"):
        for phase in PHASES:
            group = [r for r in run_phases if r["policy"] == policy and r["phase"] == phase]
            row = dict(policy=policy, phase=phase, runs=len(group))
            for metric in metrics:
                values = [r[metric] for r in group]
                row[metric + "_mean"] = statistics.mean(values)
                row[metric + "_min"] = min(values)
                row[metric + "_max"] = max(values)
            result.append(row)
    return result


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def render(root, out, run_phases, validation):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(run_phases)
    write_csv(out / "figB_runs.csv", run_phases)
    write_csv(out / "figB_summary.csv", summary)
    lookup = {(r["policy"], r["phase"]): r for r in summary}
    fig, axes = plt.subplots(2, 2, figsize=(11.4, 7.6), constrained_layout=True)
    for ax, metric, title, ylabel, scale in (
            (axes[0, 0], "window_ontime_goodput_rps", "(a) Completion-window deadline goodput", "On-time responses / second", 1),
            (axes[0, 1], "cohort_ontime_yield", "(b) Submission-cohort deadline yield", "On-time / all offered requests (%)", 100)):
        for policy, color, offset in (("on", "#236b99", -.04), ("off", "#bc642c", .04)):
            group = [lookup[policy, phase] for phase in PHASES]
            means = [r[metric + "_mean"] * scale for r in group]
            errors = [[(r[metric + "_mean"] - r[metric + "_min"]) * scale for r in group],
                      [(r[metric + "_max"] - r[metric + "_mean"]) * scale for r in group]]
            ax.errorbar([i + offset for i in range(3)], means, yerr=errors, marker="o", capsize=4,
                        color=color, label=f"Local eligibility {policy}")
            for i, phase in enumerate(PHASES):
                values = [r[metric] * scale for r in run_phases if r["policy"] == policy and r["phase"] == phase]
                ax.scatter([i + offset + (j - (len(values) - 1) / 2) * .025 for j in range(len(values))], values,
                           s=15, color=color, alpha=.55, zorder=3)
        ax.set_xticks(range(3), [p.capitalize() for p in PHASES])
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=.25)
        ax.set_ylim(bottom=0)
    axes[0, 0].legend(fontsize=8)
    axes[0, 1].set_ylim(0, 105)
    positions = [(policy, phase) for phase in PHASES for policy in ("on", "off")]
    xs = list(range(6))
    for ax, series, title in (
            (axes[1, 0], (("local_routes", "Local route", "#418e8a"), ("remote_routes", "Remote route", "#8a70af"),
                           ("admission_rejected", "Admission rejected", "#acacac")), "(c) Offered cohort by routing decision"),
            (axes[1, 1], (("cohort_ontime", "On time", "#418e8a"), ("late", "Late success", "#e2b655"),
                           ("cohort_failures", "All failures", "#bf665d")), "(d) Offered cohort by client outcome")):
        bottom = [0] * 6
        for metric, label, color in series:
            values = [(lookup[p, phase]["cohort_successes_mean"] - lookup[p, phase]["cohort_ontime_mean"]
                       if metric == "late" else lookup[p, phase][metric + "_mean"]) for p, phase in positions]
            ax.bar(xs, values, bottom=bottom, color=color, width=.75, label=label)
            bottom = [a + b for a, b in zip(bottom, values)]
        ax.set_xticks(xs, [f"{p.capitalize()}\n{phase.capitalize()}" for p, phase in positions], fontsize=8)
        ax.set_ylabel("Mean offered requests per phase")
        ax.set_title(title, fontsize=10)
        ax.legend(fontsize=8, loc="upper center", ncol=3)
        ax.set_ylim(0, max(bottom) * 1.22)
        ax.grid(axis="y", alpha=.2)
    fig.suptitle("Real HTTP scheduling adapter: scoped remote endpoint outage", fontsize=12)
    for extension in ("pdf", "png"):
        fig.savefig(out / f"figB_degradation.{extension}", dpi=200, bbox_inches="tight")
    plt.close(fig)
    lines = [r"\begin{tabular}{llrrr}", r"\hline", r"Local eligible & Phase & Goodput (req/s) & Yield (\%) & Failures \\", r"\hline"]
    for row in summary:
        def interval(metric, scale=1):
            return f"{row[metric + '_mean'] * scale:.2f} [{row[metric + '_min'] * scale:.2f}, {row[metric + '_max'] * scale:.2f}]"
        lines.append(f"{row['policy'].capitalize()} & {row['phase'].capitalize()} & {interval('window_ontime_goodput_rps')} & {interval('cohort_ontime_yield', 100)} & {row['cohort_failures_mean']:.1f} " + r"\\")
    lines.extend((r"\hline", r"\end{tabular}"))
    (out / "figB_table.tex").write_text("\n".join(lines) + "\n")
    reps = summary[0]["runs"]
    caption = (f"Measured real HTTP scheduling adapter with two serial fixed-work workers on one host, {reps} paired seed repetitions. "
               "The same offered trace and kernel are reused with local eligibility on/off. Connected, disconnected and recovered phases use actual gate transition times. "
               "Panel (a) counts on-time responses completed inside each half-open phase window and divides by its actual duration; (b) retains all requests submitted in that phase in the deadline-yield denominator, including failures and later drain. "
               "Points are runs, lines are arithmetic means, and whiskers are observed minima/maxima, not confidence intervals. Panels (c,d) show mean cohort route and outcome counts; failures include admission rejection, transport errors and timeouts. "
               "Each worker has a verified two-CPU affinity budget and concurrency one, not an exclusive CPU allocation or cgroup quota. "
               "The outage cancels only adapter-owned remote HTTP requests; running kernels can finish after the client loses its response and retain their serial slot until completion. There are no retries. "
               "This experiment does not exercise native Tide distributed recovery, establish exactly-once completion, or prove a universal C4 guarantee.\n")
    (out / "figB_caption.txt").write_text(caption)
    sources = {str(root / "manifest.json"): digest(root / "manifest.json"), str(root / "validation.json"): digest(root / "validation.json")}
    for name in load_json(root / "manifest.json")["artifact_sha256"]:
        sources[str(root / name)] = digest(root / name)
    generated = [out / f"figB_{name}" for name in ("degradation.pdf", "degradation.png", "summary.csv", "runs.csv", "table.tex", "caption.txt")]
    figure_manifest = dict(evidence_type="measured real HTTP scheduling adapter; not native Tide distributed recovery",
                           sources_sha256=sources, artifacts_sha256={str(p): digest(p) for p in generated},
                           script_sha256=digest(Path(__file__)), repetitions=reps,
                           uncertainty="observed seed minimum/maximum; no confidence interval", validation=validation["status"],
                           regenerate=f"python cmd/revision-fault/plot_results.py --fault {root} --out {out} --source-root .")
    (out / "figB_manifest.json").write_text(json.dumps(figure_manifest, indent=2) + "\n")
    print(caption)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    rows, validation = audit(args.fault, args.source_root)
    print(json.dumps({k: v for k, v in validation.items() if k != "diagnostics"}, indent=2))
    if not args.audit_only:
        render(args.fault, args.out, rows, validation)


if __name__ == "__main__":
    main()
