"""Read-only five-second pressure, CPU, disk and owned QEMU-thread samples."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading
import time

def read(f):
 try:return Path(f).read_text()
 except OSError as error:return 'UNAVAILABLE:'+str(error)

def main(a):
 stop=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stop.set());until=time.monotonic()+a.seconds
 with a.out.open('x',buffering=1) as f:
  while not stop.is_set() and time.monotonic()<until:
   row=dict(unix_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),files={name:read('/proc/'+name) for name in ['stat','softirqs','meminfo','loadavg','diskstats','vmstat','net/dev','pressure/cpu','pressure/memory','pressure/io']},qemu=[])
   for pid in a.pids:
    root=Path('/proc')/str(pid)
    if not root.exists():row['qemu'].append(dict(pid=pid,absent=True));continue
    command=(root/'cmdline').read_bytes().decode(errors='replace')
    if a.owned not in command or 'qemu-system-x86_64' not in command:raise RuntimeError('owned process identity changed')
    threads=[]
    for task in (root/'task').iterdir():
     try:threads.append(dict(tid=task.name,affinity=sorted(os.sched_getaffinity(int(task.name))),files={name:read(task/name) for name in ['comm','stat','schedstat','status','wchan']}))
     except ProcessLookupError:continue
    row['qemu'].append(dict(pid=pid,cmdline=command.replace('\x00',' '),io=read(root/'io'),threads=threads))
   f.write(json.dumps(row)+'\n');stop.wait(5)
  f.write(json.dumps(dict(kind='complete',unix_ns=time.time_ns(),interrupted=stop.is_set()))+'\n')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=2100);p.add_argument('--pids',nargs='+',type=int,required=True);p.add_argument('--owned',required=True);p.add_argument('--out',type=Path,required=True);main(p.parse_args())
