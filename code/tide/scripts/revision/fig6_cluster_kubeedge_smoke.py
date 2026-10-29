#!/usr/bin/env python3
"""Exactly 12 sequential, unscored correctness checks across the four KE slots."""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--fixtures',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():
        raise RuntimeError('preserve existing smoke evidence; choose a new output')
    os.sched_setaffinity(0,set(range(8,16)))
    fixtures=json.loads(a.fixtures.read_text())
    endpoints=[('nj1','http://11.0.4.47:29890'),('nj1','http://11.0.4.47:29891'),
               ('nj2','http://11.0.4.48:29890'),('nj3','http://11.0.4.49:29890')]
    rows=[]
    start=time.time_ns()
    for node,url in endpoints:
        for fixture in fixtures:
            row={'node_expected':node,'endpoint':url,'workload':fixture['workload'],
                 'expected_result':fixture['expected_result'],'start_ns':time.time_ns()}
            try:
                request=urllib.request.Request(url+'/run',json.dumps(fixture['workload']).encode(),
                                               {'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=15) as response:
                    row['reply']=json.load(response)
                reply=row['reply']
                row['ok']=(reply.get('node')==node and reply.get('result')==fixture['expected_result']
                           and not reply.get('error') and bool(reply.get('worker_instance'))
                           and reply.get('worker_pid',0)>0)
            except Exception as error:
                row.update(ok=False,error=repr(error))
            row['end_ns']=time.time_ns()
            rows.append(row)
    instances={url:sorted({r['reply']['worker_instance'] for r in rows
                          if r['endpoint']==url and 'reply' in r}) for _,url in endpoints}
    identity_ok=all(len(ids)==1 for ids in instances.values()) and len({i for ids in instances.values() for i in ids})==4
    result={'system':'kubeedge','origin':'nj2','unscored':True,'sequential':True,
            'start_ns':start,'end_ns':time.time_ns(),'rows':rows,'instances':instances,
            'ok':len(rows)==12 and all(r['ok'] for r in rows) and identity_ok}
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
    if not result['ok']:
        raise SystemExit(1)

if __name__=='__main__':
    main()
