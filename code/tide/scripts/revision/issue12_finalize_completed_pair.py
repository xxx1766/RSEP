#!/usr/bin/env python3
"""Finalize a complete Tide-first pair whose K8s SSH transport failed after output."""

import argparse
from datetime import datetime, timezone
import json

from issue12_audit import audit_cell, audit_rows, jsonl, validate_payload_scope
from issue12_run_matrix import (CHECKSUM, CLIENT_SHA256, LOCAL, NODES, REMOTE,
                                TIDE_SHA256, ssh)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", required=True)
    args = parser.parse_args()
    plan = json.loads((LOCAL / "plan.json").read_text())
    matches = [cell for cell in plan["cells"] if cell["id"] == args.cell]
    if len(matches) != 1 or matches[0]["order"] != ["tide", "k8s"]:
        raise RuntimeError("frozen Tide-first cell/order mismatch")
    cell = matches[0]
    folder = LOCAL / "raw" / args.cell
    if (folder / "audit.json").exists() or (folder / "recovery.json").exists():
        raise RuntimeError("cell already finalized or recovered")
    if not (folder / "tide-end-utc.txt").exists():
        raise RuntimeError("Tide side not completed")
    output = folder / "k8s-output"
    summary = json.loads((output / "run.json").read_text())
    records = jsonl(output / "requests.jsonl")
    validate_payload_scope(summary, records, 65536)
    checked = audit_rows(jsonl(LOCAL / "traces" / f"{args.cell}.jsonl"), records,
                         cell["window_ms"], CHECKSUM, 135)
    if (summary["offered"], summary["success"], summary["on_time"]) != (
            checked["offered"], checked["success"], checked["on_time"]):
        raise RuntimeError("K8s summary does not match raw records")
    pods = json.loads(ssh("NJ-k1", "kubectl -n tmc-revision get pods tmc-issue12-nj2 tmc-issue12-nj3 -o json"))
    if len(pods["items"]) != 2:
        raise RuntimeError("experiment Pod count drift")
    for pod in pods["items"]:
        node = pod["metadata"]["name"].removeprefix("tmc-issue12-")
        if node not in NODES or pod["metadata"]["uid"] != NODES[node][3] or pod["status"]["containerStatuses"][0]["restartCount"] != 0:
            raise RuntimeError("Pod identity/restart drift")
    for host in ("BJ-k1", "NJ-k2", "NJ-k3"):
        if ssh(host, f"sha256sum {REMOTE}/tide-test").split()[0] != TIDE_SHA256:
            raise RuntimeError(f"Tide binary drift on {host}")
    if ssh("BJ-k1", f"sha256sum {REMOTE}/issue12_k8s.py").split()[0] != CLIENT_SHA256:
        raise RuntimeError("K8s client drift")
    qdisc = ssh("BJ-k1", "ip netns exec t12-formal13 tc -s qdisc show dev n12formal13")
    if "delay 44ms" not in qdisc or "loss 0.1%" not in qdisc:
        raise RuntimeError("qdisc drift")
    app_end = summary["start_unix_ns"] / 1e9 + summary["drain_duration_s"]
    (folder / "recovery-pods.json").write_text(json.dumps(pods, indent=2) + "\n")
    (folder / "k8s-post-qdisc-stats.txt").write_text(qdisc + "\n")
    (folder / "k8s-end-utc.txt").write_text(
        datetime.fromtimestamp(app_end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    (folder / "recovery.json").write_text(json.dumps({
        "reason": "BJ SSH transport stopped responding after the complete Kubernetes application output was written",
        "k8s_application_audit": checked,
        "k8s_end_time_derived_from_start_and_monotonic_drain": True,
        "k8s_qdisc_snapshot_delayed": True,
        "qdisc_snapshot_delay_seconds": datetime.now(timezone.utc).timestamp() - app_end,
    }, indent=2) + "\n")
    result = audit_cell(LOCAL, cell, CHECKSUM, 135)
    with (folder / "audit.json").open("x") as target:
        json.dump(result, target, indent=2)
        target.write("\n")
    print(json.dumps({"finalized": args.cell,
                      "on_time": {system: value["on_time"] for system, value in result["systems"].items()}}))


if __name__ == "__main__":
    main()
