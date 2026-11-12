"""Match native Processor bindings to request starts, including bounded cancellations."""
import collections
import json
from pathlib import Path
from audit_binding import cpu_list
from protocol import APP_CPUS,MAPPING,PAIRS,cell_flags,workers,demand


def check(path,case,starts,complete):
    flags=cell_flags(case['cell'])
    if not flags['ei']:return dict(applicable=False)
    runtime=[json.loads(l) for l in (path/'runtime.jsonl').read_text().splitlines() if l.strip()]
    configs=[e for e in runtime if e.get('msg')=='resptestv2 config']
    assert len(configs)==1
    cfg=configs[0]
    assert cfg['EnableConcurrencyControl']==flags['cc'] and cfg['EnableCpuset']==flags['pi']
    created={e['ExecutorId'] for e in runtime if e.get('msg')=='Containerxv2: start creating new container'}
    ready={e['ExecutorId'] for e in runtime if e.get('msg')=='StackPool: received one INIT event'}
    assert len(created)==workers(case) and ready==created
    assert any(e.get('msg')=='ablation application registered' for e in runtime)
    assert any(e.get('msg')=='bounded observation complete; owned teardown delegated' for e in runtime)
    physical=lambda r:sorted(MAPPING[i] for i,b in enumerate(r['CpuSet']) if b)
    resources={};acquired={};active={};targets={};executed={};ended=set();cgroups={};bound={};slots={}
    for e in runtime:
        msg=e.get('msg');ex=e.get('ExecutorId');proc=e.get('ProcessorId');rid=e.get('RoutineId')
        if msg=='Containerxv2: start creating new container':cgroups[ex]='/sys/fs/cgroup'+e['CgroupPath']
        elif msg=='ablation oversubscribed worker slot':
            resources['ablation-'+ex]=physical(e['PhysicRes']);slots[ex]=resources['ablation-'+ex]
        elif msg=='Stub: got a processor':
            assert e['logic resource']['CpuNum']==demand(case)
            resources[proc]=physical(e['physics resource']);acquired[rid]=proc
            assert len(resources[proc])==demand(case) and set(resources[proc])<=set(APP_CPUS)
        elif msg=='Exec: bind processor':
            assert ex not in active;active[ex]=proc;bound.pop(ex,None)
        elif msg in ['Containerxv2: bound container CPU set','Containerxv2: move a container']:
            assert flags['pi'] and physical(e['phyRes'])==resources[active[ex]];bound[ex]=e
        elif msg=='Exec: bind routine':
            key=(ex,rid);assert key not in targets and ex in active
            assert active[ex]==(acquired[rid] if flags['cc'] else 'ablation-'+ex)
            target=resources[active[ex]] if flags['pi'] else APP_CPUS
            if flags['pi']:
                assert target in PAIRS and bound[ex]['pids']
                if bound[ex]['msg']=='Containerxv2: bound container CPU set':
                    assert cpu_list(bound[ex]['cpus'])==target
            targets[key]=target
        elif msg=='Stub: notice executor to execute':
            key=(ex,rid);assert key in targets;executed[key]=True
        elif msg=='Exec: unbind routine':
            assert (ex,rid) in executed;ended.add((ex,rid))
        elif msg=='Exec: unbind processor':assert active.pop(ex)==proc
    for rid,(owner,e) in starts.items():
        key=(owner[0],rid);assert key in executed
        assert e['affinity']==targets[key]
    for rid,(owner,e) in complete.items():
        key=(owner[0],rid)
        assert key in executed
        assert e['affinity']==targets[key]
    assert ended<=set(executed)
    if case['cell']=='C5':
        assert collections.Counter(tuple(v) for v in slots.values())==collections.Counter({tuple(p):2 for p in PAIRS})
    thread_samples=0
    if flags['pi']:
        for line in (path/'host-samples.jsonl').read_text().splitlines():
            sample=json.loads(line);lo=sample['started_at']*1e9;hi=sample['time']*1e9
            groups={g['path']:g for g in sample['groups']}
            for rid,(owner,e) in complete.items():
                if e['start_ns']<lo and hi<e['finish_ns'] and cgroups[owner[0]] in groups:
                    group=groups[cgroups[owner[0]]];target=targets[owner[0],rid]
                    assert group['thread_affinities'] and all(cpu_list(a)==target for a in group['thread_affinities'])
                    thread_samples+=1
        assert thread_samples>0
    return dict(applicable=True,created=len(created),initialized=len(ready),bound_requests=len(targets),
        started=len(starts),completed_witnesses=len(complete),unfinished_bindings_at_stop=len(active),
        binding_mismatches=0,execution_thread_samples=thread_samples)
