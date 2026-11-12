"""Parent-only remote baseline and isolated source copy; launches no workloads."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from runtime_config import BASE, NERDCTL, KUBECONFIG


def command(args):
    return subprocess.check_output(args, text=True, timeout=30)


def cluster_state():
    k = [NERDCTL, 'exec',
         'tmc-ke-control-plane', 'kubectl', '--kubeconfig='+KUBECONFIG]
    pods = json.loads(command(k+['get','pods','-A','-o','json']))['items']
    nodes = json.loads(command(k+['get','nodes','-o','json']))['items']
    return dict(pods=[dict(namespace=p['metadata']['namespace'], name=p['metadata']['name'],
        uid=p['metadata']['uid'], phase=p['status']['phase'],
        containers=sorted([dict(name=c['name'],ready=c['ready'],restarts=c['restartCount'])
                           for c in p['status'].get('containerStatuses',[])],key=lambda c:c['name']))
        for p in pods], nodes=[dict(name=n['metadata']['name'],uid=n['metadata']['uid'],
        ready=next(c['status'] for c in n['status']['conditions'] if c['type']=='Ready')) for n in nodes])


def outer_state():
    k=['kubectl','--request-timeout=15s']
    pods=json.loads(command(k+['get','pods','-A','-o','json']))['items']
    nodes=json.loads(command(k+['get','nodes','-o','json']))['items']
    return dict(pods=sorted([dict(namespace=p['metadata']['namespace'],name=p['metadata']['name'],uid=p['metadata']['uid'],phase=p['status']['phase'],containers=sorted([dict(name=c['name'],ready=c['ready'],restarts=c['restartCount']) for c in p['status'].get('containerStatuses',[])],key=lambda c:c['name'])) for p in pods],key=lambda x:(x['namespace'],x['name'])),nodes=sorted([dict(name=n['metadata']['name'],uid=n['metadata']['uid'],conditions={c['type']:c['status'] for c in n['status']['conditions']}) for n in nodes],key=lambda x:x['name']))
