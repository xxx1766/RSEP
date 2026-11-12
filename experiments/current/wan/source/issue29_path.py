"""Directed native path smoke with uncensored budget, not scored system ranking."""
import argparse
import json
import math
from pathlib import Path
import shlex
import statistics
import time
import issue29_common as p

VM='i23-r01'
VM_ROOT=p.GUEST_ROOT


def remote(where,guest,cmd):return p.guest(where,cmd) if guest else p.ssh(where,cmd)
def python(where,guest,code):return remote(where,guest,'python3 -c '+shlex.quote(code))
def copy(where,guest,src,dst,pull=False):
    return p.guest_copy(where,src,dst,pull) if guest else p.copy(where,src,dst,pull)


def launch(where,guest,root,argv,env,folder,label):
    remote(where,guest,'mkdir -p '+root)
    spec=folder/(label+'-spec.json');p.save(spec,dict(argv=argv,env=env));copy(where,guest,spec,root+'/spec.json')
    helper=(VM_ROOT if guest else p.REMOTE)+'/issue12_nj_job.py'
    code="import subprocess,json;from pathlib import Path;log=Path("+repr(root+'/wrapper.log')+").open('w');q=subprocess.Popen(['python3',"+repr(helper)+",'run',"+repr(root)+"],stdout=log,stderr=subprocess.STDOUT,start_new_session=True);print(json.dumps(dict(wrapper_pid=q.pid)))"
    p.save(folder/(label+'-launch.json'),json.loads(python(where,guest,code)))
    return where,guest,root,helper


def status(job):
    where,guest,root,helper=job
    for attempt in range(3):
        try:return json.loads(remote(where,guest,shlex.join(['python3',helper,'status',root])))
        except RuntimeError as error:
            if 'command failed (255)' not in str(error) or attempt==2:raise
            time.sleep(attempt+1)


def stop_collect(job,folder,label):
    where,guest,root,helper=job
    state=status(job)
    if state['phase']=='running':remote(where,guest,shlex.join(['python3',helper,'stop',root]))
    for _ in range(20):
        if status(job)['phase']=='finished':break
        time.sleep(.5)
    else:raise RuntimeError('owned helper did not finish')
    copy(where,guest,root,folder/label,pull=True)


def shape(rtt,folder):
    link=json.loads(p.guest(VM,'ip -j link show dev enp0s3'))[0]
    if link.get('address')!='52:54:00:23:01:15':raise RuntimeError('dedicated guest NIC identity changed')
    if rtt=='unshaped':
        for invoke,interface in [(lambda c:p.guest(VM,c),'enp0s3'),(lambda c:p.ssh('NJ-k2',c),'i23tap21')]:
            roots=[r for r in json.loads(invoke('tc -j qdisc show dev '+interface)) if r.get('root')]
            for root in roots:
                if root['kind']=='netem' and root['handle']=='1:':invoke('tc qdisc del dev '+interface+' root')
                elif root.get('handle') not in ('0:',None):raise RuntimeError('unexpected owned-interface root qdisc; preserve and stop')
        p.save(folder/'shape.json',dict(rtt=rtt,guest=p.guest(VM,'tc -s qdisc show dev enp0s3'),tap=p.ssh('NJ-k2','tc -s qdisc show dev i23tap21')))
        return
    delay={'25':12,'65':32}[rtt]
    commands=[['tc','qdisc','replace','dev','enp0s3','root','handle','1:','netem','limit','100000','delay',f'{delay}ms','5ms','loss','0.1%','rate','1gbit'],
              ['tc','qdisc','replace','dev','enp0s3','parent','1:','handle','10:','pfifo','limit','100000']]
    for cmd in commands:p.guest(VM,shlex.join(cmd))
    for cmd in commands:p.ssh('NJ-k2',shlex.join([x.replace('enp0s3','i23tap21') for x in cmd]))
    p.save(folder/'shape.json',dict(rtt=rtt,delay_ms=delay,guest=p.guest(VM,'tc -s qdisc show dev enp0s3'),tap=p.ssh('NJ-k2','tc -s qdisc show dev i23tap21')))


