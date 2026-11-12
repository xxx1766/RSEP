#!/usr/bin/env python3
"""Open-loop inference requests to one Service VIP; no backend selection."""
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


def run(trace,endpoint,input_dir,qos_ms,window_ms,channels=48):
 host,port=endpoint.split(':');port=int(port)
 inputs=[(input_dir/f'image-{i}.jpg').read_bytes() for i in range(4)]
 conn=http.client.HTTPConnection(host,port,timeout=6)
 conn.request('GET','/manifest');response=conn.getresponse()
 if response.status!=200:raise RuntimeError('Service manifest unavailable')
 manifest=json.loads(response.read());conn.close()
 for body,meta in zip(inputs,manifest['inputs']):
  if hashlib.sha256(body).hexdigest()!=meta['input_sha256'] or len(body)!=meta['bytes']:raise RuntimeError('input identity mismatch')
 jobs=queue.Queue();rows=[None]*len(trace);ready=[];errors=[]
 def worker(channel,event):
  conn=http.client.HTTPConnection(host,port,timeout=6)
  try:
   conn.request('GET','/health');r=conn.getresponse()
   if r.status!=200 or not json.loads(r.read()).get('ready'):raise RuntimeError('Service health failed')
  except Exception as e:errors.append(str(e));event.set();conn.close();return
  event.set()
  while True:
   job=jobs.get()
   if job is None:break
   i,row,emitted=job;body=inputs[(i//2)%4];meta=manifest['inputs'][(i//2)%4]
   row.update(endpoint=endpoint,channel=channel,dispatch_unix_ns=time.time_ns(),
     client_queue_ms=(time.monotonic_ns()-emitted)/1e6,image_index=(i//2)%4,
     input_sha256=meta['input_sha256'],input_bytes=len(body))
   try:
    conn.request('POST','/infer',body,{'Content-Type':'image/jpeg','X-Request-ID':row['id']})
    r=conn.getresponse();reply=json.loads(r.read());validate_reply(r.status,reply,meta)
    if reply['request_id']!=row['id']:raise RuntimeError('request identity mismatch')
    row.update(status='success',checksum=reply['checksum'],output_sha256=reply['output_sha256'],worker_response=reply,
      connection_local_port=conn.sock.getsockname()[1])
   except Exception as e:
    row.update(status='request_error',error=f'{type(e).__name__}: {e}');conn.close();conn=http.client.HTTPConnection(host,port,timeout=6)
   row.update(reply_unix_ns=time.time_ns(),latency_ms=(time.monotonic_ns()-emitted)/1e6)
   row['on_time']=row['status']=='success' and row['latency_ms']<=qos_ms;rows[i]=row
  conn.close()
 threads=[]
 for channel in range(channels):
  event=threading.Event();ready.append(event);thread=threading.Thread(target=worker,args=(channel,event),daemon=True);thread.start();threads.append(thread)
 for event in ready:
  if not event.wait(15):errors.append('connection readiness timeout')
 if errors:
  for _ in threads:jobs.put(None)
  raise RuntimeError(errors)
 started=time.monotonic_ns();unix=time.time_ns()
 for i,item in enumerate(trace):
  pause=(started+item['scheduled_offset_ns']-time.monotonic_ns())/1e9
  if pause>0:time.sleep(pause)
  emitted=time.monotonic_ns();row=dict(item,submit_unix_ns=time.time_ns(),qos_ms=qos_ms)
  jobs.put((i,row,emitted))
 for _ in threads:jobs.put(None)
 for thread in threads:thread.join()
 if any(r is None for r in rows):raise RuntimeError('incomplete offered accounting')
 summary=summarize(rows,window_ms)
 summary.update(start_unix_ns=unix,end_unix_ns=time.time_ns(),drain_duration_s=(time.monotonic_ns()-started)/1e9,
   service_endpoint=endpoint,channels=channels,policy='native-Service',worker_manifests=[manifest],
   endpoint_selection='Kubernetes Service; client does not discover or choose backend Pod IP',warmups=[])
 return rows,summary


if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trace',type=Path,required=True);p.add_argument('--endpoint',required=True)
 p.add_argument('--inputs',type=Path,required=True);p.add_argument('--qos-ms',type=int,default=135);p.add_argument('--window-ms',type=int,required=True)
 p.add_argument('--channels',type=int,default=48);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if not 1<=a.channels<=64 or a.qos_ms<=0 or a.window_ms<=0:p.error('positive bounded controls required')
 a.out.mkdir(parents=True,exist_ok=False);rows,summary=run(load_trace(a.trace),a.endpoint,a.inputs,a.qos_ms,a.window_ms,a.channels)
 summary['trace_sha256']=hashlib.sha256(a.trace.read_bytes()).hexdigest()
 (a.out/'requests.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));(a.out/'run.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(json.dumps({k:summary[k] for k in ['offered','success','on_time','on_time_goodput_rps']}))
