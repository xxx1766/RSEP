from issue30_kube import *
created=json.loads((OUT/'calibration/deployment-created.json').read_text());current=json.loads(kubectl(['get','deploy','fig7-inference','-n',NS,'-o','json']));assert current['metadata']['uid']==created['metadata']['uid']
end=time.monotonic()+600
while time.monotonic()<end:
 nodes=json.loads(kubectl(['get','nodes','-o','json']));pressure=[v['metadata']['name'] for v in nodes['items'] if any(c['type']=='DiskPressure' and c['status']=='True' for c in v['status']['conditions'])]
 if not pressure:break
 print('waiting for kubelet disk-pressure transition',flush=True);time.sleep(15)
assert not pressure,'actual disk pressure did not clear; do not tolerate it'
p=wait_ready(18,180);save(OUT/'calibration/pods-ready.json',p)
assert all(sum(v['spec']['nodeName']==name for v in p['items'])==6 for name in NAMES)
for name in ['calibrate_capacity_remote.py','issue30_client.py']:upload('NJ-k1',OUT/'source'/name,REMOTE+'/source/'+name)
ssh('NJ-k1','cat > '+REMOTE+'/runtime/calibration-pods.json',json.dumps(p))
code=f'''import subprocess,pathlib,json,time
r=pathlib.Path({REMOTE!r});log=(r/'evidence/capacity-calibration.log').open('x')
p=subprocess.Popen(['taskset','-c','12-15','python3',str(r/'source/calibrate_capacity_remote.py')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=dict(os.environ,RSEP_ISSUE30_ROOT={REMOTE!r}))
identity=dict(pid=p.pid,proc_start=pathlib.Path('/proc/'+str(p.pid)+'/stat').read_text().split()[21],started_ns=time.time_ns());(r/'evidence/calibration-launch.json').write_text(json.dumps(identity));print(json.dumps(identity))
'''
save(OUT/'calibration/remote-launch.json',json.loads(py('NJ-k1',code)));print('fixed-inventory calibration launched',flush=True)
