#!/usr/bin/env python3
"""Small actual PAIX and ranking-hook microbenchmarks, run serially."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--worker',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    binary=a.out.resolve()/'revision-ipc';worker=a.out.resolve()/'revision-worker'
    shutil.copy2(a.binary,binary);shutil.copy2(a.worker,worker)
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    manifest=dict(status='running',started_unix_ns=time.time_ns(),binary_sha256=sha(binary),worker_sha256=sha(worker),jobs=[],
                  scope='Two-process actual PAIX roundtrip with minimal kernel; actual ranking hook benchmark, not full asynchronous scheduling.')
    path=a.out/'matrix.json'
    def save():path.write_text(json.dumps(manifest,indent=2)+'\n')
    save()
    try:
        for padding in (64,1024,4096):
            for repeat in (1,2,3):
                root=a.out/f'padding{padding}'/f'run{repeat}'
                cmd=[sys.executable,str(Path(__file__).with_name('run_checked.py')),'--out',str(root),'--timeout','30','--',str(binary),
                     '--worker',str(worker),'--requests','3000','--padding',str(padding),'--out',str(root/'run')]
                job=dict(padding_bytes=padding,repeat=repeat,path=str(root.relative_to(a.out)),command=cmd,status='running')
                manifest['jobs'].append(job);save()
                proc=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                job.update(exit_code=proc.returncode,runner_output=proc.stdout,status='completed' if proc.returncode==0 else 'failed');save()
                if proc.returncode:raise RuntimeError(f'IPC run failed {root}')
                print(f'IPC padding={padding} repeat={repeat} completed',flush=True)
        cmd=['/usr/local/go/bin/go','test','./pkg/stub/sched','-run','^$','-bench','^BenchmarkRevisionRank$','-benchtime=200ms','-count=5','-timeout=60s']
        proc=subprocess.run(cmd,cwd=a.source,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        (a.out/'ranking_benchmark.txt').write_text(proc.stdout)
        manifest['ranking']=dict(command=cmd,cwd=str(a.source),exit_code=proc.returncode,
                                source_sha256={n:sha(a.source/n) for n in ['pkg/stub/sched/default.go','pkg/stub/sched/default_benchmark_test.go','pkg/schedpolicy/score.go']})
        if proc.returncode:raise RuntimeError('ranking benchmark failed')
    except BaseException as e:
        manifest.update(status='failed',error=str(e),finished_unix_ns=time.time_ns());save();raise
    manifest.update(status='completed',finished_unix_ns=time.time_ns(),ranking_output_sha256=sha(a.out/'ranking_benchmark.txt'));save()


if __name__=='__main__':main()
