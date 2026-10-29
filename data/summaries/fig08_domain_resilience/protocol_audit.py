#!/usr/bin/env python3
"""Read-only, independent protocol gate for all 36 domain treatments.

No SSH, application outcome threshold, inferred worker labels, or replacement
selection. Missing evidence yields INCOMPLETE. Test fixtures are not inputs.
"""
import argparse
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import shlex
import time

SECOND=10**9
WORK={'kind':'cpu','bytes':65536,'iterations':1000}
IMAGE='docker.io/tide/domain-resilience-20261027:amd64'
HEX=re.compile(r'[0-9a-f]{64}\Z')


class AuditError(ValueError):pass


def need(condition,message):
    if not condition:raise AuditError(message)


def load(path):return json.loads(Path(path).read_text())


def jsonl(path):
    records=[]
    for line in Path(path).read_bytes().splitlines(keepends=True):
        if not line.strip():continue
        need(line.endswith(b'\n'),'Incomplete final JSONL record: '+str(path))
        records.append(json.loads(line))
    return records


def check(checks,name,operation):
    try:
        details=operation();checks.append(dict(check=name,status='PASS',details=details));return details
    except (OSError,ValueError,KeyError,TypeError,IndexError,ZeroDivisionError) as error:
        checks.append(dict(check=name,status='INCOMPLETE',error=str(error)));return None


def reference_arrivals(prefix):
    with localcontext() as context:
        context.prec=50
        values=[Decimal(i)/2 for i in range(60)]
        values += [Decimal(30)+((Decimal(4)+Decimal('0.4')*i).sqrt()-2)/Decimal('0.2') for i in range(150)]
        values += [Decimal(60)+Decimal(i)/8 for i in range(720)]
        return [dict(id=f'{prefix}-{i:04d}',offset_ns=int((value*SECOND).to_integral_value())) for i,value in enumerate(values)]


def check_arrivals(spec,rows):
    need(spec['workload']==WORK and spec['duration_s']==150 and spec['deadline_ms']==500 and spec['timeout_s']==2,'Frozen workload/window/deadline/timeout differs')
    offers=spec['arrivals'];reference=reference_arrivals(spec['run_id']);ids=[row['id'] for row in offers]
    need(len(offers)==930 and len(set(ids))==930,'Exactly930 distinct offers required')
    need(all(a['id']==b['id'] and abs(a['offset_ns']-b['offset_ns'])<=2 for a,b in zip(offers,reference)),'Arrival identity/analytic schedule differs (2ns numeric tolerance)')
    need(len(rows)==930 and len({row['id'] for row in rows})==930 and {row['id'] for row in rows}==set(ids),'Missing,duplicate,or unplanned client records')
    byid={row['id']:row for row in rows};origins=set()
    for offer in offers:
        row=byid[offer['id']];offset=offer['offset_ns'];origin=row['scheduled_monotonic_ns']-offset;origins.add(origin)
        need(row['scheduled_offset_ns']==offset,'Client changed scheduled offset')
        need(origin<=row['started_monotonic_ns']<origin+150*SECOND,'Actual request start outside offered window')
        need(row['started_monotonic_ns']>=row['scheduled_monotonic_ns'] and row['ended_monotonic_ns']>=row['started_monotonic_ns'],'Incoherent request monotonic timing')
        need(spec['start_unix_ns']<=row['started_unix_ns']<spec['start_unix_ns']+150*SECOND,'Actual request Unix start outside offered window')
    need(len(origins)==1,'Client used inconsistent monotonic schedule origin')
    return dict(offers=930,client_records=len(rows),offset_sha256=hashlib.sha256(json.dumps([row['offset_ns'] for row in offers]).encode()).hexdigest(),outcome_threshold_applied=False)


