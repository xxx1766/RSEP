#!/usr/bin/env python3
"""Serial, matched-kernel control-plane experiment with complete process evidence."""
import argparse
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import signal
import shutil
import subprocess
import time
import urllib.request

import aiohttp
from control_metrics import processes, descendants, memory, container_root, inherited_cgroup_value
from run_kubeedge_matrix import kubectl, assert_edge_node, ready_http

BASE=Path('/root/rsep-revision-20260928')
WORK=dict(kind='cpu', bytes=65536, iterations=100)

def save(path, value):
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value,indent=2)+'\n'); tmp.replace(path)

def command(args, **kw):
    return subprocess.check_output([str(x) for x in args], text=True, stderr=subprocess.STDOUT, **kw)

def ready(url, proc=None):
    limit=time.monotonic()+100
    while time.monotonic()<limit:
        if proc and proc.poll() is not None: raise RuntimeError('framework launcher exited')
        try:
            with urllib.request.urlopen(url+'/health',timeout=1) as r:
                if r.status==200: return
        except OSError: time.sleep(.2)
    raise RuntimeError('readiness timed out')

def cleanup_owned(owned, path):
    actions=[]
    for sig in (signal.SIGTERM,signal.SIGKILL):
        current=processes()
        alive=[pid for pid,start in owned.items() if pid in current and current[pid]['start']==start]
        if not alive: break
        for pid in alive:
            try: os.kill(pid,sig); actions.append(dict(pid=pid,start=owned[pid],signal=int(sig)))
            except ProcessLookupError: pass
        time.sleep(2)
    current=processes()
    remaining=[pid for pid,start in owned.items() if pid in current and current[pid]['start']==start and current[pid]['cmd']]
    save(path,dict(actions=actions,remaining=remaining))
    if remaining: raise RuntimeError(f'owned Ray processes remain: {remaining}')

