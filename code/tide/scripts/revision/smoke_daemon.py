#!/usr/bin/env python3
"""Bounded real-daemon smoke; records readiness, process samples, and cleanup."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('--binary', required=True)
p.add_argument('--out', required=True)
p.add_argument('--port', type=int, default=19128)
p.add_argument('--cpus', type=int, default=2)
p.add_argument('--seconds', type=float, default=5)
a = p.parse_args()
out = Path(a.out)
out.mkdir(parents=True, exist_ok=False)
before = {str(x) for x in Path('/sys/fs/cgroup/tide').glob('*') if x.is_dir()}
cmd = [str(Path(a.binary).resolve()), 'serve', '--name', 'revision-smoke',
       '--outbound-ip', '127.0.0.1', '--port', str(a.port), '--role', 'edge',
       '--cpu-num', str(a.cpus), '--log-path', str(out / 'runtime.jsonl'), '--log-level', 'info']
samples = []
ready = False
started = time.monotonic()
with (out / 'console.log').open('w') as log:
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    try:
        while time.monotonic() - started < a.seconds and proc.poll() is None:
            try:
                with socket.create_connection(('127.0.0.1', a.port), timeout=.2):
                    ready = True
            except OSError:
                pass
            try:
                samples.append({'elapsed_s': time.monotonic() - started,
                                'stat': Path(f'/proc/{proc.pid}/stat').read_text(),
                                'status': Path(f'/proc/{proc.pid}/status').read_text()})
            except FileNotFoundError:
                break
            time.sleep(.2)
        alive = proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            rc = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
after = {str(x) for x in Path('/sys/fs/cgroup/tide').glob('*') if x.is_dir()}
result = {'command': cmd, 'ready': ready, 'alive_at_end': alive,
          'exit_code': rc, 'samples': samples,
          'clock_ticks_per_second': os.sysconf('SC_CLK_TCK'),
          'remaining_new_cgroups': sorted(after - before)}
(out / 'manifest.json').write_text(json.dumps(result, indent=2))
print(json.dumps({k: v for k, v in result.items() if k != 'samples'}))
raise SystemExit(0 if ready and alive and rc == 0 and not after-before else 1)
