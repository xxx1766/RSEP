"""Correct the witnessed transport limit, qualify it, run all three fresh pairs."""
from issue30_campaign import *
import copy,subprocess,concurrent.futures
assert (OUT/'runs/b1-hpa/classification.json').exists()
assert not pods()['items']
cm=json.loads(kubectl(['get','cm','issue30-source','-n',NS,'-o','json']));assert cm['data']['issue30_http_worker.py']==(OUT/'source-history/issue30_http_worker.py.pre-fd-fix').read_text();save(OUT/'preflight/configmap-before-fd-fix.json',cm)
cm['data']['issue30_http_worker.py']=(SOURCE/'issue30_http_worker.py').read_text();save(OUT/'config/workload-source-configmap-v2.json',cm);save(OUT/'preflight/configmap-after-fd-fix.json',json.loads(kubectl(['replace','-f','-','-o','json'],json.dumps(cm))))
for host in HOSTS:upload(host,SOURCE/'node_control_v3.py',REMOTE+'/source/node_control_v3.py')
upload('NJ-k1',SOURCE/'qualify_fd_remote.py',REMOTE+'/source/qualify_fd_remote.py')
def probe(run,host,port,control):
 code=f'''import subprocess,json,os
p=subprocess.run(['taskset','-c','12-15','python3',{REMOTE!r}+'/source/qualify_fd_remote.py',{host!r},str({port}),str({control})],capture_output=True,text=True,timeout=40,env=dict(os.environ,RSEP_ISSUE30_ROOT={REMOTE!r}))
assert p.returncode==0,dict(stdout=p.stdout,stderr=p.stderr)
print(p.stdout)
'''
 x=json.loads(py('NJ-k1',code,timeout=50));save(OUT/'runs'/run/'fd-regression-qualified.json',x);print(run+' '+x['status'],flush=True)
run='fd-qual-hpa';(OUT/'runs'/run).mkdir();dep=create_deployment(1,run)
try:
 save(OUT/'runs'/run/'deployment-created.json',dep);p=wait_ready(1);save(OUT/'runs'/run/'pods.json',p);probe(run,p['items'][0]['status']['podIP'],19384,19385)
finally:remove_deployment(dep['metadata']['uid'])
run='fd-qual-tide';(OUT/'runs'/run).mkdir()
try:native_action('start',run);join(run);probe(run,'198.51.100.12',31841,31841)
finally:native_action('stop',run)
original=json.loads((OUT/'plan.json').read_text());plan=copy.deepcopy(original);plan['plan_file']='plan-v2.json';plan['frozen_unix_ns']=time.time_ns();plan['supersedes_for_qualified_pairs']='plan.json';plan['original_plan_sha256']=digest(OUT/'plan.json');plan['transport_nofile_soft_limit']=65536
for item in plan['order']:item['run']='v2-'+item['run']
for block in range(1,4):
 old=OUT/original['traces'][str(block)]['path'];new=OUT/'traces'/f'v2-block{block}.jsonl'
 with new.open('x') as f:
  for line in old.read_text().splitlines():
   row=json.loads(line);row['id']=row['id'].replace('issue30-','issue30-v2-',1);f.write(json.dumps(row)+'\n')
 plan['traces'][str(block)]=dict(path=str(new.relative_to(OUT)),sha256=digest(new),requests=original['traces'][str(block)]['requests'])
 upload('NJ-k1',new,REMOTE+'/runtime/v2-block'+str(block)+'.jsonl')
plan['sources']={p.name:digest(p) for p in SOURCE.glob('*.py')};frozen=OUT/'source-frozen-v2';frozen.mkdir()
for p in SOURCE.glob('*.py'):(frozen/p.name).write_bytes(p.read_bytes())
plan['notes']+=['Original b1-hpa retained: infrastructure invalid because a 1024-FD HTTP adapter prevented readiness; it is not interpreted as HPA scheduling behavior.','Both HTTP ingress paths now have a 65536 soft descriptor limit and passed 1500 concurrent-socket health/inference regression.','The CPU-HPA must report ScalingActive=True before the baseline clock; all other workload, CPU/memory and scaling settings stay frozen.']
save(OUT/'plan-v2.json',plan);print('V2 FROZEN '+json.dumps(plan['order']),flush=True)
for item in plan['order']:run_one(item,plan)
write_status('COMPARISONS_COLLECTED',runs=6,failed_original_trial_retained=True,plan_file='plan-v2.json')
