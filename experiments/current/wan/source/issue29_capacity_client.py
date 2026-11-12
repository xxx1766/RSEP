#!/usr/bin/env python3
"""Four persistent channels per endpoint,512 balanced requests per real VM."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import queue
import threading
import time
from issue29_capacity import capacity_jobs
from issue20_domain_client import validate_reply


def run(endpoints,inputs,name):
    jobs=[queue.Queue() for _ in endpoints];rows=[None]*(512*len(endpoints));manifests=[]
    for endpoint in endpoints:
        host,port=endpoint.split(':');connection=http.client.HTTPConnection(host,int(port),timeout=10)
        connection.request('GET','/manifest');reply=connection.getresponse()
        if reply.status!=200:raise RuntimeError('capacity manifest failed')
        manifest=json.loads(reply.read());connection.close();manifests.append(manifest)
        for body,meta in zip(inputs,manifest['inputs']):
            if hashlib.sha256(body).hexdigest()!=meta['input_sha256']:raise ValueError('capacity input mismatch')
    barrier=threading.Barrier(4*len(endpoints)+1);errors=[]
    def worker(node,channel):
        host,port=endpoints[node].split(':');connection=http.client.HTTPConnection(host,int(port),timeout=15)
        try:
            connection.request('GET','/health');reply=connection.getresponse()
            if reply.status!=200 or not json.loads(reply.read()).get('ready'):raise RuntimeError('capacity health failed')
        except Exception as error:errors.append(str(error));barrier.abort();connection.close();return
        try:barrier.wait(timeout=30)
        except threading.BrokenBarrierError:connection.close();return
        while True:
            job=jobs[node].get()
            if job is None:break
            index,image=job;meta=manifests[node]['inputs'][image];started=time.monotonic_ns()
            row=dict(id=f'{name}-n{node:02d}-{index:04d}',endpoint=endpoints[node],endpoint_index=node,image_index=image,
                input_sha256=meta['input_sha256'],input_bytes=len(inputs[image]),submit_unix_ns=time.time_ns(),channel=channel)
            try:
                connection.request('POST','/infer',inputs[image],{'Content-Type':'image/jpeg','X-Request-ID':row['id']})
                reply=connection.getresponse();body=json.loads(reply.read());validate_reply(reply.status,body,meta)
                if body['request_id']!=row['id']:raise ValueError('capacity ID mismatch')
                row.update(status='success',checksum=body['checksum'],worker_response=body,output_sha256=body['output_sha256'])
            except Exception as error:
                row.update(status='request_error',error=f'{type(error).__name__}: {error}')
                connection.close();connection=http.client.HTTPConnection(host,int(port),timeout=15)
            row.update(reply_unix_ns=time.time_ns(),latency_ms=(time.monotonic_ns()-started)/1e6)
            rows[node*512+index]=row
        connection.close()
    for node,index,image in capacity_jobs(len(endpoints)):jobs[node].put((index,image))
    for job in jobs:
        for _ in range(4):job.put(None)
    threads=[threading.Thread(target=worker,args=(node,channel),daemon=True) for node in range(len(endpoints)) for channel in range(4)]
    for thread in threads:thread.start()
    try:barrier.wait(timeout=30)
    except threading.BrokenBarrierError:raise RuntimeError('capacity channels failed: '+str(errors))
    start=time.time_ns()
    for thread in threads:thread.join()
    if any(row is None for row in rows):raise RuntimeError('capacity offered accounting incomplete')
    return rows,dict(name=name,start_unix_ns=start,end_unix_ns=time.time_ns(),offered=len(rows),
        success=sum(r['status']=='success' for r in rows),endpoints=endpoints,channels_per_endpoint=4,
        scope='intrinsic application saturation calibration, no135ms deadline score',worker_manifests=manifests)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--endpoints',nargs='+',required=True)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--name',required=True);parser.add_argument('--out',type=Path,required=True);a=parser.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    rows,summary=run(a.endpoints,[(a.inputs/f'image-{i}.jpg').read_bytes() for i in range(4)],a.name)
    (a.out/'requests.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));(a.out/'run.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ['name','offered','success']}))
