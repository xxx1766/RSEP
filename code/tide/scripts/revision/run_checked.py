#!/usr/bin/env python3
"""Run one isolated experiment, retaining exit status and Linux resource samples.

Samples are observations, not synthetic load or inferred resource reservations.
The tide parent cgroup must be exclusive to the experiment for its CPU counters
to be attributable; the manifest records this assumption rather than hiding it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import time


def sample(pid, workload_pid=None):
    out = {"unix_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        out.update(process_cpu_ticks=int(fields[11]) + int(fields[12]),
                   process_rss_bytes=int(fields[21]) * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        pass
    if workload_pid is not None:
        try:
            fields = Path(f"/proc/{workload_pid}/stat").read_text().rsplit(")", 1)[1].split()
            out.update(workload_cpu_ticks=int(fields[11]) + int(fields[12]),
                       workload_rss_bytes=int(fields[21]) * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            pass
    try:
        stat = dict(line.split() for line in Path("/sys/fs/cgroup/tide/cpu.stat").read_text().splitlines())
        out["tide_parent_cpu_usage_usec"] = int(stat["usage_usec"])
        out["tide_parent_memory_bytes"] = int(Path("/sys/fs/cgroup/tide/memory.current").read_text())
    except (OSError, ValueError, KeyError):
        pass
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--workload-pid", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout <= 0:
        parser.error("command and positive timeout required")
    args.out.mkdir(parents=True, exist_ok=False)
    binary = Path(command[0]).resolve()
    manifest = dict(status="running", command=command, cwd=str(Path.cwd()),
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    host=platform.node(), kernel=platform.release(), machine=platform.machine(),
                    allowed_cpus=sorted(os.sched_getaffinity(0)),
                    clock_ticks_per_second=os.sysconf("SC_CLK_TCK"),
                    sample_interval_s=.1, started_unix_ns=time.time_ns(),
                    cgroup_scope="/sys/fs/cgroup/tide (requires exclusive experiment use)")
    manifest["workload_pid"] = args.workload_pid
    path = args.out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    timed_out = False
    with (args.out / "console.log").open("w") as log, (args.out / "samples.jsonl").open("w") as samples:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        manifest["pid"] = proc.pid
        start = time.monotonic()
        while proc.poll() is None:
            samples.write(json.dumps(sample(proc.pid, args.workload_pid)) + "\n")
            samples.flush()
            if time.monotonic() - start > args.timeout:
                timed_out = True
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                break
            time.sleep(.1)
        manifest.update(exit_code=proc.wait(), timed_out=timed_out,
                        finished_unix_ns=time.time_ns())
        samples.write(json.dumps(sample(proc.pid, args.workload_pid)) + "\n")
    manifest["status"] = "completed" if manifest["exit_code"] == 0 and not timed_out else "failed"
    manifest["files_sha256"] = {str(p.relative_to(args.out)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in args.out.rglob("*") if p != path and p.is_file()}
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
