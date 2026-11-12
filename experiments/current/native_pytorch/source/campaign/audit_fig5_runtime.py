#!/usr/bin/env python3
"""Reject ablation runs that silently exceed the declared worker budget."""
import argparse
import json
from pathlib import Path


def audit(path, cpu_count=6):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    config = next(row for row in rows if row.get("msg") == "resptestv2 config")
    limit = cpu_count // 2 if config["EnableConcurrencyControl"] else cpu_count
    created = {row["ExecutorId"] for row in rows
               if row.get("msg") == "Containerxv2: start creating new container"}
    ready = {row["ExecutorId"] for row in rows
             if row.get("msg") == "StackPool: received one INIT event"}
    assert len(created) <= limit, f"{path}: {len(created)} containers created; cap={limit}"
    assert len(ready) == limit, f"{path}: only {len(ready)} ready workers; expected={limit}"
    assert any(row.get("msg") == "ablation application registered" for row in rows), f"{path}: missing app registration"
    return {"runtime": str(path), "created": len(created), "ready": len(ready), "cap": limit}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.runtime:
        print(json.dumps(audit(path)))