class Deployment:
    def __init__(self, system, root, args):
        self.system,self.root,self.args=system,root,args
        self.proc=None; self.uid=None; self.log=None
        self.url='http://127.0.0.1:19280'
        self.ke_roots=[]; self.worker_pid=None; self.owned={}
    def start(self):
        setup={}
        if self.system=='tide':
            if len(command(['ctr','-n','tide','tasks','ls']).splitlines())!=1:
                raise RuntimeError('pre-existing Tide tasks; refusing concurrent run')
            self.log=(self.root/'service.log').open('w')
            self.proc=subprocess.Popen([str(self.args.tide)],stdout=self.log,stderr=subprocess.STDOUT,
                                       env=dict(os.environ,GOMAXPROCS='2'))
        elif self.system=='ray':
            self.log=(self.root/'service.log').open('w')
            cmd=[str(BASE/'ray-venv/bin/python'),str(Path(__file__).with_name('ray_serve_baseline.py')),
                 '--worker',str(self.args.worker),'--state-dir',str(self.root/'ray-state')]
            self.proc=subprocess.Popen(cmd,stdout=self.log,stderr=subprocess.STDOUT)
        else:
            template=json.loads((BASE/'kubeedge-tools/kubeedge_worker.json').read_text())
            self.namespace=template['metadata']['namespace']
            self.name='tmc-control-'+self.root.name
            template['metadata']['name']=self.name
            node,version=assert_edge_node(self.args.kubeconfig,template['spec']['nodeName'])
            self.url='http://'+next(a['address'] for a in node['status']['addresses'] if a['type']=='InternalIP')+':19280'
            created=json.loads(kubectl(self.args.kubeconfig,'create','-f','-','-o','json',input=json.dumps(template)))
            self.uid=created['metadata']['uid']
            kubectl(self.args.kubeconfig,'wait','-n',self.namespace,'--for=condition=Ready','pod/'+self.name,'--timeout=90s')
            setup.update(pod=json.loads(kubectl(self.args.kubeconfig,'get','pod','-n',self.namespace,self.name,'-o','json')),edge_version=version)
            for name in ('tmc-ke-control-plane','tmc-ke-worker'):
                data=json.loads(command([BASE/'kubeedge-tools/bin/nerdctl','inspect',name]))[0]
                pid=int(data['State']['Pid'])
                self.ke_roots.append(container_root(Path(f'/proc/{pid}/cgroup').read_text()))
            matches=[p for p in processes().values() if '/worker --mode=http' in p['cmd'] and self.uid.replace('-','_') in p['cgroup']]
            if len(matches)!=1: raise RuntimeError(f'worker identity ambiguous: {matches}')
            self.worker_pid=matches[0]['pid']
            command(['taskset','-apc','0,1',self.worker_pid])
        ready(self.url,self.proc)
        if self.system=='ray':
            setup['identity']=json.loads((self.root/'ray-state/identity.json').read_text())
            self.worker_pid=setup['identity']['worker_pid']
        if self.system=='tide':
            matches=[p for p in processes().values() if p['cmd'].startswith('/worker --mode=paix')]
            if len(matches)!=1: raise RuntimeError('expected one Tide kernel')
            self.worker_pid=matches[0]['pid']
            command(['taskset','-apc','0,1',self.worker_pid])
        masks={p.name:sorted(os.sched_getaffinity(int(p.name))) for p in Path(f'/proc/{self.worker_pid}/task').iterdir()}
        if any(v!=[0,1] for v in masks.values()): raise RuntimeError(f'worker affinity mismatch {masks}')
        setup['worker_exe_sha256']=hashlib.sha256(Path(f'/proc/{self.worker_pid}/exe').read_bytes()).hexdigest()
        if setup['worker_exe_sha256']!=hashlib.sha256(self.args.worker.read_bytes()).hexdigest():
            raise RuntimeError('application binary differs from matched worker')
        setup.update(url=self.url,worker_pid=self.worker_pid,worker_affinity=masks,ke_cgroup_roots=self.ke_roots,
                     launcher_pid=self.proc.pid if self.proc else None)
        self.setup=setup
        save(self.root/'setup.json',setup)
    def inventory(self):
        allp=processes(); children=descendants(allp,self.proc.pid) if self.proc else set()
        selected=[]
        for pid,p in allp.items():
            group=None; cmd=p['cmd']; comm=p['comm']
            if self.system=='ray' and pid in children:
                group='application' if pid==self.worker_pid else ('request_path' if 'ServeReplica' in cmd or 'ProxyActor' in cmd else 'core')
            elif self.system=='tide':
                if pid==self.proc.pid: group='core'
                elif cmd.startswith('/worker --mode=paix'): group='application'
                elif 'containerd-shim' in cmd and '-namespace tide ' in cmd: group='runtime_support'
                elif comm=='containerd' and p['cgroup']=='0::/system.slice/containerd.service': group='shared_host_runtime'
            elif self.system=='kubeedge' and any(p['cgroup'].split('::',1)[-1].startswith(r+'/') or p['cgroup'].split('::',1)[-1]==r for r in self.ke_roots):
                if pid==self.worker_pid: group='application'
                elif comm=='edgecore': group='edgecore'
                elif comm=='cloudcore': group='cloudcore'
                else: group='dedicated_cluster_support'
            if group:
                if group=='application':
                    p['affinity']=sorted(os.sched_getaffinity(pid))
                    if p['affinity']!=[0,1]: raise RuntimeError(f'application affinity changed: {p}')
                    cg=Path('/sys/fs/cgroup')/p['cgroup'].split('::',1)[-1].lstrip('/')
                    p['cpu_max']=(cg/'cpu.max').read_text().strip() if (cg/'cpu.max').exists() else None
                    p['cpuset_effective'],p['cpuset_source']=inherited_cgroup_value(cg,'cpuset.cpus.effective')
                p['group']=group; selected.append(memory(p))
                if self.system=='ray': self.owned[pid]=p['start']
        if not any(p['pid']==self.worker_pid for p in selected): raise RuntimeError('application vanished from process inventory')
        return selected
    def stop(self):
        if self.proc:
            self.proc.terminate()
            try: self.proc.wait(timeout=50)
            except subprocess.TimeoutExpired: raise RuntimeError('framework shutdown stuck; inspect before cleanup')
            self.log.close()
            if self.system=='ray':
                cleanup_owned(self.owned,self.root/'cleanup.json')
            if self.proc.returncode not in (0,-signal.SIGTERM): raise RuntimeError(f'framework exit {self.proc.returncode}')
        if self.uid:
            now=json.loads(kubectl(self.args.kubeconfig,'get','pod','-n',self.namespace,self.name,'-o','json'))
            if now['metadata']['uid']!=self.uid: raise RuntimeError('Pod UID changed; refusing deletion')
            kubectl(self.args.kubeconfig,'delete','pod','-n',self.namespace,self.name,'--wait=true','--timeout=30s')

