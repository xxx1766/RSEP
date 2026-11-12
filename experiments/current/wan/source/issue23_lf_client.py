#!/usr/bin/env python3
"""Custom deadline-blind local-first client; not a native Kubernetes policy."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import queue
import threading
import time
from issue12_k8s import load_trace,summarize
from issue20_domain_client import validate_reply


class LocalFirst:
    def __init__(self,endpoints,local_count,cap=2):
        if not 0<local_count<len(endpoints) or cap<1:raise ValueError('local and remote endpoints with positive cap required')
        self.endpoints=list(endpoints);self.local_count=local_count;self.cap=cap
        self.inflight=[0]*len(endpoints);self.cursor=0;self.lock=threading.Lock()

    def reserve(self):
        with self.lock:
            local=[i for i in range(self.local_count) if self.inflight[i]<self.cap]
            remote=[i for i in range(self.local_count,len(self.endpoints)) if self.inflight[i]<self.cap]
            eligible=local or remote or list(range(len(self.endpoints)))
            choice=min(eligible,key=lambda i:(self.inflight[i],(i-self.cursor)%len(self.endpoints)))
            self.inflight[choice]+=1;self.cursor=(choice+1)%len(self.endpoints)
            return choice

    def release(self,index):
        with self.lock:
            if self.inflight[index]<=0:raise ValueError('unbalanced inflight release')
            self.inflight[index]-=1

    def snapshot(self):
        with self.lock:return list(self.inflight)


class SharedPool:
    def __init__(self,endpoint):
        self.host,self.port=endpoint.split(':');self.port=int(self.port);self.idle=queue.LifoQueue()

    def acquire(self):
        try:return self.idle.get_nowait()
        except queue.Empty:return http.client.HTTPConnection(self.host,self.port,timeout=6)

    def release(self,connection,healthy):
        if healthy:self.idle.put(connection)
        else:connection.close()

    def close(self):
        while True:
            try:self.idle.get_nowait().close()
            except queue.Empty:break


def run(trace,endpoints,local_count,input_dir,qos_ms,window_ms,channels=48,cap=2):
    policy=LocalFirst(endpoints,local_count,cap);pools=[SharedPool(e) for e in endpoints]
    inputs=[(input_dir/f'image-{i}.jpg').read_bytes() for i in range(4)];manifests=[]
    try:
        for pool in pools:
            warm=[]
            for _ in range(cap):
                connection=pool.acquire();connection.request('GET','/manifest');reply=connection.getresponse()
                if reply.status!=200:raise RuntimeError('endpoint manifest failed')
                manifest=json.loads(reply.read());warm.append(connection)
            for connection in warm:pool.release(connection,True)
            manifests.append(manifest)
            for body,meta in zip(inputs,manifest['inputs']):
                if hashlib.sha256(body).hexdigest()!=meta['input_sha256'] or len(body)!=meta['bytes']:raise ValueError('input identity mismatch')
        jobs=queue.Queue();rows=[None]*len(trace)
        def worker():
            while True:
                job=jobs.get()
                if job is None:return
                i,row,emitted,index=job;connection=None;healthy=False
                image=(i//2)%4;meta=manifests[index]['inputs'][image]
                row.update(endpoint=endpoints[index],endpoint_index=index,image_index=image,input_sha256=meta['input_sha256'],
                    input_bytes=len(inputs[image]),dispatch_unix_ns=time.time_ns(),client_queue_ms=(time.monotonic_ns()-emitted)/1e6)
                try:
                    connection=pools[index].acquire()
                    connection.request('POST','/infer',inputs[image],{'Content-Type':'image/jpeg','X-Request-ID':row['id']})
                    reply=connection.getresponse();body=json.loads(reply.read());validate_reply(reply.status,body,meta)
                    if body['request_id']!=row['id']:raise ValueError('response ID mismatch')
                    row.update(status='success',checksum=body['checksum'],output_sha256=body['output_sha256'],worker_response=body,
                        connection_local_port=connection.sock.getsockname()[1]);healthy=True
                except Exception as error:row.update(status='request_error',error=f'{type(error).__name__}: {error}')
                finally:
                    if connection is not None:pools[index].release(connection,healthy)
                    policy.release(index)
                row.update(reply_unix_ns=time.time_ns(),latency_ms=(time.monotonic_ns()-emitted)/1e6)
                row['on_time']=row['status']=='success' and row['latency_ms']<=qos_ms;rows[i]=row
        threads=[threading.Thread(target=worker,daemon=True) for _ in range(channels)]
        for thread in threads:thread.start()
        started=time.monotonic_ns();unix=time.time_ns()
        for i,item in enumerate(trace):
            delay=(started+item['scheduled_offset_ns']-time.monotonic_ns())/1e9
            if delay>0:time.sleep(delay)
            emitted=time.monotonic_ns();row=dict(item,submit_unix_ns=time.time_ns(),qos_ms=qos_ms)
            index=policy.reserve();row['inflight_at_selection']=policy.snapshot();jobs.put((i,row,emitted,index))
        for _ in threads:jobs.put(None)
        for thread in threads:thread.join()
        if any(r is None for r in rows) or any(policy.snapshot()):raise RuntimeError('incomplete request accounting')
        summary=summarize(rows,window_ms)
        summary.update(start_unix_ns=unix,end_unix_ns=time.time_ns(),drain_duration_s=(time.monotonic_ns()-started)/1e9,
            policy='custom-local-first',endpoint_selection='deadline-blind local-first with inflight cap2 and shared per-endpoint keepalive pool',
            endpoints=endpoints,local_count=local_count,local_cap=cap,channels=channels,admission_rejections=0,worker_manifests=manifests)
        return rows,summary
    finally:
        for pool in pools:pool.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace',type=Path,required=True);parser.add_argument('--endpoints',nargs='+',required=True)
    parser.add_argument('--local-count',type=int,required=True);parser.add_argument('--local-cap',type=int,default=2)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--window-ms',type=int,required=True)
    parser.add_argument('--qos-ms',type=int,default=135);parser.add_argument('--channels',type=int,default=48)
    parser.add_argument('--out',type=Path,required=True);a=parser.parse_args()
    if a.channels!=48 or a.local_cap!=2 or a.qos_ms!=135:parser.error('frozen formal48channels/cap2/deadline135 required')
    a.out.mkdir(parents=True,exist_ok=False)
    rows,summary=run(load_trace(a.trace),a.endpoints,a.local_count,a.inputs,a.qos_ms,a.window_ms,a.channels,a.local_cap)
    summary['trace_sha256']=hashlib.sha256(a.trace.read_bytes()).hexdigest()
    (a.out/'requests.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));(a.out/'run.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ['offered','success','on_time','on_time_goodput_rps']}))
