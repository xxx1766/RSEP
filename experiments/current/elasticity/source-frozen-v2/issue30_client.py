"""Identical open-loop trace client and source-specific allocation observers."""
import argparse,asyncio,hashlib,json,os,resource,subprocess,time
from pathlib import Path
from collect_measurement import http_request,Journal,atomic_json,wait_until,descriptor_limit
from issue30_measurement import summarize_requests
INPUT='6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620';GOLD='70342e4b8d42e2cfe364e443400f61d3d9ffa55b44bd20cbf247fe0032eaf365'
OBSERVE_HOSTS=os.environ.get('RSEP_ELASTICITY_OBSERVE_HOSTS','198.51.100.11,198.51.100.12,198.51.100.13').split(',')
async def get(url):
 begin=time.monotonic_ns()
 try:
  status,_,body=await http_request(url,None,{},1)
  return dict(url=url,begin_ns=begin,end_ns=time.monotonic_ns(),http_status=status,body=json.loads(body))
 except Exception as exc:return dict(url=url,begin_ns=begin,end_ns=time.monotonic_ns(),error=repr(exc))
async def kubectl(args):
 start=time.monotonic_ns()
 try:
  p=await asyncio.create_subprocess_exec('kubectl','--request-timeout=3s',*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
  out,err=await asyncio.wait_for(p.communicate(),5)
  return dict(begin_ns=start,end_ns=time.monotonic_ns(),rc=p.returncode,body=json.loads(out) if p.returncode==0 else None,stderr=err.decode())
 except Exception as exc:
  if 'p' in locals() and p.returncode is None:p.kill();await p.wait()
  return dict(begin_ns=start,end_ns=time.monotonic_ns(),error=repr(exc))
async def observe(a,stop):
 journal=Journal(a.output/'resources.jsonl');i=0;next_time=time.monotonic_ns();interval=100000000 if a.arm=='tide' else 1000000000
 try:
  while not stop.is_set():
   start=time.monotonic_ns()
   if a.arm=='tide':
    samples=await asyncio.gather(*(get('http://'+host+':31841/state') for host in OBSERVE_HOSTS))
    row=dict(index=i,begin_ns=start,end_ns=time.monotonic_ns(),source='native-local-processors',samples=samples)
   else:
    objs=await kubectl(['get','pods,hpa,deployment','-n','tide-fig7-issue30','-o','json'])
    ips=[p['status']['podIP'] for p in (objs.get('body') or {}).get('items',[]) if p['kind']=='Pod' and p.get('status',{}).get('podIP')]
    states=await asyncio.gather(*(get('http://'+ip+':19385/state') for ip in ips))
    row=dict(index=i,begin_ns=start,end_ns=time.monotonic_ns(),source='kubernetes-api',objects=objs,worker_states=states)
    if i%15==0:row['metrics']=await kubectl(['get','--raw','/apis/metrics.k8s.io/v1beta1/namespaces/tide-fig7-issue30/pods'])
   journal.append(row);i+=1;next_time+=interval
   next_time=max(next_time,time.monotonic_ns());await asyncio.sleep(max(0,(next_time-time.monotonic_ns())/1e9))
 finally:journal.close()
async def run(a):
 a.output.mkdir(exist_ok=False);trace=[json.loads(s) for s in a.trace.read_text().splitlines()];jpeg=a.jpeg.read_bytes();assert hashlib.sha256(jpeg).hexdigest()==INPUT
 soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE);resource.setrlimit(resource.RLIMIT_NOFILE,(descriptor_limit(soft,hard,len(trace)),hard))
 baseline_start=time.monotonic_ns();load_start=baseline_start+round(a.baseline*1e9);observation_end=load_start+round((a.duration+a.recovery)*1e9)
 meta=dict(arm=a.arm,url=a.url,trace_sha256=hashlib.sha256(a.trace.read_bytes()).hexdigest(),baseline_start_ns=baseline_start,start_monotonic_ns=load_start,end_load_ns=load_start+round(a.duration*1e9),planned_observation_end_ns=observation_end,wall_unix_ns=time.time_ns(),duration_s=a.duration,baseline_s=a.baseline,recovery_s=a.recovery,affinity=sorted(os.sched_getaffinity(0)),expected_ids=[r['id'] for r in trace],request_timeout_s=180,deadline_ms=350)
 atomic_json(a.output/'run.json',meta);journal=Journal(a.output/'requests.jsonl');rows=[];stop=asyncio.Event();observer=asyncio.create_task(observe(a,stop));watchers=[];watchfiles=[]
 if a.arm=='hpa':
  for kind in ['pods','hpa']:
   f=(a.output/(kind+'-watch.jsonstream')).open('xb');e=(a.output/(kind+'-watch.stderr')).open('xb');watchfiles.extend([f,e]);watchers.append(subprocess.Popen(['kubectl','get',kind,'-n','tide-fig7-issue30','--watch','--output-watch-events','-o','json'],stdout=f,stderr=e))
 async def one(item):
  planned=load_start+item['offset_ns'];row=dict(id=item['id'],planned_submit_monotonic_ns=planned,actual_submit_monotonic_ns=time.monotonic_ns(),http_status=None,response=None,error=None)
  try:
   status,headers,body=await http_request(a.url,jpeg,{'Content-Type':'image/jpeg','X-Request-ID':item['id']},180)
   row.update(return_monotonic_ns=time.monotonic_ns(),http_status=status,response_headers=headers,response_body=body.decode(errors='replace'))
   try:row['response']=json.loads(body)
   except ValueError:pass
  except Exception as exc:row.update(error=type(exc).__name__,error_detail=repr(exc))
  finally:
   row.setdefault('return_monotonic_ns',time.monotonic_ns());rows.append(row);journal.append(row)
 try:
  tasks=[]
  for item in trace:
   await wait_until(load_start+item['offset_ns']);tasks.append(asyncio.create_task(one(item)));await asyncio.sleep(0)
  await asyncio.gather(*tasks);await wait_until(observation_end)
 finally:
  stop.set();await observer;journal.close()
  for p in watchers:
   if p.poll() is None:p.terminate()
  for p in watchers:
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.kill();p.wait()
  for f in watchfiles:f.close()
 meta['ended_monotonic_ns']=time.monotonic_ns();meta['summary']=summarize_requests(meta['expected_ids'],rows,350,a.duration,INPUT,GOLD)
 atomic_json(a.output/'run.json',meta);atomic_json(a.output/'DONE.json',dict(status='PASS',offered=len(rows),end_unix_ns=time.time_ns()));print(json.dumps(meta['summary']),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--arm',choices=['tide','hpa'],required=True);p.add_argument('--url',required=True);p.add_argument('--trace',type=Path,required=True);p.add_argument('--jpeg',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--baseline',type=float,default=20);p.add_argument('--duration',type=float,default=60);p.add_argument('--recovery',type=float,default=360)
 args=p.parse_args()
 try:asyncio.run(run(args))
 except BaseException as exc:
  args.output.mkdir(exist_ok=True);atomic_json(args.output/'FAILED.json',dict(error=repr(exc),unix_ns=time.time_ns()));raise
