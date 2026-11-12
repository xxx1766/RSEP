"""Complete-path validation using actual application Pods and private driver VM."""
import json
import math
from pathlib import Path
import shlex
import statistics
import time
import issue29_common as p
from issue29_path import launch, status, stop_collect, shape

DRIVER='i23-d01'
ROOT=p.GUEST_ROOT


def vm_ip(vm):return p.GUEST_SUBNET+'.'+str((10 if vm[4]=='l' else 20)+int(vm[-2:]))


def new_trace(folder,name,count,rate):
    path=folder/'trace.jsonl'
    path.write_text(''.join(json.dumps(dict(id=f'{name}-{i:04d}',scheduled_offset_ns=round(i*1e9/rate)))+'\n' for i in range(count)))
    return path


def server_offsets(vms):
    return {vm:int(p.guest(vm,'stat -c %s '+p.VM_REMOTE+'/pod-output/requests.jsonl')) for vm in vms}


def collect_servers(folder,offsets):
    all_rows=[]
    for vm,offset in offsets.items():
        code="from pathlib import Path;import sys;f=Path('+repr(p.VM_REMOTE+'/pod-output/requests.jsonl')+').open('rb');f.seek("+str(offset)+");sys.stdout.write(f.read().decode())"
        body=p.guest(vm,'python3 -c '+shlex.quote(code));(folder/(vm+'-server.jsonl')).write_text(body+'\n')
        all_rows.extend(dict(json.loads(x),executing_vm=vm) for x in body.splitlines() if x.strip())
    return all_rows


def wait(job,name,seconds):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        row=status(job)
        if row['phase']=='finished':
            if row['exit_code']!=0:raise RuntimeError('job failed: '+name+' '+str(row))
            return row
        if row['phase']=='missing_process':raise RuntimeError('owned job disappeared: '+name)
        time.sleep(2)
    raise RuntimeError('job timeout: '+name)


def native(folder,name,trace,candidates,rate,qos,execution,network,window_ms=None):
    root=ROOT+'/runs/'+name;p.guest(DRIVER,'mkdir -p '+root);p.guest_copy(DRIVER,trace,root+'/trace.jsonl')
    count=len(trace.read_text().splitlines());duration=window_ms if window_ms is not None else math.ceil(count*1000/rate)
    jobs=[];origin_job=None
    try:
        for vm in candidates:
            worker_root=ROOT+'/runs/'+name;p.guest(vm,'mkdir -p '+worker_root)
            config=dict(node_id=vm,listen=vm_ip(vm)+':30731',origin_addr=p.GUEST_SUBNET+'.100:30683',total_cpu=1,
                execution_ms=execution[vm],qos_ms=qos,warmup_qos_ms=2000,
                workload=dict(kind='cpu',bytes=64,iterations=1),ready_path=worker_root+'/ready.json',
                resource_path=worker_root+'/resources.jsonl',spans_path=worker_root+'/spans.jsonl',heartbeat=True)
            file=folder/(vm+'-worker.json');p.save(file,config);p.guest_copy(vm,file,worker_root+'/worker.json')
            jobs.append((vm,launch(vm,True,worker_root+'/worker',[ROOT+'/tide-vm-test','-test.run=^TestIssue23WorkerProcess$','-test.timeout=30m','-test.v'],
                dict(TMC_ISSUE5_WORKER_CONFIG=worker_root+'/worker.json',TMC_ISSUE12_IMAGE_ENDPOINT='http://127.0.0.1:19383',GOMAXPROCS='1'),folder,vm)))
            for _ in range(30):
                if p.guest(vm,'test -s '+worker_root+'/ready.json && echo ready || true')=='ready':break
                if status(jobs[-1][1])['phase']=='finished':raise RuntimeError('native worker exited before readiness')
                time.sleep(.5)
            else:raise RuntimeError('native worker readiness timeout')
        spec=dict(id=name,rate=max(1,round(rate)),pattern='steady',repeat=1,seed=20261031,duration_ms=duration,
            payload_bytes=0,weight_name='equal',weights=[1,1,1],trace_path=root+'/trace.jsonl')
        origin=dict(listen=p.GUEST_SUBNET+'.100:30683',advertise_addr=p.GUEST_SUBNET+'.100:30683',
            candidates=[dict(node_id=vm,address=vm_ip(vm)+':30731',total_cpu=1,network_ms=network.get(vm,0)) for vm in candidates],
            workload=dict(kind='cpu',bytes=64,iterations=1),output_dir=root+'/output',resource_path=root+'/resources.jsonl',spans_path=root+'/spans.jsonl',
            qos_ms=qos,warmup_qos_ms=2000,execution_ms=max(execution.values()),node_execution_ms={v:execution[v] for v in candidates},entry='external',max_inflight=48,runs=[spec])
        p.save(folder/'origin.json',origin);p.guest_copy(DRIVER,folder/'origin.json',root+'/origin.json')
        origin_job=launch(DRIVER,True,root+'/origin',[ROOT+'/tide-vm-test','-test.run=^TestIssue23OriginProcess$','-test.timeout=30m','-test.v'],
            dict(TMC_ISSUE5_ORIGIN_CONFIG=root+'/origin.json',TMC_ISSUE12_IMAGE_DIR=ROOT+'/inputs',GOMAXPROCS='2'),folder,'origin')
        wait(origin_job,name,duration/1000+120)
        for file in ['requests.jsonl','run.json']:p.guest_copy(DRIVER,root+'/output/'+name+'/'+file,folder/file,pull=True)
    finally:
        if origin_job is not None:
            stop_collect(origin_job,folder,'origin-job')
            for file in ['resources.jsonl','spans.jsonl']:p.guest_copy(DRIVER,root+'/'+file,folder/('origin-'+file),pull=True)
        for vm,job in jobs:
            stop_collect(job,folder,vm+'-job')
            for file in ['resources.jsonl','spans.jsonl']:p.guest_copy(vm,ROOT+'/runs/'+name+'/'+file,folder/(vm+'-'+file),pull=True)


