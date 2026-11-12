"""Read live observers without stopping or restarting them."""
from concurrent.futures import ThreadPoolExecutor
import json
import shlex
import time
import issue29_common as p
from issue29_start import ROOT

CODE='''import json,time
from pathlib import Path
root=Path(ROOT);out={}
for name in ['heartbeat','health','infer']:
 f=root/(name+'.jsonl')
 if not f.exists():continue
 lines=f.read_text().splitlines();rows=[]
 for x in lines:
  try:rows.append(json.loads(x))
  except json.JSONDecodeError:pass
 samples=[r for r in rows if r['kind']!='complete']
 out[name]=dict(samples=len(samples),completed=any(r['kind']=='complete' for r in rows),failures=sum(r.get('ok') is False for r in samples),max_gap_ms=max([r.get('gap_ms',0) for r in samples],default=0),max_latency_ms=max([r.get('elapsed_ms',0) for r in samples],default=0),first_unix_ns=samples[0]['unix_ns'] if samples else None,last_unix_ns=samples[-1]['unix_ns'] if samples else None,last=rows[-1] if rows else None)
print(json.dumps(dict(unix_ns=time.time_ns(),observations=out)))'''.replace('ROOT',repr(ROOT))

def sample(vm):
 try:return vm,json.loads(p.guest(vm,'python3 -c '+shlex.quote(CODE),timeout=20))
 except Exception as error:return vm,dict(read_error=str(error))

def main():
 with ThreadPoolExecutor(4) as pool:rows=dict(pool.map(sample,['i23-c01','i23-d01','i23-l01','i23-l02','i23-l03','i23-r01','i23-r02','i23-r03']))
 started=json.loads((p.OUT/'started.json').read_text())
 out=dict(unix_ns=time.time_ns(),minutes_since_started=(time.time_ns()-started['unix_ns'])/60e9,guests=rows)
 p.save(p.OUT/'progress'/(str(time.time_ns())+'.json'),out)
 print(json.dumps(dict(minutes=round(out['minutes_since_started'],2),guests={vm:dict(error=r.get('read_error'),observations={k:{x:y for x,y in v.items() if x not in ['last','first_unix_ns','last_unix_ns']} for k,v in r.get('observations',{}).items()}) for vm,r in rows.items()})))
 return out

if __name__=='__main__':main()
