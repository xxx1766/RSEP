from issue30_transport import *
import concurrent.futures,subprocess,traceback,sys
try:
 end=time.monotonic()+240
 while time.monotonic()<end:
  code=f'''import pathlib,json
r=pathlib.Path({REMOTE!r});p=r/'evidence/capacity-calibration.json';print(json.dumps(dict(done=p.exists(),log=(r/'evidence/capacity-calibration.log').read_text()[-1000:])))
'''
  x=json.loads(py('NJ-k1',code));print(json.dumps(x),flush=True)
  if x['done']:break
  if 'Traceback' in x['log']:raise RuntimeError(x)
  time.sleep(15)
 assert x['done'],'independent capacity calibration did not finish'
 def qualify(host):
  code=f'''import pathlib,json,time,collections
r=pathlib.Path({REMOTE!r});rows=[]
for p in (r/'hpa-requests').rglob('*.jsonl'):
 rows += [json.loads(s) for s in p.read_text().splitlines()]
c=collections.Counter(v['kind'] for v in rows);assert c['accepted']==c['inference_completed'] and not c['inference_failed'],c
before=list(map(int,pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:]));time.sleep(1);after=list(map(int,pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:]));d=[a-b for a,b in zip(after,before)];steal=d[7]/sum(d);assert steal<=.10,steal
print(json.dumps(dict(backend_event_counts=dict(c),cpu_steal_fraction=steal)))
'''
  x=json.loads(py(host,code,timeout=20));save(OUT/'preflight'/(host+'-capacity-backend-qualified.json'),x);return host,x
 with concurrent.futures.ThreadPoolExecutor(3) as pool:
  for x in pool.map(qualify,HOSTS):print(json.dumps(x),flush=True)
 result=subprocess.run([sys.executable,str(OUT/'source/smoke_and_launch.py')]);assert result.returncode==0,result.returncode
except BaseException as exc:
 save(OUT/'STOPPED.json',dict(error=repr(exc),traceback=traceback.format_exc(),unix_ns=time.time_ns()));raise
