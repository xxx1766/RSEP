from issue30_kube import *
import concurrent.futures

def isolated(node):
 host,nid,role,ip=node
 code=f'''import pathlib,subprocess,json
r=pathlib.Path({REMOTE!r});argv=['ctr','-n','tide','run','--rm','--cpuset-cpus','0-1','--mount','type=bind,src='+str(r/'runtime')+',dst=/data,options=rbind:ro','--mount','type=bind,src='+str(r/'source')+',dst=/measure,options=rbind:ro','--env','ORT_INTRA_THREADS=2','--env','ORT_INTER_THREADS=1',{IMAGE!r},'issue30-calibration','python3','/measure/calibrate_vision.py']
p=subprocess.run(argv,capture_output=True,text=True,timeout=180)
(r/'evidence/isolated-calibration-command.json').write_text(json.dumps(dict(argv=argv,rc=p.returncode,stdout=p.stdout,stderr=p.stderr)))
assert p.returncode==0,p.stderr
result=json.loads(p.stdout);(r/'evidence/isolated-calibration.json').write_text(json.dumps(result));print(json.dumps(result))
'''
 result=json.loads(py(host,code,timeout=210));save(OUT/'calibration'/f'{host}-isolated.json',result)
 for row in result['rows']+result['warm']:
  from issue30_measurement import validate_witness
  validate_witness(row,INPUT,GOLD)
 return host
with concurrent.futures.ThreadPoolExecutor(3) as pool:
 for host in pool.map(isolated,NODES):print(host+' isolated calibration PASS',flush=True)
created=create_deployment(18,'capacity-calibration');save(OUT/'calibration/deployment-created.json',created)
p=wait_ready(18,240);save(OUT/'calibration/pods-ready.json',p)
assert all(sum(v['spec']['nodeName']==name for v in p['items'])==6 for name in NAMES)
for name in ['calibrate_capacity_remote.py','issue30_client.py','issue30_http_worker.py']:
 upload('NJ-k1',OUT/'source'/name,REMOTE+'/source/'+name)
ssh('NJ-k1','cat > '+REMOTE+'/runtime/calibration-pods.json',json.dumps(p))
code=f'''import subprocess,pathlib,json,os,time
r=pathlib.Path({REMOTE!r});log=(r/'evidence/capacity-calibration.log').open('x')
p=subprocess.Popen(['taskset','-c','12-15','python3',str(r/'source/calibrate_capacity_remote.py')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,RSEP_ISSUE30_ROOT={REMOTE!r}))
identity=dict(pid=p.pid,proc_start=pathlib.Path('/proc/'+str(p.pid)+'/stat').read_text().split()[21],started_ns=time.time_ns());(r/'evidence/calibration-launch.json').write_text(json.dumps(identity));print(json.dumps(identity))
'''
save(OUT/'calibration/remote-launch.json',json.loads(py('NJ-k1',code)));print('fixed-inventory capacity calibration launched',flush=True)
