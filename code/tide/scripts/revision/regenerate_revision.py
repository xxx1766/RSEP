#!/usr/bin/env python3
"""Regenerate the revision bundle from saved evidence; never start experiments."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
RESULTS=Path('results-revision-20260928')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def bundle():
    figures={}
    names={'A':'ctrl_plane','B':'degradation','C':'sensitivity','D':'hetero','E':'utilization','F':'optgap'}
    scopes={'A':'measured Tide controller/lifecycle and actual PAIX/ranking microbenchmarks',
            'B':'measured HTTP scheduling/transport adapter; not native Tide distributed recovery',
            'C':'restricted ideal-estimate weight simulation plus measured native Tide TTL',
            'D':'measured CPU/memory-access/I/O/mixed-QoS kernels on actual execution paths',
            'E':'measured CPU use normalized to declared two-logical-CPU budgets',
            'F':'restricted offline model; all120optima independently verified by MILP'}
    for key,name in names.items():
        paths=[RESULTS/'figures'/f'fig{key}_{name}.{ext}' for ext in ('pdf','png')]
        meta=RESULTS/'figures'/f'fig{key}_manifest.json'
        caption=RESULTS/'figures'/f'fig{key}_caption.txt'
        for p in paths+[meta,caption]:
            if not (ROOT/p).is_file():raise ValueError(f'missing artifact {p}')
        figures[key]=dict(scope=scopes[key],figure_sha256={str(p.relative_to(RESULTS)):sha(ROOT/p) for p in paths},
                          provenance=str(meta.relative_to(RESULTS)),provenance_sha256=sha(ROOT/meta),
                          caption=str(caption.relative_to(RESULTS)),caption_sha256=sha(ROOT/caption))
    value=dict(status='complete',figures=figures,regenerate='python scripts/revision/regenerate_revision.py',
               source_root='code/Tide',source_snapshot='validation/final-source/source_sha256.json',
               no_native_distributed_recovery_claim=True,performance_superiority_not_assumed=True,
               authoritative_data=['workload-matrix-01','baseline-matrix-01','mixed-matrix-01','control-matrix-01','ttl-matrix-01','fault-01','weights-02','model-01'],
               earlier_trials='validation/ retains failed and smoke runs; weights-01 preserves superseded endpoint convention')
    (ROOT/RESULTS/'figure_data_manifest.json').write_text(json.dumps(value,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest-only',action='store_true');a=p.parse_args()
    def run(*args):subprocess.run([sys.executable,*args],cwd=ROOT,check=True)
    if not a.manifest_only:
        run('scripts/revision/analyze_workloads.py','--tide',str(RESULTS/'workload-matrix-01'),'--baseline',str(RESULTS/'baseline-matrix-01'),'--mixed',str(RESULTS/'mixed-matrix-01'),'--out',str(RESULTS/'workload-analysis-final'))
        for matrix in ('baseline-matrix-01','mixed-matrix-01'):
            run('scripts/revision/verify_worker_image.py','--archive',str(RESULTS/'validation/worker.oci.tar'),'--matrix',str(RESULTS/matrix))
        run('scripts/revision/plot_revision_panels.py','--results',str(RESULTS),'--out',str(RESULTS/'figures'))
        run('scripts/revision/publish_workload_figures.py','--analysis',str(RESULTS/'workload-analysis-final'),'--out',str(RESULTS/'figures'))
        run('scripts/revision/plot_optimum.py','--model',str(RESULTS/'model-01'),'--out',str(RESULTS/'figures'))
        run('cmd/revision-fault/plot_results.py','--fault',str(RESULTS/'fault-01'),'--out',str(RESULTS/'figures'),'--source-root','.')
        run('scripts/revision/build_supplement.py','--root',str(RESULTS))
    bundle()
    run('scripts/revision/build_revision_notebook.py','--results',str(RESULTS))
    print('Saved figure_data_manifest.json; all6figures linked to evidence')

if __name__=='__main__':main()
