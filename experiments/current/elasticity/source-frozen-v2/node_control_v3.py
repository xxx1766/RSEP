"""Parent-only owned-node lifecycle. Never deletes unknown tasks or cgroups."""
import argparse,json,os,pathlib,re,resource,signal,subprocess,time,urllib.request,hashlib
R=pathlib.Path(os.environ.get('RSEP_ISSUE30_ROOT','/var/lib/rsep/issue30'));IMAGE='docker.io/tide/threefig-20261019:amd64'
UUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
def command(a,check=True):
 p=subprocess.run(a,capture_output=True,text=True,timeout=25)
 if check and p.returncode:raise RuntimeError(dict(command=a,rc=p.returncode,out=p.stdout,err=p.stderr))
 return dict(command=a,rc=p.returncode,out=p.stdout,err=p.stderr)
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def readlog(path):
 rows=[]
 if path.exists():
  for line in path.read_text().splitlines():
   try:rows.append(json.loads(line))
   except ValueError:pass
 return rows

def owned_running(path,identity):
 try:parts=(path/'stat').read_text().split()
 except (FileNotFoundError,ProcessLookupError):return False
 assert identity.get('proc_start') is not None and parts[21]==identity['proc_start'],'PID reused or missing process-start witness'
 return parts[2]!='Z'

def signal_owned(pid,sig,actions):
 try:os.killpg(pid,sig);actions.append(dict(action=signal.Signals(sig).name,pid=pid))
 except ProcessLookupError:actions.append(dict(action='already_exited_before_signal',signal=signal.Signals(sig).name,pid=pid))

def start_inner(a):
 assert re.fullmatch(r'[a-z0-9-]+',a.attempt)
 expected=json.loads((R/'runtime/expected-identities.json').read_text())
 assert hashlib.sha256((R/'runtime/native-dynamic').read_bytes()).hexdigest()==expected['binary_sha256'],'binary identity changed'
 images=[line.split() for line in command(['ctr','-n','tide','images','ls'])['out'].splitlines() if line.startswith(IMAGE+' ')]
 assert len(images)==1 and images[0][2]==expected['image_manifest'],'image identity changed'
 assert not command(['ctr','-n','tide','tasks','ls','-q'])['out'].strip(),'busy Tide namespace'
 assert not command(['ctr','-n','tide','containers','ls','-q'])['out'].strip(),'busy Tide namespace'
 assert not list(pathlib.Path('/dev/mqueue').iterdir()),'foreign MQ'
 assert not [p for p in pathlib.Path('/sys/fs/cgroup/tide').iterdir() if p.is_dir()],'foreign cgroup'
 root=R/'runs'/a.attempt;root.mkdir()
 originals={p:pathlib.Path(p).read_text() for p in ['/proc/sys/fs/mqueue/msgsize_max','/sys/fs/cgroup/tide/cpuset.cpus']}
 save(root/'original-settings.json',originals)
 cpus=a.cpus
 assert cpus in (2,4,6,8,10,12)
 pathlib.Path('/proc/sys/fs/mqueue/msgsize_max').write_text('262144\n')
 pathlib.Path('/sys/fs/cgroup/tide/cpuset.cpus').write_text('0-'+str(cpus-1)+'\n')
 resource.setrlimit(resource.RLIMIT_MSGQUEUE,(134217728,134217728))
 nofile_soft,nofile_hard=resource.getrlimit(resource.RLIMIT_NOFILE)
 assert nofile_hard>=65536
 resource.setrlimit(resource.RLIMIT_NOFILE,(65536,nofile_hard))
 argv=[str(R/'runtime/native-dynamic'),'--node',a.node,'--kind',a.kind,'--cpus',str(cpus),'--address',a.ip+':31840','--http','0.0.0.0:31841','--root',str(root),'--config',str(R/'runtime/native-config-preflight.json'),'--request-timeout','185s']
 env=dict(os.environ,GOMAXPROCS='4')
 log=(root/'launcher.log').open('x');p=subprocess.Popen(['taskset','-c','12-15']+argv,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True);log.close()
 identity=dict(pid=p.pid,proc_start=None,argv=argv,started_ns=time.time_ns(),node=a.node,kind=a.kind,ip=a.ip,binary_sha256=hashlib.sha256((R/'runtime/native-dynamic').read_bytes()).hexdigest())
 save(root/'launch.json',identity)
 procstat=pathlib.Path(f'/proc/{p.pid}/stat')
 if procstat.exists():identity['proc_start']=procstat.read_text().split()[21]
 for _ in range(50):
  if not procstat.exists():break
  affinity=sorted(os.sched_getaffinity(p.pid))
  if affinity==[12,13,14,15]:break
  time.sleep(.01)
 identity['control_nofile_limit']=65536
 identity['control_affinity']=sorted(os.sched_getaffinity(p.pid)) if procstat.exists() else None
 save(root/'launch.json',identity)
 assert identity['control_affinity']==[12,13,14,15],'control affinity did not apply'
 print(json.dumps(identity),flush=True)

def start(a):
 try:start_inner(a)
 except BaseException as exc:
  root=R/'runs'/a.attempt
  if root.exists():save(root/'start-failure.json',dict(status='FAIL',error=repr(exc),failed_ns=time.time_ns()))
  raise

