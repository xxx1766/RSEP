#!/usr/bin/env python3
"""Audit raw control-plane experiment and generate run-level summary tables."""
import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import statistics as st
from control_metrics import summarize_samples

def read_csv(p):
    with p.open() as f: return list(csv.DictReader(f))

def write_csv(p, rows):
    with p.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--root',type=Path,required=True); p.add_argument('--partial',action='store_true'); a=p.parse_args()
    manifest=json.loads((a.root/'progress.json').read_text())
    if not a.partial and manifest['status']!='completed': raise ValueError('matrix is incomplete')
    output=a.root/'summary'; output.mkdir(exist_ok=True)
    runs=[]; components=[]; audit=[]; inventory={}
    for job in manifest['jobs']:
        block=a.root/f"{job['system']}-r{job['repeat']}"
        paths=[block/'idle']+[block/f'rps{r}-ddl{d}' for r,d in job['cells']]
        if not (block/'setup.json').exists() and a.partial: continue
        setup=json.loads((block/'setup.json').read_text())
        for root in paths:
            if not (root/'measurement.json').exists() and a.partial: continue
            m=json.loads((root/'measurement.json').read_text())
            samples=sorted([json.loads(s) for s in (root/'samples.jsonl').read_text().splitlines()],key=lambda s:s['monotonic_s'])
            rows=read_csv(root/'requests.csv') if m['rate'] else []
            if len(rows)!=m['requests'] or len(rows)!=int(m['rate']*m['duration_s']): raise ValueError(f'count mismatch {root}')
            if {int(r['req_id']) for r in rows}!=set(range(len(rows))): raise ValueError('request ID mismatch')
            if any(int(r['ddl_ms'])!=m['ddl_ms'] for r in rows): raise ValueError('DDL mismatch')
            for row in rows:
                if int(row['status'])==200 and (int(row['bytes_processed'])!=6553600 or int(row['worker_service_ns'])<=0): raise ValueError('bad kernel result')
            groups=summarize_samples(samples,m['start_monotonic_s'],m['arrival_end_monotonic_s'],m['hz'])
            expected={'tide':{'core','application','runtime_support'},'ray':{'core','request_path','application'},'kubeedge':{'edgecore','cloudcore','dedicated_cluster_support','application'}}[job['system']]
            if not expected.issubset(groups): raise ValueError(f'missing processes {expected-set(groups)}')
            for s in samples:
                for proc in s['processes']:
                    inventory[(job['system'],proc['pid'],proc['start'])]=dict(system=job['system'],**{k:proc[k] for k in ('pid','start','group','comm','cmd','cgroup')})
            core_groups={'tide':['core'],'ray':['core'],'kubeedge':['edgecore','cloudcore']}[job['system']]
            framework=core_groups+(['request_path'] if job['system']=='ray' else [])
            stack=[g for g in groups if g not in ('application','shared_host_runtime')]
            for name,parts in [('framework',framework),('stack_ex_application',stack)]:
                groups[name]={metric:sum(groups[g][metric] for g in parts) for metric in ('cpu_pct','cpu_s','rss_mib','pss_mib','missing_cpu_intervals','missing_pss')}
                groups[name]['seconds']=min(groups[g]['seconds'] for g in parts)
            full_groups=summarize_samples(samples,m['start_monotonic_s'],m['end_monotonic_s'],m['hz'])
            full_framework_cpu_s=sum(full_groups[g]['cpu_s'] for g in framework)
            order=0 if not m['rate'] else [tuple(c) for c in job['cells']].index((m['rate'],m['ddl_ms']))+1
            base=dict(system=job['system'],repeat=job['repeat'],rps=m['rate'],ddl_ms=m['ddl_ms'] if m['rate'] else 0,
                      order_in_block=order,prior_high_pressure_cells=sum(c[0]==80 for c in job['cells'][:max(0,order-1)]),
                      started_unix_ns=m['start_unix_ns'])
            for group,values in groups.items(): components.append(base|dict(group=group,**values))
            successes=[r for r in rows if int(r['status'])==200]
            ontime=[r for r in successes if float(r['latency_ms'])<=m['ddl_ms']]
            drain=max(m['duration_s'],m['end_monotonic_s']-m['start_monotonic_s'])
            groups_fw=groups['framework']; groups_stack=groups['stack_ex_application']
            lag=sorted((float(r['submit_s'])-float(r['planned_s']))*1000 for r in rows)
            run=base|dict(requests=len(rows),successes=len(successes),on_time=len(ontime),
                goodput=len(ontime)/drain,throughput=len(successes)/drain,drain_s=drain,
                arrival_lag_p99_ms=lag[max(0,math.ceil(.99*len(lag))-1)] if lag else 0,
                framework_cpu_pct=groups_fw['cpu_pct'],framework_rss_mib=groups_fw['rss_mib'],framework_pss_mib=groups_fw['pss_mib'],
                stack_cpu_pct=groups_stack['cpu_pct'],stack_rss_mib=groups_stack['rss_mib'],stack_pss_mib=groups_stack['pss_mib'],
                application_cpu_pct=groups['application']['cpu_pct'],
                framework_cpu_ms_per_offered=full_framework_cpu_s*1000/len(rows) if rows else 0,
                framework_full_window_cpu_s=full_framework_cpu_s,
                sample_coverage_s=groups_fw['seconds'],missing_cpu_intervals=groups_stack['missing_cpu_intervals'])
            if run['sample_coverage_s']<m['duration_s']*.9: raise ValueError('inadequate sample coverage')
            if any(groups[g]['missing_cpu_intervals'] or groups[g]['missing_pss'] for g in core_groups): raise ValueError(f'core process changed {root}')
            runs.append(run)
            audit.append(dict(path=str(root.relative_to(a.root)),requests=len(rows),samples=len(samples),worker_sha256=setup['worker_exe_sha256']))
    if not runs: raise ValueError('no measured cells')
    write_csv(output/'per_run.csv',runs); write_csv(output/'components.csv',components); write_csv(output/'process_inventory.csv',list(inventory.values()))
    component_buckets=defaultdict(list)
    for component in components:
        component_buckets[tuple(component[k] for k in ('system','rps','ddl_ms','group'))].append(component)
    component_summary=[]
    for key,values in sorted(component_buckets.items()):
        row=dict(zip(('system','rps','ddl_ms','group'),key)); row['n']=len(values)
        for metric in ('cpu_pct','rss_mib','pss_mib'):
            numbers=[r[metric] for r in values]
            row[metric]=st.mean(numbers); row[metric+'_sd']=st.stdev(numbers) if len(numbers)>1 else 0
        component_summary.append(row)
    write_csv(output/'component_summary.csv',component_summary)
    buckets=defaultdict(list)
    for run in runs: buckets[tuple(run[k] for k in ('system','rps','ddl_ms'))].append(run)
    summary=[]
    metrics=('framework_cpu_pct','framework_rss_mib','framework_pss_mib','stack_cpu_pct','stack_pss_mib','goodput','throughput','framework_cpu_ms_per_offered')
    for key,values in sorted(buckets.items()):
        row=dict(zip(('system','rps','ddl_ms'),key)); row['n']=len(values)
        for metric in metrics:
            numbers=[r[metric] for r in values]; mean=st.mean(numbers); sd=st.stdev(numbers) if len(numbers)>1 else 0
            row[metric]=mean; row[metric+'_sd']=sd
            row[metric+'_ci95_half']=4.30265273*sd/math.sqrt(3) if len(numbers)==3 else None
        summary.append(row)
    write_csv(output/'summary.csv',summary)
    paired=[]
    lookup={(r['system'],r['repeat'],r['rps'],r['ddl_ms']):r for r in runs}
    for system in sorted({r['system'] for r in runs}):
        repeats=sorted({r['repeat'] for r in runs if r['system']==system})
        rates=sorted({r['rps'] for r in runs if r['system']==system and r['rps']})
        ddls=sorted({r['ddl_ms'] for r in runs if r['system']==system and r['rps']})
        contrasts=[]
        if len(rates)>1:
            contrasts += [('pressure',f'ddl{d}',(rates[0],d),(rates[-1],d)) for d in ddls]
        if len(ddls)>1:
            contrasts += [('deadline',f'rps{r}',(r,ddls[0]),(r,ddls[-1])) for r in rates]
        for factor,condition,low,high in contrasts:
            for metric in metrics:
                deltas=[lookup[(system,rep,*high)][metric]-lookup[(system,rep,*low)][metric] for rep in repeats if (system,rep,*high) in lookup and (system,rep,*low) in lookup]
                if not deltas: continue
                mean=st.mean(deltas); sd=st.stdev(deltas) if len(deltas)>1 else 0
                half=4.30265273*sd/math.sqrt(3) if len(deltas)==3 else None
                paired.append(dict(system=system,factor=factor,condition=condition,metric=metric,n=len(deltas),
                                   high_minus_low=mean,sd=sd,ci95_low=mean-half if half is not None else None,
                                   ci95_high=mean+half if half is not None else None))
    if paired: write_csv(output/'paired_effects.csv',paired)
    # Compare framework gaps at the same rate/deadline within randomized repeat blocks.
    gaps=[]
    for first,second in (('ray','tide'),('kubeedge','tide'),('ray','kubeedge')):
        for rate,ddl in sorted({(r['rps'],r['ddl_ms']) for r in runs}):
            for metric in ('framework_cpu_pct','framework_pss_mib','stack_cpu_pct','stack_pss_mib'):
                delta=[lookup[(first,rep,rate,ddl)][metric]-lookup[(second,rep,rate,ddl)][metric]
                       for rep in (1,2,3) if (first,rep,rate,ddl) in lookup and (second,rep,rate,ddl) in lookup]
                if not delta: continue
                mean=st.mean(delta); sd=st.stdev(delta) if len(delta)>1 else 0
                half=4.30265273*sd/math.sqrt(3) if len(delta)==3 else None
                gaps.append(dict(first=first,second=second,rps=rate,ddl_ms=ddl,metric=metric,n=len(delta),
                                 mean_difference=mean,sd=sd,ci95_low=mean-half if half is not None else None,
                                 ci95_high=mean+half if half is not None else None))
    if gaps: write_csv(output/'system_gaps.csv',gaps)
    lines=['| System | req/s | DDL (ms) | n | Framework CPU (% core) | Framework PSS (MiB) | Stack CPU (% core) | Stack PSS (MiB) | On-time req/s |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary:
        vals=[f"{r[k]:.2f} ± {r[k+'_sd']:.2f}" for k in ('framework_cpu_pct','framework_pss_mib','stack_cpu_pct','stack_pss_mib','goodput')]
        lines.append(f"| {r['system']} | {r['rps']} | {r['ddl_ms']} | {r['n']} | "+' | '.join(vals)+' |')
    (output/'table.md').write_text('Mean ± sample SD across three measurement blocks; KubeEdge controllers persist while application Pods restart. CPU 100% = one logical core.\n\n'+'\n'.join(lines)+'\n')
    result=dict(status='partial' if a.partial else 'complete',runs=len(runs),loaded_runs=sum(r['rps']>0 for r in runs),requests=sum(r['requests'] for r in runs),cells=audit)
    (output/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='cells'}))

if __name__=='__main__': main()
