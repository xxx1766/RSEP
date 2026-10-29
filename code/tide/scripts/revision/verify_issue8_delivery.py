#!/usr/bin/env python3
"""Verify issue-8 evidence and generated manuscript artifacts; record hashes."""
import csv,hashlib,json,re,subprocess,tarfile,datetime
from pathlib import Path
r=Path('code/Tide/results-issue8-20261005');formal=r/'formal';summ=formal/'summary'
a=json.loads((summ/'audit.json').read_text());assert a['status']=='complete' and a['sessions']==80 and a['requests']==1620
p=json.loads((formal/'progress.json').read_text());assert p['status']=='completed' and all(j['status']=='completed' for j in p['jobs'])
raw=json.loads((summ/'raw-sha256.json').read_text())
for name,digest in raw.items():assert hashlib.sha256((formal/name).read_bytes()).hexdigest()==digest,name
for path,key in [(r/'provenance/revision-latency','binary_sha256'),(r/'provenance/scripts/run_issue8_latency.py','script_sha256')]:assert hashlib.sha256(path.read_bytes()).hexdigest()==p[key]
assert json.loads((r/'provenance/final-process-check.json').read_text())['status']=='passed'
clean=json.loads((formal/'cleanup.json').read_text());assert not clean['queues'] and not clean['child_cgroups'] and not clean['parent_cpuset']
ctrl=Path('code/Tide/results-control-20261004/confirmatory-02/summary')
old=json.loads((ctrl/'independent-audit.json').read_text());assert old['status']=='complete' and old['requests']==85050
with (ctrl/'summary.csv').open() as f:rows=list(csv.DictReader(f))
assert len(rows)==30 and all(int(x['n'])==3 for x in rows)
text=Path('revision_data/6_control_overhead.tex').read_text();reply=Path('response_to_reviewers.tex').read_text();major=reply.split('\\subsection{Major 4 ---')[1].split('\\subsection{Major 5 ---')[0]
assert '\\input{revision_data/6_control_overhead}' in Path('revision_data/6_evaluation.tex').read_text()
assert '\\label{sec:control-footprint}' in text
assert 'not pure control-plane' in text and 'KubeEdge\'s core CPU is lower' in text
for number in ('163.41','61.21','31.32','104.81','85,050','13,031'):
 assert number in text and number in major,number
for phrase in ('flat in the request rate','CPU $<3','sustains $9.8','Consequently the control plane','attributable to the scheduling model'):
 assert phrase not in major,phrase
issue=Path('revision_issue_log.md').read_text().split('### 8.')[1].split('### 9.')[0];assert '- [ ]' not in issue and issue.count('- [x]')==5
for path in (Path('main_revision.pdf'),Path('response_to_reviewers.pdf')):
 pdftext=subprocess.check_output(['pdftotext','-layout',str(path),'-'],text=True)
 for number in ('163.41','61.21','31.32','104.81','85,050','29.23','204.56','1542.54'):assert number in pdftext,(str(path),number)
for path in (Path('main_revision.log'),Path('response_to_reviewers.log')):
 log=path.read_text();assert not re.search(r'Reference .*?undefined|There were undefined references',log)
for match in re.finditer(r'\]\(([^)]+)\)',(r/'RESULTS.md').read_text()):assert (r/match[1]).exists(),match[1]
files=[Path(x) for x in ('main_revision.tex','main_revision.pdf','response_to_reviewers.tex','response_to_reviewers.pdf','revision_issue_log.md','revision_data/6_evaluation.tex','revision_data/6_control_overhead.tex','revision_data/control_resource_table.tex','revision_data/5_RESP_algorithm.tex')]
files += [r/'RESULTS.md',r/'PLAN.md',r/'RUN_STATE.md',r/'provenance/instrumented-source.tar.gz',r/'provenance/instrumentation.patch']+list(summ.glob('*'))+list(Path('revision-figs/evaluation/issue8-20261005').glob('*'))+list(Path('code/Tide/scripts/revision').glob('*issue8*.py'))+list(Path('code/Tide/scripts/revision/issue8_trace').glob('*.go'))
manifest={str(f):dict(bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in files if f.is_file()}
(r/'delivery-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
result=dict(status='passed',verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),lifecycle_blocks=30,perturbation_blocks=10,new_requests=1620,control_matrix_requests=85050,raw_files_verified=len(raw),delivery_files=len(manifest),source_and_binary_verified=True,cleanup='passed',manuscript_numbers_and_references='passed',issue8_unchecked_items=0,visual_review='new figures and main pages 14-17; response Major 4 pages 6-8',build_notes='Both PDFs compiled. Main has no overfull boxes or undefined references; response has pre-existing overfull boxes outside Major 4. Template font/PDF/color-stack warnings remain; revised figures and text were visually checked.')
(r/'delivery-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