def cpu_set(text):
    selected=set()
    for item in text.split(','):
        need(bool(re.fullmatch(r'\d+(?:-\d+)?',item)),'Malformed CPU set')
        numbers=list(map(int,item.split('-')));left,right=numbers[0],numbers[-1]
        need(0<=left<=right<=1048576,'Invalid CPU range');selected.update(range(left,right+1))
    return selected


def check_worker_budget(worker):
    need(not worker.get('budget_errors'),'Owned-worker budget observation errors')
    chain=worker.get('budget_chain');threads=worker.get('thread_affinities')
    need(bool(chain) and bool(threads),'Missing inherited limits or thread affinity proof')
    quotas=[];memory=[];swap=[];allowed=None
    for ancestor in chain:
        limits=ancestor['limits']
        if 'cpu.max' in limits:
            amount,period=limits['cpu.max'].split();need(int(period)>0,'Invalid CPU period')
            if amount!='max':quotas.append(Fraction(int(amount),int(period)))
        for key,values in [('memory.max',memory),('memory.swap.max',swap)]:
            if key in limits and limits[key]!='max':values.append(int(limits[key]))
        if limits.get('cpuset.cpus.effective'):
            mask=cpu_set(limits['cpuset.cpus.effective']);allowed=mask if allowed is None else allowed&mask
    need(quotas and min(quotas)==2,'Inherited CPU quota is not exactly2')
    need(memory and min(memory)==536870912 and swap and min(swap)==0,'Inherited memory/swap budget differs')
    need(allowed is not None,'No inherited effective CPU mask')
    for thread in threads:need(cpu_set(thread['cpus_allowed_list'])&allowed=={0,1},'A worker thread effective mask differs from0-1')
    return dict(pid=worker['pid'],cgroup=worker['cgroup'],quota_cpus=2,memory_bytes=536870912,swap_bytes=0)


