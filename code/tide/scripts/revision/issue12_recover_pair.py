#!/usr/bin/env python3
"""Finish an unrun Tide side after a complete K8s cell lost its SSH session."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from issue12_audit import audit_cell, audit_rows, jsonl, validate_payload_scope
from issue12_run_matrix import (CLIENT_SHA256, LOCAL, NODES, REMOTE, TIDE_SHA256,
                                run_tide, ssh)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", required=True)
    args = parser.parse_args()
    cell_id = args.cell
    plan = json.loads((LOCAL / "plan.json").read_text())
    cells = [cell for cell in plan["cells"] if cell["id"] == cell_id]
    if len(cells) != 1 or cells[0]["order"] != ["k8s", "tide"]:
        raise RuntimeError("frozen cell/order mismatch")
    cell = cells[0]
    folder = LOCAL / "raw" / cell_id
    if (folder / "audit.json").exists() or (folder / "tide-output").exists():
        raise RuntimeError("Tide side already started or audited; refuse replacement")
    raw_k8s = folder / "k8s-output"
    records = jsonl(raw_k8s / "requests.jsonl")
    summary = json.loads((raw_k8s / "run.json").read_text())
    validate_payload_scope(summary, records, 65536)
    checked = audit_rows(jsonl(LOCAL / "traces" / f"{cell_id}.jsonl"), records,
                         cell["window_ms"], plan["checksum"], plan["qos_ms"])
    if (summary["offered"], summary["success"], summary["on_time"]) != (
            checked["offered"], checked["success"], checked["on_time"]):
        raise RuntimeError("K8s summary does not match raw records")
    pods = json.loads(ssh("NJ-k1", "kubectl -n tmc-revision get pods tmc-issue12-nj2 tmc-issue12-nj3 -o json"))
    for pod in pods["items"]:
        node = pod["metadata"]["name"].removeprefix("tmc-issue12-")
        if node not in NODES or pod["metadata"]["uid"] != NODES[node][3] or pod["status"]["containerStatuses"][0]["restartCount"] != 0:
            raise RuntimeError("Pod identity/restart drift before recovery")
    if len(pods["items"]) != 2:
        raise RuntimeError("Pod count drift")
    for host in ("BJ-k1", "NJ-k2", "NJ-k3"):
        if ssh(host, f"sha256sum {REMOTE}/tide-test").split()[0] != TIDE_SHA256:
            raise RuntimeError(f"Tide binary drift on {host}")
    if ssh("BJ-k1", f"sha256sum {REMOTE}/issue12_k8s.py").split()[0] != CLIENT_SHA256:
        raise RuntimeError("K8s client drift")
    qdisc = ssh("BJ-k1", "ip netns exec t12-formal13 tc -s qdisc show dev n12formal13")
    if "delay 44ms" not in qdisc or "loss 0.1%" not in qdisc:
        raise RuntimeError("qdisc drift")
    (folder / "recovery-pods.json").write_text(json.dumps(pods, indent=2) + "\n")
    (folder / "k8s-post-qdisc-stats.txt").write_text(qdisc + "\n")
    estimated_end = (summary["start_unix_ns"] / 1e9) + summary["drain_duration_s"]
    (folder / "k8s-end-utc.txt").write_text(
        datetime.fromtimestamp(estimated_end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    gap_seconds = time.time() - estimated_end
    (folder / "recovery.json").write_text(json.dumps({
        "reason": "SSH session closed during or after K8s client execution; complete K8s application result recovered without rerun",
        "k8s_application_audit": checked,
        "k8s_end_time_derived_from_start_and_monotonic_drain": True,
        "k8s_qdisc_snapshot_delayed": True,
        "pair_gap_before_tide_seconds": gap_seconds,
        "recovery_started_utc": utc_now(),
    }, indent=2) + "\n")
    (folder / "tide-start-utc.txt").write_text(utc_now() + "\n")
    run_tide(cell, folder)
    (folder / "tide-end-utc.txt").write_text(utc_now() + "\n")
    (folder / "tide-post-qdisc-stats.txt").write_text(
        ssh("BJ-k1", "ip netns exec t12-formal13 tc -s qdisc show dev n12formal13") + "\n")
    result = audit_cell(LOCAL, cell, plan["checksum"], plan["qos_ms"])
    with (folder / "audit.json").open("x") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print(json.dumps({"recovered": cell_id, "gap_seconds": gap_seconds,
                      "on_time": {system: value["on_time"] for system, value in result["systems"].items()}}))


if __name__ == "__main__":
    main()
