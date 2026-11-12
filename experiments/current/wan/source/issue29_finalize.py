"""Fresh integrity checks and sealing, after collection and owned cleanup."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import issue29_common as p
from issue29_analyze import analyze

def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def frozen_run_checks():
 frozen=json.loads((p.OUT/'parameters-frozen.json').read_text());progress=json.loads((p.OUT/'pilot-results-progress.json').read_text());compute=set(json.loads((p.OUT/'plan.json').read_text())['compute']);checks=[]
 def pods(file):
  return {r['metadata']['name']:dict(uid=r['metadata']['uid'],node=r['spec']['nodeName'],containers=[(c['name'],c.get('containerID'),c['restartCount']) for c in r['status'].get('containerStatuses',[])]) for r in json.loads(file.read_text())['items'] if r['spec'].get('nodeName') in compute}
 for row in progress['results']:
  folder=p.OUT/'runs'/row['name'];before=pods(folder/'pods-before.json');after=pods(folder/'pods-after.json');assert len(before)==6 and before==after,row['name']
  native=row['arm'].startswith('tide')
  if native:
   origin=json.loads((folder/'origin.json').read_text());profile=frozen['predictors']['contexts'][str(row['rtt_ms'])];nodes={vm for vm in compute if vm[4]=='l'} if row['arm']=='tide-local-only' else compute
   assert origin['entry']=='external' and origin['qos_ms']==135 and origin['max_inflight']==48 and origin['warmup_qos_ms']==2000,row['name']
   assert origin['node_execution_ms']=={vm:profile[vm]['execution_ms'] for vm in nodes},row['name']
   assert {c['node_id']:c['network_ms'] for c in origin['candidates']}=={vm:profile[vm]['network_ms'] for vm in nodes},row['name']
   assert len(origin['runs'])==1 and origin['runs'][0]['weights']==[1,1,1],row['name']
  checks.append(dict(name=row['name'],six_pod_identities_and_restart_counts_unchanged=True,frozen_native_parameters_verified=native))
 p.save(p.OUT/'source/run-invariants.json',dict(runs=checks))
 return checks

def main():
 assert not (p.OUT/'ARTIFACT_SHA256.json').exists(),'do not overwrite sealed evidence'
 final=json.loads((p.OUT/'campaign-final.json').read_text())
 assert final['status'] in ['COMPLETE','STOPPED']
 cleanup=json.loads((p.OUT/'cleanup/verified.json').read_text());assert cleanup['status']=='PASS'
 assert (p.OUT/'collection/complete.json').exists()
 invariants=frozen_run_checks()
 analyze()
 r=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(p.SOURCE_DIR),'-p','test_issue29_pilot.py','-v'],capture_output=True,text=True)
 (p.OUT/'source/tests.txt').write_text(r.stdout+r.stderr);assert r.returncode==0
 for filename,args in [('git-head.txt',['git','rev-parse','HEAD']),('git-status.txt',['git','status','--short']),('git-tracked-diff.patch',['git','diff','--','code/tide/scripts/revision','code/tide/pkg/stub/issue23_native_process_test.go'])]:
  snapshot=subprocess.run(args,capture_output=True,text=True,check=True);(p.OUT/'source'/filename).write_text(snapshot.stdout)
 previous={}
 names=['results-issue23-vm-wan-20261031','results-issue24-network-recheck-20261031',*[f'results-issue{i}-{name}-pair-stability-20261031' for i,name in [(25,'single'),(26,'two'),(27,'four'),(28,'three')]]]
 for name in names:
  root=p.CANONICAL/name;manifest=json.loads((root/'ARTIFACT_SHA256.json').read_text())['files']
  for rel,meta in manifest.items():
   f=root/rel;assert f.stat().st_size==meta['bytes'] and sha(f)==meta['sha256'],str(f)
  previous[name]=len(manifest)
 prod=json.loads((p.OLD/'source/production-sha256.json').read_text())
 for rel,want in prod.items():assert sha(Path(rel))==want,rel
 for f in p.SOURCE_DIR.glob('*issue29*.py'):(p.OUT/'source'/f.name).write_bytes(f.read_bytes())
 for name in ['issue12_trace.py','issue12_nj_job.py','issue22_service_client.py','issue23_lf_client.py','issue23_http_client.py','issue22_clock_probe.py','issue24_network_probe.py']:
  f=p.SOURCE_DIR/name
  if f.exists():(p.OUT/'source'/f.name).write_bytes(f.read_bytes())
 p.save(p.OUT/'source/current-helper-hashes.json',{f.name:sha(f) for f in sorted((p.OUT/'source').glob('*issue29*.py'))})
 summary=json.loads((p.OUT/'pilot_summary.json').read_text());qualified=sum(r['infrastructure_qualified'] for r in summary['records'])
 assert qualified==final['completed'],(qualified,final)
 if final['status']=='COMPLETE':assert qualified==34 and not summary['missing_runs']
 checks=dict(campaign=final,observed_runs=summary['observed_runs'],qualified_runs=qualified,run_invariants_checked=len(invariants),tests_passed=5,previous_artifacts_unchanged=previous,production_go_files_unchanged=len(prod),cleanup=cleanup,unix_ns=time.time_ns())
 p.save(p.OUT/'source/verification.json',checks)
 with (p.OUT/'REPORT.md').open('a') as f:
  f.write(f"\nFinal verification: {qualified}/34 cells passed the runtime gate; 8 dedicated VMs shut down normally, the dedicated network was released, and the shared cluster was 3/3 Ready. The historical files 23-28 ({sum(previous.values())} sealed files) and {len(prod)} production Go file hashes remain consistent. Details are in source/verification.json and cleanup/verified.json.\n")
 p.save(p.OUT/'CONTINUATION_STATE.json',dict(status='COMPLETE_'+final['status'],root=str(p.OUT),worktree=str(Path.cwd()),scope='3+3nestedVMsingle-repeatWANpilot',report='REPORT.md',summary='summary.csv',cleanup=cleanup,next='No automatic enlargement, retuning, extra repeats or manuscript replacement. Review all conditions and path boundaries.',updated_unix_ns=time.time_ns()))
 files={str(f.relative_to(p.OUT)):dict(bytes=f.stat().st_size,sha256=sha(f)) for f in sorted(p.OUT.rglob('*')) if f.is_file() and f.name!='ARTIFACT_SHA256.json'}
 p.save(p.OUT/'ARTIFACT_SHA256.json',dict(files=files,unix_ns=time.time_ns()))
 manifest=json.loads((p.OUT/'ARTIFACT_SHA256.json').read_text())['files']
 for rel,meta in manifest.items():
  f=p.OUT/rel;assert f.stat().st_size==meta['bytes'] and sha(f)==meta['sha256'],rel
 print(json.dumps(dict(status=final['status'],qualified_runs=qualified,sealed_files=len(files),historical_files=sum(previous.values()),production_files=len(prod),report=str(p.OUT/'REPORT.md'))))

if __name__=='__main__':main()
