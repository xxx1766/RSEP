#!/usr/bin/env python3
"""Freeze the paired native Tide issue-5 placement matrix."""

import argparse
import json
import random
from pathlib import Path


WEIGHTS = (
    ("equal", [1, 1, 1]),
    ("latency", [0, 0.9, 0.1]),
    ("neighbor", [0.1, 0.8, 0.1]),
)
NODES = {
    "nj3": {"node_id": "nj3", "address": "11.0.4.49:29521", "total_cpu": 2, "network_ms": 1},
    "nj2": {"node_id": "nj2", "address": "11.0.4.48:29520", "total_cpu": 4, "network_ms": 2},
    "bj1": {"node_id": "bj1", "address": "9.0.3.35:29522", "total_cpu": 4, "network_ms": 23},
}
EXECUTION_MS = {"nj3": 2, "nj2": 7, "bj1": 2}
AFFINITY = {"origin": "12,13", "nj3": "8,9", "nj2": "8-11", "bj1": "8-11"}
OUT_ROOT = "/tmp/tide-issue5-20261006"


def build(layout):
    if layout not in ("nj", "bj"):
        raise ValueError(layout)
    candidates = [NODES["nj3"], NODES["nj2" if layout == "nj" else "bj1"]]
    rng = random.Random(20261006)
    blocks = []
    for rate in (50, 700, 1000):
        for pattern_number, pattern in enumerate(("steady", "burst")):
            for repeat in range(1, 6):
                seed = 202610060000 + rate * 100 + pattern_number * 10 + repeat
                pair = []
                for name, weights in WEIGHTS:
                    pair.append({
                        "id": f"{pattern}-r{rate:04d}-rep{repeat}-{name}",
                        "rate": rate,
                        "pattern": pattern,
                        "repeat": repeat,
                        "seed": seed,
                        "duration_ms": 2000,
                        "payload_bytes": 65536,
                        "weight_name": name,
                        "weights": weights,
                    })
                rng.shuffle(pair)
                blocks.append(pair)
    rng.shuffle(blocks)
    runs = [run for block in blocks for run in block]
    workload = {"kind": "cpu", "bytes": 65536, "iterations": 30}
    origin = {
        "listen": "11.0.4.47:29519",
        "candidates": candidates,
        "workload": workload,
        "output_dir": f"{OUT_ROOT}/formal-{layout}-01",
        "qos_ms": 500,
        "execution_ms": 10,
        "node_execution_ms": {item["node_id"]: EXECUTION_MS[item["node_id"]] for item in candidates},
        "resource_path": f"{OUT_ROOT}/formal-{layout}-01/origin-resources.jsonl",
        "runs": runs,
    }
    workers = {}
    for item in candidates:
        node = item["node_id"]
        workers[node] = {
            "node_id": node,
            "listen": item["address"],
            "origin_addr": origin["listen"],
            "total_cpu": item["total_cpu"],
            "execution_ms": EXECUTION_MS[node],
            "qos_ms": 500,
            "workload": workload,
            "ready_path": f"{OUT_ROOT}/formal-{layout}-01-{node}-ready.json",
            "resource_path": f"{OUT_ROOT}/formal-{layout}-01-{node}-resources.jsonl",
        }
    return origin, workers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("layout", choices=("nj", "bj"))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    origin, workers = build(args.layout)
    args.destination.mkdir(parents=True, exist_ok=True)
    (args.destination / "origin.json").write_text(json.dumps(origin, indent=2) + "\n")
    for node, config in workers.items():
        (args.destination / f"{node}-worker.json").write_text(json.dumps(config, indent=2) + "\n")
    (args.destination / "affinity.json").write_text(json.dumps(AFFINITY, indent=2) + "\n")


if __name__ == "__main__":
    main()
