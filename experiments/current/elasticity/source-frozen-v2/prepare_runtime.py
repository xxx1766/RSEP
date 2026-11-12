from issue30_transport import *
import shlex
import concurrent.futures

def prepare(node):
 host,nid,role,ip=node
 result=ssh(host,'RSEP_ISSUE30_ROOT='+shlex.quote(REMOTE)+' python3 -', (OUT/'source/remote_prepare.py').read_text(),timeout=40);save(OUT/'preflight'/f'{host}-new-root.json',json.loads(result))
 for f in ['node_control_v3.py','calibrate_vision.py','collect_measurement.py','issue30_measurement.py','issue30_http_worker.py']:
  upload(host,OUT/'source'/f,REMOTE+'/source/'+f)
 code=f'''import pathlib,shutil,json,hashlib,subprocess
r=pathlib.Path({REMOTE!r});old=pathlib.Path({OLD!r})
for n in ['native-dynamic','dog.jpg','expected-identities.json','node-roles.json','node-cpu-budgets.json','native-config-preflight.json']:
 shutil.copy2(old/'runtime'/n,r/'runtime'/n)
assert hashlib.sha256((r/'runtime/native-dynamic').read_bytes()).hexdigest()=={BINARY!r}
assert hashlib.sha256((r/'runtime/dog.jpg').read_bytes()).hexdigest()=={INPUT!r}
(r/'hpa-requests').mkdir()
images=subprocess.check_output(['ctr','-n','k8s.io','images','ls'],text=True)
if {IMAGE!r} not in images:
 archive=str(r/'runtime/inference-image.tar')
 subprocess.run(['ctr','-n','tide','images','export',archive,{IMAGE!r}],check=True,timeout=90)
 subprocess.run(['ctr','-n','k8s.io','images','import',archive],check=True,timeout=90)
images=subprocess.check_output(['ctr','-n','k8s.io','images','ls'],text=True)
rows=[x.split() for x in images.splitlines() if x.startswith({IMAGE!r}+' ')]
assert len(rows)==1 and rows[0][2]=={DIGEST!r},rows
print(json.dumps(dict(host={host!r},binary_sha256={BINARY!r},image=rows,source='existing verified native workload')))
'''
 result=py(host,code,timeout=220);save(OUT/'preflight'/f'{host}-runtime-prepared.json',dict(stdout=result));return host
with concurrent.futures.ThreadPoolExecutor(3) as pool:
 for host in pool.map(prepare,NODES):print(host+' prepared',flush=True)
