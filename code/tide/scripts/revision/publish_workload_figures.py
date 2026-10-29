#!/usr/bin/env python3
"""Publish already-audited D/E figures with their data and provenance."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--analysis',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    audit=json.loads((a.analysis/'audit.json').read_text())
    if audit['status']!='passed' or audit['total_runs']!=135:raise ValueError('expected complete135run audit')
    for n,h in audit['outputs_sha256'].items():
        if sha(a.analysis/n)!=h:raise ValueError(f'changed artifact {n}')
    a.out.mkdir(parents=True,exist_ok=True)
    for src,dst in [('D_workloads','figD_hetero'),('E_worker_cpu','figE_utilization'),('E_cpu_timeseries','figE_timeseries')]:
        for ext in ('pdf','png'):shutil.copy2(a.analysis/f'{src}.{ext}',a.out/f'{dst}.{ext}')
    captions={
        'figD':'Actual Tide SubmitRoutine/PAIX and Bare/Kubernetes HTTP FCFS kernels, 2 logical CPU budget, one serial worker, 3 repeated windows per cell. CPU/memory-access/synchronous I/O windows are 8 s; alternating 100/500 ms CPU deadline-class windows are 6 s. Goodput counts on-time completions within [start,end); yields retain drain and failures. Successful latency quantiles do not include timeouts. Means and 95% t CI use runs, not pooled requests. Bare I/O uses host /tmp while containers use writable overlay /tmp; these storage paths confound system comparison. Kubernetes uses one hostNetwork Pod, not KubeEdge or HPA. No universal Tide superiority is supported.',
        'figE':'Actual worker CPU time divided by window duration and a declared two-logical-CPU budget. Tide uses exclusive parent cgroup CPU counters; baselines use worker /proc CPU ticks. Counters are interpolated at bracketed window bounds from approximately100ms samples. Separate time-series figure uses0.5s bins. CPU reservation is distinct from use and is measured separately in the TTL panel. Tide cgroup memory.current includes non-RSS charges and is not compared with baseline RSS. Three-run means and95%tCI; time-series intervals are pointwise.'}
    for name,caption in captions.items():
        (a.out/f'{name}_caption.txt').write_text(caption+'\n')
        (a.out/f'{name}_manifest.json').write_text(json.dumps(dict(evidence_type='measured runtime kernels',caption=caption,
            input_sha256={str(a.analysis/n):sha(a.analysis/n) for n in ('audit.json','per_run.csv','per_cell.csv','timeseries_summary.csv','mixed_qos_per_cell.csv')},
            publisher_sha256=sha(Path(__file__)),outputs_sha256={f.name:sha(f) for f in a.out.glob(name+'*') if f.suffix in ('.pdf','.png','.txt')}),indent=2)+'\n')
    print('Published audited D/E and E time series')

if __name__=='__main__':main()
