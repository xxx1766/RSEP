"""Tide ablation workload entrypoint dispatcher.

Chooses between --mode={http,cpuburn,paix} based on the flag or TIDE_MODE env.

If WORKLOAD_CPUSET is set (e.g. "2-7" or "2,3,4,5,6,7"), pin the process to
those logical cores via sched_setaffinity. This is how the EI=off HTTP
baseline (cmd/httpbase) restricts its backend container to the paper's 6-core
slice without needing a per-container cgroup. Threads/forks inherit affinity.
"""
from __future__ import annotations

import argparse
import os
import sys


def _parse_cpuset(spec: str) -> set[int]:
    cpus: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            cpus.update(range(int(lo), int(hi) + 1))
        else:
            cpus.add(int(part))
    return cpus


def _apply_cpuset() -> None:
    spec = os.environ.get("WORKLOAD_CPUSET", "").strip()
    if not spec:
        return
    cpus = _parse_cpuset(spec)
    if not cpus:
        return
    try:
        os.sched_setaffinity(0, cpus)
        print(f"[entrypoint] pinned to cpus={sorted(cpus)} (WORKLOAD_CPUSET={spec})",
              file=sys.stderr, flush=True)
    except OSError as e:
        print(f"[entrypoint] sched_setaffinity({sorted(cpus)}) failed: {e}",
              file=sys.stderr, flush=True)


def main() -> int:
    _apply_cpuset()
    # Paper-matched workload weight: 10 detection tasks per request (RSEP
    # §VI.A). Honored by http_worker.py and paix_worker.py via the same env
    # name. Keeping it as setdefault allows callers to override (e.g. =1 for
    # light-workload sensitivity comparison).
    os.environ.setdefault("TIDE_DETECTIONS_PER_REQ", "10")

    parser = argparse.ArgumentParser(prog="tide-workload")
    parser.add_argument(
        "--mode",
        choices=["http", "cpuburn", "paix"],
        default=os.environ.get("WORKLOAD_MODE", "http"),
        help="Worker mode. Env WORKLOAD_MODE overrides default "
             "(NOT TIDE_MODE — Tide uses that for entry|fn).",
    )
    args, passthrough = parser.parse_known_args()
    sys.argv = [sys.argv[0], *passthrough]  # let sub-modules see remaining args

    if args.mode == "http":
        from http_worker import main as run
    elif args.mode == "cpuburn":
        from http_worker import main as run  # same server, burn mode set via env
        os.environ["TIDE_WORKLOAD_FORCE_CPUBURN"] = "1"
    elif args.mode == "paix":
        from paix_worker import main as run
    else:
        print(f"unknown mode: {args.mode}", file=sys.stderr)
        return 2

    return run() or 0


if __name__ == "__main__":
    sys.exit(main())
