#!/usr/bin/env python3
"""Read-only snapshot inside one nested KE node; no pinning or kernel calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

def command(*args):
    return subprocess.check_output(args,text=True)

def main():
    rows=[]
    for entry in json.loads(command('crictl','ps','-o','json'))['containers']:
        name=entry['metadata']['name']
        if not name.startswith('fig6-'):
            continue
        inspect=json.loads(command('crictl','inspect',entry['id']))
        pid=int(inspect['info']['pid'])
        proc=Path('/proc',str(pid))
        group=(proc/'cgroup').read_text().split('0::',1)[1].strip()
        cg=Path('/sys/fs/cgroup')/group.lstrip('/')
        io=(proc/'root/fig6-io').stat()
        fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
        port=29891 if name.endswith('nj1-slot1') else 29890
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=5) as response:
            health={'status':response.status,'body':response.read().decode().strip()}
        status=(proc/'status').read_text()
        rows.append({'name':name,'container_id':entry['id'],'pid':pid,
                     'pid_namespace_ids':next((l.split(':',1)[1].strip() for l in status.splitlines()
                                               if l.startswith('NSpid:')),None),
                     'process_start_ticks':int(fields[19]),
                     'container_started_at':inspect['status']['startedAt'],
                     'container_created_at':inspect['status']['createdAt'],
                     'container_labels':inspect['status']['labels'],
                     'worker_sha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest(),
                     'image_ref':inspect['status']['imageRef'],
                     'io_device_inode':[io.st_dev,io.st_ino],
                     'cgroup':group,'cpu_max':(cg/'cpu.max').read_text().strip(),
                     'memory_max':(cg/'memory.max').read_text().strip(),
                     'cpuset_requested':(cg/'cpuset.cpus').read_text().strip(),
                     'cpuset_effective':(cg/'cpuset.cpus.effective').read_text().strip(),
                     'thread_affinity':{t.name:next(l.split(':',1)[1].strip()
                         for l in (t/'status').read_text().splitlines() if l.startswith('Cpus_allowed_list:'))
                         for t in (proc/'task').iterdir()},'health':health})
    print(json.dumps({'read_only':True,'time_ns':time.time_ns(),'kernel_requests':0,
                      'process_memory_reads':False,'workers':rows},indent=2))

if __name__=='__main__':
    main()
