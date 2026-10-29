#!/usr/bin/env python3
"""Independently verify restricted-model certificates and tiny saved optima."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path


def verify(root, output):
    rows = json.loads((output / "results.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    for path, expected in manifest["source_sha256"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == expected, path
    checked = 0
    for row in rows:
        instance = row["instance"]
        tasks = instance["tasks_in_arrival_order"]
        nodes = instance["nodes"]
        deadline = instance["common_deadline_ticks"]

        def feasible(assignment):
            loads = [0] * len(nodes)
            for i, node in enumerate(assignment):
                if node >= 0:
                    loads[node] += tasks[i]["service_ticks_by_node"][node]
            return all(load == 0 or load + nodes[j]["network_ticks"] <= deadline
                       for j, load in enumerate(loads))

        for method in ("heuristic", "exact"):
            solution = row[method]
            assignment = solution["node_index_by_task_minus_one_rejected"]
            assert len(assignment) == len(tasks)
            assert all(-1 <= node < len(nodes) for node in assignment)
            assert sum(node >= 0 for node in assignment) == solution["accepted"]
            assert feasible(assignment)
        assert row["exact"]["proven_optimal"]
        assert row["objective_gap"] == row["exact"]["accepted"] - row["heuristic"]["accepted"] >= 0
        expected_ratio = row["heuristic"]["accepted"] / row["exact"]["accepted"] if row["exact"]["accepted"] else 1
        assert abs(row["heuristic_over_optimum"] - expected_ratio) < 1e-12
        if len(tasks) <= 6:
            best = 0
            for assignment in itertools.product(range(-1, len(nodes)), repeat=len(tasks)):
                count = sum(node >= 0 for node in assignment)
                if count > best and feasible(assignment):
                    best = count
            assert best == row["exact"]["accepted"], instance["id"]
            checked += 1
    result = {
        "assignment_certificates_checked": len(rows) * 2,
        "independent_python_full_enumeration_instances": checked,
        "enumeration_max_task_count": 6,
        "source_hashes_match": True,
        "negative_objective_gaps": 0,
        "zero_optimum_instances": sum(row["exact"]["accepted"] == 0 for row in rows),
        "validator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "note": "Enumeration traverses every assignment without solver bound or task reordering. Go tests separately cross-check 150 tiny random instances."
    }
    (output / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--results", type=Path, default=Path("results-revision-20260928/model-01"))
    args = parser.parse_args()
    verify(args.source_root, args.results)
