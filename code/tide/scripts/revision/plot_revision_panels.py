#!/usr/bin/env python3
"""Build A/C exclusively from audited measured records and saved model decisions."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics as st
from collections import defaultdict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import t


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p): return json.loads(p.read_text())
def csvrows(p):
    with p.open() as f: return list(csv.DictReader(f))
def check(ok,message):
    if not ok: raise ValueError(message)
def writecsv(p,rows):
    with p.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def meanerr(values):
    return st.mean(values),float(t.ppf(.975,len(values)-1)*st.stdev(values)/math.sqrt(len(values))) if len(values)>1 else 0
def errorline(ax,x,groups,**kw):
    means,errors=zip(*(meanerr(v) for v in groups));ax.errorbar(x,means,yerr=errors,capsize=3,marker='o',**kw)
def save(fig,out,name):
    for ext in ('pdf','png'):fig.savefig(out/f'{name}.{ext}',dpi=180,bbox_inches='tight')
    plt.close(fig)
def manifest(out,name,inputs,scope):
    (out/f'{name}_manifest.json').write_text(json.dumps(dict(evidence_scope=scope,script_sha256=sha(Path(__file__)),
        input_sha256={str(p):sha(p) for p in inputs},outputs_sha256={p.name:sha(p) for p in out.glob(name+'*') if p.suffix!='.json'}),indent=2)+'\n')
def checked_run(p):
    m=read(p/'manifest.json')
    check(m['status']=='completed' and m['exit_code']==0 and not m['timed_out'],f'failedrun{p}')
    for n,h in m['files_sha256'].items():check(sha(p/n)==h,f'hash mismatch {p/n}')
    return read(p/'run/run.json'),m


def ttl_data(root):
    directory=root/'ttl-matrix-01';m=read(directory/'matrix.json')
    check(m['status']=='completed' and len(m['jobs'])==9,'incomplete TTLmatrix')
    check(sha(directory/'revision-ttl')==m['binary_sha256'],'TTLbinaryhash')
    rows=[];requests=[];inputs=[directory/'matrix.json']
    check({(j['ttl_ms'],j['repeat']) for j in m['jobs']}=={(v,r) for v in (100,500,2000) for r in (1,2,3)},'TTLmatrixcoverage')
    for j in m['jobs']:
        p=directory/j['path'];run,outer=checked_run(p)
        check(j['status']=='completed' and run['status']=='completed','TTLstatus')
        raw=csvrows(p/'run/raw.csv');check({int(r['req_id']) for r in raw}==set(range(12)) and len(raw)==12,'TTLrequestIDs')
        check(all(int(r['status'])==200 for r in raw),'TTL contains failures; inspect before plotting')
        resources=[json.loads(v) for v in (p/'run/resources.jsonl').read_text().splitlines()]
        reserved=idle=idle_ns=0
        for a,b in zip(resources,resources[1:]):
            dt=b['t_ns']-a['t_ns'];check(dt>=0 and 0<=a['remaining_cpu']<=2,'TTLsampleinvalid')
            reserved+=(2-a['remaining_cpu'])*dt
            if a['post_burst_idle']:idle+=(2-a['remaining_cpu'])*dt;idle_ns+=dt
        check(reserved==run['allocation']['reserved_cpu_ns'] and idle==run['allocation']['idle_reserved_cpu_ns'] and idle_ns==run['allocation']['post_burst_idle_ns'],'TTLintegral mismatch')
        events=[json.loads(v) for v in (p/'run/events.jsonl').read_text().splitlines()]
        created=[e['detail']['stub_id'] for e in events if e['kind']=='stub_create_end']
        stopped=[e['detail']['stub_id'] for e in events if e['kind']=='stub_stop_handled']
        check(len(created)=={100:3,500:2,2000:1}[j['ttl_ms']] and sorted(created)==sorted(stopped),'TTLlifecycle')
        inventories=[e['detail']['owned_container_pids'] for e in events if e['kind']=='container_inventory']
        check(inventories and len(inventories[0])==1 and all(v==inventories[0] for v in inventories),'TTLworkerchanged')
        check(resources[-1]['remaining_cpu']==2 and resources[-1]['alive_stubs']==0,'TTLfinalreservation')
        later=[float(r['latency_ms']) for r in raw if int(r['req_id']) in (4,8)]
        rows.append(dict(ttl_ms=j['ttl_ms'],repeat=j['repeat'],requests=len(raw),creations=len(created),
                         idle_reserved_cpu_s=idle/1e9,idle_s=idle_ns/1e9,reserved_cpu_s=reserved/1e9,
                         later_burst_first_latency_ms=st.mean(later),max_heartbeat_gap_ms=run['allocation']['max_observed_heartbeat_gap_ns']/1e6))
        requests.extend([{**r,'ttl_ms':j['ttl_ms'],'repeat':j['repeat']} for r in raw])
        inputs.extend([p/'manifest.json',p/'run/run.json',p/'run/raw.csv',p/'run/resources.jsonl',p/'run/events.jsonl'])
    return rows,requests,inputs


def panel_c(root,out,ttl,inputs):
    directory=root/'weights-02';m=read(directory/'manifest.json');validation=read(directory/'validation.json')
    check(validation['verified_runs']==1980 and validation['verified_decisions']==934560 and validation['source_and_artifact_hashes_match'],'weights validation')
    for n,h in m['artifact_sha256'].items():check(sha(directory/n)==h,f'weights file {n}')
    raw=csvrows(directory/'summary.csv');check(len(raw)==1980,'weightsruns')
    groups=defaultdict(list)
    for r in raw:
        key=(int(r['offered_rate_rps']),r['network_profile'],round(float(r['weight_resource']),2),round(float(r['weight_latency']),2),round(float(r['weight_device']),2))
        groups[key].append(float(r['cohort_yield'])*100)
    summary=[]
    for (rate,net,a,b,c),v in sorted(groups.items()):
        check(len(v)==5,'weightrepcount');mean,err=meanerr(v)
        summary.append(dict(rps=rate,network=net,weight_resource=a,weight_latency=b,weight_device=c,mean_yield_pct=mean,ci95_halfwidth_pct=err,min_yield_pct=min(v),max_yield_pct=max(v)))
    fig=plt.figure(figsize=(12.5,10),layout='constrained');grid=fig.add_gridspec(3,3)
    axes=[]
    for row,net in enumerate(('low','high')):
        for col,rate in enumerate((10,20,40)):
            ax=fig.add_subplot(grid[row,col]);axes.append(ax)
            subset=[r for r in summary if r['rps']==rate and r['network']==net]
            # Barycentric coordinates: resource atleft,latencyatright,deviceattop.
            x=[r['weight_latency']+.5*r['weight_device'] for r in subset]
            y=[math.sqrt(3)/2*r['weight_device'] for r in subset]
            points=ax.scatter(x,y,c=[r['mean_yield_pct'] for r in subset],cmap='viridis',vmin=97,vmax=100,s=48)
            ax.plot([0,1,.5,0],[0,0,math.sqrt(3)/2,0],color='gray',lw=.7)
            ax.text(-.02,-.06,'Resource',ha='center',fontsize=8);ax.text(1.02,-.06,'Latency',ha='center',fontsize=8);ax.text(.5,.90,'Device',ha='center',fontsize=8)
            ax.set_title(f'Model: {net} network, {rate} req/s',fontsize=10)
            ax.set_aspect('equal');ax.set_xlim(-.15,1.15);ax.set_ylim(-.12,1);ax.axis('off')
    fig.colorbar(points,ax=axes,shrink=.75,label='Mean admitted / offered (%) — 5 saved traces per point')
    ax=fig.add_subplot(grid[2,:2]);xs=[100,500,2000]
    errorline(ax,xs,[[r['idle_reserved_cpu_s'] for r in ttl if r['ttl_ms']==x] for x in xs],color='#1768ac')
    ax.set_xscale('log');ax.set_xticks(xs,labels=xs);ax.set_xlabel('Runtime TTL (ms)');ax.set_ylabel('Idle reserved logical-CPU seconds')
    ax.set_title('Measured runtime: identical gaps 250 / 1000 / 2500 ms');ax.grid(alpha=.25)
    ax=fig.add_subplot(grid[2,2]);errorline(ax,xs,[[r['later_burst_first_latency_ms'] for r in ttl if r['ttl_ms']==x] for x in xs],color='#be6c21')
    ax.set_xscale('log');ax.set_xticks(xs,labels=xs);ax.set_xlabel('Runtime TTL (ms)');ax.set_ylabel('Instrumented first-response latency (ms)');ax.set_title('Later bursts: reuse / recreation');ax.grid(alpha=.25)
    fig.suptitle('C. Weight sensitivity (restricted simulation) and TTL (real runtime)\nTTL: mean ± 95% t CI across 3 runs; container retained after stub expiration',fontsize=12)
    save(fig,out,'figC_sensitivity');writecsv(out/'figC_weights.csv',summary);writecsv(out/'figC_ttl.csv',ttl)
    caption='Upper panels: restricted ideal-estimate online simulation, 66 weight combinations, 3 loads, 2 stipulated network profiles, 5 shared traces. Accepted tasks meet the deadline by construction; color is admission yield, not observed reliability. Lower panels: actual Tide SubmitRoutine/PAIX bursts, 3 runs per TTL, observed heartbeat reservation integral. Post-burst idle gaps 250/1000/2500 ms are shared; TTL 100/500/2000 ms produce 3/2/1 stubs respectively while preserving one worker container. Error bars are run-level 95% t CI. Later-burst first-response latency includes local stub recreation, a 5 ms settle and container-inventory RPCs when recreated; this is instrumented driver latency, not pure cold-start cost.\n'
    (out/'figC_caption.txt').write_text(caption)
    manifest(out,'figC',inputs+[directory/'manifest.json',directory/'validation.json',directory/'summary.csv'],caption)


def panel_a(root,out,ttlrequests,ttlinputs):
    analysis=root/'workload-analysis-final';audit=read(analysis/'audit.json')
    check(audit['status']=='passed' and audit['total_runs']==135,'workloadanalysis')
    for n,h in audit['outputs_sha256'].items():check(sha(analysis/n)==h,f'analysisoutput{n}')
    runrows=csvrows(analysis/'per_run.csv')
    control=root/'control-matrix-01';m=read(control/'matrix.json')
    check(m['status']=='completed' and len(m['jobs'])==9,'controlmatrix')
    check(sha(control/'revision-ipc')==m['binary_sha256'] and sha(control/'revision-worker')==m['worker_sha256'],'control binaries')
    ipc=[];inputs=[analysis/'audit.json',analysis/'per_run.csv',control/'matrix.json',control/'ranking_benchmark.txt']+ttlinputs
    for j in m['jobs']:
        p=control/j['path'];run,_=checked_run(p);raw=csvrows(p/'run/raw.csv')
        check(len(raw)==3050 and {int(r['request_id']) for r in raw}==set(range(3050)),'IPCcount')
        check(all(r['phase']==('warmup' if int(r['request_id'])<50 else 'measured') and int(r['round_trip_ns'])>0 and int(r['worker_service_ns'])>0 for r in raw),'IPCphase')
        measured=[r for r in raw if r['phase']=='measured']
        ipc.append(dict(padding_bytes=j['padding_bytes'],repeat=j['repeat'],request_wire_bytes_mean=st.mean(int(r['request_wire_bytes']) for r in measured),rtt_mean_us=st.mean(int(r['round_trip_ns'])/1000 for r in measured),rtt_p95_us=float(np.quantile([int(r['round_trip_ns'])/1000 for r in measured],.95)),roundtrips_per_second=3000/(run['measurement_duration_ns']/1e9)))
        inputs.extend([p/'manifest.json',p/'run/run.json',p/'run/raw.csv'])
    bench=(control/'ranking_benchmark.txt').read_text();check(sha(control/'ranking_benchmark.txt')==m['ranking_output_sha256'],'benchmarkhash')
    ranks=[dict(candidates=int(a),iterations=int(b),ns_per_op=float(c)) for a,b,c in re.findall(r'BenchmarkRevisionRank/(\d+)-\d+\s+(\d+)\s+([\d.]+) ns/op',bench)]
    check(len(ranks)==20,'rankbenchmarkcount')
    fig,axs=plt.subplots(2,3,figsize=(14,8),layout='constrained')
    for kind,color in zip(('cpu','memory','io'),('#1768ac','#be6c21','#478c58')):
        xs=[5,10,20,40]
        subset=[r for r in runrows if r['system']=='tide' and r['workload']==kind]
        for ax,field,label in [(axs[0,0],'controller_cpu_pct_1cpu','Controller CPU (% of one logical CPU)'),(axs[0,1],'controller_rss_mean_mib','Controller RSS (MiB)')]:
            errorline(ax,xs,[[float(r[field]) for r in subset if int(r['rps'])==x] for x in xs],label=kind,color=color)
            ax.set_xlabel('Offered requests / s');ax.set_ylabel(label);ax.grid(alpha=.25)
    axs[0,0].set_title('(a) Tide harness + controller');axs[0,0].legend(fontsize=8)
    axs[0,1].set_title('(b) Process memory (workers excluded)')
    cold=[float(r['stub_initial_executor_create_ms']) for r in runrows if r['system']=='tide' and r['workload']!='mixed-qos']
    warm=[int(r['stub_create_ns'])/1e6 for r in ttlrequests if r['creation_kind']=='stub_rebind_existing_executor']
    ax=axs[0,2];ax.boxplot([cold,warm],tick_labels=['Initial stub\n+ executor','Stub rebind\nretained executor'],showfliers=True);ax.set_ylabel('CreateStub elapsed time (ms)');ax.set_title('(c) Measured creation paths (all events)');ax.grid(axis='y',alpha=.25)
    ax=axs[1,0];xs=[4,16,64,256];errorline(ax,xs,[[r['ns_per_op']/1000 for r in ranks if r['candidates']==x] for x in xs]);ax.set_xscale('log',base=4);ax.set_xticks(xs,labels=xs);ax.set_ylabel('Filter + rank hook (µs / call)');ax.set_xlabel('Feasible candidates');ax.set_title('(d) Runtime hook; 5 benchmark repetitions');ax.grid(alpha=.25)
    ax=axs[1,1];xs=[64,1024,4096];errorline(ax,xs,[[r['roundtrips_per_second']/1000 for r in ipc if r['padding_bytes']==x] for x in xs],color='#478c58');ax.set_xscale('log');ax.set_xticks(xs,labels=xs);ax.set_xlabel('Request JSON padding bytes');ax.set_ylabel('Thousand round trips / s');ax.set_title('(e) Two-process PAIX + minimal kernel');ax.grid(alpha=.25)
    matched=[]
    for kind in ('cpu','memory','io'):
        for repeat in (1,2,3):
            p=root/'workload-matrix-01'/kind/'rps5'/f'run{repeat}'
            _,outer=checked_run(p);raw=csvrows(p/'run/raw.csv');check(all(int(r['status'])==200 for r in raw),'lowloadfailed')
            e2e=st.mean(float(r['latency_ms']) for r in raw);service=st.mean(int(r['worker_service_ns'])/1e6 for r in raw)
            matched.append(dict(workload=kind,repeat=repeat,e2e_ms=e2e,kernel_ms=service,outside_kernel_ms=e2e-service,submit_call_us=st.mean(int(r['submit_call_ns'])/1000 for r in raw)))
            inputs.extend([p/'manifest.json',p/'run/raw.csv'])
    ax=axs[1,2];kinds=['cpu','memory','io'];kernel=[st.mean(r['kernel_ms'] for r in matched if r['workload']==k) for k in kinds];other=[st.mean(r['outside_kernel_ms'] for r in matched if r['workload']==k) for k in kinds]
    ax.bar(kinds,kernel,label='worker kernel');ax.bar(kinds,other,bottom=kernel,label='outside kernel');ax.set_ylabel('Matched mean response time (ms)');ax.set_title('(f) Normal requests @ 5 req/s');ax.legend(fontsize=8);ax.grid(axis='y',alpha=.25)
    fig.suptitle('A. Measured controller, lifecycle, ranking and PAIX costs\nLines: run-level mean ± 95% t CI. Outside-kernel time includes queueing, IPC and runtime work.',fontsize=12)
    save(fig,out,'figA_ctrl_plane');writecsv(out/'figA_ipc.csv',ipc);writecsv(out/'figA_ranking.csv',ranks);writecsv(out/'figA_matched.csv',matched)
    lifecycle=[dict(kind='initial_stub_executor',time_ms=v) for v in cold]+[dict(kind='rebind_retained_executor',time_ms=v) for v in warm];writecsv(out/'figA_lifecycle.csv',lifecycle)
    caption='Actual Tide controller process CPU/RSS over 3 repetitions per workload/load; worker processes excluded. Initial CreateStub includes the first executor; TTL rebind reuses a running retained container. Ranking is the actual filter/rank hook (5 benchmark repetitions), not full asynchronous scheduling. PAIX is real protobuf/POSIX mqueue between two OS processes using the same worker and a minimal 8-byte SHA256 kernel, 3000 measured after 50 warmup exchanges per run, 3 runs per padding. Matched kernel/e2e time uses every successful 5 req/s request; outside-kernel time includes waiting, IPC, control and runtime work and is not pure control-plane share. Raw synchronous SubmitRoutine call times are reported separately in CSV.\n'
    (out/'figA_caption.txt').write_text(caption);manifest(out,'figA',inputs,caption)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--results',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    rows,requests,inputs=ttl_data(a.results)
    panel_c(a.results,a.out,rows,inputs);panel_a(a.results,a.out,requests,inputs)
    print('A/C generated from audited records; no generated outcomes')