async def measure(dep, root, rate, ddl, duration):
    root.mkdir()
    sample_file=(root/'samples.jsonl').open('w')
    def append_sample(value):
        samples.append(value); sample_file.write(json.dumps(value)+'\n'); sample_file.flush()
    stop=asyncio.Event(); samples=[]
    async def sample():
        while not stop.is_set():
            t=time.monotonic(); ps=await asyncio.to_thread(dep.inventory)
            append_sample(dict(monotonic_s=t,unix_ns=time.time_ns(),processes=ps))
            try: await asyncio.wait_for(stop.wait(),timeout=max(.01,.5-(time.monotonic()-t)))
            except asyncio.TimeoutError: pass
    sampling=asyncio.create_task(sample())
    rows=[]
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0),timeout=aiohttp.ClientTimeout(total=30)) as client:
        start=time.monotonic(); start_unix=time.time_ns()
        async def request(i,planned):
            submit=time.monotonic(); status=0; service=0; error=''; processed=0
            try:
                async with client.post(dep.url+'/run',json=WORK,headers={'X-Qos-Ms':str(ddl)}) as response:
                    status=response.status; body=await response.text()
                    if status==200:
                        parsed=json.loads(body); service=parsed['service_ns']; processed=parsed['result']['bytes_processed']
                        if processed!=WORK['bytes']*WORK['iterations'] or service<=0: raise ValueError('kernel result mismatch')
                    else: error=body[:400]
            except Exception as e: status=599; error=str(e)
            reply=time.monotonic()
            rows.append(dict(req_id=i,planned_s=planned-start,submit_s=submit-start,reply_s=reply-start,
                             latency_ms=(reply-submit)*1000,status=status,ddl_ms=ddl,worker_service_ns=service,
                             bytes_processed=processed,error=error))
        try:
            tasks=[]
            if rate:
                for i in range(round(rate*duration)):
                    planned=start+i/rate
                    await asyncio.sleep(max(0,planned-time.monotonic()))
                    tasks.append(asyncio.create_task(request(i,planned)))
            await asyncio.sleep(max(0,start+duration-time.monotonic()))
            arrival_end=time.monotonic()
            await asyncio.gather(*tasks)
            end=time.monotonic()
            # Serialize final snapshot with periodic sampler to preserve timestamp order.
            stop.set(); await sampling
            append_sample(dict(monotonic_s=time.monotonic(),unix_ns=time.time_ns(),processes=await asyncio.to_thread(dep.inventory)))
        finally:
            stop.set()
            try: await sampling
            finally: sample_file.close()
    if rows:
        with (root/'requests.csv').open('w') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(sorted(rows,key=lambda r:r['req_id']))
    meta=dict(rate=rate,ddl_ms=ddl,duration_s=duration,start_monotonic_s=start,arrival_end_monotonic_s=arrival_end,
              end_monotonic_s=end,start_unix_ns=start_unix,requests=len(rows),samples=len(samples),
              success=sum(r['status']==200 for r in rows),hz=os.sysconf('SC_CLK_TCK'),work=WORK)
    save(root/'measurement.json',meta)
    return meta

