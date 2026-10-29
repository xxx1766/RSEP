#!/usr/bin/env python3
"""Join native JSON event timestamps to request records on one Linux host."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]


def analyze(root):
    manifest = json.loads((root / "manifest.json").read_text())
    cases = []
    for name, delay, payload, count, gap in manifest["cases"]:
        folder = root / name
        rows = read_jsonl(folder / "output" / name / "requests.jsonl")
        assert [(r["id"], r["scheduled_offset_ns"]) for r in rows] == [(f"{name}-{i:04}", i * gap * 1000000) for i in range(count)]
        events = defaultdict(list)
        for node in ("origin", "nj2", "nj3"):
            for event in read_jsonl(folder / (node + ".log")):
                if "RoutineId" in event:
                    events[event["RoutineId"]].append(dict(event, node=node))
        details = []
        for row in rows:
            ident = row["id"]
            evts = sorted(events[ident], key=lambda e: e["trace_unix_ns"])
            def find(message, origin=False):
                return next((e for e in evts if e["msg"] == message and (e["node"] == "origin") == origin), None)
            stages = {"client_submit": row["submit_unix_ns"], "client_finish": row["reply_unix_ns"]}
            for key, message, origin in [
                ("origin_schedule", "Sched: start scheduling this routine", True),
                ("origin_send_begin", "Sched: start submitting routine to stub", True),
                ("origin_send_end", "Sched: finish submitting routine to stub", True),
                ("worker_submit", "Stub: receive a routine submitting", False),
                ("worker_admission", "StubFn submit: self condition", False),
                ("worker_execute", "Stub: notice executor to execute", False),
                ("worker_exit", "Stub: receive the routine exit event", False),
                ("worker_wait_request", "StubHdlr: start waitting routine done", False),
                ("worker_result_send", "StubHdlr: start sending RoutineDoneEvent", False),
                ("origin_result_event", "StubHdlr: receive a RoutineDoneEvent", True),
                ("origin_no_cloud", "there is no cloud node", True),
                ("worker_no_cloud", "there is no cloud node", False),
            ]:
                event = find(message, origin)
                if event:
                    stages[key] = event["trace_unix_ns"]
            if row.get("receive_unix_ns"):
                stages["client_receive_ack"] = row["receive_unix_ns"]
            if row["status"] == "success":
                assert row["checksum"] == "1743019207182703820"
                assert all(k in stages for k in ("worker_execute", "worker_exit", "worker_wait_request", "origin_result_event"))
                classification = "executed_and_returned"
            elif "origin_no_cloud" in stages and "worker_submit" not in stages:
                classification = "origin_no_placement_no_terminal_reply"
            elif "worker_no_cloud" in stages and "worker_execute" not in stages:
                admission = find("StubFn submit: self condition")
                assert admission["Timenow"] + admission["estWaitTime"] + 2000000 > admission["endBefore"]
                classification = "worker_deadline_rejection_no_terminal_reply"
            else:
                classification = "unresolved"
            intervals = {}
            for key, start, end in [
                ("submit_to_send_ms", "client_submit", "origin_send_begin"),
                ("send_to_worker_ms", "origin_send_begin", "worker_submit"),
                ("worker_to_execute_ms", "worker_submit", "worker_execute"),
                ("execute_to_exit_ms", "worker_execute", "worker_exit"),
                ("exit_to_wait_request_ms", "worker_exit", "worker_wait_request"),
                ("wait_request_to_client_finish_ms", "worker_wait_request", "client_finish"),
                ("ack_to_finish_ms", "client_receive_ack", "client_finish"),
                ("origin_failure_to_timeout_ms", "origin_no_cloud", "client_finish"),
                ("worker_failure_to_timeout_ms", "worker_no_cloud", "client_finish"),
            ]:
                if start in stages and end in stages:
                    intervals[key] = (stages[end] - stages[start]) / 1e6
            details.append({"id": ident, "status": row["status"], "classification": classification,
                            "on_time": row["on_time"], "stages_unix_ns": stages, "intervals_ms": intervals})
        classes = Counter(d["classification"] for d in details)
        stats = {}
        for classification in classes:
            interval_values = defaultdict(list)
            for detail in details:
                if detail["classification"] == classification:
                    for key, value in detail["intervals_ms"].items():
                        interval_values[key].append(value)
            stats[classification] = {k: {"n": len(v), "median": median(v), "min": min(v), "max": max(v)} for k, v in interval_values.items()}
        cases.append({"name": name, "count": count, "classification": dict(classes), "interval_statistics_ms": stats, "requests": details})
    cleanup = json.loads((root / "cleanup.json").read_text())
    assert cleanup["namespace_absent"] and all(code == 0 for code in cleanup["child_returncodes"])
    return {"binary_sha256": manifest["binary_sha256"], "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root), indent=2))
