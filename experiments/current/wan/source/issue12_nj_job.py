#!/usr/bin/env python3
"""Run one frozen experiment command detached from SSH; never rerun a job."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def write(path, row):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(row, indent=2)+'\n')
    temporary.replace(path)


def identity(pid):
    try:
        parts = Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return parts[19], parts[0]
    except FileNotFoundError:
        return None, None


def run_job(folder):
    with (folder/'started.lock').open('x') as marker:
        marker.write(str(time.time_ns()))
    spec = json.loads((folder/'spec.json').read_text())
    with (folder/'process.log').open('xb') as output:
        started = time.time_ns()
        try:
            child = subprocess.Popen(spec['argv'], env=dict(os.environ, **spec.get('env',{})),
                                     stdout=output, stderr=subprocess.STDOUT,
                                     cwd=spec.get('cwd'), start_new_session=True)
        except Exception as error:
            write(folder/'exit.json', dict(exit_code=127, started_unix_ns=started,
                                           ended_unix_ns=time.time_ns(), launch_error=str(error)))
            return
        ticks, _ = identity(child.pid)
        state = dict(pid=child.pid, wrapper_pid=os.getpid(), start_ticks=ticks,
                     started_unix_ns=started, spec=spec)
        write(folder/'state.json', state)
        result = child.wait()
    write(folder/'exit.json', dict(exit_code=result, started_unix_ns=started,
                                   ended_unix_ns=time.time_ns(), pid=child.pid))


def job_status(folder):
    if (folder/'exit.json').exists():
        return dict(phase='finished', **json.loads((folder/'exit.json').read_text()))
    if not (folder/'state.json').exists():
        return dict(phase='starting' if (folder/'started.lock').exists() else 'absent')
    state = json.loads((folder/'state.json').read_text())
    ticks, status = identity(state['pid'])
    return dict(phase='running' if ticks == state['start_ticks'] and status != 'Z' and ticks is not None
                else 'missing_process', **state)


def stop_job(folder):
    if (folder/'exit.json').exists():
        return job_status(folder)
    state = json.loads((folder/'state.json').read_text())
    ticks, _ = identity(state['pid'])
    if ticks is None or ticks != state['start_ticks']:
        raise RuntimeError('refusing to signal absent or reused PID')
    os.kill(state['pid'], signal.SIGTERM)
    return dict(stop_requested=True, pid=state['pid'])


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('run','status','stop'))
    p.add_argument('folder', type=Path)
    a=p.parse_args()
    if a.action=='run':
        run_job(a.folder)
    else:
        print(json.dumps(job_status(a.folder) if a.action=='status' else stop_job(a.folder)))
