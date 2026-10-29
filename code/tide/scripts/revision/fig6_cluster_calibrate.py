#!/usr/bin/env python3
"""Record standalone service/path calibration before freezing Fig. 6 loads."""
import argparse
import json
import os
from pathlib import Path
import random
import statistics
import time
import urllib.request

from fig6_cluster_driver import expected_result

WORK = {
    'cpu': {'kind':'cpu','bytes':65536,'iterations':100},
    'memory': {'kind':'memory','bytes':16777216,'iterations':4},
    'io': {'kind':'io','bytes':4194304,'iterations':4},
}

def request(url, body):
    start=time.monotonic_ns()
    r=urllib.request.Request(url,json.dumps(body).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=12) as reply:
        result=json.load(reply)
    return result,time.monotonic_ns()-start

def main():
    p=argparse.ArgumentParser();p.add_argument('--node',required=True,choices=['nj1','nj2','nj3'])
    p.add_argument('--system',required=True,choices=['bare','tide']);p.add_argument('--samples',type=int,default=20)
    p.add_argument('--output',required=True,type=Path); a=p.parse_args()
    if a.output.exists(): raise RuntimeError('calibration artifact already exists')
    os.sched_setaffinity(0,set(range(8,16)))
    port=29810 if a.system=='bare' else 29801
    jobs=[(kind,i) for kind in WORK for i in range(a.samples)]
    random.Random(20261014).shuffle(jobs)
    rows=[]
    for kind in WORK:
        for i in range(3):
            reply,elapsed=request(f'http://127.0.0.1:{port}/run',WORK[kind])
            if reply.get('result')!=expected_result(WORK[kind]) or reply.get('node')!=a.node:
                raise RuntimeError('incorrect warmup response '+str(reply))
    for kind,i in jobs:
        reply,elapsed=request(f'http://127.0.0.1:{port}/run',WORK[kind])
        if reply.get('result')!=expected_result(WORK[kind]) or reply.get('node')!=a.node:
            raise RuntimeError('incorrect calibration response '+str(reply))
        rows.append({'kind':kind,'sample':i,'elapsed_ns':elapsed,'response':reply})
        time.sleep(.025)
    summary={k:{'median_service_ms':statistics.median(r['response']['service_ns']/1e6 for r in rows if r['kind']==k),
                'median_path_ms':statistics.median(r['elapsed_ns']/1e6 for r in rows if r['kind']==k),
                'samples':a.samples} for k in WORK}
    result={'node':a.node,'system':a.system,'workloads':WORK,'rows':rows,'summary':summary,'time_ns':time.time_ns(),
            'warmup_per_kernel':3,'client_cpus':sorted(os.sched_getaffinity(0))}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2))
    print(json.dumps({'node':a.node,'system':a.system,'summary':summary}),flush=True)

if __name__=='__main__':main()
