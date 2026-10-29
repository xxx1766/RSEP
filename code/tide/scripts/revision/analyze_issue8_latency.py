#!/usr/bin/env python3
"""Independent full-data validation and aggregation of issue-8 stage traces."""
import argparse,csv,hashlib,json,math,statistics
from pathlib import Path
from issue8_metrics import partition,NAMES

def mean(x):return statistics.mean(x)
def stats(x):
 return dict(n=len(x),mean=mean(x),sd=statistics.stdev(x) if len(x)>1 else 0,p50=statistics.median(x),p95=sorted(x)[math.ceil(.95*len(x))-1])
def writecsv(p,rows):
 with p.open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def analyze(root,expected_blocks,expected_perturb):
 state=json.loads((root/'progress.json').read_text())
 assert state['status']=='completed','matrix incomplete'
 assert len(state['jobs'])==2*(expected_blocks+expected_perturb),'wrong job count'
 cleanup=json.loads((root/'cleanup.json').read_text());assert cleanup['parent_cpuset']=='' and not cleanup['queues'] and not cleanup['child_cgroups']
 allrows=[];lifecycles=[];perturb=[];hashes={};total=0
 for job in state['jobs']:
  assert job['status']=='completed'
  block=root/job['name'];m=json.loads((block/'metadata.json').read_text());assert m['status']=='completed' and m['exit_code']==0
  assert len(m['requests'])==job['count'] and len(m['workers_before'])==job['initial']
  worker=m['worker_after'];assert worker['sha256']==state['worker_sha256'];assert all(v==[0,1] for v in worker['thread_affinity'].values())
  assert not m['cleanup']['queues'] and not m['cleanup']['child_cgroups']
  if job['initial']:assert m['workers_before'][0]['pid']==worker['pid'] and m['workers_before'][0]['start']==worker['start']
  for path in block.iterdir():
   if path.is_file():hashes[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
  trace=json.loads((block/'trace.json').read_text()) if job['traced'] else None
  parts=[];executors=set()
  for i,r in enumerate(m['requests']):
   assert r['index']==i and r['id']==f'latency-{i+1}' and r['status']==200 and r['bytes_processed']==6553600
   assert abs(r['latency_ms']-(r['end_ns']-r['start_ns'])/1e6)<1e-9 and r['worker_service_ns']>0
   assert r['latency_ms']>=r['worker_service_ns']/1e6
   row=dict(session=job['name'],block=job['block'],kind=job['kind'],initial=job['initial'],traced=job['traced'],index=i,latency_ms=r['latency_ms'],worker_ms=r['worker_service_ns']/1e6)
   if trace is not None:
    q=partition(trace,r);parts.append(q);executors.add(q['executor']);row.update({k:q[k] for k in ('handler_ms','external_ms','ipc_residual_ms')});row.update(q['stages_ms'])
   else:row.update({k:None for k in ['handler_ms','external_ms','ipc_residual_ms']+NAMES})
   allrows.append(row);total+=1
  if trace is not None:
   assert len(executors)==1,'executor count changed'
   executor=next(iter(executors));ev=[e for e in trace if e['id']==executor]
   names=['container_create_start','container_create_end','container_start_start','container_start_end','executor_ready']
   assert len(ev)==len(names) and {e['phase'] for e in ev}==set(names)
   by={e['phase']:e['ns'] for e in ev};ts=[by[p] for p in names];assert all(b>=a for a,b in zip(ts,ts[1:]))
   req0={e['phase']:e['ns'] for e in trace if e['id']=='latency-1'}
   if job['initial']==0:assert req0['pool_get']<=ts[0]<=ts[-1]<=req0['pool_acquired']
   else:assert ts[-1]<req0['http_enter']
   assert len(trace)==len(m['requests'])*11+5
   if job['kind']=='lifecycle':
    q=parts[0]
    lifecycle=dict(session=job['name'],block=job['block'],mode='cold' if job['initial']==0 else 'warm',latency_ms=m['requests'][0]['latency_ms'],handler_ms=q['handler_ms'],worker_ms=q['worker_ms'],external_ms=q['external_ms'],ipc_residual_ms=q['ipc_residual_ms'],container_create_ms=(ts[1]-ts[0])/1e6,container_start_ms=(ts[3]-ts[2])/1e6,container_ready_ms=(ts[4]-ts[0])/1e6,subsequent_warm_ms=mean([r['latency_ms'] for r in m['requests'][1:]]))
    lifecycle.update(q['stages_ms']);lifecycles.append(lifecycle)
  if job['kind']=='perturbation':perturb.append(dict(block=job['block'],traced=job['traced'],mean_ms=mean([r['latency_ms'] for r in m['requests'][1:]])))
 assert total==expected_blocks*20+expected_perturb*102
 out=root/'summary';out.mkdir(exist_ok=True)
 writecsv(out/'requests.csv',allrows);writecsv(out/'lifecycle.csv',lifecycles)
 metrics=[k for k in lifecycles[0] if k not in ('session','block','mode')]
 summary=[]
 for mode in ('cold','warm'):
  rows=[r for r in lifecycles if r['mode']==mode];assert len(rows)==expected_blocks
  for metric in metrics:summary.append(dict(mode=mode,metric=metric,**stats([r[metric] for r in rows])))
 writecsv(out/'summary.csv',summary)
 deltas=[]
 for block in range(1,expected_blocks+1):
  cold=next(r for r in lifecycles if r['block']==block and r['mode']=='cold');warm=next(r for r in lifecycles if r['block']==block and r['mode']=='warm')
  deltas.append(cold['latency_ms']-warm['latency_ms'])
 effect=stats(deltas);effect['ci95_half']=2.0452296421327*effect['sd']/math.sqrt(len(deltas)) if expected_blocks==30 else None
 perturb_result={}
 if perturb:
  delta=[next(r['mean_ms'] for r in perturb if r['block']==b and r['traced'])-next(r['mean_ms'] for r in perturb if r['block']==b and not r['traced']) for b in range(1,expected_perturb+1)]
  perturb_result=stats(delta);perturb_result['off_mean_ms']=mean([r['mean_ms'] for r in perturb if not r['traced']]);perturb_result['relative_pct']=100*perturb_result['mean']/perturb_result['off_mean_ms'];perturb_result['ci95_half']=2.2621571628541*perturb_result['sd']/math.sqrt(len(delta)) if expected_perturb==10 else None
  writecsv(out/'perturbation.csv',perturb)
 result=dict(status='complete',sessions=len(state['jobs']),requests=total,lifecycle_blocks=expected_blocks,perturbation_blocks=expected_perturb,paired_cold_minus_warm_ms=effect,trace_on_minus_off_ms=perturb_result,worker_sha256=state['worker_sha256'],binary_sha256=state['binary_sha256'],raw_files=len(hashes))
 (out/'audit.json').write_text(json.dumps(result,indent=2)+'\n');(out/'raw-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--blocks',type=int,default=30);p.add_argument('--perturb-blocks',type=int,default=10);a=p.parse_args();print(json.dumps(analyze(a.root,a.blocks,a.perturb_blocks)))
