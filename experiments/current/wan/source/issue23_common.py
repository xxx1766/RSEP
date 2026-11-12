"""Independent transport: never imports helpers whose output paths name old runs."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

SOURCE_ROOT=Path(__file__).resolve().parents[4]
CONTEXT=SOURCE_ROOT/'.issue22-context.json'
CANONICAL=Path(json.loads(CONTEXT.read_text())['canonical_rsep']) if CONTEXT.exists() else SOURCE_ROOT
OUT=CANONICAL/'results-issue23-vm-wan-20261031'
REMOTE=os.environ.get('RSEP_WAN_LEGACY_HOST_ROOT','/var/lib/rsep/issue23')
from issue29_config import GUEST_SUBNET, HOSTS, KNOWN_HOSTS_FILE, SSH_ALIASES, VM_KEY_ROOT


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv,timeout=120):
    start=time.time_ns()
    try:r=subprocess.run(argv,capture_output=True,timeout=timeout)
    except (subprocess.TimeoutExpired,KeyboardInterrupt) as error:
        row=dict(argv=list(map(str,argv)),started_unix_ns=start,ended_unix_ns=time.time_ns(),
                 exit_code=None,exception=type(error).__name__,timeout_s=timeout)
        for key in ('stdout','stderr'):
            body=getattr(error,key,None) or b''
            row[key]=body.decode(errors='backslashreplace') if isinstance(body,bytes) else str(body)
        with (OUT/'preflight/commands.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        raise
    row=dict(argv=list(map(str,argv)),started_unix_ns=start,ended_unix_ns=time.time_ns(),exit_code=r.returncode,
             stdout=r.stdout.decode(errors='backslashreplace'),stderr=r.stderr.decode(errors='backslashreplace'))
    with (OUT/'preflight/commands.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    if r.returncode:raise RuntimeError(f'command failed ({r.returncode}): {shlex.join(list(map(str,argv)))}\n'+row['stderr'][-2000:])
    return row['stdout'].strip()


def options(host):
    result=['-o','BatchMode=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2']
    if host=='NJ-k2':result+=['-o','UserKnownHostsFile='+str(KNOWN_HOSTS_FILE),'-o','StrictHostKeyChecking=yes']
    return result


def ssh(host,cmd,timeout=120):return command(['ssh',*options(host),SSH_ALIASES[host],cmd],timeout)
def py(host,code,timeout=120):return ssh(host,'python3 -c '+shlex.quote(code),timeout)
def copy(host,src,dst,pull=False):
    ends=[f'{SSH_ALIASES[host]}:{src}',str(dst)] if pull else [str(src),f'{SSH_ALIASES[host]}:{dst}']
    return command(['scp',*options(host),'-r',*ends],300)


def guest_transport(vm):
    role=vm.split('-')[1][0];host={'l':'NJ-k1','r':'NJ-k2','c':'NJ-k3','d':'NJ-k3'}[role]
    port=(22310 if role=='d' else 22300)+int(vm[-2:]);keyroot=str(VM_KEY_ROOT)+'/'
    number=(10 if role=='l' else 20)+int(vm[-2:]) if role in ('l','r') else 99 if role=='c' else 100
    proxy=shlex.join(['ssh',*options(host),'-W','%h:%p',SSH_ALIASES[host]])
    opts=['-i',keyroot+'guest','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=2',
          '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+keyroot+'known_hosts',
          '-o',f'HostKeyAlias=[{HOSTS[host]}]:{port}','-o','ProxyCommand='+proxy]
    return opts,'root@'+GUEST_SUBNET+'.'+str(number)


def guest(vm,cmd,timeout=120):
    opts,target=guest_transport(vm)
    return command(['ssh',*opts,target,cmd],timeout)


def guest_copy(vm,src,dst,pull=False):
    opts,target=guest_transport(vm)
    ends=[target+':'+str(src),str(dst)] if pull else [str(src),target+':'+str(dst)]
    return command(['scp',*opts,'-r',*ends],300)
