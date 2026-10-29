#!/usr/bin/env python3
"""Independently sample the actual container/worker thread CPU masks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def read(path):
    try: return Path(path).read_text().strip()
    except (FileNotFoundError,ProcessLookupError): return ''


def masks(pid):
    pids=[str(pid)]
    result=[]
    for p in pids:
        if not Path('/proc',p).exists(): continue
        pids.extend(x for x in read(f'/proc/{p}/task/{p}/children').split() if x not in pids)
        affinities=sorted(set(line.split(':',1)[1].strip()
                      for status in Path(f'/proc/{p}/task').glob('*/status')
                      for line in read(status).splitlines()
                      if line.startswith('Cpus_allowed_list:')))
        result.append({'pid':int(p),'comm':read(f'/proc/{p}/comm'),
                       'affinities':affinities,'cgroup':read(f'/proc/{p}/cgroup')})
    return result


def cpu_set(spec):
    values=set()
    for part in spec.split(','):
        if '-' in part:
            lo,hi=map(int,part.split('-')); values.update(range(lo,hi+1))
        elif part: values.add(int(part))
    return values


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    seen=set()
    with (args.root/'worker-affinity.jsonl').open('a') as out:
        while True:
            progress=json.loads((args.root/'progress.json').read_text())
            if progress['status']!='running': break
            os.kill(progress['pid'],0)
            case=progress.get('current')
            key=tuple(case.values()) if case else None
            if not case or key in seen:
                time.sleep(2); continue
            path=args.root/case['cell']/f"rps{case['rps']}"/f"run{case['repeat']}"
            if not (path/'raw.csv').exists():
                time.sleep(1); continue
            manifest=json.loads((path/'manifest.json').read_text())
            http=case['cell'] in ['C1','C2']
            if http:
                cmd=manifest['command']
                ids={arg.split('=',1)[1] for arg in cmd if arg.startswith('-backend-id=')}
                if not ids and '-backend-id' in cmd:
                    ids={cmd[cmd.index('-backend-id')+1]}
                assert len(ids)==1, cmd
            else:
                logs=[json.loads(s) for s in read(path/'runtime.jsonl').splitlines() if s.endswith('}')]
                ids={r['ExecutorId'] for r in logs if r.get('msg')=='StackPool: received one INIT event'}
            cmd=['ctr','-n','default' if http else 'tide','tasks','ls']
            listing=subprocess.run(cmd,text=True,capture_output=True,check=True).stdout.splitlines()[1:]
            workers=[]
            for line in listing:
                fields=line.split()
                if len(fields)>=2 and fields[0] in ids:
                    workers.append({'container':fields[0],'processes':masks(int(fields[1]))})
            if not workers:
                time.sleep(1); continue
            violations=[]
            for worker in workers:
                for process in worker['processes']:
                    for affinity in process['affinities']:
                        cpus=cpu_set(affinity)
                        if not cpus or not cpus <= set(range(2,8)):
                            violations.append({'pid':process['pid'],'mask':affinity,'reason':'outside six-CPU slice'})
                        if case['cell'] in ['C5','C6'] and len(cpus)!=2:
                            violations.append({'pid':process['pid'],'mask':affinity,'reason':'PI-on mask is not two CPUs'})
            out.write(json.dumps({'case':case,'time':time.time(),'workers':workers,'violations':violations})+'\n')
            out.flush()
            if violations: raise RuntimeError(violations)
            seen.add(key)
            time.sleep(2)


if __name__=='__main__': main()
