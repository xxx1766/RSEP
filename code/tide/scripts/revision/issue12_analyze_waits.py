#!/usr/bin/env python3
"""Correlate diagnostic request outcomes with native scheduler/admission logs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
from statistics import median


def analyze(root):
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["binary_sha256"] == "009f3deb29f3995853a2f1a9cba68ff457c0003f651f6bce29ea3ea3fb5d84fe"
    cleanup = json.loads((root / "cleanup.json").read_text())
    assert cleanup["namespace_absent"] and all(code == 0 for code in cleanup["child_returncodes"])
    result = {"binary_sha256": manifest["binary_sha256"], "cases": []}
    for name, delay, payload, count, gap in manifest["cases"]:
        folder = root / name
        rows = [json.loads(s) for s in (folder / "output" / name / "requests.jsonl").read_text().splitlines()]
        assert len(rows) == count
        assert [(r["id"], r["scheduled_offset_ns"]) for r in rows] == [(f"{name}-{i:04}", i * gap * 1000000) for i in range(count)]
        assert json.loads((folder / "exit.json").read_text())["origin"] == 0
        timeout = {r["id"] for r in rows if r["status"] == "no_receiver_timeout"}
        success = [r for r in rows if r["status"] == "success"]
        assert all(r["checksum"] == "1743019207182703820" for r in success)
        no_cloud, admission, finished_before_wait = {}, {}, set()
        for node in ("origin", "nj2", "nj3"):
            lines = (folder / (node + ".log")).read_text().splitlines()
            no_cloud[node] = set()
            exits = set()
            for line in lines:
                match = re.search(r"RoutineId=(\S+)", line)
                if not match:
                    continue
                rid = match.group(1)
                if "there is no cloud node" in line:
                    no_cloud[node].add(rid)
                if node != "origin" and "StubFn submit: self condition" in line:
                    fields = dict(re.findall(r"(SubmitTime|Timenow|endBefore|estWaitTime)=(\d+)", line))
                    admission[rid] = {"node": node,
                                      "age_ms": (int(fields["Timenow"]) - int(fields["SubmitTime"])) / 1e6,
                                      "queue_estimate_ms": int(fields["estWaitTime"]) / 1e6}
                if node != "origin" and "receive the routine exit event" in line:
                    exits.add(rid)
                if node != "origin" and "start waitting routine done" in line and rid in exits:
                    finished_before_wait.add(rid)
        at_origin = timeout & no_cloud["origin"]
        at_worker = timeout & (no_cloud["nj2"] | no_cloud["nj3"])
        assert not at_origin & at_worker
        unresolved = timeout - at_origin - at_worker
        case = {"name": name, "one_way_delay_ms": delay, "payload_bytes": payload,
                "offered": len(rows), "statuses": dict(Counter(r["status"] for r in rows)),
                "on_time": sum(r["on_time"] for r in rows),
                "timeout_origin_no_cloud": len(at_origin), "timeout_worker_no_cloud": len(at_worker),
                "timeout_unresolved": sorted(unresolved),
                "worker_timeout_admission": {rid: admission.get(rid) for rid in sorted(at_worker)},
                "success_already_finished_before_wait_request": len(finished_before_wait & {r["id"] for r in success})}
        if success:
            case["success_medians_ms"] = {
                "end_to_end": median(r["latency_ms"] for r in success),
                "before_receiver": median((r["receive_unix_ns"] - r["submit_unix_ns"]) / 1e6 for r in success),
                "after_receiver": median((r["reply_unix_ns"] - r["receive_unix_ns"]) / 1e6 for r in success)}
        result["cases"].append(case)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.root), indent=2))
