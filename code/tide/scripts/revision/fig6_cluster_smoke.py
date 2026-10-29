#!/usr/bin/env python3
"""Unscored correctness/routing smoke, run on an experiment origin."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import urllib.request
from fig6_cluster_driver import expected_result
from fig6_cluster_calibrate import WORK

def post(url,body):
    req=urllib.request.Request(url,json.dumps(body).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)

def main():
    p=argparse.ArgumentParser();p.add_argument('--origin',required=True);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--system',choices=['tide','ray','bare','kubeedge'],default='tide');a=p.parse_args()
    if a.output.exists():raise RuntimeError('smoke output already exists')
    os.sched_setaffinity(0,set(range(8,16)))
    saved=[]
    if a.system=='tide':
        for ip in ['11.0.4.47','11.0.4.48','11.0.4.49']:
            url=f'http://{ip}:29801/config'
            with urllib.request.urlopen(url) as r:c=json.load(r)
            saved.append((url,c)); post(url,{**c,'qos_ms':100,'execution_ms':35,'node_execution_ms':{'nj1':35,'nj2':35,'nj3':35}})
    port={'tide':29801,'ray':29870,'bare':29810,'kubeedge':29890}[a.system]
    rows=[]
    try:
        for kind in WORK:
            start=time.monotonic_ns()
            result=post(f'http://127.0.0.1:{port}/run',WORK[kind])
            rows.append({'kind':kind,'reply':result,'elapsed_ns':time.monotonic_ns()-start})
            if result.get('result')!=expected_result(WORK[kind]):raise RuntimeError('kernel mismatch')
        def one(i):
            start=time.monotonic_ns()
            try:
                result=post(f'http://127.0.0.1:{port}/run',WORK['cpu'])
                return {'id':i,'kind':'cpu','reply':result,'elapsed_ns':time.monotonic_ns()-start}
            except Exception as e:return {'id':i,'kind':'cpu','error':repr(e),'elapsed_ns':time.monotonic_ns()-start}
        with ThreadPoolExecutor(max_workers=24) as ex:rows+=list(ex.map(one,range(24)))
    finally:
        for url,c in saved:post(url,c)
        result={'system':a.system,'origin':a.origin,'time_ns':time.time_ns(),'unscored':True,'rows':rows,
                'destinations':dict(Counter(r.get('reply',{}).get('node','error') for r in rows)),
                'worker_instances':sorted({r['reply']['worker_instance'] for r in rows if 'reply'in r}),
                'ok':len(rows)==27 and all(r.get('reply',{}).get('result')==expected_result(WORK[r['kind']]) for r in rows)}
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
    if not result['ok']:raise SystemExit(1)

if __name__=='__main__':main()
