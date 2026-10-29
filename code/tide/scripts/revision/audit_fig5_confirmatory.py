#!/usr/bin/env python3
"""Cross-check raw traces, immutable inputs, worker caps and observed CPU masks."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

from analyze_fig5_confirmatory import summarize_rows
from audit_fig5_runtime import audit
from observe_fig5_affinity import cpu_set


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--partial',action='store_true')
    args=parser.parse_args()
    plan=json.loads((args.root/'plan.json').read_text())
    source=json.loads((args.evidence/'source-and-binary-sha256.json').read_text())
    assert source['cmd/httpbase/dog.jpg']==source['cmd/resptestv2/dog.jpg'], 'different input images'
    expected={(f'C{c}',r,k) for c in range(1,7) for r in [1,2,4,8,16,32] for k in range(1,6)}
    assert {(c['cell'],c['rps'],c['repeat']) for c in plan['cases']}==expected
    assert len(plan['cases'])==180 and plan['duration_sec']==10 and plan['repeats']==5
    report=dict(scope='partial' if args.partial else 'complete',runs=0,requests=0,failures=0,warmup_requests=0,
                runtime_audits=0,cpu_snapshots=0,affinity_samples=0,affinity_coverage={},runtime_errors={})
    errors=collections.Counter()
    for case in plan['cases']:
        path=args.root/case['cell']/f"rps{case['rps']}"/f"run{case['repeat']}"
        if args.partial and not (path/'confirmatory-audit.json').exists(): continue
        manifest=json.loads((path/'manifest.json').read_text())
        assert manifest['status']=='completed'
        assert (manifest['cell'],manifest['rps'],manifest['repeat'])==(case['cell'],case['rps'],case['repeat'])
        assert manifest['duration_sec']=='10' and manifest['deadline_sec']=='90'
        env=manifest['environment']
        assert env['WORKLOAD_CPUSET']=='2-7' and env['TIDE_DETECTIONS_PER_REQ']=='10'
        assert env['TIDE_COMB_CPUIDS']=='2,4,3,5,6,7' and env['TIDE_COMB_DEV_CPUNUM']=='16'
        assert env['PAPER_CPUSET']=='2-7'
        http=case['cell'] in ['C1','C2']
        binary='httpbase' if http else 'resptestv2'
        assert manifest['binary_sha256']==source['bin/'+binary], 'binary changed'
        assert hashlib.sha256((path/'raw.csv').read_bytes()).hexdigest()==manifest['raw_sha256']
        rows=list(csv.DictReader((path/'raw.csv').open()))
        values=summarize_rows(rows,10)
        assert values['n_total']==case['rps']*10
        assert values['throughput']<=case['rps']+1e-9
        cc=case['cell'] in ['C2','C4','C6']
        cap=3 if cc else 6
        warm=list(csv.DictReader((path/'warmup.csv').open()))
        assert len(warm)==cap*4 and all(r['status']=='200' for r in warm)
        report['runs']+=1; report['requests']+=values['n_total']; report['failures']+=values['n_failed']; report['warmup_requests']+=len(warm)
        if not http:
            audit(path/'runtime.jsonl')
            report['runtime_audits']+=1
            logs=[json.loads(s) for s in (path/'runtime.jsonl').read_text().splitlines()]
            config=next(r for r in logs if r.get('msg')=='resptestv2 config')
            assert config['EnableConcurrencyControl']==cc
            assert config['EnableCpuset']==(case['cell'] in ['C5','C6'])
            assert config['warmup'] is True
            assert any(r.get('msg')=='start exp' and r.get('phase')=='measured' for r in logs)
            for r in logs:
                if r.get('level') in ['error','fatal']: errors[r.get('msg','')]+=1
            stderr=list((path/'worker-logs').glob('*/stderr'))
            assert len(stderr)==cap
            assert all('burn=False' in p.read_text() for p in stderr), 'worker did not confirm real inference'
            for line in (path/'host-samples.jsonl').read_text().splitlines():
                sample=json.loads(line)
                for group in sample['groups']:
                    allowed=cpu_set(group['cpus'] or '')
                    assert allowed and allowed<=set(range(2,8)), group
                    for mask in group['thread_affinities']:
                        assert cpu_set(mask)<=set(range(2,8)), group
                report['cpu_snapshots']+=1
    coverage=collections.Counter()
    observed_patterns={}
    expected_masks={
        'C1': {'2-7': 7},  # Gunicorn master plus six inference workers.
        'C2': {'2-7': 4},  # Gunicorn master plus three inference workers.
        'C3': {'2-7': 6}, 'C4': {'2-7': 3},
        'C5': {'2-3': 2, '4-5': 2, '6-7': 2},
        'C6': {'2-3': 1, '4-5': 1, '6-7': 1},
    }
    expected_containers={'C1':1,'C2':1,'C3':6,'C4':3,'C5':6,'C6':3}
    affinities=args.root/'worker-affinity.jsonl'
    if affinities.exists():
        for line in affinities.read_text().splitlines():
            sample=json.loads(line)
            assert not sample['violations'], sample
            cell=sample['case']['cell']
            actual=collections.Counter(mask for worker in sample['workers']
                                       for process in worker['processes']
                                       for mask in process['affinities'])
            assert len(sample['workers'])==expected_containers[cell], sample
            assert actual==expected_masks[cell], (sample['case'],actual)
            observed_patterns[cell]=dict(actual)
            coverage[cell]+=1
            report['affinity_samples']+=1
    report['affinity_coverage']=dict(coverage)
    report['affinity_patterns']=observed_patterns
    report['runtime_errors']=dict(errors)
    if not args.partial:
        assert report['runs']==180 and report['requests']==18900
        assert set(coverage)=={f'C{i}' for i in range(1,7)}, 'missing actual-mask evidence for a configuration'
        progress=json.loads((args.root/'progress.json').read_text())
        assert progress['status']=='completed' and progress['completed']==180
    dest=args.evidence/('partial-data-audit.json' if args.partial else 'final-data-audit.json')
    dest.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__': main()
