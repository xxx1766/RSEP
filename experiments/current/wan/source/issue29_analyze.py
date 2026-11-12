"""Analyze every recorded pilot cell without changing experimental parameters."""
import csv
import json
import math
from pathlib import Path
import statistics
import issue29_common as p

LABELS={'tide':'Tide','k0':'K8s default Service','kz':'K8s PreferClose','lf':'Custom local-first','tide-local-only':'Tide local-only ablation'}

def load(path):return json.loads(path.read_text())
def read_rows(path):return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
def fraction(a,b):return a/b if b else None
def fmt(x,digits=2):return 'N/A' if x is None else f'{x:.{digits}f}'

def analyze():
 out=p.OUT;matrix=load(out/'matrix-frozen.json')['cells'];records=[];missing=[]
 progress=load(out/'pilot-results-progress.json') if (out/'pilot-results-progress.json').exists() else {'results':[]}
 valid_names={r['name'] for r in progress['results']}
 for cell in matrix:
  for arm in cell['arms']:
   name=cell['name']+'-'+arm;folder=out/'runs'/name
   if not (folder/'audit.json').exists():
    missing.append(dict(name=name,state='incomplete' if folder.exists() else 'not_run'));continue
   a=load(folder/'audit.json');raw=read_rows(folder/'requests.jsonl')
   assert len(raw)==1000 and len({r['id'] for r in raw})==1000
   assert a['trace_sha256']==cell['trace_sha256']==p.digest(out/'traces'/Path(cell['trace']).name)
   a['infrastructure_qualified']=name in valid_names
   a['label']=LABELS[arm];a['exec_local_fraction']=fraction(a['exec_local'],a['exec_local']+a['exec_remote'])
   a['remote_timely_fraction']=fraction(a['remote_timely'],a['exec_remote'])
   a['late_success']=a['success']-a['cohort_on_time']
   a['unclassified_no_execution']=a['no_successful_server_execution']-a['admission_rejections']-a['server_errors']
   a['latency_p50_ms']=a['physical_audit']['median_ms'];a['latency_p95_ms']=a['physical_audit']['p95_ms']
   events=[]
   for vm in ['i23-l01','i23-l02','i23-l03','i23-r01','i23-r02','i23-r03']:
    f=folder/(vm+'-server.jsonl')
    if f.exists():events.extend(dict(e,executing_vm=vm) for e in read_rows(f))
   ids={r['id']:r for r in raw};matched=[e for e in events if e['request_id'] in ids]
   for role,prefix in [('local','i23-l'),('remote','i23-r')]:
    a[role+'_primary_timely']=sum(e['executing_vm'].startswith(prefix) and ids[e['request_id']]['status']=='success' and ids[e['request_id']]['latency_ms']<=135 and ids[e['request_id']]['reply_unix_ns']<=a['global_window_end_ns'] for e in matched)
   assert a['local_primary_timely']+a['remote_primary_timely']==a['on_time']
   timed=[e for e in matched if 'service_ns' in e and 'queue_ns' in e]
   a['server_service_median_ms']=statistics.median(e['service_ns']/1e6 for e in timed) if timed else None
   a['server_queue_median_ms']=statistics.median(e['queue_ns']/1e6 for e in timed) if timed else None
   a['remote_missed_deadline']=sum(e['executing_vm'].startswith('i23-r') and (ids[e['request_id']]['status']!='success' or ids[e['request_id']]['latency_ms']>135) for e in matched)
   a['local_missed_deadline']=sum(e['executing_vm'].startswith('i23-l') and (ids[e['request_id']]['status']!='success' or ids[e['request_id']]['latency_ms']>135) for e in matched)
   by_destination={}
   for role,prefix in [('local','i23-l'),('remote','i23-r')]:
    selected=[e for e in matched if e['executing_vm'].startswith(prefix)];timed_role=[e for e in selected if 'service_ns' in e and 'queue_ns' in e]
    by_destination[role]=dict(executed=len(selected),service_median_ms=statistics.median(e['service_ns']/1e6 for e in timed_role) if timed_role else None,queue_median_ms=statistics.median(e['queue_ns']/1e6 for e in timed_role) if timed_role else None)
   a['service_queue_by_destination']=by_destination
   a['observed_heartbeat_gate_files']=[f.name for f in folder.glob('*heartbeat*.json')]
   records.append(a)
 fields=['name','infrastructure_qualified','rtt_ms','load','arm','offered','success','on_time','cohort_on_time','yield_fraction','goodput_rps','exec_local','exec_remote','exec_local_fraction','local_primary_timely','remote_primary_timely','remote_timely','remote_missed_deadline','local_missed_deadline','admission_rejections','no_successful_server_execution','unclassified_no_execution','server_errors','late_success','latency_p50_ms','latency_p95_ms','server_service_median_ms','server_queue_median_ms','emission_lateness_median_ms','emission_lateness_max_ms','arrival_window_ms','observation_window_ms','trace_sha256']
 with (out/'summary.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fields,extrasaction='ignore');w.writeheader();w.writerows(records)
 comparisons=[]
 for cell in matrix:
  selected={r['arm']:r for r in records if r['infrastructure_qualified'] and r['name'].startswith(cell['name']+'-') and r['load']==cell['load'] and r['rtt_ms']==int(cell['rtt'])}
  tide=selected.get('tide');ratios={arm:fraction(tide['goodput_rps'],r['goodput_rps']) if tide else None for arm,r in selected.items() if arm not in ['tide','tide-local-only']}
  comparisons.append(dict(condition=cell['name'],rtt_ms=int(cell['rtt']),load=cell['load'],ratios_tide_over=ratios,block_interrupted=cell['name']=='original65-gradual',ratio_qualification='exploratory; LF follows a long collection interruption' if cell['name']=='original65-gradual' else 'single-repeat block',arms={k:{f:v[f] for f in ['goodput_rps','on_time','success','exec_local_fraction','exec_remote','remote_timely','remote_primary_timely','admission_rejections']} for k,v in selected.items()},ablation_full_over_local_only=fraction(tide['goodput_rps'],selected['tide-local-only']['goodput_rps']) if tide and 'tide-local-only' in selected else None))
 final=load(out/'campaign-final.json') if (out/'campaign-final.json').exists() else dict(status='RUNNING')
 result=dict(status=final,observed_runs=len(records),planned_runs=34,missing_runs=missing,comparisons=comparisons,records=records)
 p.save(out/'pilot_summary.json',result)
 capacity=load(out/'calibration/capacity/frozen.json');contexts=load(out/'calibration/contexts/frozen.json')
 report=['# 3+3 WAN pilot: single-repeat data', '',f"Status: {final['status']}; recorded {len(records)}/34 cells. Each cell has 1000 requests and a 135 ms deadline.",'',
 'This pilot uses NJ-k1 and NJ-k2 as hosts, each running three 1-vCPU/2-GiB nested VMs; NJ-k3 runs the control plane and the request generator. The NJ hosts are themselves VMs. The pilot examines six isolated compute nodes; it is not equivalent to six independent physical edge machines, nor a reproduction of the earlier large-scale experiments.', '',
 'Each compute VM keeps the node-pinned YOLOX-Nano Pod from the original VM experiment: CPU request/limit 900m/1 core and memory request/limit 512 MiB/1 GiB. This differs from the earlier E5 configuration with two 2-CPU Pods, so old and new ratios must not be treated as repeated measurements of the same experiment.', '',
 f"The measured three-node local capacity is {capacity['local_capacity_rps']:.4f} requests/s (median of three independent saturation calibrations); the 80%/100%/120% loads are {capacity['rates']['p080']}/{capacity['rates']['p100']}/{capacity['rates']['p120']} requests/s. The 25/65 ms targets are shaped measured RTTs, not real cross-region deployments.",'',
 'Baselines: the K8s default Service uses a real ClusterIP; K8s PreferClose uses real topology hints and local forwarding. Custom local-first is an independent client reference policy with an in-flight threshold of 2 per local endpoint, overflowing to remote endpoints when full, and no deadline knowledge or admission rejection; it is not a native K8s scheduling policy. Tide uses the frozen binary and the public SubmitExternal/Stub path; algorithm weights, TTL and concurrency limits are not adjusted based on comparison results.', '',
 'This is a full-system path comparison: Tide native scheduling, receive notification and result retrieval protocol overhead are included in end-to-end latency; K8s and local-first use HTTP application paths. Differences between systems therefore include control/transport overhead and cannot be attributed entirely to placement algorithms. The local-only ablation is an internal Tide control with reduced available capacity, not a capacity-matched fair baseline.', '',
 'The primary metric is the number of requests that return correctly, have per-request latency at most 135 ms, and return within the common arrival window + 135 ms, divided by that common window. The table uses this metric; per-request cohort timeliness and emission lateness are stored separately in CSV so different tail-drain times do not change the denominator. Every actual execution is cross-checked by server request ID, input/output SHA-256 and checksum.', '',
 '| Condition | System | On time/1000 | goodput (req/s) | Local execution share | Remote executed/on time | Admission rejections |', '|---|---|---:|---:|---:|---:|---:|']
 for r in records:
  label=r['label']+(' [gate failed/incomplete]' if not r['infrastructure_qualified'] else '')
  report.append(f"| {r['rtt_ms']}ms/{r['load']} | {label} | {r['on_time']} | {r['goodput_rps']:.2f} | {fmt(None if r['exec_local_fraction'] is None else 100*r['exec_local_fraction'])}% | {r['exec_remote']}/{r['remote_primary_timely']} | {r['admission_rejections']} |")
 report+=['','## Ratios and attribution','', '| Condition | Tide/default | Tide/PreferClose | Tide/local-first | Tide/local-only ablation |','|---|---:|---:|---:|---:|']
 for c in comparisons:
  ratio=c['ratios_tide_over'];report.append(f"| {c['condition']} | {fmt(ratio.get('k0'),3)} | {fmt(ratio.get('kz'),3)} | {fmt(ratio.get('lf'),3)} | {fmt(c['ablation_full_over_local_only'],3)} |")
 report+=['', '## Queueing and execution diagnostics','', '| Condition | System | Server service median (ms) | Server queue median (ms) | End-to-end median (ms) | Local timeout/remote timeout |','|---|---|---:|---:|---:|---:|']
 for r in records:
  if r['load'] in ['p100','p120']:
   report.append(f"| {r['rtt_ms']}ms/{r['load']} | {r['label']} | {fmt(r['server_service_median_ms'])} | {fmt(r['server_queue_median_ms'])} | {fmt(r['latency_p50_ms'])} | {r['local_missed_deadline']}/{r['remote_missed_deadline']} |")
 report+=['','service is the wall-clock duration during which the server holds the single inference lock, including shared-VM CPU contention; queue is the waiting time before that lock. A change in service alone cannot prove a change in model computation. Medians cannot replace tail or per-request causal decomposition.','']
 report+=['', '## Path boundary (independent calibration before comparisons)','', '| Node | RTT condition | app service median (ms) | Full-path residual median (ms) | End-to-end median (ms) | Within 135 ms/40 |','|---|---|---:|---:|---:|---:|']
 path_diagnostics=[]
 for b in contexts['blocks']:
  report.append(f"| {b['vm']} | {b['rtt']} | {b['service_median_ms']:.2f} | {b['path_residual_median_ms']:.2f} | {b['caller_median_ms']:.2f} | {b['within135']} |")
  raw=read_rows(out/'calibration/contexts'/f"context-{b['vm']}-{b['rtt']}"/'requests.jsonl')
  received=[r for r in raw if r.get('receive_unix_ns',0) and r['status']=='success']
  path_diagnostics.append(dict(vm=b['vm'],rtt=b['rtt'],submit_to_receiver_median_ms=statistics.median((r['receive_unix_ns']-r['submit_unix_ns'])/1e6 for r in received),receiver_to_return_median_ms=statistics.median((r['reply_unix_ns']-r['receive_unix_ns'])/1e6 for r in received),caller_median_ms=b['caller_median_ms'],within135=b['within135'],offered=40))
 p.save(out/'path_diagnostics.json',dict(source='unchanged pre-comparison calibration; both phase endpoints use the origin clock; medians of phases need not sum to median total',contexts=path_diagnostics))
 report+=['', 'The path residual includes native request/response protocol, serialization and network round-trip overhead; ping RTT cannot be substituted directly. Whether remote capacity increases on-time service should be judged together with the number of remote on-time returns and the full/local-only ablation; Tide beating a default Service with more remote traffic alone does not prove that cross-region resources create benefit. If all systems execute locally, remaining differences should be explained by queueing and admission.', '',
 'After gradual/PreferClose completed, result-directory SCP collection was interrupted; the original 1000 requests, server events and exit0 state were all recovered and not rerun (interruptions/002-*). After the same file succeeded with traditional SCP, only the transfer option changed to scp -O; experimental parameters did not change. The original gradual block therefore has a long gap between its last local-first arm and the first three arms; that block is not continuous and its ratios are exploratory, not a strict reproduction of a continuous session. The first 26 cells completed before this interruption.', '', '## Integrity and limitations','', 'Each condition currently has one repeat and no confidence interval; the best repeat is not selected. Every completed, failed and unrun cell is retained. The initial QMP local escaping error and the SSH read-only collection interruption during calibration are preserved in setup-attempts/ and interruptions/; the interrupted original 40 requests were recovered by ID from the original job without resending requests. Any subsequent infrastructure gate failure must be separated from application performance results; invalid cells are not evidence of benefit.', '',
 f"Unfinished cells: {', '.join(x['name']+' ('+x['state']+')' for x in missing) or 'none'}.", '',
 'Raw evidence: plan.json / parameters-frozen.json / matrix-frozen.json / runs/*/{requests.jsonl,run.json,audit.json}; the trace SHA-256 of every cell is in summary.csv. This pilot does not automatically update the manuscript or fill gaps with older results.','']
 (out/'REPORT.md').write_text('\n'.join(report))
 plot([r for r in records if r['infrastructure_qualified']],out)
 print(json.dumps(dict(status=final['status'],observed=len(records),missing=len(missing),comparisons=[{k:v for k,v in c.items() if k!='arms'} for c in comparisons]),ensure_ascii=False))

def plot(records,out):
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 plt.rcParams.update({'font.size':8,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42})
 fig,axes=plt.subplots(1,2,figsize=(6.9,2.65),sharey=True)
 colors={'tide':'#2166ac','k0':'#b2182b','kz':'#ef8a62','lf':'#4d9221'}
 for ax,rtt in zip(axes,[25,65]):
  for arm in ['tide','k0','kz','lf']:
   chosen={r['load']:r for r in records if r['rtt_ms']==rtt and r['arm']==arm}
   ys=[chosen[x]['goodput_rps'] if x in chosen else math.nan for x in ['p080','p100','p120']]
   ax.plot([80,100,120],ys,label=LABELS[arm],marker='o',markersize=3,linewidth=1,color=colors[arm])
  ab=[r for r in records if r['rtt_ms']==rtt and r['arm']=='tide-local-only']
  if ab:ax.scatter([120],[ab[0]['goodput_rps']],marker='x',s=28,color='black',label='Tide local-only ablation')
  ax.set_title(f'RTT {rtt} ms',fontsize=9);ax.set_xlabel('Offered load / local capacity (%)');ax.set_xticks([80,100,120]);ax.grid(axis='y',alpha=.2);ax.set_ylim(bottom=0)
 axes[0].set_ylabel('On-time goodput (requests/s)')
 handles,labels=axes[0].get_legend_handles_labels();fig.legend(handles,labels,ncol=3,loc='upper center',frameon=False,fontsize=7.5)
 fig.tight_layout(rect=[0,0,1,.79],pad=.6);fig.savefig(out/'pilot_goodput.pdf',bbox_inches='tight');fig.savefig(out/'pilot_goodput.png',dpi=200,bbox_inches='tight');plt.close(fig)
 fig,ax=plt.subplots(figsize=(4.5,2.55))
 for index,arm in enumerate(['tide','k0','kz','lf']):
  ys=[]
  for pattern in ['gradual','burst']:
   matches=[r for r in records if r['rtt_ms']==65 and r['load']==pattern and r['arm']==arm]
   ys.append(matches[0]['goodput_rps'] if matches else math.nan)
  ax.bar([x+(index-1.5)*.18 for x in [0,1]],ys,width=.18,label=LABELS[arm],color=colors[arm])
 ax.set_xticks([0,1],['Original gradual','Original burst']);ax.set_ylabel('On-time goodput (requests/s)');ax.set_ylim(bottom=0);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
 handles,labels=ax.get_legend_handles_labels();fig.legend(handles,labels,ncol=2,loc='upper center',frameon=False,fontsize=7.5)
 fig.tight_layout(rect=[0,0,1,.80],pad=.6);fig.savefig(out/'original_trace_goodput.pdf',bbox_inches='tight');fig.savefig(out/'original_trace_goodput.png',dpi=200,bbox_inches='tight');plt.close(fig)

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=p.OUT);args=parser.parse_args();p.OUT=args.out.resolve();analyze()
