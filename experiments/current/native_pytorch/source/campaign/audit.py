"""Fail-closed execution audit that retains genuine client timeouts and partial work."""
import collections
import csv
import json
import math
from pathlib import Path
import shutil
import statistics as st
from protocol import *
from shared_audit import metrics,_manifest,_host,read_events
from binding import check as check_binding


def trace(path,case,rows,warm):
    flags=cell_flags(case['cell']);ready={};starts={};calls=collections.defaultdict(dict);complete={}
    sources=sorted((path/'worker-logs').glob('*/stdout')) if flags['ei'] else [path/'http-worker.jsonl']
    for source in sources:
        for e in read_events(source):
            kind=e.get('event');owner=(source.parent.name,e.get('pid'))
            if kind=='worker_ready':
                assert owner not in ready;ready[owner]=e
                assert e['model_sha256']==MODEL and e['input_size']==[640,640]
                assert e['torch']=='2.0.1+cpu' and e['torch_intraop']==threads(case)
                assert e['torch_interop']==8 and e['opencv_threads']==1
                assert e['native_tide_dispatch']==flags['ei'] and not e['full_result_image_transferred']
            elif kind=='request_start':
                rid=e['request_id'];assert rid not in starts;starts[rid]=(owner,e)
                assert e['input_sha256']==INPUT
                expected=PAIRS if flags['pi'] else [APP_CPUS]
                assert e['affinity'] in expected
                assert all(mask==e['affinity'] for mask in e['thread_masks'].values())
            elif kind=='detection_complete':
                rid=e['request_id'];index=e['call_index'];assert index not in calls[rid];calls[rid][index]=e['call']
                c=e['call'];assert c['success'] and c['forward_calls']==c['postprocess_calls']==c['draw_calls']==1
                assert c['test_size']==[640,640] and c['torch_intraop_threads']==threads(case)
                assert c['output_shape']==[576,768,3] and c['detections']>0
            elif kind=='request_complete':
                rid=e['request_id'];assert rid not in complete;complete[rid]=(owner,e)
                assert e['success'] and e['inferences_completed']==10 and e['shape']==[576,768,3]
                assert e['input_sha256']==INPUT and not e['full_result_image_transferred']
            elif kind=='request_failure':raise AssertionError(('workload implementation failure',e))
    assert len(ready)==workers(case)
    expected={f'{phase}-{r["req_id"]}' for phase,rs in [('warmup',warm),('measured',rows)] for r in rs}
    assert set(complete)<=set(starts)<=expected
    assert set(calls)<=set(starts)
    for rid,(owner,start) in starts.items():
        assert owner in ready
        assert set(calls[rid])==set(range(1,len(calls[rid])+1)) and len(calls[rid])<=10
        for c in calls[rid].values():assert c['affinity']==start['affinity']
    for rid,(owner,e) in complete.items():
        assert owner==starts[rid][0] and e['start_ns']==starts[rid][1]['start_ns']
        assert len(calls[rid])==10 and e['finish_ns']>e['start_ns']
        assert e['affinity']==starts[rid][1]['affinity']
        assert math.isclose(sum(c['execution_ms'] for c in calls[rid].values()),e['function_execution_sum_ms'],rel_tol=1e-12)
    for phase,rs in [('warmup',warm),('measured',rows)]:
        for r in rs:
            rid=f'{phase}-{r["req_id"]}'
            if r['status']=='200':
                assert rid in complete,('unverified successful return',rid)
                e=complete[rid][1]
                assert float(r['latency_ms'])+.005>=(e['finish_ns']-e['start_ns'])/1e6
    active={};peak=0;by_worker=set()
    points=[]
    for rid,(owner,e) in starts.items():
        points.append((e['start_ns'],1,rid,owner))
        end=complete[rid][1]['finish_ns'] if rid in complete else 10**30
        points.append((end,-1,rid,owner))
    for stamp,sign,rid,owner in sorted(points):
        if sign==1:
            assert owner not in by_worker;by_worker.add(owner);active[rid]=tuple(starts[rid][1]['affinity'])
        else:by_worker.remove(owner);del active[rid]
        peak=max(peak,len(active));assert len(active)<=workers(case)
        if flags['pi']:assert all(n<=(1 if flags['cc'] else 2) for n in collections.Counter(active.values()).values())
    return ready,starts,calls,complete,peak


def audit_case(path,case,copy_worker_logs=False,expected_binaries=None):
    path=Path(path);manifest=_manifest(path,case,expected_binaries)
    flags=cell_flags(case['cell'])
    if flags['ei'] and copy_worker_logs:
        runtime=list(read_events(path/'runtime.jsonl'))
        for e in runtime:
            if e.get('msg')=='StackPool: received one INIT event':
                eid=e['ExecutorId'];source=Path(__file__).resolve().parent.parent/'native-evidence'/eid
                assert source.is_dir() and not source.is_symlink(),('missing direct worker evidence',eid)
                dest=path/'worker-logs'/eid;dest.mkdir(parents=True,exist_ok=True)
                assert (source/'stdout').is_file() and not (source/'stdout').is_symlink()
                shutil.copy2(source/'stdout',dest/'stdout')
                stderr=Path('/tmp/tide/exec-logs')/eid/'stderr'
                if stderr.exists():shutil.copy2(stderr,dest/'stderr')
    with (path/'raw.csv').open() as f:rows=list(csv.DictReader(f))
    with (path/'warmup.csv').open() as f:warm=list(csv.DictReader(f))
    assert len(rows)==case['rps']*case['duration_sec'] and len(warm)==workers(case)*4
    assert all(r['status']=='200' for r in warm),'functional warmup incomplete'
    stats=metrics(rows,case['duration_sec']);metrics(warm,1)
    ready,starts,calls,complete,peak=trace(path,case,rows,warm)
    binding=check_binding(path,case,starts,complete)
    host=_host(path,case,complete)
    valid=[complete['measured-'+r['req_id']][1] for r in rows if r['status']=='200']
    durations=[(e['finish_ns']-e['start_ns'])/1e6 for e in valid]
    stats.update(worker_execution_ms=st.mean(durations) if durations else None,
        worker_execution_sd_ms=st.pstdev(durations) if durations else None,
        server_completed=sum(rid.startswith('measured-') for rid in complete),
        completed_detection_witnesses=sum(len(c) for c in calls.values()))
    classifications=collections.Counter()
    for row in rows:
        rid='measured-'+row['req_id']
        label='valid_return' if row['status']=='200' else ('completed_without_valid_return' if rid in complete else ('stopped_during_execution' if rid in starts else 'not_started_before_stop'))
        classifications[label]+=1
    inputs=[path/n for n in ['case-config.json','launch.json','manifest.json','raw.csv','warmup.csv','host-samples.jsonl']]
    inputs += [path/'runtime.jsonl',*sorted((path/'worker-logs').glob('*/stdout'))] if flags['ei'] else [path/'http-worker.jsonl',path/'backend-owner.json']
    result=dict(status='passed',case=case,workers=len(ready),stats=stats,actual_peak=peak,binding=binding,
        starts=len(starts),completions=len(complete),partial_requests=len(starts)-len(complete),
        outcome_classes=dict(classifications),**host,raw_sha256=sha(path/'raw.csv'),warmup_sha256=sha(path/'warmup.csv'),
        binary_sha256=manifest['binary_sha256'],evidence_sha256={str(p.relative_to(path)):sha(p) for p in inputs})
    save(path/'audit.json',result);return result
