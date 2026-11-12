"""Bounded management I/O. Writes only issue30 artifacts; no scored retry."""
import hashlib,json,shlex,subprocess,time
from pathlib import Path
from issue30_config import OUT, REMOTE, OLD, HOSTS, SSH_ALIASES, NODES, KNOWN_HOSTS_FILE
IMAGE='docker.io/tide/threefig-20261019:amd64'
BINARY='12429bbfbf6b6fcf099f053816051e5b678f53ba1c116a6a3abce9eafac5be12'
DIGEST='sha256:a1a7ea75ebdc5da69bc51a61f375d1a7378d7dce9889c6659ac31cfac7f5e568'
GOLD='70342e4b8d42e2cfe364e443400f61d3d9ffa55b44bd20cbf247fe0032eaf365'
INPUT='6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620'


def save(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.partial');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def options(host):
 opts=['-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=1','-o','StrictHostKeyChecking=yes']
 if host=='NJ-k2':opts+=['-o','UserKnownHostsFile='+str(KNOWN_HOSTS_FILE)]
 return opts

def command(argv,body=None,timeout=30):
 start=time.time_ns()
 try:
  r=subprocess.run(argv,input=body,capture_output=True,text=True,timeout=timeout);row=dict(argv=argv,rc=r.returncode,stdout=r.stdout,stderr=r.stderr,unix_ns=start,ended_ns=time.time_ns())
 except subprocess.TimeoutExpired as e:
  row=dict(argv=argv,rc=None,error='management_timeout',unix_ns=start,ended_ns=time.time_ns())
  with (OUT/'preflight/commands.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
  raise
 with (OUT/'preflight/commands.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
 if r.returncode:raise RuntimeError(f'management command failed({r.returncode}): '+r.stderr[-1200:])
 return r.stdout.strip()

def ssh(host,cmd,body=None,timeout=30):return command(['ssh',*options(host),SSH_ALIASES[host],cmd],body,timeout)
def py(host,code,timeout=30):return ssh(host,'python3 -c '+shlex.quote(code),timeout=timeout)
def upload(host,src,dst):return command(['scp','-O',*options(host),str(src),SSH_ALIASES[host]+':'+dst],timeout=90)
def pull(host,src,dst):
 contents=src.endswith('/.')
 source=src[:-2] if contents else src
 result=command(['scp','-O',*options(host),'-r',SSH_ALIASES[host]+':'+source,str(dst)],timeout=180)
 if contents:
  nested=Path(dst)/Path(source).name
  for child in nested.iterdir():
   target=Path(dst)/child.name
   assert not target.exists(),'refuse to overwrite existing collected artifact'
   child.rename(target)
  nested.rmdir()
 return result
def kubectl(argv,body=None,timeout=30):return ssh('NJ-k1',shlex.join(['kubectl','--request-timeout=15s',*argv]),body,timeout)
