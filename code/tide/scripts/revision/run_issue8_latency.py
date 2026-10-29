#!/usr/bin/env python3
"""Randomized cold/reused Tide lifecycle and trace perturbation experiment."""
import argparse,hashlib,json,os,random,signal,subprocess,time,urllib.request
from pathlib import Path
from control_metrics import processes
WORK={'kind':'cpu','bytes':65536,'iterations':100}
def save(p,x):
 q=p.with_suffix('.tmp');q.write_text(json.dumps(x,indent=2)+'\n');q.replace(p)
def cmd(*args):return subprocess.check_output(args,text=True,stderr=subprocess.STDOUT)
def clean_state():
 tasks=cmd('ctr','-n','tide','tasks','ls');ctns=cmd('ctr','-n','tide','containers','ls')
 queues=list(Path('/dev/mqueue').iterdir());children=list(Path('/sys/fs/cgroup/tide').glob('*'))
 dirs=[str(p) for p in children if p.is_dir()]
 if len(tasks.splitlines())!=1 or len(ctns.splitlines())!=1 or queues or dirs:raise RuntimeError(f'owned cleanup not clean: {tasks} {ctns} {queues} {dirs}')
 return dict(tasks=tasks,containers=ctns,queues=[],child_cgroups=[])
def session(args,root,initial,traced,count):
 root.mkdir(); meta=dict(initial=initial,traced=traced,status='running',requests=[],work=WORK)
 save(root/'metadata.json',meta)
 env=dict(os.environ,GOMAXPROCS='2');env.pop('TIDE_STAGE_TRACE',None)
 if traced:env['TIDE_STAGE_TRACE']=str(root/'trace.json')
 log=(root/'service.log').open('w'); proc=subprocess.Popen([str(args.binary),'--initial',str(initial)],env=env,stdout=log,stderr=subprocess.STDOUT)
 meta['launcher_pid']=proc.pid
 try:
  deadline=time.monotonic()+30
  while True:
   if proc.poll() is not None:raise RuntimeError(f'adapter exited {proc.returncode}')
   try:
    with urllib.request.urlopen('http://127.0.0.1:19280/health',timeout=.3) as r:
     if r.status==200:break
   except OSError:pass
   if time.monotonic()>deadline:raise TimeoutError('adapter readiness')
   time.sleep(.01)
  before=[p for p in processes().values() if p['cmd'].startswith('/worker --mode=paix')]
  if len(before)!=initial:raise RuntimeError('initial worker count mismatch')
  meta['workers_before']=before
  for i in range(count):
   request=urllib.request.Request('http://127.0.0.1:19280/run',data=json.dumps(WORK).encode(),headers={'Content-Type':'application/json','X-Qos-Ms':'1000'})
   start=time.monotonic_ns();status=599;body='';rid='';service=0;work=0
   try:
    with urllib.request.urlopen(request,timeout=32) as r:
     status=r.status;rid=r.headers.get('X-Trace-Id');body=r.read().decode()
    end=time.monotonic_ns();data=json.loads(body);service=data['service_ns'];work=data['result']['bytes_processed']
   except Exception as e:
    end=time.monotonic_ns();body=repr(e)
   row=dict(index=i,id=rid,status=status,start_ns=start,end_ns=end,latency_ms=(end-start)/1e6,worker_service_ns=service,bytes_processed=work,body=body)
   meta['requests'].append(row);save(root/'metadata.json',meta)
   if status!=200 or work!=6553600:raise RuntimeError(f'request failed {row}')
   time.sleep(.05)
  after=[p for p in processes().values() if p['cmd'].startswith('/worker --mode=paix')]
  if len(after)!=1:raise RuntimeError('expected one worker')
  worker=after[0];pid=worker['pid']
  worker['sha256']=hashlib.sha256(Path(f'/proc/{pid}/exe').read_bytes()).hexdigest()
  worker['thread_affinity']={p.name:sorted(os.sched_getaffinity(int(p.name))) for p in Path(f'/proc/{pid}/task').iterdir()}
  if any(v!=[0,1] for v in worker['thread_affinity'].values()):raise RuntimeError('worker affinity mismatch')
  if worker['sha256']!=args.worker_sha256:raise RuntimeError('worker differs from resource matrix')
  meta['worker_after']=worker
  meta['status']='completed'
 except BaseException as e:meta.update(status='failed',error=repr(e));raise
 finally:
  proc.terminate()
  try:proc.wait(timeout=40)
  except subprocess.TimeoutExpired:
   meta.update(status='failed',error='shutdown timeout');save(root/'metadata.json',meta);raise
  log.close();meta['exit_code']=proc.returncode;save(root/'metadata.json',meta)
  if proc.returncode!=0:raise RuntimeError(f'adapter shutdown exit {proc.returncode}')
  meta['cleanup']=clean_state();save(root/'metadata.json',meta)
 return meta

def main(args):
 os.setpriority(os.PRIO_PROCESS,0,0);args.out.mkdir(parents=True)
 rng=random.Random(20261005);jobs=[]
 for block in range(1,args.blocks+1):
  modes=[0,1];rng.shuffle(modes)
  for initial in modes:jobs.append(dict(name=f'b{block:02d}-'+('cold' if initial==0 else 'warm'),block=block,kind='lifecycle',initial=initial,traced=True,count=10,status='pending'))
 for block in range(1,args.perturb_blocks+1):
  flags=[True,False];rng.shuffle(flags)
  for flag in flags:jobs.append(dict(name=f'p{block:02d}-'+('on' if flag else 'off'),block=block,kind='perturbation',initial=1,traced=flag,count=51,status='pending'))
 state=dict(status='running',pid=os.getpid(),jobs=jobs,started_ns=time.time_ns(),binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),worker_sha256=args.worker_sha256,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),protocol=dict(seed=20261005,ddl_ms=1000,work=WORK,application_cpus=[0,1],application_gomaxprocs=1,image_cached=True))
 save(args.out/'progress.json',state);clean_state()
 cg=Path('/sys/fs/cgroup/tide/cpuset.cpus');old=cg.read_text();cg.write_text('0,1')
 try:
  for job in jobs:
   job['status']='running';save(args.out/'progress.json',state)
   meta=session(args,args.out/job['name'],job['initial'],job['traced'],job['count'])
   job['status']=meta['status'];save(args.out/'progress.json',state)
   print(job['name'],meta['status'],'first_ms',round(meta['requests'][0]['latency_ms'],3),flush=True)
  state['status']='completed'
 except BaseException as e:state.update(status='failed',error=repr(e));raise
 finally:
  cg.write_text(old);state['ended_ns']=time.time_ns();save(args.out/'progress.json',state)
  save(args.out/'cleanup.json',clean_state()|dict(parent_cpuset=cg.read_text().strip()))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--binary',type=Path,required=True);p.add_argument('--blocks',type=int,default=30);p.add_argument('--perturb-blocks',type=int,default=10);p.add_argument('--worker-sha256',default='7a7a6f3af52810394d02737b23ecc0f62c7c715d474ec72c9b44b0d1e8bf3659');main(p.parse_args())
