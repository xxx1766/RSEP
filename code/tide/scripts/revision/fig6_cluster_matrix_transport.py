#!/usr/bin/env python3
"""Audited SSH transport and one-attempt remote wrapper for the Fig. 6 matrix."""
from contextlib import contextmanager
import json
from pathlib import Path
import select
import shlex
import subprocess
import time

REMOTE = "/root/tmc-fig6-cluster-20261014"
HOSTS = {"nj1": "NJ-k1", "nj2": "NJ-k2", "nj3": "NJ-k3"}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def ssh_options(node):
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if node == "nj2":
        options += ["-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=/private/tmp/tmc-fig6-known-hosts"]
    return options


def ssh_command(node, argv):
    return ["ssh", *ssh_options(node), HOSTS[node], shlex.join([str(x) for x in argv])]


def logged_command(root, label, argv, timeout=60):
    prefix = root / "formal" / "logs" / f"{label}-{time.time_ns()}"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    result = {"argv": argv, "started_ns": time.time_ns()}
    with Path(str(prefix) + ".stdout.log").open("wb") as out, Path(str(prefix) + ".stderr.log").open("wb") as err:
        process = subprocess.Popen(argv, stdout=out, stderr=err)
        result["local_pid"] = process.pid
        write_json(str(prefix) + ".command.json", result)
        try:
            result["exit_code"] = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            result["timed_out"] = True
            result["exit_code"] = process.wait(timeout=10)
        finally:
            result["finished_ns"] = time.time_ns()
            write_json(str(prefix) + ".command.json", result)
    if result.get("timed_out") or result["exit_code"]:
        raise RuntimeError("remote command failed; see " + str(prefix))
    return Path(str(prefix) + ".stdout.log").read_text()


@contextmanager
def remote_locks(root, campaign):
    """Live SSH sessions hold advisory campaign locks; EOF releases each lock."""
    holders = []
    code = ("import fcntl,json,os,pathlib,sys; "
            "p=pathlib.Path(sys.argv[1]); p.mkdir(parents=True,exist_ok=True); "
            "f=(p/'campaign.lock').open('a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); "
            "print(json.dumps({'pid':os.getpid(),'campaign':sys.argv[2],'locked':True}),flush=True); sys.stdin.buffer.read()")
    try:
        for node in HOSTS:
            log_path = root / "formal" / "logs" / f"lock-{node}-{time.time_ns()}.stderr.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            stream = log_path.open("wb")
            process = subprocess.Popen(ssh_command(node, ["python3", "-c", code, REMOTE + "/formal", campaign]),
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stream)
            holders.append((node, process, stream))
            readable, _, _ = select.select([process.stdout], [], [], 20)
            if not readable:
                raise RuntimeError("remote campaign lock timed out on " + node)
            line = process.stdout.readline()
            if not line or not json.loads(line).get("locked"):
                raise RuntimeError("remote campaign lock unavailable on " + node)
            write_json(root / "formal" / "logs" / f"lock-{node}-{time.time_ns()}.json",
                       {"node": node, "local_ssh_pid": process.pid, "remote": json.loads(line)})
        yield holders
    finally:
        for _, process, stream in reversed(holders):
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
            stream.close()


REMOTE_RUN = r'''
import fcntl,hashlib,json,os,pathlib,subprocess,sys,time
spec=json.loads(sys.argv[1]); prefix=pathlib.Path(spec['prefix']); state_path=pathlib.Path(str(prefix)+'.state.json')
prefix.parent.mkdir(parents=True,exist_ok=True)
lock=(prefix.parent/'load.lock').open('a'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
if state_path.exists(): raise RuntimeError('remote attempt already has state; never relaunch')
if hashlib.sha256(pathlib.Path(spec['trace_path']).read_bytes()).hexdigest()!=spec['trace_file_sha256']: raise RuntimeError('trace file hash differs')
for name,wanted in spec['source_sha256'].items():
    if hashlib.sha256((pathlib.Path(spec['root'])/name).read_bytes()).hexdigest()!=wanted: raise RuntimeError('remote source differs: '+name)
state={k:spec[k] for k in ('job','origin','manifest_sha256','command')}; state.update(status='launching',wrapper_pid=os.getpid(),launched_ns=time.time_ns())
def save():
    p=pathlib.Path(str(state_path)+'.partial');p.write_text(json.dumps(state,sort_keys=True));p.replace(state_path)
save()
process=subprocess.Popen(spec['command'])
state.update(status='running',driver_pid=process.pid)
try: state['driver_start_ticks']=(pathlib.Path('/proc')/str(process.pid)/'stat').read_text().rsplit(') ',1)[1].split()[19]
except FileNotFoundError: pass
save()
state.update(exit_code=process.wait(),status='completed',finished_ns=time.time_ns());save()
sys.exit(state['exit_code'])
'''


