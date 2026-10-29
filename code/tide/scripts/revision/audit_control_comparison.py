#!/usr/bin/env python3
"""Independent protocol, process-scope, affinity and raw-trace audit.

Does not equate successful framework requests with a valid experiment: retained
failures are counted, while missing samples/requests or changed inputs fail audit.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path

EXPECTED_WORKER='7a7a6f3af52810394d02737b23ecc0f62c7c715d474ec72c9b44b0d1e8bf3659'
RATES=(5,20,80)
DDLS=(50,200,1000)
SYSTEMS=('tide','ray','kubeedge')

def require(condition,message):
    if not condition: raise ValueError(message)

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def audit_request(row,rate,ddl,duration):
    rid=int(row['req_id']); planned=float(row['planned_s']); submit=float(row['submit_s']); reply=float(row['reply_s']); latency=float(row['latency_ms'])
    require(all(math.isfinite(v) for v in (planned,submit,reply,latency)), 'nonfinite timing')
    require(0<=rid<round(rate*duration),'request ID out of range')
    require(abs(planned-rid/rate)<1e-6,'offered schedule mismatch')
    require(submit>=0 and submit>=planned-1e-6 and reply>=submit and latency>=0,'invalid timestamp ordering')
    require(abs((reply-submit)*1000-latency)<1e-5,'latency does not match timestamps')
    require(int(row['ddl_ms'])==ddl,'deadline mismatch')
    if int(row['status'])==200:
        require(int(row['bytes_processed'])==6553600 and int(row['worker_service_ns'])>0,'invalid successful kernel payload')
    else:
        require(100<=int(row['status'])<=599,'invalid failure status')

def audit(root,partial=False,provenance=None,history_provenance=()):
    manifest=json.loads((root/'progress.json').read_text())
    expected_jobs={(s,r) for s in SYSTEMS for r in (1,2,3)}
    jobs=manifest['jobs']
    require(len(jobs)==9 and {(j['system'],j['repeat']) for j in jobs}==expected_jobs,'not the full 3-system/3-repeat plan')
    require(manifest['duration_s']==30 and manifest['idle_s']==20,'window duration changed')
    require(manifest['nofile_after'][0]>=16384,'insufficient generator file-descriptor budget')
    if not partial: require(manifest['status']=='completed','supervisor is incomplete')
    checked=[]; failures=Counter(); files={}; all_worker_hashes=set(); total_samples=0; process_inventory={}; warmup_requests=0
    for job in jobs:
        require(len(job['cells'])==9 and {tuple(c) for c in job['cells']}=={(r,d) for r in RATES for d in DDLS},'factorial matrix mismatch')
        if not partial: require(job['status']=='completed' and job['completed_cells']==9,'deployment block incomplete')
        block=root/f"{job['system']}-r{job['repeat']}"
        if partial and not (block/'setup.json').is_file(): continue
        setup=json.loads((block/'setup.json').read_text()); files[str((block/'setup.json').relative_to(root))]=sha(block/'setup.json')
        require(setup['worker_exe_sha256']==EXPECTED_WORKER,'application binary changed')
        all_worker_hashes.add(setup['worker_exe_sha256'])
        warm=block/'warmup'
        if not partial or (warm/'measurement.json').exists():
            wm=json.loads((warm/'measurement.json').read_text())
            with (warm/'requests.csv').open() as f: wr=list(csv.DictReader(f))
            require((wm['rate'],wm['ddl_ms'],wm['duration_s'],wm['requests'],wm['success'])==(5,1000,2,10,10),'warmup protocol mismatch')
            require(len(wr)==10 and {int(r['req_id']) for r in wr}==set(range(10)),'missing warmup requests')
            for r in wr:
                audit_request(r,5,1000,2); require(int(r['status'])==200,'warmup failed')
            warmup_requests+=10
            for path in (warm/'measurement.json',warm/'requests.csv',warm/'samples.jsonl'):
                files[str(path.relative_to(root))]=sha(path)
        require(all(mask==[0,1] for mask in setup['worker_affinity'].values()),'startup worker affinity differs')
        if job['system']=='tide':
            ready=[]
            for line in (block/'service.log').read_text().splitlines():
                try: record=json.loads(line)
                except json.JSONDecodeError: continue
                if record.get('status')=='ready' and 'config' in record: ready.append(record)
            require(len(ready)==1 and ready[0]['pid']==setup['launcher_pid'],'missing Tide runtime configuration')
            cfg=ready[0]['config']
            require(cfg['qos_by_function_ms']=={'cpu-qos50':50,'cpu-qos200':200,'cpu-qos1000':1000},'Tide deadline policy mismatch')
            require(cfg['execution_ms']==22 and cfg['ttl_ms']==60000 and cfg['weights']==[1,1,1],'Tide scheduling parameters changed')
            files[str((block/'service.log').relative_to(root))]=sha(block/'service.log')
        if job['system']=='ray':
            require(setup['identity']['ray_version']=='2.58.0','Ray version changed')
            require(setup['identity']['application_concurrency']==1 and setup['identity']['cpus']=='0,1','Ray kernel allocation changed')
            if not partial or (block/'cleanup.json').exists():
                require(not json.loads((block/'cleanup.json').read_text())['remaining'],'owned Ray processes remain')
        if job['system']=='kubeedge':
            require(setup['edge_version'].endswith('kubeedge-v1.23.1'),'KubeEdge version changed')
            pod=setup['pod']; require(pod['spec']['nodeName']=='tmc-ke-worker','unexpected edge node')
            require(pod['spec']['containers'][0]['env']==[{'name':'GOMAXPROCS','value':'1'}],'kernel concurrency configuration changed')
        cells=[('idle',0,1000,20)]+[(f'rps{r}-ddl{d}',r,d,30) for r,d in job['cells']]
        for name,rate,ddl,duration in cells:
            cell=block/name
            if partial and not (cell/'measurement.json').exists(): continue
            m=json.loads((cell/'measurement.json').read_text())
            require((m['rate'],m['ddl_ms'],m['duration_s'])==(rate,ddl,duration),'cell metadata differs from plan')
            require(m['work']==dict(kind='cpu',bytes=65536,iterations=100),'workload changed')
            require(m['hz']==100,'CPU tick calibration changed')
            start=m['start_monotonic_s']; end=m['end_monotonic_s']; arrival_end=m['arrival_end_monotonic_s']
            require(start+duration-.01<=arrival_end<=end,'measurement window inconsistency')
            samples=[json.loads(line) for line in (cell/'samples.jsonl').read_text().splitlines()]
            require(len(samples)==m['samples'] and len(samples)>duration,'sample count mismatch or undersampling')
            gaps=[b['monotonic_s']-a['monotonic_s'] for a,b in zip(samples,samples[1:])]
            require(all(0<g<1.5 for g in gaps),'reordered or missing samples')
            require(samples[0]['monotonic_s']-start<.25 and samples[-1]['monotonic_s']>=end-.05,'sampling window does not cover requests')
            previous={}; births=0; pss_missing=0; groups=Counter()
            for snap in samples:
                procs=snap['processes']; current={p['pid']:p for p in procs}
                require(len(current)==len(procs),'PID counted twice')
                kernels=[p for p in procs if p['group']=='application']
                require(len(kernels)==1 and kernels[0]['pid']==setup['worker_pid'],'application identity changed')
                require(kernels[0]['affinity']==[0,1],'kernel escaped its CPU slice')
                if job['system']=='kubeedge': require(kernels[0]['cpu_max']=='200000 100000','KubeEdge CPU quota changed')
                for p in procs:
                    groups[p['group']]+=1
                    require(p['rss_kib']>=0 and p['ticks']>=0,'negative process counters')
                    if p.get('pss_kib') is None: pss_missing+=1
                    else: require(0<=p['pss_kib']<=p['rss_kib']+4,'invalid PSS')
                    if p['pid'] in previous and previous[p['pid']]['start']==p['start']:
                        require(p['ticks']>=previous[p['pid']]['ticks'],'CPU counter moved backwards')
                    elif previous: births+=1
                    process_inventory[(job['system'],p['pid'],p['start'])]=p['group']
                if job['system']=='kubeedge':
                    require(sum(p['comm']=='edgecore' and p['group']=='edgecore' for p in procs)==1,'EdgeCore missing or double counted')
                    require(sum(p['comm']=='cloudcore' and p['group']=='cloudcore' for p in procs)==1,'CloudCore missing or double counted')
                    require(any('kube-apiserver' in p['cmd'] for p in procs),'dedicated Kubernetes dependencies missing')
                elif job['system']=='tide':
                    require(sum(p['pid']==setup['launcher_pid'] and p['group']=='core' for p in procs)==1,'Tide daemon missing')
                else:
                    require(any(p['comm']=='gcs_server' for p in procs) and any(p['comm']=='raylet' for p in procs),'Ray core services missing')
                    require(any('ProxyActor' in p['cmd'] for p in procs) and any('ServeReplica' in p['cmd'] for p in procs),'Ray request wrappers missing')
                previous=current
            rows=[]
            if rate:
                with (cell/'requests.csv').open() as f: rows=list(csv.DictReader(f))
            require(len(rows)==rate*duration==m['requests'],'missing offered requests')
            require({int(r['req_id']) for r in rows}==set(range(len(rows))),'duplicate/missing request IDs')
            for r in rows:
                audit_request(r,rate,ddl,duration)
                require(float(r['reply_s'])<=end-start+.01,'reply outside recorded drain interval')
                if int(r['status'])!=200: failures[(job['system'],r['status'],r['error'])]+=1
            require(sum(int(r['status'])==200 for r in rows)==m['success'],'success count mismatch')
            for path in [cell/'measurement.json',cell/'samples.jsonl']+([cell/'requests.csv'] if rate else []): files[str(path.relative_to(root))]=sha(path)
            total_samples+=len(samples)
            checked.append(dict(path=str(cell.relative_to(root)),requests=len(rows),samples=len(samples),max_sample_gap_s=max(gaps),process_birth_intervals=births,missing_pss_samples=pss_missing,groups=dict(groups)))
    if not partial:
        require(len(checked)==90 and sum(c['requests'] for c in checked)==85050,'full matrix totals mismatch')
        require(len(all_worker_hashes)==1,'not all systems used same worker')
        require(warmup_requests==90,'missing warmup blocks')
    provenance_checked=[]
    if provenance:
        for remote,expected in manifest['hashes'].items():
            path=provenance/Path(remote).name
            if not path.exists(): path=provenance/'scripts'/Path(remote).name
            require(path.is_file() and sha(path)==expected,f'frozen artifact changed: {remote}')
            provenance_checked.append(str(path))
    elif not partial: raise ValueError('full audit requires --provenance')
    history_checked=[]
    for history in manifest.get('history',[]):
        matches=[]
        for directory in history_provenance:
            valid=True
            for remote,expected in history['hashes'].items():
                path=directory/Path(remote).name
                if not path.exists(): path=directory/'scripts'/Path(remote).name
                if not path.is_file() or sha(path)!=expected: valid=False; break
            if valid: matches.append(str(directory))
        require(bool(matches),f'missing historical provenance for PID {history["pid"]}')
        history_checked.append(dict(pid=history['pid'],paths=matches))
    checkpoint=root.parent/'checkpoint-02-raw-sha256.json'
    checkpoint_verified=0
    if checkpoint.exists():
        for rel,expected in json.loads(checkpoint.read_text()).items():
            require((root/rel).is_file() and sha(root/rel)==expected,f'completed checkpoint changed: {rel}')
            checkpoint_verified+=1
    result=dict(status='partial' if partial else 'complete',warmup_requests=warmup_requests,cells=len(checked),requests=sum(c['requests'] for c in checked),samples=total_samples,
                historical_provenance_checked=history_checked,checkpoint_files_verified=checkpoint_verified,
                unique_process_identities=len(process_inventory),worker_sha256=sorted(all_worker_hashes),provenance_checked=provenance_checked,
                failures=[dict(system=k[0],status=k[1],error=k[2],count=v) for k,v in sorted(failures.items())],details=checked)
    output=root/'summary'; output.mkdir(exist_ok=True)
    (output/'independent-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    (output/'raw-sha256.json').write_text(json.dumps(files,indent=2)+'\n')
    return {k:v for k,v in result.items() if k not in ('details','failures')}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--root',type=Path,required=True); p.add_argument('--partial',action='store_true'); p.add_argument('--provenance',type=Path); p.add_argument('--history-provenance',type=Path,action='append',default=[])
    a=p.parse_args(); print(json.dumps(audit(a.root,a.partial,a.provenance,a.history_provenance)))