def http(folder,name,trace,endpoint,rate,qos=135,window_ms=None):
    root=ROOT+'/runs/'+name;p.guest(DRIVER,'mkdir -p '+root);p.guest_copy(DRIVER,trace,root+'/trace.jsonl')
    count=len(trace.read_text().splitlines());duration=window_ms if window_ms is not None else math.ceil(count*1000/rate)
    args=['python3',ROOT+'/issue22_service_client.py','--trace',root+'/trace.jsonl','--endpoint',endpoint,
        '--inputs',ROOT+'/inputs','--qos-ms',str(qos),'--window-ms',str(duration),'--channels','48','--out',root+'/output']
    job=launch(DRIVER,True,root+'/client',args,{},folder,'client')
    try:
        wait(job,name,duration/1000+60)
        for file in ['requests.jsonl','run.json']:p.guest_copy(DRIVER,root+'/output/'+file,folder/file,pull=True)
    finally:stop_collect(job,folder,'client-job')


def audit(rows,servers,trace,qos,golden=None):
    if golden is None:
        manifest=json.loads((p.OUT/'guests/i23-l01-manifest.json').read_text())
        golden={i['input_sha256']:i['output_sha256'] for i in manifest['inputs']}
    offered=[json.loads(x) for x in Path(trace).read_text().splitlines()]
    ids=[r['id'] for r in offered]
    if len(rows)!=len(ids) or len(set(ids))!=len(ids) or {r['id'] for r in rows}!=set(ids):raise ValueError('offered identity mismatch')
    grouped={i:[] for i in ids}
    for row in servers:
        if row['request_id'] in grouped:grouped[row['request_id']].append(row)
    lat=[];executed={};timely_remote=0;no_execution=0;server_errors=0;rejections=0
    for row in rows:
        events=grouped[row['id']]
        if len(events)>1:raise ValueError('duplicate physical execution')
        if not events:
            no_execution+=1
            if row.get('error') in ('no feasible execution destination','request exceeds destination stub capacity'):rejections+=1
        else:
            event=events[0];vm=event['executing_vm'];executed[vm]=executed.get(vm,0)+1
            if event['status']!='success':no_execution+=1;server_errors+=1
            if (event['status']=='success' or 'input_sha256' in event) and event.get('input_sha256')!=row.get('input_sha256'):raise ValueError('physical input mismatch')
        if row['status']=='success':
            if len(events)!=1 or events[0]['status']!='success':raise ValueError('success has no physical execution')
            event=events[0]
            if (not event.get('finite') or event.get('shape')!=[1,3549,85] or
                    event.get('output_sha256')!=golden.get(row['input_sha256'])):raise ValueError('physical inference golden output mismatch')
            if str(event['checksum'])!=str(row['checksum']):raise ValueError('return checksum mismatch')
            if row.get('worker_response',{}).get('output_sha256',event['output_sha256'])!=event['output_sha256']:raise ValueError('output mismatch')
            lat.append(row['latency_ms'])
            if event['executing_vm'].startswith('i23-r') and row['latency_ms']<=qos:timely_remote+=1
        expected=row['status']=='success' and row['latency_ms']<=qos
        if row['on_time']!=expected:raise ValueError('deadline labeling mismatch')
    return dict(offered=len(rows),success=len(lat),on_time=sum(r['on_time'] for r in rows),
        median_ms=statistics.median(lat) if lat else None,p95_ms=sorted(lat)[math.ceil(.95*len(lat))-1] if lat else None,
        executed=executed,timely_remote=timely_remote,no_successful_server_execution=no_execution,server_errors=server_errors,admission_rejections=rejections,trace_sha256=p.digest(trace))


def one(name,transport,rtt,rate,count,execution,qos=135):
    folder=p.OUT/'validation/final-paths'/name;folder.mkdir(parents=True,exist_ok=False)
    shape(rtt,folder);trace=new_trace(folder,name,count,rate);before=server_offsets(['i23-l01','i23-r01'])
    try:
        if transport=='native':native(folder,name,trace,['i23-r01'],rate,qos,execution,{'i23-r01':0 if rtt=='unshaped' else int(rtt)})
        elif transport=='http':http(folder,name,trace,p.GUEST_SUBNET+'.21:19383',rate,qos)
        else:raise ValueError('unknown transport')
    except BaseException as error:p.save(folder/'failed.json',dict(error=str(error),unix_ns=time.time_ns()));raise
    finally:servers=collect_servers(folder,before)
    rows=[json.loads(x) for x in (folder/'requests.jsonl').read_text().splitlines()]
    result=dict(name=name,transport=transport,rtt=rtt,rate=rate,deadline_ms=qos,**audit(rows,servers,trace,qos))
    if transport=='http':
        summary=json.loads((folder/'run.json').read_text());summary.update(policy='direct-HTTP diagnostic',endpoint_selection='forced remote VM; no placement-policy claim');p.save(folder/'run.json',summary)
    p.save(folder/'audit.json',result);print('FINAL_PATH_RESULT',json.dumps(result),flush=True)
    return result
