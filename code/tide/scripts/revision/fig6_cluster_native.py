#!/usr/bin/env python3
"""Owned native Tide deployment for the three-node Fig. 6 measurement."""
import argparse
import json
import shlex
import subprocess
from pathlib import Path

ROOT = '/root/tmc-fig6-cluster-20261014'
HOSTS = {'nj1':'NJ-k1','nj2':'NJ-k2','nj3':'NJ-k3'}

def ssh(node, code):
    args=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10']
    if node=='nj2': args += ['-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile=/private/tmp/tmc-fig6-known-hosts']
    return subprocess.check_output(args+[HOSTS[node], 'python3 -c '+shlex.quote(code)], text=True)

def start(node):
    addr={'nj1':'11.0.4.47','nj2':'11.0.4.48','nj3':'11.0.4.49'}[node]
    cpus=4 if node=='nj1' else 2
    code=f'''
import json,os,subprocess,time,urllib.request
from pathlib import Path
root=Path({ROOT!r}); d=root/'native'; d.mkdir(exist_ok=True)
pidfile=d/'pid'
if pidfile.exists():
 p=int(pidfile.read_text())
 if Path('/proc/'+str(p)+'/cmdline').exists() and b'native-node' in Path('/proc/'+str(p)+'/cmdline').read_bytes():
  raise RuntimeError('owned native process already active '+str(p))
subprocess.run(['ctr','-n','tide','images','import',str(root/'worker.oci.tar')],check=True,stdout=subprocess.DEVNULL)
cfg=dict(execution_ms=35,qos_ms=500,ttl_ms=86400000,weights=[1,1,1],latency_scale_ms=100,state_max_age_ms=5000,node_execution_ms={{'nj1':35,'nj2':35,'nj3':35}},network_ms={{'nj1':1,'nj2':1,'nj3':1}})
(d/'initial-config.json').write_text(json.dumps(cfg))
args=['taskset','-c','8-15',str(root/'native-node'),'--node={node}','--address={addr}:29800','--http=0.0.0.0:29801','--role={'cloud' if node=='nj1' else 'edge'}','--cpus={cpus}','--config='+str(d/'initial-config.json'),'--log='+str(d/'runtime.jsonl')]
log=(d/'console.log').open('ab')
p=subprocess.Popen(args,stdout=log,stderr=log,start_new_session=True)
pidfile.write_text(str(p.pid))
for i in range(100):
 if p.poll() is not None: raise RuntimeError((d/'console.log').read_text()[-4000:])
 try:
  with urllib.request.urlopen('http://127.0.0.1:29801/health',timeout=1) as r: info=json.load(r)
  (d/'ready.json').write_text(json.dumps(info)); print(json.dumps(info));break
 except Exception: time.sleep(.2)
else: raise RuntimeError('native readiness timeout')
'''
    return json.loads(ssh(node,code))

def peers(infos):
    result={}
    for node in HOSTS:
        code=f'''
import urllib.request,json
payload={json.dumps(infos)!r}.encode()
req=urllib.request.Request('http://127.0.0.1:29801/peers',payload,{{'Content-Type':'application/json'}})
with urllib.request.urlopen(req,timeout=10) as r: print(r.read().decode())
'''
        result[node]=json.loads(ssh(node,code))
    return result

def stop(node):
    return ssh(node,f'''
import os,signal,time
from pathlib import Path
p=Path({ROOT!r})/'native'/'pid'
if p.exists():
 pid=int(p.read_text()); command=Path('/proc')/str(pid)/'cmdline'
 if command.exists() and b'native-node' in command.read_bytes():
  os.kill(pid,signal.SIGTERM)
  for i in range(100):
   if not command.exists(): break
   time.sleep(.1)
  else: raise RuntimeError('native graceful stop timed out')
 print('stopped',pid)
''')

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['start','stop']); p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action=='start':
        infos=[start(n) for n in HOSTS]; result=peers(infos)
        if a.output: a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    else:
        for n in HOSTS: print(n,stop(n))
