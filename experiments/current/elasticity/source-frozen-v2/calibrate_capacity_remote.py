"""Three fixed-duration saturated blocks on the full independent inventory."""
import asyncio,json,os,time,pathlib
from collect_measurement import http_request,atomic_json,wait_until
from issue30_measurement import normalize_native_response
R=pathlib.Path(os.environ.get('RSEP_ISSUE30_ROOT','/var/lib/rsep/issue30'));G='70342e4b8d42e2cfe364e443400f61d3d9ffa55b44bd20cbf247fe0032eaf365';I='6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620'
async def main():
 pods=json.loads((R/'runtime/calibration-pods.json').read_text())['items'];payload=(R/'runtime/dog.jpg').read_bytes();out=R/'evidence/capacity-calibration.jsonl';blocks=[]
 assert len(pods)==18
 async def request(p,ident):
  start=time.monotonic_ns();status,_,raw=await http_request('http://'+p['status']['podIP']+':19384/run',payload,{'X-Request-ID':ident},60);row=dict(id=ident,http_status=status,response=json.loads(raw),begin_ns=start,end_ns=time.monotonic_ns())
  assert status==200 and normalize_native_response(row,I,G) is not None;return row
 warm=await asyncio.gather(*(request(p,'capacity-warm-'+p['metadata']['uid']) for p in pods));atomic_json(R/'evidence/capacity-warm.json',warm)
 with out.open('x') as f:
  for block in range(3):
   begin=time.monotonic_ns()+1_000_000_000;end=begin+30_000_000_000
   async def worker(p):
    await wait_until(begin);rows=[];i=0
    while time.monotonic_ns()<end:
     rows.append(await request(p,f'capacity-b{block}-{p["metadata"]["uid"]}-{i}'));i+=1
    return dict(pod=p['metadata']['name'],node=p['spec']['nodeName'],rows=rows)
   result=await asyncio.gather(*(worker(p) for p in pods));count=sum(r['end_ns']<=end for p in result for r in p['rows']);capacity=count/30
   b=dict(block=block,begin_ns=begin,end_ns=end,duration_s=30,completed_in_window=count,all_verified_completions=sum(len(p['rows']) for p in result),capacity_rps=capacity,workers=result);blocks.append(b);f.write(json.dumps(b)+'\n');f.flush();print(json.dumps(dict(block=block,capacity_rps=capacity)),flush=True)
 atomic_json(R/'evidence/capacity-calibration.json',dict(method='all 18 two-CPU workers saturated for 30 seconds, three blocks; every response physically verified including drain',blocks=blocks))
asyncio.run(main())
