from issue30_campaign import *
if not (OUT/'plan.json').exists():
 result=json.loads(py('NJ-k1',f'import pathlib;print((pathlib.Path({REMOTE!r})/"evidence/capacity-calibration.json").read_text())',timeout=30));save(OUT/'calibration/capacity-calibration.json',result)
 created=json.loads((OUT/'calibration/deployment-created.json').read_text());remove_deployment(created['metadata']['uid'])
 for host in HOSTS:
  for name in ['issue30_measurement.py','issue30_client.py']:upload(host,SOURCE/name,REMOTE+'/source/'+name)
plan=freeze() if not (OUT/'plan.json').exists() else json.loads((OUT/'plan.json').read_text());print('FROZEN '+json.dumps(dict(rate=plan['high_rate_rps'],capacity=plan['calibration_capacity_rps'],order=plan['order'])),flush=True)
trace=''.join(json.dumps(dict(id='smoke-'+str(i),offset_ns=i*500_000_000))+'\n' for i in range(4));ssh('NJ-k1','cat > '+REMOTE+'/runtime/smoke.jsonl',trace)
run='smoke-tide';(OUT/'runs'/run).mkdir(exist_ok=False)
try:
 native_action('start',run);join(run);launch_client(run,'tide','http://198.51.100.12:31841/run',REMOTE+'/runtime/smoke.jsonl',baseline=2,duration=2,recovery=8);wait_client(run,250)
finally:
 native_action('stop',run)
 for host in HOSTS:
  dst=OUT/'runs'/run/host;dst.mkdir();pull(host,REMOTE+'/runs/'+run+'/.',dst)
summary=json.loads((OUT/'runs'/run/'NJ-k1/measurement/run.json').read_text())['summary'];assert summary['success']>0,summary
print('native smoke physical inference PASS',flush=True)
run='smoke-hpa';(OUT/'runs'/run).mkdir();dep=create_deployment(1,run);h=None
try:
 save(OUT/'runs'/run/'deployment-created.json',dep);p=wait_ready(1);save(OUT/'runs'/run/'pods-initial-ready.json',p)
 service=json.loads(kubectl(['get','svc','fig7-inference','-n',NS,'-o','json']));h=create_hpa(run);save(OUT/'runs'/run/'hpa-created.json',h)
 launch_client(run,'hpa','http://'+service['spec']['clusterIP']+':19384/run',REMOTE+'/runtime/smoke.jsonl',baseline=2,duration=2,recovery=35);wait_client(run,250)
 metrics=json.loads(kubectl(['get','--raw','/apis/metrics.k8s.io/v1beta1/namespaces/'+NS+'/pods']));assert metrics['items'];save(OUT/'runs'/run/'actual-metrics.json',metrics)
 actual=json.loads(kubectl(['get','hpa','fig7-inference','-n',NS,'-o','json']));save(OUT/'runs'/run/'hpa-after.json',actual)
 assert any(c['type']=='ScalingActive' and c['status']=='True' for c in actual.get('status',{}).get('conditions',[])),actual
finally:remove_deployment(dep['metadata']['uid'],h['metadata']['uid'] if h else None)
dst=OUT/'runs'/run/'NJ-k1';dst.mkdir();pull('NJ-k1',REMOTE+'/runs/'+run+'/.',dst)
summary=json.loads((dst/'measurement/run.json').read_text())['summary'];assert summary['success']==4,summary
print('HPA native metric + Service physical inference PASS',flush=True)
for item in plan['order']:run_one(item,plan)
write_status('COMPARISONS_COLLECTED',runs=6)
