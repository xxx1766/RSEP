#!/usr/bin/env python3
"""Audit isolated egress qdisc counter changes for each formal pair."""

import argparse
import json
from pathlib import Path
import re


COUNTERS = re.compile(r"Sent (\d+) bytes (\d+) pkt \(dropped (\d+),")


def parse_qdisc(text: str) -> dict:
    if "netem" not in text or "delay 44ms" not in text or "loss 0.1%" not in text:
        raise ValueError("unexpected qdisc setting")
    match = COUNTERS.search(text)
    if not match:
        raise ValueError("qdisc counters missing")
    return dict(zip(("bytes", "packets", "dropped"), map(int, match.groups())))


def deltas(before: dict, first: dict, second: dict, order: list[str]) -> dict:
    if sorted(order) != ["k8s", "tide"]:
        raise ValueError("expected exactly one Tide and one K8s system")
    output = {}
    for system, old, new in ((order[0], before, first), (order[1], first, second)):
        row = {key: new[key] - old[key] for key in ("bytes", "packets", "dropped")}
        if min(row.values()) < 0:
            raise ValueError("qdisc counters reset during formal cell")
        output[system] = row
    return output


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    args = p.parse_args()
    plan = json.loads((args.base / "plan.json").read_text())
    output = {}
    for cell in plan["cells"]:
        folder = args.base / "raw" / cell["id"]
        snapshots = [parse_qdisc((folder / "preflight-qdisc-stats.txt").read_text())]
        snapshots.extend(parse_qdisc((folder / f"{system}-post-qdisc-stats.txt").read_text())
                         for system in cell["order"])
        if (folder / "recovery.json").exists():
            recovery = json.loads((folder / "recovery.json").read_text())
            if recovery.get("k8s_qdisc_snapshot_delayed"):
                output[cell["id"]] = {"excluded": True,
                                      "reason": "K8s qdisc snapshot was delayed after an SSH session closed"}
                continue
        output[cell["id"]] = deltas(*snapshots, cell["order"])
    with (args.base / "qdisc-deltas.json").open("x") as target:
        json.dump(output, target, indent=2)
        target.write("\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