def check_clocks(before,after):
    for bracket in (before,after):
        samples=bracket['samples'];need(len(samples)==20,'Clock requires20 brackets before AND after')
        for sample in samples:
            start,end=sample['edge_send_ns'],sample['edge_receive_ns']
            need(end>=start and sample['edge_receive_monotonic_ns']>=sample['edge_send_monotonic_ns'],'Clock bracket moves backwards')
            need(sample['uncertainty_ns']==(end-start)//2,'Clock uncertainty does not equal half bracket')
            need(sample['offset_ns']==sample['cloud']['unix_ns']-(start+end)//2,'Clock offset differs from bracket midpoint')
        need(bracket['best']==min(samples,key=lambda value:value['uncertainty_ns']),'Clock does not use observed minimum bracket')
    a,b=before['best'],after['best'];bound=a['uncertainty_ns']+b['uncertainty_ns']+abs(a['offset_ns']-b['offset_ns'])
    need(bound<10_000_000,'Conservative clock uncertainty/drift bound is not below10ms')
    return dict(offset_ns=a['offset_ns'],bound_ns=bound,before=a,after=b)


def check_connectivity(cloud,peer,bounds,fault,margin_ns=0):
    counts={}
    for phase,(left,right) in bounds.items():
        if phase not in ('normal','outage','recovery'):continue
        for name,rows,host in [('C',cloud,'C'),('AB',peer,'B')]:
            selected=[row for row in rows if left+margin_ns<=row['unix_ns'] and row['end_unix_ns']<right-margin_ns]
            need(len(selected)>=max(1,int((right-left)/SECOND)//2),f'Insufficient {name} probes during {phase}')
            times=sorted(row['unix_ns'] for row in selected)
            need(max([times[0]-left,right-times[-1]]+[b-a for a,b in zip(times,times[1:])])<=3*SECOND,f'{name} probe coverage gap during {phase}')
            expected=not(name=='C' and fault and phase=='outage')
            need(all(row.get('ok') is expected for row in selected),f'{name} connectivity differs from assigned {phase} treatment')
            if expected:need(all(row.get('reply',{}).get('physical_host')==host for row in selected),f'{name} probe endpoint identity mismatch')
            counts[name+'_'+phase]=dict(observations=len(selected),successes=sum(row['ok'] is True for row in selected))
    return counts


def expected_rules(run_id):
    return [[chain,flag,peer,'-p','tcp','-m','multiport',ports,'34000:34299','-m','comment','--comment','tmc-domain-'+run_id,'-j','DROP']
            for chain,flag in [('INPUT','-s'),('OUTPUT','-d'),('FORWARD','-s'),('FORWARD','-d')]
            for peer in ['11.0.4.48','11.0.4.49'] for ports in ['--dports','--sports']]


def check_fault(spec,directory):
    start=spec['start_unix_ns'];end=start+150*SECOND;path=directory/'C/fault.json'
    if not spec['fault']:
        need(not path.exists(),'Connected control has an unexpected fault artifact')
        return dict(bounds={'normal':(start,start+20*SECOND),'outage':(start+20*SECOND,start+110*SECOND),'recovery':(start+110*SECOND,end)},packet_count=0)
    value=load(path);a,b,c,d=[value[name] for name in ('on_start_unix_ns','on_end_unix_ns','off_start_unix_ns','off_end_unix_ns')]
    need(start<a<=b<c<=d<end,'Actual fault boundaries are missing/unordered')
    need(value.get('removed') is True,'Fault removal not verified')
    rules=expected_rules(spec['run_id']);need(value.get('rules')==rules,'Packet-drop rules differ from exact C-host transport scope')
    need(len(value.get('insertions',[]))==16 and all(row['rc']==0 for row in value['insertions']),'Not all16 rules were inserted successfully')
    need(len(value.get('deletions',[]))==16 and all(row['rc']==0 for row in value['deletions']),'Not all16 owned rules were removed successfully')
    counters=value['counters'];need(counters['rc']==0,'Packet counter command failed');packets=0;lines=0
    for line in counters['out'].splitlines():
        match=re.match(r'\[(\d+):(\d+)\]\s+(.*)',line)
        if not match:continue
        tokens=shlex.split(match[3])
        if '--comment' in tokens and tokens[tokens.index('--comment')+1]=='tmc-domain-'+spec['run_id']:
            need('-j' in tokens and tokens[tokens.index('-j')+1]=='DROP','Run-tag counter is not DROP')
            packets+=int(match[1]);lines+=1
    need(lines==16 and packets>0,'Missing scoped rule counters or no observed dropped packets')
    raw=load(directory/'C/fault-raw.json');offset=value['clock_conversion']['cloud_minus_edge_ns']
    for key in ('on_start_unix_ns','on_end_unix_ns','off_start_unix_ns','off_end_unix_ns'):need(raw[key]-offset==value[key],'Converted fault timestamp differs from retained raw source')
    return dict(bounds={'normal':(start,a),'outage':(b,c),'recovery':(d,end)},packet_count=packets,conversion_offset_ns=offset)


def check_initial(spec,directory,artifact,b_clock):
    evidence={}
    for host in ('A','B','C'):
        sample=load(directory/host/'initial-observation.json');workers=sample['workers']
        need(sample.get('physical_host')==host and sample.get('worker_count')==len(workers),'Initial physical inventory mismatch')
        need(sample.get('initial')==spec['initial'] and sample.get('run_id')==spec['run_id'],'Initial snapshot belongs to another treatment')
        need(sample['runtime_worker_sha256']==artifact['hosts'][host]['worker_sha256'],'Staged initial worker hash differs from frozen artifact')
        expected=host=='A' or(host=='B' and spec['initial']=='warm')
        need(bool(workers)==expected,'Initial A/Bwarm/cold/C capacity differs')
        offset=b_clock['offset_ns'] if host=='B' else 0
        if host!='C':need(sample['hash_end_unix_ns']-offset<spec['start_unix_ns'],'Initial snapshot was not completed before offers')
        for worker in workers:
            check_worker_budget(worker)
            need(worker['executable_sha256']==sample['runtime_worker_sha256'],'Actual initial ELF differs from staged worker')
        evidence[host]=dict(workers=len(workers),unix_ns=sample['unix_ns'])
    return evidence


def check_resources(spec,directory):
    counts={};frames={};warnings=[]
    for host in ('A','B','C'):
        rows=jsonl(directory/host/'observations.jsonl');need(bool(rows),'Missing physical observer samples: '+host);frames[host]=rows
        need(rows[0]['unix_ns']<=spec['start_unix_ns'] and rows[-1]['unix_ns']>=spec['start_unix_ns']+150*SECOND,'Observer does not cover entire offered window: '+host)
        sequence=[row['sequence'] for row in rows];need(sequence==list(range(1,len(rows)+1)),'Observer sequence gap: '+host)
        times=[row['monotonic_ns'] for row in rows];need(all(0<=b-a<=3*SECOND for a,b in zip(times,times[1:])),'Observer sampling gap/backwards clock: '+host)
        total=0
        for row in rows:
            need(row.get('physical_host')==host and row['worker_count']==len(row['workers']),'Observer physical inventory mismatch')
            if row.get('errors'):warnings.append(dict(host=host,sequence=row['sequence'],scanner_errors=row['errors']))
            need(host!='C' or not row['workers'],'C hosted an application worker')
            for worker in row['workers']:
                need(worker.get('physical_host')==host and worker.get('ownership_witness'),'Worker lacks physical ownership proof')
                check_worker_budget(worker);total+=1
        counts[host]=dict(samples=len(rows),owned_worker_samples=total)
    return dict(counts=counts,scanner_warnings=warnings)


def check_ready(spec,directory,b_clock):
    seen={};paths=[]
    for host in ('A','B'):
        for path in (directory/host).rglob('*.jsonl'):
            if 'journal' not in str(path.relative_to(directory/host)).lower():continue
            paths.append(str(path.relative_to(directory)))
            for row in jsonl(path):
                if row.get('event')!='instance_ready':continue
                instance=row.get('worker_instance');need(bool(instance),'Ready record lacks worker instance')
                need(instance not in seen or seen[instance]['host']==host,'Instance appears on both physical hosts')
                offset=b_clock['offset_ns'] if host=='B' else 0
                seen[instance]=dict(host=host,a_unix_ns=row['observed_unix_ns']-offset)
    need(any(row['host']=='A' and row['a_unix_ns']<spec['start_unix_ns'] for row in seen.values()),'A transport-ready event missing before offers')
    before_b=[row for row in seen.values() if row['host']=='B' and row['a_unix_ns']<spec['start_unix_ns']]
    need(bool(before_b)==(spec['initial']=='warm'),'Warm B readiness or cold B absence is not supported by physical journal')
    return dict(instances=seen,journals=paths,node_label_used=False)


def check_cleanup(root,directory,item):
    receipt=load(root/'evidence'/item['run_id']/'cleanup.json')
    need(receipt['run_id']==item['run_id'] and receipt['system']==item['system'],'Cleanup receipt identity mismatch')
    if item['system']=='tide':
        for host in ('A','B','C'):
            data=load(directory/host/'native-cleanup.json');need(data['status']=='STOPPED','Native cleanup incomplete')
            need(data['budget']['status']==('CONTROL_ONLY_NO_BUDGET_MUTATION' if host=='C' else 'RESTORED'),'Native ancestor budget not restored')
    elif item['system']=='ray':
        for host in ('A','B','C'):
            data=load(directory/host/'ray/cleanup.json');need(not data['remaining'] and not data.get('cgroup_remaining'),'Ray owned processes remain')
    else:
        data=receipt['result'];data=json.loads(data) if isinstance(data,str) else data
        namespace=load(directory/'C/ke-namespace.json');need(data['deletion_requested'] is True and data['uid']==namespace['uid'],'KubeEdge UID-guarded deletion receipt missing')
    return dict(receipt=str(root/'evidence'/item['run_id']/'cleanup.json'))


def frozen_protocol(root):
    value=load(root/'protocol/protocol.json')
    for key,expected in [('duration_s',150),('deadline_ms',500),('timeout_s',2),('fault_on_s',20),('fault_off_s',110),('offers_per_episode',930),('repeats',3),('cloud_app_workers',0),('no_retries',True),('paired_offsets_identical',True),('domain',['A','B']),('kernel',WORK)]:
        need(value.get(key)==expected,'Frozen protocol differs: '+key)
    budget=value['app_budget_per_host'];need(budget==dict(cpu_max='200000 100000',cpus='0-1',memory_bytes=536870912,swap_bytes=0),'Frozen application budget differs')
    return value


def audit_run(root,item,protocol,artifact):
    root=Path(root);directory=root/'runs'/item['run_id'];checks=[]
    spec=check(checks,'treatment_spec',lambda:load(directory/'A/spec.json'))
    if spec is None:return dict(run_id=item['run_id'],status='INCOMPLETE',checks=checks)
    def identity_check():
        need(all(spec[key]==item[key] for key in ('run_id','system','initial','fault','repeat')) and not spec.get('pilot'),'Treatment differs from frozen order')
        attempt=load(root/'evidence'/item['run_id']/'attempt.json');need(not attempt['pilot'] and attempt['started_unix_ns']>protocol['frozen_unix_ns'],'Treatment preceded protocol freeze')
        need(load(root/'evidence'/item['run_id']/'complete.json')['run_id']==item['run_id'],'Coordinator completion receipt missing')
        return dict(no_outcome_threshold=True)
    check(checks,'frozen_treatment_identity',identity_check)
    check(checks,'all_offers',lambda:check_arrivals(spec,jsonl(directory/'A/requests.jsonl')))
    def driver():
        value=load(directory/'A/driver.json');exit=load(directory/'A/driver-exit.json')
        need(value['offered']==value['finished']==930 and value['driver_valid'] is True and value['no_retries'] is True and exit['rc']==0,'Driver offer/count/no-retry receipt invalid')
        return dict(offered=930,finished=930,application_success_not_required=True)
    check(checks,'driver_receipt',driver)
    c_clock=check(checks,'C_clock_bound',lambda:check_clocks(load(directory/'A/clock-before.json'),load(directory/'A/clock-after.json')))
    b_clock=check(checks,'B_clock_bound',lambda:check_clocks(load(directory/'A/clock-B-before.json'),load(directory/'A/clock-B-after.json')))
    fault=check(checks,'actual_fault_scope_and_removal',lambda:check_fault(spec,directory))
    if fault and c_clock:
        check(checks,'C_outage_AB_reachable',lambda:check_connectivity(jsonl(directory/'A/cloud-probes.jsonl'),jsonl(directory/'A/peer-probes.jsonl'),fault['bounds'],item['fault'],c_clock['bound_ns']))
    if b_clock:
        check(checks,'initial_capacity_and_actual_ELF',lambda:check_initial(spec,directory,artifact,b_clock))
        check(checks,'physical_transport_readiness',lambda:check_ready(spec,directory,b_clock))
    check(checks,'all_owned_worker_effective_budgets',lambda:check_resources(spec,directory))
    check(checks,'bounded_owned_cleanup',lambda:check_cleanup(root,directory,item))
    return dict(run_id=item['run_id'],status='PASS' if all(row['status']=='PASS' for row in checks) else 'INCOMPLETE',checks=checks)


def audit_sources(root):
    manifest=load(root/'protocol/source-hashes.json');required={'campaign.py','deploy.py','ops.py','trace.py','protocol/protocol.json','protocol/order.json','protocol/artifact-provenance.json','source/tide-source.tar.gz'}
    for folder in ('runtime','source','report'):
        required|={str(path.relative_to(root)) for path in (root/folder).rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py','.go')}
    need(required<=set(manifest),'Frozen hash coverage missing: '+','.join(sorted(required-set(manifest))))
    for name,digest in manifest.items():
        path=root/name;need(not Path(name).is_absolute() and '..' not in Path(name).parts and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),'Unsafe frozen source path')
        need(HEX.fullmatch(digest) and hashlib.sha256(path.read_bytes()).hexdigest()==digest,'Frozen source changed: '+name)
    return dict(files=len(manifest),manifest_sha256=hashlib.sha256((root/'protocol/source-hashes.json').read_bytes()).hexdigest())


def audit_artifacts(root):
    value=load(root/'protocol/artifact-provenance.json');need(set(value['hosts'])=={'A','B','C'},'Artifact provenance requires all3 hosts')
    for key in ('worker_sha256','native_sha256','worker_oci_sha256','worker_image_digest','worker_image_ref'):
        values=[value['hosts'][host][key] for host in ('A','B','C')];need(len(set(values))==1,'Artifact differs across hosts: '+key)
        need(values[0]==IMAGE if key=='worker_image_ref' else bool(HEX.fullmatch(values[0].removeprefix('sha256:'))),'Malformed artifact identity: '+key)
    need(value['source_sha256']==hashlib.sha256((root/'source/tide-source.tar.gz').read_bytes()).hexdigest(),'Go source archive differs from artifact provenance')
    for name in ('edgemesh','metrics'):need(bool(value['images'][name].get('manifests')),'Missing pinned dependency OCI index: '+name)
    return value


def audit_campaign(root):
    root=Path(root);checks=[];runs=[]
    check(checks,'frozen_source_integrity',lambda:audit_sources(root))
    artifacts=check(checks,'binary_image_source_provenance',lambda:audit_artifacts(root))
    protocol=check(checks,'frozen_protocol',lambda:frozen_protocol(root))
    order=check(checks,'frozen_order',lambda:load(root/'protocol/order.json'))
    expected={(s,i,f,r) for s in ('tide','kubeedge','ray') for i in ('warm','cold') for f in (False,True) for r in (1,2,3)}
    def matrix():
        need(isinstance(order,list),'No frozen order')
        cells=[(row['system'],row['initial'],row['fault'],row['repeat']) for row in order]
        need(len(cells)==len(set(cells))==36 and set(cells)==expected,'Order is not exactly36 unique frozen conditions')
        need(len({row['run_id'] for row in order})==36,'Repeated formal run ID')
        return dict(conditions=36,replications=3)
    matrix_ok=check(checks,'complete_frozen_matrix',matrix) is not None
    if isinstance(order,list):
        for item in order:runs.append(audit_run(root,item,protocol or {},artifacts or {}))
    def paired_inputs():
        need(order and len(runs)==36,'All36 treatments needed for paired-input audit')
        traces=[load(root/'runs'/item['run_id']/'A/spec.json') for item in order]
        first=[row['offset_ns'] for row in traces[0]['arrivals']]
        need(all(value['workload']==WORK and [row['offset_ns'] for row in value['arrivals']]==first for value in traces),'Frozen kernel/offset trace differs across treatments')
        return dict(treatments=36,offers_each=930,request_ID_prefixes_are_run_specific=True)
    check(checks,'identical_all_treatment_inputs',paired_inputs)
    passed=matrix_ok and len(runs)==36 and all(row['status']=='PASS' for row in checks+runs)
    return dict(status='PASS' if passed else 'INCOMPLETE',matrix_complete=matrix_ok,expected_runs=36,checks=checks,runs=runs,
                audit_unix_ns=time.time_ns(),policy='Every frozen treatment retained. No application success, goodput, latency, or B expansion threshold qualifies a run.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--campaign',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    result=audit_campaign(args.campaign);args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],runs=len(result['runs']),output=str(args.output))));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
