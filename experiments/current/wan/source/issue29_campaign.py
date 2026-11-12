"""Single-repeat3+3pilot: freeze independent calibration before any comparison."""
import collections
import json
import math
from pathlib import Path
import statistics
import time
import traceback
import issue29_common as p
import issue29_ops as ops
import issue29_validation as v
from issue29_path import launch,stop_collect
from issue29_capacity import node_capacity
from issue29_design import context_estimate,metrics
from issue12_trace import generate

ROOT=ops.ROOT;VMS=ops.COMPUTE;LOCAL=[vm for vm in VMS if vm[4]=='l']

def state(phase,**kwargs):p.save(p.OUT/'STATE.json',dict(phase=phase,unix_ns=time.time_ns(),**kwargs))
def jsonl(file,rows):file.parent.mkdir(parents=True,exist_ok=True);file.write_text(''.join(json.dumps(r,separators=(',',':'))+'\n' for r in rows))
def rows(file):return [json.loads(x) for x in file.read_text().splitlines() if x.strip()]

def capacity():
 root=p.OUT/'calibration/capacity';root.mkdir(parents=True,exist_ok=False);ops.shape('unshaped',root/'network')
 blocks=[]
 for repeat in [1,2,3]:
  name=f'capacity-six-r{repeat}';folder=root/name;folder.mkdir();state('capacity',run=name);ops.infrastructure(folder,'before');before=v.server_offsets(VMS);started=time.time_ns()
  remote=ROOT+'/runs/'+name;p.guest(v.DRIVER,'mkdir -p '+remote)
  job=launch(v.DRIVER,True,remote+'/client',['python3',ROOT+'/issue29_capacity_client.py','--endpoints',*[ops.ip(vm)+':19383' for vm in VMS],'--inputs',ROOT+'/inputs','--name',name,'--out',remote+'/output'],{},folder,'client')
  try:
   v.wait(job,name,180)
   for f in ['requests.jsonl','run.json']:p.guest_copy(v.DRIVER,remote+'/output/'+f,folder/f,pull=True)
  finally:stop_collect(job,folder,'client-job');events=v.collect_servers(folder,before)
  raw=rows(folder/'requests.jsonl');trace=folder/'identity-trace.jsonl';jsonl(trace,[dict(id=r['id'],scheduled_offset_ns=0) for r in raw])
  audit=v.audit([dict(r,on_time=r['status']=='success' and r['latency_ms']<=2000) for r in raw],events,trace,2000)
  assert len(raw)==3072 and audit['success']==3072,'capacity failed; preserve and stop'
  ids={r['id']:r for r in raw};caps={}
  for index,vm in enumerate(VMS):
   selected=[e for e in events if e['request_id'] in ids and e['executing_vm']==vm]
   assert all(ids[e['request_id']]['endpoint_index']==index for e in selected)
   caps[vm]=node_capacity(selected)
  item=dict(repeat=repeat,per_node=caps,local_capacity_rps=sum(caps[vm]['capacity_rps'] for vm in LOCAL),physical_audit=audit)
  p.save(folder/'audit.json',item);blocks.append(item);ops.infrastructure(folder,'after');ops.heartbeat_gate(folder,'capacity',started,time.time_ns());print('CAPACITY_RESULT',repeat,item['local_capacity_rps'],flush=True)
 value=statistics.median(b['local_capacity_rps'] for b in blocks)
 result=dict(local_capacity_rps=value,blocks=blocks,rates={label:max(1,round(value*f)) for label,f in [('p080',.8),('p100',1),('p120',1.2)]},before_comparisons=True)
 p.save(root/'frozen.json',result);return result

