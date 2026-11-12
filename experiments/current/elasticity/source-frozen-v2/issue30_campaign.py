"""Three complete randomized blocks, immutable open-loop trace per pair."""
from issue30_kube import *
import concurrent.futures,statistics,math,random,traceback
SOURCE=OUT/'source'
def write_status(stage,**kw):save(OUT/'STATUS.json',dict(status=stage,updated_unix_ns=time.time_ns(),**kw))
def native_action(action,run):
 def one(n):
  host,node,kind,ip=n
  args=['env','RSEP_ISSUE30_ROOT='+REMOTE,'python3',REMOTE+'/source/node_control_v3.py',action,'--attempt',run,'--node',node]
  if action=='start':args+=['--kind',kind,'--ip',ip,'--cpus','12']
  raw=ssh(host,shlex.join(args),timeout=75);result=json.loads(raw);save(OUT/'runs'/run/(host+'-'+action+'.json'),result);return result
 with concurrent.futures.ThreadPoolExecutor(3) as pool:return list(pool.map(one,NODES))
def join(run):
 urls=[f'http://{ip}:31841' for _,_,_,ip in NODES]
 code='''import json,time,urllib.request
urls=URLS
for i in range(100):
 try:peers=[json.load(urllib.request.urlopen(u+'/health',timeout=1)) for u in urls];break
 except Exception:
  if i==99:raise
  time.sleep(.2)
result=[]
for u in urls:result.append(json.load(urllib.request.urlopen(urllib.request.Request(u+'/peers',data=json.dumps(peers).encode(),headers={'Content-Type':'application/json'}),timeout=5)))
print(json.dumps(dict(peers=peers,join=result)))
'''.replace('URLS',repr(urls))
 result=json.loads(py('NJ-k1',code,timeout=45));save(OUT/'runs'/run/'peers.json',result)
