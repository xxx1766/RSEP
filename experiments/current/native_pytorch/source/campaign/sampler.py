"""Host observations with narrowly bounded disappearing-kernel-resource handling."""
import errno
import json
import os
from pathlib import Path
import re
import subprocess
import time
from protocol import save


def read_text(path):
    path=Path(path)
    try:return path.read_text().strip()
    except OSError as exc:
        transient=any(root in path.parents for root in (Path('/proc'),Path('/sys/fs/cgroup')))
        if transient and exc.errno in (errno.ENOENT,errno.ESRCH,errno.ENODEV):return None
        raise


def process_info(pid):
    threads=[]
    for status in sorted(Path(f'/proc/{pid}/task').glob('*/status')):
        value=read_text(status)
        if value is None:continue
        fields={k:v.strip() for k,v in (line.split(':',1) for line in value.splitlines() if ':' in line)}
        if 'Cpus_allowed_list' not in fields:continue
        threads.append(dict(tid=int(status.parent.name),name=fields.get('Name'),cpus=fields['Cpus_allowed_list'],
                            ns_pids=[int(n) for n in fields.get('NSpid','').split()]))
    namespace_pids=next((t['ns_pids'] for t in threads if t['tid']==int(pid)),[])
    return dict(pid=int(pid),comm=read_text(f'/proc/{pid}/comm'),thread_affinities=sorted({t['cpus'] for t in threads}),
                threads=threads,ns_pids=namespace_pids,namespace_pids=namespace_pids)


def process_tree(pid):
    result=[];pending=[int(pid)];seen=set()
    while pending:
        p=pending.pop()
        if p in seen:continue
        seen.add(p);info=process_info(p)
        if info['thread_affinities'] and info['comm'] is not None:result.append(info)
        for childfile in Path(f'/proc/{p}/task').glob('*/children'):
            pending.extend(int(n) for n in (read_text(childfile) or '').split())
    return result


def read_group(group,**extra):
    group=Path(group)
    fields={name:read_text(group/name) for name in ('cgroup.procs','cpuset.cpus.effective','cpu.max','cpu.stat')}
    missing=[name for name,value in fields.items() if value is None]
    if missing:return None,dict(path=str(group),reason='cgroup_disappeared',missing_fields=missing,**extra)
    pids=fields['cgroup.procs'].split()
    if not pids:return None,None
    processes=[process_info(int(pid)) for pid in pids]
    return dict(path=str(group),pids=pids,cpus=fields['cpuset.cpus.effective'],quota=fields['cpu.max'],
                cpu_stat=fields['cpu.stat'],thread_affinities=sorted({m for p in processes for m in p['thread_affinities']}),
                processes=processes,ns_pids={str(p['pid']):p['ns_pids'] for p in processes},**extra),None


def backend_id(manifest):
    found=[arg.split('=',1)[1] for arg in manifest.get('command',[]) if arg.startswith('-backend-id=')]
    if not found:return None
    if len(found)!=1 or re.fullmatch(r'tide-matrix-[0-9a-f]{32}',found[0]) is None:
        raise ValueError('invalid owned HTTP backend identifier')
    return found[0]


def unified_cgroup(raw):
    if raw is None:raise ValueError('current process cgroup unavailable')
    paths=[line.split(':',2)[2] for line in raw.splitlines() if line.startswith('0::')]
    if len(paths)!=1 or not paths[0].startswith('/') or '..' in Path(paths[0]).parts:
        raise ValueError('invalid unified cgroup identity')
    root=Path('/sys/fs/cgroup')
    result=(root/paths[0].lstrip('/')).resolve()
    if result!=root and root not in result.parents:raise ValueError('cgroup outside unified root')
    return result


def ancestor_quotas(start,role,root=Path('/sys/fs/cgroup')):
    """Read cpu.max only; persistent parents never use disappearing-leaf tolerance."""
    root=Path(root).resolve();group=Path(start).resolve();records=[]
    if group!=root and root not in group.parents:raise ValueError('ancestor outside cgroup root')
    while True:
        try:value=(group/'cpu.max').read_text().strip()
        except FileNotFoundError:
            if group!=root:raise
            records.append(dict(path=str(group),cpu_max=None,role=role,applicable=False,
                                reason='cgroup_v2_root_has_no_cpu_max'))
        else:
            tokens=value.split()
            assert len(tokens)==2 and tokens[0]=='max' and tokens[1].isdigit() and int(tokens[1])>0,('ancestor CPU quota',str(group),value)
            records.append(dict(path=str(group),cpu_max=value,role=role,applicable=True))
        if group==root:return records
        group=group.parent


class Sampler:
    def __init__(self,path):self.path=Path(path);self.owner=None

    def discover_backend(self):
        if self.owner is not None:return
        manifest=self.path/'manifest.json'
        if not manifest.exists():return
        bid=backend_id(json.loads(manifest.read_text()))
        if bid is None:return
        result=subprocess.run(['ctr','-n','default','tasks','ls'],capture_output=True,text=True,timeout=10,check=True)
        rows=[line.split() for line in result.stdout.splitlines() if line.split() and line.split()[0]==bid]
        if not rows:return
        if len(rows)!=1 or len(rows[0])<2 or not rows[0][1].isdigit():raise ValueError('invalid backend task listing')
        pid=int(rows[0][1]);raw=read_text(f'/proc/{pid}/cgroup')
        if raw is None:return
        paths=[line.split(':',2)[2] for line in raw.splitlines() if line.startswith('0::')]
        if len(paths)!=1 or paths[0]=='/' or '..' in Path(paths[0]).parts:raise ValueError('invalid backend cgroup')
        path=(Path('/sys/fs/cgroup')/paths[0].lstrip('/')).resolve()
        if Path('/sys/fs/cgroup') not in path.parents:raise ValueError('backend cgroup outside root')
        self.owner=dict(backend_id=bid,pid=pid,path=str(path),cgroup_path=str(path),namespace='default',
                        discovered_ns=time.time_ns(),proc_cgroup=raw,observed_at=time.time(),task_row=rows[0],role='http')
        save(self.path/'backend-owner.json',self.owner)

    def snapshot(self,child):
        started=time.time();groups=[];backend=[];skipped=[]
        driver_cgroup=unified_cgroup(read_text('/proc/self/cgroup'))
        quotas=ancestor_quotas('/sys/fs/cgroup/tide','tide')+ancestor_quotas(driver_cgroup,'driver')
        for entry in Path('/sys/fs/cgroup/tide').glob('*/cgroup.procs'):
            record,skip=read_group(entry.parent,role='tide')
            if record:groups.append(record)
            if skip:skipped.append(skip)
        self.discover_backend()
        if self.owner:
            quotas.extend(ancestor_quotas(Path(self.owner['path']).parent,'http'))
            record,skip=read_group(self.owner['path'],backend_id=self.owner['backend_id'],role='http')
            if record:backend.append(record)
            if skip:skipped.append(skip)
        return dict(started_at=started,time=time.time(),groups=groups,backend_groups=backend,skipped_groups=skipped,
                    ancestor_quotas=quotas,driver_cgroup=str(driver_cgroup),
                    backend_owner_path=self.owner['path'] if self.owner else None,
                    driver_processes=process_tree(child),sampler_affinity=sorted(os.sched_getaffinity(0)),
                    cpu=read_text('/proc/stat'),loadavg=read_text('/proc/loadavg'))