def contexts():
 assert not (p.OUT/'runs').exists(),'cannot recalibrate after comparisons'
 root=p.OUT/'calibration/contexts';root.mkdir(parents=True,exist_ok=True);initial=json.loads((p.OLD/'calibration/timing-frozen.json').read_text());blocks=[];session=str(time.time_ns())
 for rtt,targets in [('unshaped',LOCAL),('25',[vm for vm in VMS if vm[4]=='r']),('65',[vm for vm in VMS if vm[4]=='r'])]:
  ops.shape(rtt,root/('network-'+rtt+'-'+session));ops.clocks('contexts-'+rtt+'-before-'+session)
  for vm in targets:
   name='context-'+vm+'-'+rtt;folder=root/name
   if (folder/'audit.json').exists():
    old=json.loads((folder/'audit.json').read_text());assert old['vm']==vm and old['rtt']==rtt;blocks.append(old);continue
   folder.mkdir();state('predictor-calibration',run=name);trace=v.new_trace(folder,name,40,1);before=v.server_offsets(VMS);started=time.time_ns()
   role='i23-l01' if vm[4]=='l' else 'i23-r01';execution={vm:initial['execution_estimates_ms'][role]};budget=initial['path_budgets_ms']['25' if rtt=='unshaped' else rtt]['local' if vm[4]=='l' else 'remote']
   try:v.native(folder,name,trace,[vm],1,2000,execution,{vm:budget})
   finally:events=v.collect_servers(folder,before)
   raw=rows(folder/'requests.jsonl');physical=v.audit(raw,events,trace,2000)
   result=dict(vm=vm,rtt=rtt,physical_audit=physical,**context_estimate(raw,events,vm));p.save(folder/'audit.json',result);blocks.append(result)
   ops.heartbeat_gate(folder,'context',started,time.time_ns());print('CONTEXT_RESULT',vm,rtt,result['execution_ms'],result['network_ms'],result['caller_median_ms'],flush=True)
  ops.clocks('contexts-'+rtt+'-after-'+session)
 assert len(blocks)==9
 frozen={}
 for rtt in ['25','65']:
  selected=[b for b in blocks if b['rtt']=='unshaped' or b['rtt']==rtt]
  frozen[rtt]={b['vm']:dict(execution_ms=b['execution_ms'],network_ms=b['network_ms']) for b in selected};assert set(frozen[rtt])==set(VMS)
 result=dict(contexts=frozen,blocks=blocks,before_comparisons=True,method='unchanged archived prospective issue23 native-context40offer/ceilmedianservice/fullresidual rule; no relative goodput tuning')
 p.save(root/'frozen.json',result);return result

def matrix(capacity_data):
 root=p.OUT/'traces';root.mkdir(exist_ok=False);cells=[]
 for rtt in ['25','65']:
  for load in ['p080','p100','p120']:
   rate=capacity_data['rates'][load];name=f'rtt{rtt}-{load}';trace=root/(name+'.jsonl');jsonl(trace,[dict(id=f'issue29-{name}-{i:04d}',scheduled_offset_ns=round(i*1e9/rate)) for i in range(1000)])
   cells.append(dict(name=name,rtt=rtt,load=load,rate=rate,window_ms=math.ceil(1000*1000/rate),trace=str(trace),trace_sha256=p.digest(trace),arms=['tide',*(['tide-local-only'] if load=='p120' else []),'k0','kz','lf']))
 for pattern in ['gradual','burst']:
  data,window=generate(pattern,1,1);trace=root/(pattern+'.jsonl');jsonl(trace,data)
  cells.append(dict(name='original65-'+pattern,rtt='65',load=pattern,rate=1000/(window/1000),window_ms=window,trace=str(trace),trace_sha256=p.digest(trace),arms=['tide','k0','kz','lf']))
 assert sum(len(c['arms']) for c in cells)==34
 p.save(p.OUT/'matrix-frozen.json',dict(cells=cells,unix_ns=time.time_ns(),before_comparisons=True));return cells

