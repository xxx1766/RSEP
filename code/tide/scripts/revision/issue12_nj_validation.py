#!/usr/bin/env python3
"""Run the declared extra pair only after the original matrix has finished."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import time

import issue12_nj_matrix as matrix


def prepare():
    original = matrix.BASE / "formal01"
    target = matrix.BASE / "validation01"
    if (target / "plan.json").exists():
        return target
    addendum = json.loads((matrix.BASE / "recovery-addendum.json").read_text())
    declaration, = addendum["additional_validation"]
    if declaration["family"] != "validation01":
        raise RuntimeError("unexpected validation declaration")
    plan = json.loads((original / "plan.json").read_text())
    cell = {key: declaration[key] for key in ("pattern", "repeat", "window_ms", "order")}
    cell["id"] = declaration["cell"]
    plan["cells"] = [cell]
    plan["parent_plan_sha256"] = hashlib.sha256((original / "plan.json").read_bytes()).hexdigest()
    plan["addendum_sha256"] = hashlib.sha256((matrix.BASE / "recovery-addendum.json").read_bytes()).hexdigest()
    plan["validation_controller_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    plan["source_sha256"]["issue12_nj_matrix.py"] = hashlib.sha256(Path(matrix.__file__).read_bytes()).hexdigest()
    rows, window = matrix.generate(cell["pattern"], 1, cell["repeat"])
    if window != cell["window_ms"]:
        raise RuntimeError("declared window differs from generated trace")
    for row in rows:
        row["id"] = "nj-image-" + row["id"]
    target.mkdir(exist_ok=False)
    (target / "traces").mkdir()
    (target / "raw" / cell["id"]).mkdir(parents=True)
    (target / "traces" / (cell["id"] + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
    matrix.save(target / "plan.json", plan)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    target = prepare()
    if args.prepare_only:
        print(target / "plan.json")
        return
    original = matrix.BASE / "formal01"
    with (original / "controller.flock").open("a") as guard:
        while True:
            try:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                print("Waiting for the original matrix controller to release its lock", flush=True)
                time.sleep(30)
        progress = json.loads((original / "progress.json").read_text())
        completed = json.loads((original / "completed-pairs.json").read_text())
        original_plan = json.loads((original / "plan.json").read_text())
        expected = {cell["id"] for cell in original_plan["cells"]}
        if (progress["phase"] != "matrix_complete" or len(completed) != len(expected) or
                {row["cell"]["id"] for row in completed} != expected):
            raise RuntimeError("original matrix did not finish; validation must not overlap or bypass it")
        plan = json.loads((target / "plan.json").read_text())
        matrix.save(target / "preflight.json", matrix.preflight(plan))
        matrix.LOCAL = target
        matrix.REMOTE = matrix.ROOT + "/validation01"
        for host in matrix.HOSTS.values():
            matrix.ssh(host, f"mkdir -p {matrix.REMOTE}")
            matrix.copy(host, target / "traces", matrix.REMOTE + "/", pull=False)
        sys.argv = [matrix.__file__, "run"]
        matrix.main()


if __name__ == "__main__":
    main()