def freeze():
 assert not (OUT/'plan.json').exists(),'never revise an existing freeze'
 cap=json.loads((OUT/'calibration/capacity-calibration.json').read_text());capacity=statistics.median(r['capacity_rps'] for r in cap['blocks']);rate=round(.7*capacity)
 assert rate>0
 estimates={}
 for h,n,_,_ in NODES:
  data=json.loads((OUT/'calibration'/f'{h}-isolated.json').read_text());estimates[n]=math.ceil(statistics.median(w['elapsed_ns']/1e6 for w in data['rows']))
 config=dict(execution_ms=estimates['threefig-nj2'],node_execution_ms=estimates,qos_ms=350,ttl_ms=2000,weights=[1,1,1],latency_scale_ms=100,state_max_age_ms=5000)
 save(OUT/'config/native-config-preflight.json',config)
 seed=20261101;rng=random.Random(seed);order=[]
 (OUT/'traces').mkdir(exist_ok=True)
 traces={}
 for b in range(1,4):
  arms=['tide','hpa'];rng.shuffle(arms);order += [dict(block=b,arm=a,run=f'b{b}-{a}') for a in arms]
  trace=OUT/'traces'/f'block{b}.jsonl'
  with trace.open('x') as f:
   for i in range(rate*60):f.write(json.dumps(dict(id=f'issue30-b{b}-{i:05d}',offset_ns=i*1_000_000_000//rate))+'\n')
  traces[str(b)]=dict(path=str(trace.relative_to(OUT)),sha256=digest(trace),requests=rate*60)
 plan=dict(protocol='PLAN.md',frozen_unix_ns=time.time_ns(),calibration_capacity_rps=capacity,high_rate_rps=rate,rate_rule='round(0.7 * median independently measured fixed-inventory capacity)',calibration_sha256=digest(OUT/'calibration/capacity-calibration.json'),input_sha256=INPUT,output_sha256=GOLD,image_manifest=DIGEST,binary_sha256=BINARY,units_cpu=2,cpu_per_host=12,nodes=NODES,baseline_s=20,duration_s=60,recovery_s=360,request_timeout_s=180,deadline_ms=350,randomization_seed=seed,order=order,traces=traces,native_config=config,hpa=hpa(),sources={p.name:digest(p) for p in SOURCE.glob('*.py')},notes=['Initial allocation baselines differ; main ordinate is change from own stable baseline.','CPU grants are distinct from utilization and native borrowing.','HPA min=1,max=18,target CPU=60%; stock controller/default behavior.','All failed runs and right-censoring are retained. No best run selection.'])
 save(OUT/'plan.json',plan)
 for host in HOSTS:upload(host,OUT/'config/native-config-preflight.json',REMOTE+'/runtime/native-config-preflight.json')
 for k,v in traces.items():upload('NJ-k1',OUT/v['path'],REMOTE+'/runtime/block'+k+'.jsonl')
 return plan
def launch_client(run,arm,url,trace,baseline=20,duration=60,recovery=360):
 code=f'''import subprocess,pathlib,json,time,os
r=pathlib.Path({REMOTE!r})/'runs'/{run!r};r.mkdir(exist_ok=True)
args=['taskset','-c','12-15','python3',{REMOTE!r}+'/source/issue30_client.py','--arm',{arm!r},'--url',{url!r},'--trace',{trace!r},'--jpeg',{REMOTE!r}+'/runtime/dog.jpg','--output',str(r/'measurement'),'--baseline',str({baseline}),'--duration',str({duration}),'--recovery',str({recovery})]
f=(r/'client.log').open('x');p=subprocess.Popen(args,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
obj=dict(pid=p.pid,proc_start=pathlib.Path('/proc/'+str(p.pid)+'/stat').read_text().split()[21],argv=args,started_unix_ns=time.time_ns());(r/'client-launch.json').write_text(json.dumps(obj));print(json.dumps(obj))
'''
 result=json.loads(py('NJ-k1',code));save(OUT/'runs'/run/'client-launch.json',result)
def wait_client(run,maximum=700):
 deadline=time.monotonic()+maximum;last=None
 while time.monotonic()<deadline:
  code=f'''import pathlib,json
r=pathlib.Path({REMOTE!r})/'runs'/{run!r}/'measurement'
result=dict(done=(r/'DONE.json').exists(),failed=(r/'FAILED.json').exists(),rows=sum(1 for _ in (r/'requests.jsonl').open()) if (r/'requests.jsonl').exists() else 0)
if result['failed']:result['failure']=json.loads((r/'FAILED.json').read_text())
print(json.dumps(result))
'''
  try:state=json.loads(py('NJ-k1',code,timeout=20))
  except Exception as exc:
   save(OUT/'runs'/run/f'management-read-error-{time.time_ns()}.json',dict(error=repr(exc)));time.sleep(5);continue
  if state!=last:print(json.dumps(dict(run=run,**state)),flush=True);last=state
  if state['failed']:raise RuntimeError(state)
  if state['done']:return
  time.sleep(10)
 raise RuntimeError('client observation did not finish within fixed management deadline')
def qualify_nodes():
 n=json.loads(kubectl(['get','nodes','-o','json']));assert len(n['items'])==3
 for v in n['items']:
  c={x['type']:x['status'] for x in v['status']['conditions']}
  assert c.get('Ready')=='True' and all(c.get(k)=='False' for k in ['DiskPressure','MemoryPressure','PIDPressure']),c
 return n
def qualify_hpa_metrics(run,timeout=120):
 records=[];end=time.monotonic()+timeout
 while time.monotonic()<end:
  actual=json.loads(kubectl(['get','hpa','fig7-inference','-n',NS,'-o','json']));records.append(dict(observed_unix_ns=time.time_ns(),object=actual))
  if any(c['type']=='ScalingActive' and c['status']=='True' for c in actual.get('status',{}).get('conditions',[])):
   save(OUT/'runs'/run/'hpa-baseline-qualified.json',records);return actual
  time.sleep(3)
 save(OUT/'runs'/run/'hpa-baseline-failure.json',records);raise RuntimeError('HPA has no valid post-Ready CPU metric within qualification window')
def run_one(item,plan):
 run=item['run'];arm=item['arm'];(OUT/'runs'/run).mkdir(exist_ok=False);write_status('RUNNING',current=item,plan_sha256=digest(OUT/plan.get('plan_file','plan.json')))
 save(OUT/'runs'/run/'nodes-before.json',qualify_nodes());dep=None;h=None;started=False
 clocks=[]
 for host in HOSTS:
  before=time.time_ns();raw=py(host,"import time,json,subprocess; print(json.dumps(dict(unix_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),ntp=subprocess.run(['timedatectl','show','--property=NTPSynchronized'],capture_output=True,text=True).stdout,qdisc=subprocess.run(['tc','qdisc','show'],capture_output=True,text=True).stdout)))");after=time.time_ns();clocks.append(dict(host=host,client_before_ns=before,client_after_ns=after,remote=json.loads(raw)))
 save(OUT/'runs'/run/'clock-network-before.json',clocks)
 try:
  if arm=='tide':
   started=True;native_action('start',run);join(run);url='http://'+NODES[1][3]+':31841/run'
  else:
   dep=create_deployment(1,run);save(OUT/'runs'/run/'deployment-created.json',dep);save(OUT/'runs'/run/'pods-initial-ready.json',wait_ready(1))
   end=time.monotonic()+90
   while time.monotonic()<end:
    metrics=json.loads(kubectl(['get','--raw','/apis/metrics.k8s.io/v1beta1/namespaces/'+NS+'/pods']))
    if metrics.get('items'):break
    time.sleep(3)
   assert metrics.get('items'),'new baseline Pod has no actual CPU metric';save(OUT/'runs'/run/'initial-pod-metrics.json',metrics)
   h=create_hpa(run);save(OUT/'runs'/run/'hpa-created.json',h);qualify_hpa_metrics(run)
   service=json.loads(kubectl(['get','svc','fig7-inference','-n',NS,'-o','json']));url='http://'+service['spec']['clusterIP']+':19384/run'
  launch_client(run,arm,url,REMOTE+'/runtime/'+('v2-' if plan.get('plan_file')=='plan-v2.json' else '')+'block'+str(item['block'])+'.jsonl');wait_client(run)
 finally:
  if started:native_action('stop',run)
  if dep:
   save(OUT/'runs'/run/'pods-final.json',pods());save(OUT/'runs'/run/'events-final.json',json.loads(kubectl(['get','events','-n',NS,'-o','json'])))
   if h:save(OUT/'runs'/run/'hpa-final.json',json.loads(kubectl(['get','hpa','fig7-inference','-n',NS,'-o','json'])))
   remove_deployment(dep['metadata']['uid'],h['metadata']['uid'] if h else None)
  for host in HOSTS:
   dst=OUT/'runs'/run/host;dst.mkdir(exist_ok=True)
   if arm=='tide' or host=='NJ-k1':pull(host,REMOTE+'/runs/'+run+'/.',dst)
 print(json.dumps(dict(run=run,status='COLLECTED')),flush=True)
def main():
 plan=freeze() if not (OUT/'plan.json').exists() else json.loads((OUT/'plan.json').read_text())
 for item in plan['order']:
  assert not (OUT/'runs'/item['run']).exists(),'never implicitly resume/replay an existing scored run'
  run_one(item,plan)
 write_status('COMPARISONS_COLLECTED',runs=6)
if __name__=='__main__':
 try:main()
 except BaseException as exc:
  write_status('STOPPED_WITH_EVIDENCE',error=repr(exc),traceback=traceback.format_exc());raise