async def main(args):
    args.out.mkdir(parents=True,exist_ok=args.resume)
    os.setpriority(os.PRIO_PROCESS,0,0)  # zsh background jobs otherwise inherit nice=5
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    target=max(16384,soft)
    if hard!=resource.RLIM_INFINITY and hard<target: raise RuntimeError('file descriptor hard limit too small for open-loop matrix')
    resource.setrlimit(resource.RLIMIT_NOFILE,(target,hard))
    jobs=[]; rng=random.Random(20261004)
    for repeat in range(1,args.repeats+1):
        systems=args.systems.split(','); rng.shuffle(systems)
        for system in systems:
            cells=[(r,d) for r in map(int,args.rates.split(',')) for d in map(int,args.ddls.split(','))]; rng.shuffle(cells)
            jobs.append(dict(system=system,repeat=repeat,cells=cells,status='pending'))
    manifest=dict(status='running',nofile_before=[soft,hard],nofile_after=list(resource.getrlimit(resource.RLIMIT_NOFILE)),pid=os.getpid(),jobs=jobs,started_unix_ns=time.time_ns(),duration_s=args.duration,idle_s=args.idle,
                  argv=vars(args)|{'out':str(args.out),'tide':str(args.tide),'worker':str(args.worker),'kubeconfig':str(args.kubeconfig)},
                  hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.tide,args.worker,Path(__file__),Path(__file__).with_name('control_metrics.py'),Path(__file__).with_name('ray_serve_baseline.py')]})
    if args.resume:
        prior=json.loads((args.out/'progress.json').read_text())
        if prior['status']!='failed': raise RuntimeError('resume requires a verified failed supervisor')
        try: os.kill(prior['pid'],0)
        except ProcessLookupError: pass
        else: raise RuntimeError('prior PID is still alive; inspect before resuming')
        if [(j['system'],j['repeat'],j['cells']) for j in jobs] != [(j['system'],j['repeat'],[tuple(c) for c in j['cells']]) for j in prior['jobs']]:
            raise RuntimeError('resume would change the randomized plan')
        for key in ('duration_s','idle_s'):
            if manifest[key]!=prior[key]: raise RuntimeError('resume protocol changed')
        archive=args.out/'attempts'/str(prior['pid']); archive.mkdir(parents=True,exist_ok=False)
        shutil.copy2(args.out/'progress.json',archive/'progress.json')
        for j in prior['jobs']:
            block=args.out/f"{j['system']}-r{j['repeat']}"
            if j['status']=='failed' and block.exists():
                block.rename(archive/block.name)
                j['status']='pending'
            elif j['status']=='completed':
                j['collected_by_pid']=j.get('collected_by_pid',prior['pid'])
        manifest['jobs']=jobs=prior['jobs']
        manifest['history']=prior.get('history',[])+[dict(pid=prior['pid'],status=prior['status'],error=prior.get('error'),hashes=prior['hashes'],archive=str(archive))]
        manifest['original_started_unix_ns']=prior.get('original_started_unix_ns',prior['started_unix_ns'])
    save(args.out/'progress.json',manifest)
    try:
        for job in jobs:
            if job['status']=='completed': continue
            root=args.out/f"{job['system']}-r{job['repeat']}"; root.mkdir()
            dep=Deployment(job['system'],root,args)
            job.update(status='running',completed_cells=0); save(args.out/'progress.json',manifest)
            try:
                dep.start()
                warm=await measure(dep,root/'warmup',5,1000,2)
                if warm['success']!=warm['requests']: raise RuntimeError(f'warmup failed {warm}')
                await asyncio.sleep(2)
                await measure(dep,root/'idle',0,1000,args.idle)
                for rate,ddl in job['cells']:
                    meta=await measure(dep,root/f'rps{rate}-ddl{ddl}',rate,ddl,args.duration)
                    if meta['requests']!=round(rate*args.duration): raise RuntimeError('incomplete request trace')
                    job['completed_cells']+=1
                    save(args.out/'progress.json',manifest)
                    print(f"{job['system']} repeat={job['repeat']} rps={rate} ddl={ddl} success={meta['success']}/{meta['requests']}",flush=True)
                    await asyncio.sleep(2)
            finally: dep.stop()
            job['status']='completed'; job['collected_by_pid']=os.getpid(); save(args.out/'progress.json',manifest)
    except BaseException as e:
        if job['status']=='running': job['status']='failed'
        manifest.update(status='failed',error=repr(e),finished_unix_ns=time.time_ns()); save(args.out/'progress.json',manifest); raise
    manifest.update(status='completed',finished_unix_ns=time.time_ns()); save(args.out/'progress.json',manifest)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--tide',type=Path,required=True)
    p.add_argument('--worker',type=Path,default=BASE/'revision-worker')
    p.add_argument('--kubeconfig',type=Path,default=BASE/'kubeedge-tools/kind-kubeconfig')
    p.add_argument('--duration',type=int,default=30); p.add_argument('--idle',type=int,default=20)
    p.add_argument('--repeats',type=int,default=3); p.add_argument('--systems',default='tide,kubeedge,ray')
    p.add_argument('--rates',default='5,20,80'); p.add_argument('--ddls',default='50,200,1000')
    asyncio.run(main(p.parse_args()))
