#!/usr/bin/env python3
"""Read-only end-of-experiment cleanup and host-state evidence, on NJ-k1."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from control_metrics import processes

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True); p.add_argument('--out',type=Path,required=True)
a=p.parse_args()
m=json.loads((a.root/'progress.json').read_text())
if m['status']!='completed': raise RuntimeError('matrix has not completed')
current=processes(); owned=set(); pod_uids=[]
for job in m['jobs']:
    block=a.root/f"{job['system']}-r{job['repeat']}"
    setup=json.loads((block/'setup.json').read_text())
    if job['system']=='kubeedge': pod_uids.append(setup['pod']['metadata']['uid'])
    # Scan every recorded identity, including any process that exited before the last cell.
    for path in block.glob('*/samples.jsonl'):
        for line in path.read_text().splitlines():
            for proc in json.loads(line)['processes']:
                if (job['system']=='ray' or
                    job['system']=='tide' and proc['group']!='shared_host_runtime' or
                    job['system']=='kubeedge' and proc['group']=='application'):
                    owned.add((proc['pid'],proc['start']))
remaining=[q for pid,q in current.items() if (pid,q['start']) in owned]
pod_processes=[q for q in current.values() if any(uid in q['cgroup'] or uid.replace('-','_') in q['cgroup'] for uid in pod_uids)]
warnings=[]
def command(args):
    proc=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    if proc.stderr.strip(): warnings.append(dict(command=args,stderr=proc.stderr.strip()))
    return proc.stdout
tasks=command(['ctr','-n','tide','tasks','ls'])
containers=command(['ctr','-n','tide','containers','ls'])
pods=json.loads(command(['kubectl','--kubeconfig','/root/rsep-revision-20260928/kubeedge-tools/kind-kubeconfig','get','pods','-n','tmc-ke-revision','-o','json']))
children=[str(x) for x in Path('/sys/fs/cgroup/tide').iterdir() if x.is_dir()]
queues=sorted(x.name for x in Path('/dev/mqueue').iterdir())
observed_pods=[dict(name=q['metadata']['name'],uid=q['metadata']['uid']) for q in pods['items']]
owned_pods=[q for q in observed_pods if q['uid'] in pod_uids]
result=dict(checked_unix_ns=time.time_ns(),matrix_status=m['status'],supervisor_pid=m['pid'],
            remaining_owned_processes=remaining,remaining_owned_pod_processes=pod_processes,
            remaining_owned_pods=owned_pods,namespace_pods=observed_pods,
            tide_tasks=tasks,tide_containers=containers,tide_child_cgroups=children,message_queues=queues,
            tide_parent_cpuset=Path('/sys/fs/cgroup/tide/cpuset.cpus').read_text().strip(),
            mqueue_msgsize_max=Path('/proc/sys/fs/mqueue/msgsize_max').read_text().strip(),
            memory=command(['free','-b']),kubernetes_version=json.loads(command(['kubectl','--kubeconfig','/root/rsep-revision-20260928/kubeedge-tools/kind-kubeconfig','version','-o','json'])))
result['command_warnings']=warnings
result['status']='passed' if not (remaining or pod_processes or owned_pods or children or queues or len(tasks.splitlines())>1 or len(containers.splitlines())>1 or result['tide_parent_cpuset'] or result['mqueue_msgsize_max']!='8192') else 'needs_inspection'
a.out.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('memory','kubernetes_version')}))
if result['status']!='passed': raise RuntimeError('cleanup evidence needs inspection; no cleanup mutations performed')
