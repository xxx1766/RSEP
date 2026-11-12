"""Regression test for the observed 1024-FD adapter failure, with real sockets."""
import asyncio,json,os,resource,time,sys
from collect_measurement import http_request
from issue30_measurement import normalize_native_response
from pathlib import Path
resource.setrlimit(resource.RLIMIT_NOFILE,(65536,resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
async def main(host,port,control):
 holders=await asyncio.gather(*(asyncio.open_connection(host,port) for _ in range(1500)));await asyncio.sleep(.5);rows=[]
 try:
  for i in range(4):
   begin=time.monotonic_ns();code,_,raw=await http_request('http://'+host+':'+str(control)+'/health',None,{},1);row=dict(index=i,status=code,latency_ms=(time.monotonic_ns()-begin)/1e6,response=json.loads(raw));rows.append(row);assert code==200
   if 'nofile_limit' in row['response']:assert row['response']['nofile_limit']==65536 and row['response']['open_fds']>1500
   await asyncio.sleep(.5)
  ident='fd-qualification-real-inference';jpeg=(Path(os.environ.get('RSEP_ISSUE30_ROOT','/var/lib/rsep/issue30'))/'runtime/dog.jpg').read_bytes();code,_,body=await http_request('http://'+host+':'+str(port)+'/run',jpeg,{'X-Request-ID':ident},20);request=dict(id=ident,http_status=code,response=json.loads(body));assert code==200 and normalize_native_response(request,'6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620','70342e4b8d42e2cfe364e443400f61d3d9ffa55b44bd20cbf247fe0032eaf365') is not None
 finally:
  for _,w in holders:w.close()
  await asyncio.gather(*(w.wait_closed() for _,w in holders),return_exceptions=True)
 print(json.dumps(dict(status='PASS',concurrent_idle_connections=1500,health_checks=rows,inference=request)),flush=True)
asyncio.run(main(sys.argv[1],int(sys.argv[2]),int(sys.argv[3])))
