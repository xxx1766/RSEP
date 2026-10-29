#!/usr/bin/env python3
"""Sample one known experiment worker's host PID until stopped or bounded time."""

import argparse
import json
from pathlib import Path
import signal
import time


def sample(pid: int) -> dict:
    stat = Path(f"/proc/{pid}/stat").read_text()
    fields = stat[stat.rfind(")") + 2:].split()
    status = Path(f"/proc/{pid}/status").read_text().splitlines()
    rss = next(int(line.split()[1]) for line in status if line.startswith("VmRSS:"))
    return {"unix_ns": time.time_ns(), "pid": pid,
            "cpu_ticks": int(fields[11]) + int(fields[12]), "rss_kib": rss}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--seconds", type=int, default=360)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.pid < 2 or not 1 <= args.seconds <= 900:
        p.error("invalid PID/duration")
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    with args.out.open("x") as target:
        for _ in range(args.seconds):
            try:
                target.write(json.dumps(sample(args.pid)) + "\n")
                target.flush()
            except FileNotFoundError:
                break
            if stopping:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
