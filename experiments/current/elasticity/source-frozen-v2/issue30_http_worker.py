"""HTTP transport for the frozen native ten-inference implementation.

One inference at a time per two-CPU replica, no admission or deadline knowledge.
All accepted requests keep executing if their HTTP client disconnects.
"""
import os,resource
_nofile_soft,_nofile_hard=resource.getrlimit(resource.RLIMIT_NOFILE)
assert _nofile_hard>=65536
resource.setrlimit(resource.RLIMIT_NOFILE,(65536,_nofile_hard))
os.sched_setaffinity(0,set(range(12)))
os.environ.update(ORT_INTRA_THREADS='2',ORT_INTER_THREADS='1',TIDE_DETECTIONS_PER_REQ='10',TIDE_WORKLOAD_FORCE_CPUBURN='0')
import asyncio,concurrent.futures,json,signal,sys,time
from pathlib import Path
sys.path.insert(0,'/app')
import infer
from threefig_worker import execute
UID=os.environ['POD_UID'];NAME=os.environ['POD_NAME'];NODE=os.environ['NODE_NAME']
OUT=Path('/evidence')/UID;OUT.mkdir(exist_ok=True)
J=(OUT/('events-'+str(time.time_ns())+'.jsonl')).open('x',buffering=1)
COUNTS=dict(accepted=0,started=0,completed=0,failed=0,active=0,queued=0,client_disconnected=0)
STOP=False;TASKS=set();POOL=concurrent.futures.ThreadPoolExecutor(max_workers=1)
def event(kind,**kw):J.write(json.dumps(dict(kind=kind,unix_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),pod_uid=UID,pod_name=NAME,node_name=NODE,**kw))+'\n')
async def reply(writer,status,data):
 body=json.dumps(data,separators=(',',':')).encode();writer.write(('HTTP/1.1 '+str(status)+' OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body))+'\r\nConnection: close\r\n\r\n').encode()+body);await writer.drain()
def compute(payload,ident):
 event('inference_started',id=ident)
 try:
  w=execute(payload,ident);event('inference_completed',id=ident,witness=w);return w
 except BaseException as exc:event('inference_failed',id=ident,error=repr(exc));raise
async def handler(reader,writer):
 task=asyncio.current_task();TASKS.add(task)
 try:
  raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),15);lines=raw.decode('iso-8859-1').split('\r\n');method,path,_=lines[0].split(' ',2);h={}
  for s in lines[1:]:
   if ':' in s:k,v=s.split(':',1);h[k.lower().strip()]=v.strip()
  if method=='GET':
   await reply(writer,200,dict(ready=not STOP,pod_uid=UID,node_name=NODE,affinity=sorted(os.sched_getaffinity(0)),counts=COUNTS,nofile_limit=resource.getrlimit(resource.RLIMIT_NOFILE)[0],open_fds=len(os.listdir('/proc/self/fd'))));return
  assert path=='/run';n=int(h['content-length']);assert 0<n<1048576
  payload=await reader.readexactly(n);ident=h['x-request-id'];COUNTS['accepted']+=1;COUNTS['queued']+=1;event('accepted',id=ident,n=n)
  async with LOCK:
   COUNTS['queued']-=1;COUNTS['active']+=1;COUNTS['started']+=1
   try:
    w=await asyncio.get_running_loop().run_in_executor(POOL,compute,payload,ident)
    response=dict(status='success',request_id=ident,client_request_id=ident,result_json=w,receiver_after_wait=dict(node=NODE));COUNTS['completed']+=1;code=200
   except Exception as exc:COUNTS['failed']+=1;response=dict(status='failed',request_id=ident,error=repr(exc));code=500
   finally:COUNTS['active']-=1
  try:await reply(writer,code,response);event('response_sent',id=ident)
  except (ConnectionError,BrokenPipeError):COUNTS['client_disconnected']+=1;event('response_client_disconnected',id=ident)
 except Exception as exc:
  event('http_error',error=repr(exc))
  try:await reply(writer,500,dict(status='failed',error=repr(exc)))
  except Exception:pass
 finally:
  writer.close()
  try:await writer.wait_closed()
  except Exception:pass
  TASKS.discard(task)
async def main():
 global LOCK,STOP
 LOCK=asyncio.Lock();sess=infer._ensure_session();event('model_ready',affinity=sorted(os.sched_getaffinity(0)),ort_intra_threads=sess.get_session_options().intra_op_num_threads)
 server=await asyncio.start_server(handler,'0.0.0.0',19384,backlog=8192)
 ctl=await asyncio.start_server(handler,'0.0.0.0',19385,backlog=128)
 ending=asyncio.Event()
 asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,ending.set)
 await ending.wait();STOP=True;event('termination_received',counts=dict(COUNTS));server.close();await server.wait_closed()
 if TASKS:await asyncio.gather(*list(TASKS),return_exceptions=True)
 ctl.close();await ctl.wait_closed();event('terminated',counts=dict(COUNTS));J.flush();os.fsync(J.fileno())
if __name__=='__main__':asyncio.run(main())
