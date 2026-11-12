from issue30_campaign import *
plan=json.loads((OUT/'plan.json').read_text());run='smoke-hpa-ready';(OUT/'runs'/run).mkdir(exist_ok=False)
write_status('UNSCORED_HPA_READINESS_QUALIFICATION',new_comparison_runs=0)
dep=create_deployment(1,run);h=None
try:
 save(OUT/'runs'/run/'deployment-created.json',dep);save(OUT/'runs'/run/'pods-initial-ready.json',wait_ready(1))
 h=create_hpa(run);save(OUT/'runs'/run/'hpa-created.json',h);qualify_hpa_metrics(run)
 service=json.loads(kubectl(['get','svc','fig7-inference','-n',NS,'-o','json']))
 launch_client(run,'hpa','http://'+service['spec']['clusterIP']+':19384/run',REMOTE+'/runtime/smoke.jsonl',baseline=2,duration=2,recovery=15);wait_client(run,250)
 save(OUT/'runs'/run/'hpa-after.json',json.loads(kubectl(['get','hpa','fig7-inference','-n',NS,'-o','json'])))
finally:remove_deployment(dep['metadata']['uid'],h['metadata']['uid'] if h else None)
dst=OUT/'runs'/run/'NJ-k1';dst.mkdir();pull('NJ-k1',REMOTE+'/runs/'+run+'/.',dst)
summary=json.loads((dst/'measurement/run.json').read_text())['summary'];assert summary['success']==4,summary
print('Native HPA with actual post-Ready CPU metrics qualified '+json.dumps(summary),flush=True)
for item in plan['order']:run_one(item,plan)
write_status('COMPARISONS_COLLECTED',runs=6)
