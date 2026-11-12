"""Independent low-rate guest observations; monotonic duration, no deadline scoring."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import signal
import threading
import time
from issue24_network_probe import probe


def validate_inference(status,body,expected,ident):
 if status!=200 or body.get('status')!='success' or body.get('finite') is not True or body.get('shape')!=[1,3549,85]:
  raise ValueError('status/finite/shape mismatch')
 for key in ['input_sha256','output_sha256','checksum','bytes']:
  if body.get(key)!=expected[key]:raise ValueError(key+' mismatch')
 if body.get('request_id')!=ident:raise ValueError('request identity mismatch')


def inference(image,expected,ident):
 start=time.monotonic_ns();row=dict(id=ident,unix_ns=time.time_ns(),monotonic_ns=start,stage='connect',input_sha256=expected['input_sha256'])
 conn=http.client.HTTPConnection('127.0.0.1',19383,timeout=2)
 try:
  conn.connect();row['connect_ms']=(time.monotonic_ns()-start)/1e6;row['stage']='send'
  conn.request('POST','/infer',image,{'Content-Type':'image/jpeg','X-Request-ID':ident});row['stage']='headers'
  reply=conn.getresponse();row['http_status']=reply.status;row['stage']='body';body=json.loads(reply.read());row['stage']='validate'
  validate_inference(reply.status,body,expected,ident);row.update(ok=True,stage='done',worker_response=body)
 except Exception as error:row.update(ok=False,error=f'{type(error).__name__}: {error}')
 finally:conn.close()
 row.update(ended_unix_ns=time.time_ns(),elapsed_ms=(time.monotonic_ns()-start)/1e6)
 return row


def run(a):
 stop=threading.Event();signal.signal(signal.SIGTERM,lambda *_:stop.set())
 period=1 if a.mode=='heartbeat' else 5
 start=time.monotonic_ns();until=start+int(a.seconds*1e9);prev=start;index=0
 image=Path(a.image).read_bytes() if a.mode=='infer' else None
 expected=json.loads(Path(a.manifest).read_text())['inputs'][0] if image is not None else None
 if image is not None:assert len(image)==expected['bytes'] and hashlib.sha256(image).hexdigest()==expected['input_sha256']
 with a.out.open('x',buffering=1) as f,ThreadPoolExecutor(3) as pool:
  while not stop.is_set() and time.monotonic_ns()<until:
   now=time.monotonic_ns();base=dict(index=index,unix_ns=time.time_ns(),monotonic_ns=now,gap_ms=(now-prev)/1e6,scheduled_offset_ms=index*period*1000,lateness_ms=(now-start)/1e6-index*period*1000);prev=now
   if a.mode=='heartbeat':
    row=dict(kind='tick',**base,cpu=Path('/proc/stat').read_text().splitlines()[0],uptime=Path('/proc/uptime').read_text(),loadavg=Path('/proc/loadavg').read_text(),meminfo=Path('/proc/meminfo').read_text(),pressure={x:Path('/proc/pressure/'+x).read_text() for x in ['cpu','memory','io']})
    f.write(json.dumps(row)+'\n')
   elif a.mode=='health':
    endpoints=['127.0.0.1:19383','127.0.0.1:30924',a.peer+':30924']
    for row in pool.map(lambda e:probe(e,f'issue29-health-{index}-{e}',4096 if e.endswith('30924') else 0),endpoints):
     f.write(json.dumps(dict(kind='http',slot=base,**row))+'\n')
   else:f.write(json.dumps(dict(kind='infer',slot=base,**inference(image,expected,f'issue29-infer-{a.vm}-{index}')))+'\n')
   index+=1
   target=start+int(index*period*1e9)
   if time.monotonic_ns()>target:
    index=max(index,int((time.monotonic_ns()-start)//int(period*1e9))+1)
    target=start+int(index*period*1e9)
   stop.wait(max(0,min(target,until)-time.monotonic_ns())/1e9)
  f.write(json.dumps(dict(kind='complete',start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),ended_unix_ns=time.time_ns(),duration_s=(time.monotonic_ns()-start)/1e9,interrupted=stop.is_set()))+'\n')


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['heartbeat','health','infer']);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--out',type=Path,required=True);p.add_argument('--peer');p.add_argument('--image');p.add_argument('--manifest');p.add_argument('--vm',required=True);run(p.parse_args())