def stop_inner(a):
 root=R/'runs'/a.attempt
 if not root.exists():
  print(json.dumps(dict(status='NO_START',root=str(root))));return
 launch=root/'launch.json'
 if launch.exists():ident=json.loads(launch.read_text())
 else:
  ident=dict(pid=None,node=a.node)
  for cmdline in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
   try:args=cmdline.read_bytes().split(b'\0')
   except (FileNotFoundError,PermissionError,ProcessLookupError):continue
   assert str(root).encode() not in args or not any(b'native-dynamic' in arg for arg in args),'live process without persisted launch identity'
 pid=ident['pid'];actions=[]
 p=pathlib.Path(f'/proc/{pid}') if pid is not None else None
 if p is not None and owned_running(p,ident):
  try:cmdline=(p/'cmdline').read_text().replace('\0',' ')
  except (FileNotFoundError,ProcessLookupError):cmdline=None
  if cmdline is not None:
   assert 'native-dynamic' in cmdline,'foreign process'
   signal_owned(pid,signal.SIGTERM,actions)
  for _ in range(300):
   if not owned_running(p,ident):break
   time.sleep(.1)
  if owned_running(p,ident):
   signal_owned(pid,signal.SIGKILL,actions)
   for _ in range(50):
    if not owned_running(p,ident):break
    time.sleep(.1)
   assert not owned_running(p,ident),'owned native PID still active after SIGKILL'

 events=readlog(root/'native'/ident['node']/'runtime.jsonl')
 eids=set();groups=set()
 for row in events:
  if row.get('msg')=='Containerxv2: start creating new container':
   eid=row.get('ExecutorId');assert re.fullmatch(UUID,eid);eids.add(eid)
  if row.get('msg')=='Containerxv2: finish creating cgroup':
   cg=row.get('CgroupPath');assert re.fullmatch('/tide/'+UUID,cg);groups.add(cg)
 for eid in sorted(eids):
  info=command(['ctr','-n','tide','containers','info',eid],False)
  if info['rc']==0:
   assert json.loads(info['out'])['Image']==IMAGE,'foreign image'
   for cmd in [['ctr','-n','tide','tasks','kill','--signal','SIGKILL',eid],['ctr','-n','tide','tasks','rm',eid],['ctr','-n','tide','containers','rm',eid]]:actions.append(command(cmd,False))
  actions.append(command(['ctr','-n','tide','snapshots','rm',eid+'snapshot'],False))
  for suffix in ('-in','-out'):
   mq=pathlib.Path('/dev/mqueue')/(eid+suffix)
   if mq.exists():mq.unlink();actions.append(dict(action='unlink_owned_mq',name=mq.name))
 for cg in sorted(groups):
  path=pathlib.Path('/sys/fs/cgroup'+cg)
  if path.exists():
   assert not (path/'cgroup.procs').read_text().strip(),'owned cgroup still active'
   path.rmdir();actions.append(dict(action='rmdir_owned_cgroup',path=cg))
 snapshot_keys={line.split()[0] for line in command(['ctr','-n','tide','snapshots','ls'])['out'].splitlines()[1:] if line.strip()}
 leftovers=dict(owned_snapshots=sorted(snapshot_keys & {eid+'snapshot' for eid in eids}),tasks=command(['ctr','-n','tide','tasks','ls','-q'])['out'].split(),containers=command(['ctr','-n','tide','containers','ls','-q'])['out'].split(),mq=[x.name for x in pathlib.Path('/dev/mqueue').iterdir()],cgroups=[p.name for p in pathlib.Path('/sys/fs/cgroup/tide').iterdir() if p.is_dir()])
 assert not any(leftovers.values()),leftovers
 originals=json.loads((root/'original-settings.json').read_text()) if (root/'original-settings.json').exists() else {}
 for path,value in originals.items():pathlib.Path(path).write_text(value)
 result=dict(status='PASS',stopped_ns=time.time_ns(),owned_executor_ids=sorted(eids),owned_cgroups=sorted(groups),leftovers=leftovers,actions=actions,restored={path:pathlib.Path(path).read_text()==value for path,value in originals.items()})
 baseline=json.loads((R/'evidence/baseline.json').read_text())
 shared={}
 for ns in ['default','k8s.io']:
  before={tuple(line.split()) for line in baseline['tasks'][ns]['out'].splitlines()[1:] if line.strip()}
  after=command(['ctr','-n',ns,'tasks','ls'])
  current={tuple(line.split()) for line in after['out'].splitlines()[1:] if line.strip()}
  shared[ns]=dict(original_tasks_preserved=before<=current,missing=sorted(before-current),extra=sorted(current-before))
 services={svc:command(['systemctl','is-active',svc])['out'].strip()==baseline['services'][svc]['out'].strip() for svc in ['containerd','kubelet']}
 result.update(shared_tasks=shared,shared_services_unchanged=services)
 assert all(v['original_tasks_preserved'] for v in shared.values()) and all(services.values()),result
 save(root/'cleanup.json',result);print(json.dumps(result))

def stop(a):
 try:stop_inner(a)
 except BaseException as exc:
  root=R/'runs'/a.attempt
  state=dict(status='FAIL',error=repr(exc),failed_ns=time.time_ns(),settings_restoration='not_claimed; inspect actual state and preserve unknown resources')
  state['actual_settings']={p:pathlib.Path(p).read_text() for p in ['/proc/sys/fs/mqueue/msgsize_max','/sys/fs/cgroup/tide/cpuset.cpus']}
  state['actual_tasks']=command(['ctr','-n','tide','tasks','ls'],False)
  save(root/'cleanup-failure.json',state)
  raise

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['start','stop']);p.add_argument('--attempt',required=True);p.add_argument('--node');p.add_argument('--cpus',type=int,default=12);p.add_argument('--kind');p.add_argument('--ip');a=p.parse_args()
 (start if a.action=='start' else stop)(a)
if __name__=='__main__':main()