def comparison(cell,arm,estimates,endpoints):
 name=cell['name']+'-'+arm;folder=p.OUT/'runs'/name;folder.mkdir(parents=True,exist_ok=False);state('comparison',run=name,cell=cell,arm=arm)
 trace=Path(cell['trace']);assert p.digest(trace)==cell['trace_sha256'];p.save(folder/'plan.json',dict(cell=cell,arm=arm,deadline_ms=135))
 ops.infrastructure(folder,'before');ops.clocks(name+'-before');before=v.server_offsets(VMS);started=time.time_ns()
 print('RUN_BEGIN',name,flush=True)
 try:
  if arm.startswith('tide'):
   nodes=LOCAL if arm=='tide-local-only' else VMS;chosen={vm:estimates[vm]['execution_ms'] for vm in nodes};network={vm:estimates[vm]['network_ms'] for vm in nodes}
   v.native(folder,name,trace,nodes,cell['rate'],135,chosen,network,cell['window_ms'])
  elif arm in ['k0','kz']:
   v.http(folder,name,trace,endpoints['wan-default' if arm=='k0' else 'wan-prefer-close'],cell['rate'],135,cell['window_ms'])
  elif arm=='lf':
   remote=ROOT+'/runs/'+name;p.guest(v.DRIVER,'mkdir -p '+remote);p.guest_copy(v.DRIVER,trace,remote+'/trace.jsonl')
   args=['python3',ROOT+'/issue23_lf_client.py','--trace',remote+'/trace.jsonl','--endpoints',*[ops.ip(vm)+':19383' for vm in VMS],'--local-count','3','--local-cap','2','--inputs',ROOT+'/inputs','--window-ms',str(cell['window_ms']),'--qos-ms','135','--channels','48','--out',remote+'/output']
   job=launch(v.DRIVER,True,remote+'/client',args,{},folder,'client')
   try:
    v.wait(job,name,cell['window_ms']/1000+120)
    for f in ['requests.jsonl','run.json']:p.guest_copy(v.DRIVER,remote+'/output/'+f,folder/f,pull=True)
   finally:stop_collect(job,folder,'client-job')
  else:raise ValueError(arm)
 finally:events=v.collect_servers(folder,before)
 raw=rows(folder/'requests.jsonl');summary=json.loads((folder/'run.json').read_text());assert summary['trace_sha256']==cell['trace_sha256']
 audit=v.audit(raw,events,trace,135);m=metrics(raw,summary['start_unix_ns'],cell['window_ms']);assert len(raw)==1000
 execution=audit['executed'];local=sum(n for vm,n in execution.items() if vm[4]=='l');remote=sum(n for vm,n in execution.items() if vm[4]=='r')
 result=dict(name=name,rtt_ms=int(cell['rtt']),load=cell['load'],arm=arm,**m,exec_local=local,exec_remote=remote,remote_timely=audit['timely_remote'],admission_rejections=audit['admission_rejections'],no_successful_server_execution=audit['no_successful_server_execution'],server_errors=audit['server_errors'],trace_sha256=cell['trace_sha256'],physical_audit=audit)
 p.save(folder/'audit.json',result)
 ops.infrastructure(folder,'after');ops.clocks(name+'-after');ops.heartbeat_gate(folder,'offered',started,time.time_ns())
 print('RUN_RESULT',json.dumps({k:result[k] for k in ['name','success','on_time','goodput_rps','exec_local','exec_remote','remote_timely','admission_rejections']}),flush=True)
 return result

def main(resume=False):
 results=[]
 try:
  if resume:
   assert not (p.OUT/'runs').exists(),'resume mode is only for precomparison collection recovery'
   endpoints=json.loads((p.OUT/'services/endpoints.json').read_text());c=json.loads((p.OUT/'calibration/capacity/frozen.json').read_text());ops.clocks('resume-before-'+str(time.time_ns()))
  else:ops.provision();endpoints=ops.services();ops.clocks('initial');c=capacity()
  e=contexts();cells=matrix(c)
  p.save(p.OUT/'parameters-frozen.json',dict(capacity=c,predictors=e,algorithm=json.loads((p.OUT/'plan.json').read_text())['fixed_algorithm'],unix_ns=time.time_ns(),before_comparisons=True))
  last=None
  for cell in cells:
   if last!=cell['rtt']:ops.shape(cell['rtt'],p.OUT/'comparisons-network'/cell['rtt']);last=cell['rtt']
   for arm in cell['arms']:
    result=comparison(cell,arm,e['contexts'][cell['rtt']],endpoints);results.append(result);p.save(p.OUT/'pilot-results-progress.json',dict(results=results,completed=len(results),expected=34))
  state('completed',completed=len(results));p.save(p.OUT/'campaign-final.json',dict(status='COMPLETE',completed=len(results),expected=34,unix_ns=time.time_ns()))
 except BaseException as error:
  p.save(p.OUT/'campaign-final.json',dict(status='STOPPED',completed=len(results),expected=34,error=str(error),traceback=traceback.format_exc(),unix_ns=time.time_ns()));state('stopped',error=str(error),completed=len(results));raise

if __name__=='__main__':
 import sys
 main('--resume-precomparison' in sys.argv)
