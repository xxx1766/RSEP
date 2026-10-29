#!/usr/bin/env python3
"""Generate exact fixed-slot Pods or audit/set cpusets inside a nested node.

manifest: emits public manifests for kubectl apply in the isolated cloud.
pin: run INSIDE each nested node after its worker Pods have started.
"""
import argparse
import hashlib
import os
import json
from pathlib import Path
import subprocess

IMAGE = 'docker.io/tide/fig6-cluster-20261014:amd64'

def manifest():
    items = [{'apiVersion': 'v1', 'kind': 'Namespace',
              'metadata': {'name': 'fig6-cluster-20261014'}}]
    slots = [('nj1', 'tmc-fig6-ke-control-plane', 0, '0,1', 29890),
             ('nj1', 'tmc-fig6-ke-control-plane', 1, '2,3', 29891),
             ('nj2', 'tmc-fig6-ke-nj2', 0, '0,1', 29890),
             ('nj3', 'tmc-fig6-ke-nj3', 0, '0,1', 29890)]
    for host, node, slot, cpus, port in slots:
        name = f'fig6-{host}-slot{slot}'
        items.append({'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': name, 'namespace': 'fig6-cluster-20261014',
                         'labels': {'app': 'fig6-worker', 'physical-node': host},
                         'annotations': {'fig6.cpu-list': cpus}},
            'spec': {'nodeName': node, 'hostNetwork': True, 'dnsPolicy': 'Default',
                     'automountServiceAccountToken': False,
                     'tolerations': [{'operator': 'Exists'}],
                     'containers': [{'name': name, 'image': IMAGE, 'imagePullPolicy': 'Never',
                        'command': ['/worker'],
                        'args': ['--mode=http', f'--listen=0.0.0.0:{port}',
                                 f'--node={host}', f'--cpu-list={cpus}', '--tmpdir=/fig6-io'],
                        'env': [{'name': 'GOMAXPROCS', 'value': '1'}],
                        'resources': {'requests': {'cpu': '2', 'memory': '512Mi'},
                                      'limits': {'cpu': '2', 'memory': '512Mi'}},
                        'volumeMounts': [{'name': 'io', 'mountPath': '/fig6-io'}]}],
                     'volumes': [{'name': 'io', 'hostPath': {'path': '/fig6-io', 'type': 'Directory'}}]}})
    print(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items}, indent=2))

def command(*args):
    return subprocess.check_output(args, text=True)

def pin():
    rows = []
    containers = json.loads(command('crictl', 'ps', '-o', 'json'))['containers']
    for entry in containers:
        name = entry['metadata']['name']
        if not name.startswith('fig6-'):
            continue
        inspect = json.loads(command('crictl', 'inspect', entry['id']))
        pid = int(inspect['info']['pid'])
        group = Path('/proc', str(pid), 'cgroup').read_text().split('0::', 1)[1].strip()
        root = Path('/sys/fs/cgroup') / group.lstrip('/')
        cpus = '2-3' if name.endswith('nj1-slot1') else '0-1'
        (root / 'cpuset.cpus').write_text(cpus)
        rows.append({'name': name, 'container_id': entry['id'], 'pid': pid,
                     'worker_sha256': hashlib.sha256(Path('/proc', str(pid), 'exe').read_bytes()).hexdigest(),
                     'image_ref': inspect['status']['imageRef'],
                     'io_device_inode': [Path('/proc', str(pid), 'root/fig6-io').stat().st_dev,
                                         Path('/proc', str(pid), 'root/fig6-io').stat().st_ino],
                     'cgroup': group, 'cpu_max': (root / 'cpu.max').read_text().strip(),
                     'memory_max': (root / 'memory.max').read_text().strip(),
                     'cpuset_effective': (root / 'cpuset.cpus.effective').read_text().strip(),
                     'thread_affinity': {p.name: next(line.split(':', 1)[1].strip()
                         for line in (p / 'status').read_text().splitlines()
                         if line.startswith('Cpus_allowed_list:'))
                         for p in Path('/proc', str(pid), 'task').iterdir()}})
    print(json.dumps(rows, indent=2))

def controls():
    # Only invoke inside newly-owned nested nodes, never in the original cluster.
    if not Path('/run/systemd/container').exists():
        raise RuntimeError('control affinity must run inside a nested node')
    rows=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            comm=(path/'comm').read_text().strip()
            if comm == 'worker':
                continue
            old=sorted(os.sched_getaffinity(int(path.name)))
            for thread in (path/'task').iterdir():
                os.sched_setaffinity(int(thread.name),set(range(8,16)))
            rows.append({'pid':int(path.name),'comm':comm,'before':old,'after':list(range(8,16))})
        except (FileNotFoundError,ProcessLookupError):
            pass
    print(json.dumps(rows,indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['manifest', 'pin', 'controls'])
    args = parser.parse_args()
    {'manifest':manifest,'pin':pin,'controls':controls}[args.action]()