def one(name,rtt,rate,count=20):
    folder=p.OUT/'validation/paths'/name;folder.mkdir(parents=True,exist_ok=False)
    shape(rtt,folder)
    trace=[dict(id=f'{name}-{i:04d}',scheduled_offset_ns=round(i*1e9/rate)) for i in range(count)]
    path=folder/'trace.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in trace));trace_remote=p.REMOTE+'/runs/'+name+'/trace.jsonl'
    root=p.REMOTE+'/runs/'+name;worker_root=VM_ROOT+'/runs/'+name
    p.ssh('NJ-k3','mkdir -p '+root);p.guest(VM,'mkdir -p '+worker_root);p.copy('NJ-k3',path,trace_remote)
    before=int(p.guest(VM,'stat -c %s '+p.VM_REMOTE+'/app-output/requests.jsonl'))
    worker=dict(node_id=VM,listen=p.GUEST_SUBNET+'.21:30731',origin_addr=p.GUEST_SUBNET+'.3:30683',total_cpu=1,
                execution_ms=100,qos_ms=2000,warmup_qos_ms=2000,workload=dict(kind='cpu',bytes=64,iterations=1),
                ready_path=worker_root+'/ready.json',resource_path=worker_root+'/resources.jsonl',spans_path=worker_root+'/spans.jsonl',heartbeat=True)
    spec=folder/'worker.json';p.save(spec,worker);p.guest_copy(VM,spec,worker_root+'/worker.json')
    job=launch(VM,True,worker_root+'/worker',[p.VM_REMOTE+'/tide-vm-test','-test.run=^TestIssue23WorkerProcess$','-test.timeout=10m','-test.v'],
               dict(TMC_ISSUE5_WORKER_CONFIG=worker_root+'/worker.json',TMC_ISSUE12_IMAGE_ENDPOINT='http://127.0.0.1:19383',GOMAXPROCS='1'),folder,'worker')
    origin_job=None
    try:
        for _ in range(30):
            if remote(VM,True,'test -s '+worker_root+'/ready.json && echo ready || true')=='ready':break
            state=status(job)
            if state['phase']=='finished':raise RuntimeError('worker exited before readiness: '+str(state))
            time.sleep(.5)
        else:raise RuntimeError('worker readiness failed')
        run=dict(id=name,rate=rate,pattern='steady',repeat=1,seed=20261031,duration_ms=math.ceil(count*1000/rate),
                 payload_bytes=0,weight_name='equal',weights=[1,1,1],trace_path=trace_remote)
        origin=dict(listen=p.GUEST_SUBNET+'.3:30683',advertise_addr=p.GUEST_SUBNET+'.3:30683',
                    candidates=[dict(node_id=VM,address=p.GUEST_SUBNET+'.21:30731',total_cpu=1,network_ms=0 if rtt=='unshaped' else int(rtt))],
                    workload=dict(kind='cpu',bytes=64,iterations=1),output_dir=root+'/output',resource_path=root+'/origin-resources.jsonl',spans_path=root+'/origin-spans.jsonl',
                    qos_ms=2000,warmup_qos_ms=2000,execution_ms=100,node_execution_ms={VM:100},entry='external',max_inflight=48,runs=[run])
        p.save(folder/'origin.json',origin);p.copy('NJ-k3',folder/'origin.json',root+'/origin.json')
        origin_job=launch('NJ-k3',False,root+'/origin',['taskset','-c','12,13',p.REMOTE+'/tide-vm-test','-test.run=^TestIssue23OriginProcess$','-test.timeout=10m','-test.v'],
                          dict(TMC_ISSUE5_ORIGIN_CONFIG=root+'/origin.json',TMC_ISSUE12_IMAGE_DIR=p.REMOTE+'/inputs',GOMAXPROCS='2'),folder,'origin')
        for step in range(200):
            state=status(origin_job)
            if state['phase']=='finished':break
            if state['phase']=='missing_process':raise RuntimeError('origin disappeared')
            if step%5==0:print('NATIVE_PATH_RUNNING',name,state['phase'],flush=True)
            time.sleep(2)
        else:raise RuntimeError('origin timeout')
        stop_collect(origin_job,folder,'origin-job')
        if state['exit_code']!=0:raise RuntimeError('native origin failed')
        for file in ['requests.jsonl','run.json']:p.copy('NJ-k3',root+'/output/'+name+'/'+file,folder/file,pull=True)
    except BaseException as error:
        p.save(folder/'failed.json',dict(error=str(error),unix_ns=time.time_ns()));raise
    finally:
        stop_collect(job,folder,'worker-job')
        for file in ['resources.jsonl','spans.jsonl']:p.guest_copy(VM,worker_root+'/'+file,folder/('worker-'+file),pull=True)
        body=python(VM,True,"from pathlib import Path;import sys;f=Path('+repr(p.VM_REMOTE+'/app-output/requests.jsonl')+').open('rb');f.seek("+str(before)+");sys.stdout.write(f.read().decode())")
        (folder/'server.jsonl').write_text(body+'\n')
    rows=[json.loads(x) for x in (folder/'requests.jsonl').read_text().splitlines()]
    assert len(rows)==count and {r['id'] for r in rows}=={r['id'] for r in trace}
    servers=[json.loads(x) for x in (folder/'server.jsonl').read_text().splitlines() if x.strip()]
    lat=[]
    for row in rows:
        events=[s for s in servers if s['request_id']==row['id']]
        if row['status']=='success':
            assert len(events)==1 and events[0]['status']=='success' and events[0]['input_sha256']==row['input_sha256']
            assert row['receiver']=='issue5-worker-'+VM and events[0]['checksum']==row['checksum'];lat.append(row['latency_ms'])
    result=dict(name=name,scope='uncensored native timing smoke;135ms is post-hoc path feasibility, not admission performance',rtt=rtt,rate=rate,
                offered=count,success=len(lat),timely135=sum(t<=135 for t in lat),median_ms=statistics.median(lat) if lat else None,
                p95_ms=sorted(lat)[math.ceil(.95*len(lat))-1] if lat else None,trace_sha256=p.digest(path))
    p.save(folder/'path-summary.json',result);print('PATH_SMOKE_RESULT',json.dumps(result),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--name',required=True);parser.add_argument('--rtt',choices=['unshaped','25','65'],required=True);parser.add_argument('--rate',type=int,required=True);a=parser.parse_args();one(a.name,a.rtt,a.rate)
