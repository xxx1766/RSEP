"""Exclusive, serial Fig7 campaign runner; every failed attempt is retained."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from protocol import (IMAGE,DIGEST,APP_MASK,DRIVER_CPUS,MAPPING,make_plan,case_path,manifest_fields,save,sha,demand,threads)
from sampler import Sampler,backend_id
from settled_cleanup import remove_owned

from runtime_config import BASE, LEGACY_ROOT, PYTHON, REPO
UUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def environment(root,case):
    return dict(PATH=os.environ.get('PATH','/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'),LANG='C.UTF-8',
                PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1',REPO_ROOT=str(REPO),RESULTS_DIR=str(root),
                HTTPBASE_BIN=str(REPO/'bin/httpbase'),RESPTESTV2_BIN=str(REPO/'bin/resptestv2-cpu-budget'),
                TIDE_LEGACY_HOST_ROOT=str(LEGACY_ROOT),TIDE_REQUEST_CPUS=str(demand(case)),TIDE_TORCH_THREADS=str(threads(case)),TIDE_OPENCV_THREADS='1',
                WORKLOAD_IMAGE=IMAGE,APPLICATION_CPUS='6',PAPER_CPUSET=APP_MASK,WORKLOAD_CPUSET=APP_MASK,
                TIDE_COMB_DEV_CPUNUM='16',TIDE_COMB_CPUIDS=','.join(map(str,MAPPING)),
                TIDE_MQ_MAX_MESSAGES='10',TIDE_MQ_MESSAGE_BYTES='262144',TIDE_DETECTIONS_PER_REQ='10',
                DURATION_SEC=str(case['duration_sec']),DEADLINE_SEC='180',CELLS=case['cell'],RPS_LIST=str(case['rps']),
                REPEAT_START=str(case['repeat']),REPEATS=str(case['repeat']),PYTHON=PYTHON,
                METRICS=str(REPO/'scripts/revision/metrics.py'),CGROUP_ROOT='/sys/fs/cgroup')


def completed_case(case,manifest):
    assert isinstance(manifest['started_at'],str) and isinstance(manifest['finished_at'],str),'manifest timestamps must be strings'
    return dict(case,started_at=manifest['started_at'],finished_at=manifest['finished_at'],audit='passed')


def verify_binaries(identity):
    assert identity['status']=='PASS', 'build identity not accepted'
    required={'runtime-source/Tide/bin/resptestv2-cpu-budget'}
    assert required<=identity['binaries'].keys(), 'missing frozen binary identities'
    for name,digest in identity['binaries'].items():assert sha(BASE/name)==digest,('binary changed',name)


def verify_frozen():
    identity=json.loads((BASE/'frozen/identities.json').read_text())
    for name,digest in identity['files'].items():assert sha(BASE/name)==digest,('frozen input changed',name)
    binary=json.loads((BASE/'frozen/build-identity.json').read_text());verify_binaries(binary)
    return binary


def empty_owned_namespace():
    assert not subprocess.check_output(['ctr','-n','tide','tasks','ls','-q'],text=True).strip(),'Tide tasks busy'
    assert not subprocess.check_output(['ctr','-n','tide','containers','ls','-q'],text=True).strip(),'Tide containers busy'
    assert not list(Path('/dev/mqueue').iterdir()),'unexpected queues'
    assert not [p for p in Path('/sys/fs/cgroup/tide').iterdir() if p.is_dir()],'unexpected Tide cgroups'


def owned_resources(events):
    executors=set();groups=set()
    for event in events:
        if event.get('msg')=='Containerxv2: start creating new container':
            eid=event.get('ExecutorId','')
            if re.fullmatch(UUID,eid) is None:raise ValueError('invalid owned executor ID')
            executors.add(eid)
        if event.get('msg')=='Containerxv2: start creating cgroup':
            group=event.get('CgroupPath','')
            if re.fullmatch('/tide/'+UUID,group) is None:raise ValueError('invalid owned cgroup')
            groups.add(group)
    return executors,groups


def stop_owned_case(proc,path,normal=False):
    result=dict(process_group=proc.pid,commands=[],actions=[],errors=[],executors=[],groups=[])
    def record_error(action,exc):result['errors'].append(dict(action=action,error=repr(exc)))
    try:
        try:
            result['actions'].append(dict(action='kill_process_group',pid=proc.pid,signal='SIGTERM'))
            os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            result['actions'].append(dict(action='kill_process_group',pid=proc.pid,signal='SIGKILL'))
            os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
        try:
            result['actions'].append(dict(action='kill_process_group',pid=proc.pid,signal='SIGKILL'))
            os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    except BaseException as exc:record_error('stop_process_group',exc)
    events=[]
    if (path/'runtime.jsonl').exists():
        for line in (path/'runtime.jsonl').read_text().splitlines():
            try:events.append(json.loads(line))
            except json.JSONDecodeError:continue
    try:executors,groups=owned_resources(events)
    except BaseException as exc:
        record_error('validate_owned_resources',exc)
        executors,groups=set(),set()
    result.update(executors=sorted(executors),groups=sorted(groups))
    def command(cmd):
        try:
            outcome=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
            result['commands'].append(dict(command=cmd,returncode=outcome.returncode,stdout=outcome.stdout,stderr=outcome.stderr))
        except BaseException as exc:record_error(cmd,exc)
    for eid in sorted(executors):
        try:
            source=Path('/tmp/tide/exec-logs')/eid
            if source.exists() and not normal:shutil.copytree(source,path/'failure-worker-logs'/eid,dirs_exist_ok=True)
        except BaseException as exc:record_error('copy_worker_logs '+eid,exc)
        for cmd in [['ctr','-n','tide','tasks','kill','-s','SIGKILL',eid],['ctr','-n','tide','tasks','rm',eid],['ctr','-n','tide','containers','rm',eid]]:command(cmd)
        for suffix in ('-in','-out'):
            try:
                queue=Path('/dev/mqueue')/(eid+suffix)
                result['actions'].append(dict(action='unlink_queue',path=str(queue)))
                queue.unlink(missing_ok=True)
            except BaseException as exc:record_error('unlink_queue '+eid+suffix,exc)
    if executors:result['settled_cleanup']=remove_owned('tide',executors)
    for group in sorted(groups):
        try:
            cg=Path('/sys/fs/cgroup')/group.lstrip('/')
            if cg.exists():
                assert not (cg/'cgroup.procs').read_text().strip(),('owned cgroup still busy',group)
                result['actions'].append(dict(action='rmdir_cgroup',path=str(cg)))
                cg.rmdir()
        except BaseException as exc:record_error('rmdir '+group,exc)
    try:
        manifest=path/'manifest.json'
        bid=backend_id(json.loads(manifest.read_text())) if manifest.exists() else None
        if bid:
            result['backend_id']=bid
            for cmd in [['ctr','-n','default','tasks','kill','-s','SIGKILL',bid],['ctr','-n','default','tasks','rm',bid],['ctr','-n','default','containers','rm',bid]]:command(cmd)
    except BaseException as exc:record_error('owned_backend_cleanup',exc)
    result['process_group_stopped']=proc.poll() is not None
    save(path/('owned-teardown.json' if normal else 'failure-cleanup.json'),result)
    return result


def main(phase):
    from audit import audit_case
    lock=(BASE/'experiment.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert os.geteuid()==0,'root required'
    root=BASE/phase;assert not root.exists(),'existing phase retained; no retries'
    plan=make_plan(phase)
    frozen_plan=BASE/'frozen'/f'{phase}-plan.json'
    assert json.loads(frozen_plan.read_text())==plan,'frozen plan mismatch'
    binary=verify_frozen();empty_owned_namespace()
    for ns in ('default','tide'):
        rows=[line.split() for line in subprocess.check_output(['ctr','-n',ns,'images','ls'],text=True).splitlines() if line.startswith(IMAGE+' ')]
        assert len(rows)==1 and rows[0][2]==DIGEST,('unexpected image identity',ns)
    root.mkdir();save(root/'plan.json',plan)
    originals={p:Path(p).read_text() for p in ['/proc/sys/fs/mqueue/msgsize_max','/sys/fs/cgroup/tide/cpuset.cpus']}
    initial_affinity=os.sched_getaffinity(0);save(root/'host-original.json',originals)
    def interrupted(signum,frame):raise InterruptedError('runner received signal '+str(signum))
    original_handlers={sig:signal.signal(sig,interrupted) for sig in (signal.SIGTERM,signal.SIGINT)}
    state=dict(pid=os.getpid(),status='running',completed=0,total=len(plan['cases']),started_at=time.time(),cases=[])
    proc=None;path=None
    save(root/'progress.json',state)
    try:
        Path('/proc/sys/fs/mqueue/msgsize_max').write_text('262144\n')
        Path('/sys/fs/cgroup/tide/cpuset.cpus').write_text(APP_MASK+'\n')
        os.sched_setaffinity(0,set(DRIVER_CPUS))
        assert sorted(os.sched_getaffinity(0))==DRIVER_CPUS
        for case in plan['cases']:
            empty_owned_namespace();verify_binaries(binary)
            path=case_path(root,case);assert not path.exists();path.mkdir(parents=True)
            save(path/'case-config.json',manifest_fields(case));env=environment(root,case)
            state.update(current=case);save(root/'progress.json',state)
            save(path/'launch.json',dict(case=case,environment=env,actual_driver_affinity=sorted(os.sched_getaffinity(0)),started_at=time.time()))
            sampler=Sampler(path)
            with (path/'runner.log').open('x') as log,(path/'host-samples.jsonl').open('x') as samples:
                proc=subprocess.Popen(['bash',str(REPO/'scripts/run-matrix.sh')],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                state['child_pid']=proc.pid;save(root/'progress.json',state)
                deadline=time.monotonic()+600
                while proc.poll() is None:
                    assert time.monotonic()<deadline,'native case safety timeout; preserve attempt'
                    sample=sampler.snapshot(proc.pid)
                    assert sample['sampler_affinity']==DRIVER_CPUS
                    samples.write(json.dumps(sample)+'\n');samples.flush();time.sleep(.2)
                assert proc.returncode==0,('runner failed; attempt retained',str(path),proc.returncode)
            if case['cell'] not in ('C1','C2'):
                cleanup=stop_owned_case(proc,path,normal=True)
                assert not cleanup['errors'],cleanup
            if case['cell'] in ('C1','C2'):
                manifest=json.loads((path/'manifest.json').read_text())
                save(path/'http-settled-teardown.json',remove_owned('default',[backend_id(manifest)]))
            report=audit_case(path,case,copy_worker_logs=True,expected_binaries=binary['binaries'])
            assert report['status']=='passed',report
            for copied in sorted((path/'worker-logs').glob('*')):
                assert re.fullmatch(UUID,copied.name), 'invalid owned log identity'
                direct=LEGACY_ROOT/'native-evidence'/copied.name/'stdout'
                assert sha(direct)==sha(copied/'stdout'),'direct witness copy differs'
                original=Path('/tmp/tide/exec-logs')/copied.name
                for log_name in ('stdout','stderr'):
                    if (original/log_name).exists():
                        if log_name=='stderr':assert sha(original/log_name)==sha(copied/log_name), 'transient stderr copy differs'
                if original.exists():shutil.rmtree(original)
            save(path/'summary.json',report['stats'])
            manifest=json.loads((path/'manifest.json').read_text())
            state['completed']+=1;state['cases'].append(completed_case(case,manifest))
            save(root/'progress.json',state)
            print(json.dumps(dict(completed=state['completed'],total=state['total'],case=case,stats=report['stats'])),flush=True)
            proc=None
        empty_owned_namespace();state['status']='completed'
    except BaseException as exc:
        state.update(status='failed',error=repr(exc))
        if proc is not None and path is not None:
            try:state['failure_cleanup']=stop_owned_case(proc,path)
            except BaseException as cleanup_error:state['cleanup_error']=repr(cleanup_error)
        raise
    finally:
        errors=[]
        try:verify_frozen()
        except BaseException as exc:errors.append(dict(action='verify_frozen_after_phase',error=repr(exc)))
        for p,value in originals.items():
            try:Path(p).write_text(value)
            except BaseException as exc:errors.append(dict(path=p,error=repr(exc)))
        try:os.sched_setaffinity(0,initial_affinity)
        except BaseException as exc:errors.append(dict(action='restore_affinity',error=repr(exc)))
        state['finished_at']=time.time()
        if errors:state.update(status='failed',restore_errors=errors)
        save(root/'progress.json',state)
        for sig,handler in original_handlers.items():signal.signal(sig,handler)
        if errors:raise RuntimeError(('host settings restoration failed',errors))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=('smoke','pilot','formal'));main(parser.parse_args().phase)
