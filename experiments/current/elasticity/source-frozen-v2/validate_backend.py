"""Join HPA physical inference witnesses to offered IDs without hiding losses."""
from issue30_transport import *
from issue30_measurement import validate_witness
import collections

def main():
 plan=json.loads((OUT/'plan.json').read_text());physical=[]
 for host in HOSTS:
  dst=OUT/'backend'/host;dst.mkdir(parents=True,exist_ok=True);pull(host,REMOTE+'/hpa-requests/.',dst)
  for p in dst.rglob('*.jsonl'):
   for line in p.read_text().splitlines():
    row=json.loads(line);row['physical_host']=host
    if row['kind']=='inference_completed':validate_witness(row['witness'],INPUT,GOLD)
    physical.append(row)
 results=[]
 for item in plan['order']:
  if item['arm']!='hpa':continue
  run=item['run'];root=OUT/'runs'/run/'NJ-k1/measurement';meta=json.loads((root/'run.json').read_text());expected=set(meta['expected_ids']);rows=[r for r in physical if r.get('id') in expected]
  accepted=[r for r in rows if r['kind']=='accepted'];completed=[r for r in rows if r['kind']=='inference_completed'];started=[r for r in rows if r['kind']=='inference_started'];failed=[r for r in rows if r['kind']=='inference_failed'];counts=collections.Counter(r['id'] for r in completed)
  assert all(v==1 for v in counts.values()),'duplicate physical inference executions'
  client=[json.loads(s) for s in (root/'requests.jsonl').read_text().splitlines()];success=[r for r in client if isinstance(r.get('response'),dict) and r['response'].get('status')=='success'];assert all(r['id'] in counts for r in success),'client success lacks physical execution'
  results.append(dict(run=run,offered=len(expected),accepted=len(accepted),started=len(started),completed=len(completed),physical_failed=len(failed),client_success=len(success),unaccepted=len(expected)-len(accepted),accepted_not_completed_after_cleanup=len(accepted)-len(completed)-len(failed),destinations=dict(collections.Counter(r['physical_host'] for r in completed))))
 save(OUT/'backend-audit.json',dict(status='PASS',runs=results));print(json.dumps(results,indent=2))
if __name__=='__main__':main()
