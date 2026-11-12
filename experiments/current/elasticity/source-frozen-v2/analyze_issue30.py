"""Audits every offered ID, resource sample and all complete paired runs."""
from issue30_transport import *
from issue30_measurement import *
import csv

def analyze_run(item,plan):
 run=item['run'];root=OUT/'runs'/run/'NJ-k1/measurement';meta=json.loads((root/'run.json').read_text());trace=OUT/plan['traces'][str(item['block'])]['path'];raw=trace.read_bytes();assert hashlib.sha256(raw).hexdigest()==meta['trace_sha256']==plan['traces'][str(item['block'])]['sha256']
 ids=[json.loads(r)['id'] for r in raw.splitlines()];requests=[json.loads(s) for s in (root/'requests.jsonl').read_text().splitlines()];summary=summarize_requests(ids,requests,350,60,INPUT,GOLD);assert summary==meta['summary']
 resources=[extract_allocation(json.loads(s)) for s in (root/'resources.jsonl').read_text().splitlines()];start=meta['start_monotonic_ns']
 for r in resources:r['time_s']=(r['end_ns']-start)/1e9
 baseline=[r['allocated_cpus'] for r in resources if r['valid'] and -10<=r['time_s']<0];assert len(baseline)>5 and len(set(baseline))==1,'unstable allocation baseline'
 baseline=baseline[0];values=additional_allocation(resources,baseline)
 for r,v in zip(resources,values):r['additional_cpus']=v
 valid=[r for r in resources if r['valid']];maximum=max(r['additional_cpus'] for r in valid);post=[r for r in valid if r['time_s']>=0];up=next((r for r in post if r['additional_cpus']>0),None)
 returned=None
 later=[r for r in valid if r['time_s']>=60]
 positive=[r for r in later if r['additional_cpus']>0]
 last_positive=positive[-1]['time_s'] if positive else 60
 tail=[r for r in later if r['time_s']>last_positive]
 if tail and tail[-1]['time_s']-tail[0]['time_s']>=10 and all(r['additional_cpus']==0 for r in tail):returned=tail[0]['time_s']-60
 last_positive_before=next((r for r in reversed(valid) if up is not None and r['time_s']<up['time_s'] and r['additional_cpus']==0),None)
 audit=dict(run=run,block=item['block'],arm=item['arm'],trace_sha256=meta['trace_sha256'],baseline_allocated_cpus=baseline,peak_additional_cpus=maximum,first_increase_s=up['time_s'] if up else None,first_increase_previous_sample_s=last_positive_before['time_s'] if last_positive_before else None,return_to_baseline_after_stop_s=returned,recovery_right_censored=returned is None,observation_last_s=valid[-1]['time_s'],resource_samples=len(resources),invalid_resource_samples=len(resources)-len(valid),max_resource_gap_s=max((b['time_s']-a['time_s'] for a,b in zip(valid,valid[1:])),default=0),summary=summary)
 if item['arm']=='hpa':
  decisions=[r for r in valid if r.get('desired_replicas') is not None and r['desired_replicas']>1 and r['time_s']>=0];ready=[r for r in valid if r.get('ready_cpus',0)>baseline and r['time_s']>=0]
  audit.update(first_hpa_decision_s=decisions[0]['time_s'] if decisions else None,first_ready_increase_s=ready[0]['time_s'] if ready else None,pod_restarts=max(r['restarts'] for r in valid))
 else:
  audit.update(peak_committed_cpus=max(r['committed_cpus'] for r in valid),peak_borrowed_cpus=max(r['borrowed_cpus'] for r in valid))
 save(OUT/'runs'/run/'audit.json',audit);save(OUT/'runs'/run/'allocation.json',resources);return audit,resources

def main():
 plan=json.loads((OUT/'plan.json').read_text());allruns=[];timelines={}
 for item in plan['order']:
  audit,resources=analyze_run(item,plan);allruns.append(audit);timelines[item['run']]=resources
 data=dict(protocol=plan,runs=allruns,timelines=timelines,selection='All three pre-registered complete paired blocks; no best-run selection',ordinate='additional exclusively reserved application logical CPUs relative to own stable baseline; not utilization')
 save(OUT/'plot_data.json',data);save(OUT/'audit.json',dict(status='PASS',runs=allruns))
 with (OUT/'summary.csv').open('w',newline='') as f:
  fields=['run','block','arm','baseline_allocated_cpus','peak_additional_cpus','first_increase_s','return_to_baseline_after_stop_s','recovery_right_censored','offered','success','on_time','goodput_rps','yield_fraction'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for a in allruns:w.writerow({k:(a[k] if k in a else a['summary'][k]) for k in fields})
 print(json.dumps([{k:v for k,v in r.items() if k!='summary'}|r['summary'] for r in allruns],indent=2))
if __name__=='__main__':main()
