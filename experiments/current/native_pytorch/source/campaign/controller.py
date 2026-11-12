"""Detached launch, identity-aware observation, and terminal journal for Fig7."""
import argparse
import errno
import json
import os
from pathlib import Path
import resource
import subprocess
import time
from protocol import save
from runtime_config import BASE, PYTHON


def identity(pid):
    try:raw=Path(f'/proc/{pid}/stat').read_text()
    except OSError as exc:
        if exc.errno in (errno.ENOENT,errno.ESRCH,errno.ENODEV):return None
        raise
    fields=raw.rsplit(') ',1)[1].split()
    return dict(pid=pid,state=fields[0],start_ticks=fields[19])


def phase_gate(phase):
    previous={'pilot':'smoke','formal':'pilot'}[phase]
    terminal=json.loads((BASE/'evidence'/f'{previous}-terminal.json').read_text())
    acceptance=json.loads((BASE/'evidence'/f'{previous}-acceptance.json').read_text())
    assert terminal['returncode']==0 and acceptance['status']=='PASS',('required integrity gate',previous)


def supervise(phase):
    code=1
    try:
        resource.setrlimit(resource.RLIMIT_MSGQUEUE,(134217728,134217728))
        env=dict(os.environ,PATH=os.environ.get('PATH','/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'),LANG='C.UTF-8',PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1')
        child=subprocess.Popen([PYTHON,str(BASE/'run.py'),phase],env=env)
        save(BASE/'evidence'/f'{phase}-process.json',dict(controller=identity(os.getpid()),child=identity(child.pid),started_ns=time.time_ns()))
        code=child.wait()
    finally:
        save(BASE/'evidence'/f'{phase}-terminal.json',dict(status='completed' if code==0 else 'failed',returncode=code,finished_ns=time.time_ns()))
    raise SystemExit(code)


def launch(phase):
    (BASE/'evidence').mkdir(exist_ok=True)
    assert not (BASE/phase).exists(),'existing phase retained'
    if phase in ('pilot','formal'):phase_gate(phase)
    reservation=BASE/'evidence'/f'{phase}-launch.json'
    with reservation.open('x') as stream:stream.write(json.dumps(dict(phase=phase,status='reserved',started_ns=time.time_ns()))+'\n')
    with (BASE/'evidence'/f'{phase}-console.log').open('x') as log:
        proc=subprocess.Popen([PYTHON,str(Path(__file__).resolve()),'supervise',phase],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    out=dict(phase=phase,controller=identity(proc.pid),started_ns=time.time_ns())
    save(reservation,out);print(json.dumps(out))


def status(phase):
    result={}
    for key,path in [('launch',BASE/'evidence'/f'{phase}-launch.json'),('process',BASE/'evidence'/f'{phase}-process.json'),('progress',BASE/phase/'progress.json'),('terminal',BASE/'evidence'/f'{phase}-terminal.json')]:
        if path.exists():result[key]=json.loads(path.read_text())
    for role,old in result.get('process',{}).items():
        if role in ('controller','child') and old:
            now=identity(old['pid']);result[role+'_alive']=bool(now and now['state']!='Z' and now['start_ticks']==old['start_ticks'])
    print(json.dumps(result))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('launch','supervise','status'));parser.add_argument('phase',choices=('smoke','pilot','formal'))
    args=parser.parse_args();globals()[args.action](args.phase)
