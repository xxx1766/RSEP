#!/usr/bin/env python3
"""Matched real CPU kernels with alternating100/500ms deadline classes.

Tide uses existing function-specific admission; HTTP baselines retain serial
FCFS with no predictive deadline admission. No priority queue is introduced.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
from run_baseline_matrix import Worker


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tide', type=Path, required=True)
    p.add_argument('--http', type=Path, required=True)
    p.add_argument('--worker', type=Path, required=True)
    p.add_argument('--calibration', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--rps', default='20,40,80')
    p.add_argument('--duration', type=int, default=6)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--systems', default='tide,bare,kubernetes')
    p.add_argument('--ray-python', type=Path)
    p.add_argument('--ray-script', type=Path, default=Path(__file__).with_name('ray_serve_baseline.py'))
    a = p.parse_args()
    rates = [int(v) for v in a.rps.split(',')]
    systems = a.systems.split(',')
    if min(rates+[a.duration, a.repeats]) <= 0 or not set(systems) <= {'tide','bare','kubernetes','ray'}:
        p.error('invalid matrix configuration')
    if 'ray' in systems and (a.ray_python is None or not a.ray_python.is_file() or not a.ray_script.is_file()):
        p.error('Ray requires --ray-python and --ray-script')
    a.out.mkdir(parents=True, exist_ok=False)
    (a.out/'bin').mkdir()
    binaries = {}
    for name in ('tide', 'http', 'worker'):
        binaries[name] = a.out.resolve() / 'bin' / name
        shutil.copy2(getattr(a, name), binaries[name])
    ray_script = None
    if 'ray' in systems:
        ray_script = a.out.resolve()/'bin'/'ray_serve_baseline.py'
        shutil.copy2(a.ray_script, ray_script)
    calibration = json.loads((a.calibration/'summary.json').read_text())['cpu']
    config = a.out.resolve()/'cpu_config.json'
    shutil.copy2(a.calibration/'cpu_config.json', config)
    jobs = [dict(system=s, workload='mixed-qos', rps=r, repeat=n, requests=r*a.duration)
            for s in systems for r in rates for n in range(1,a.repeats+1)]
    random.Random(20260930).shuffle(jobs)
    manifest = dict(status='running', jobs=jobs, cpus=2, duration_s=a.duration,
                    repeats=a.repeats, calibration=calibration, classes_ms=[100,500],
                    binary_sha256={k:sha(v) for k,v in binaries.items()},
                    ray_script_sha256=sha(ray_script) if ray_script else None,
                    config_sha256=sha(config), started_unix_ns=time.time_ns(),
                    scope='real same CPU kernel, alternating deadlines by offered ID; no priorities',
                    kube_scope='single-host hostNetwork Pod, no cluster placement or KubeEdge claims')
    path = a.out/'matrix.json'
    def save(): path.write_text(json.dumps(manifest, indent=2)+'\n')
    save()
    try:
        for index, j in enumerate(jobs):
            root = a.out/j['system']/f"rps{j['rps']}"/f"run{j['repeat']}"
            root.mkdir(parents=True)
            measurement = root/'measurement'
            j.update(path=str(root.relative_to(a.out)), status='running'); save()
            instance = None
            try:
                extra = []
                if j['system'] == 'tide':
                    binary = binaries['tide']
                    extra = ['--config', str(config), '--cpus', '2']
                    pidargs = []
                else:
                    binary = binaries['http']
                    template = Path(__file__).with_name('kubernetes_worker.json')
                    instance = Worker(j['system'], binaries['worker'], template,
                                      f'revision-{a.out.name}-{index:03d}', root,
                                      a.ray_python, ray_script)
                    pidargs = ['--workload-pid', str(instance.start())]
                    extra = ['--system', j['system']+' HTTP FCFS', '--qos-ms', '500']
                cmd = [sys.executable, str(Path(__file__).with_name('run_checked.py')),
                       '--out', str(measurement), '--timeout', str(a.duration+30), *pidargs,
                       '--', str(binary), '--requests', str(j['requests']), '--rps', str(j['rps']),
                       '--qos-classes-ms', '100,500', '--work', json.dumps(calibration['workload']),
                       '--out', str(measurement/'run'), *extra]
                j['command'] = cmd; save()
                result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                j['runner_output'] = result.stdout
                if result.returncode: raise RuntimeError(f'failed measurement {root}')
                with (measurement/'run/raw.csv').open() as f: rows = list(csv.DictReader(f))
                if len(rows) != j['requests'] or {int(r['req_id']) for r in rows} != set(range(j['requests'])):
                    raise RuntimeError('missing offered requests')
                for row in rows:
                    deadline = [100,500][int(row['req_id'])%2]
                    if float(row['qos_ms']) != deadline or row['fn_name'] != f'cpu-qos{deadline}':
                        raise RuntimeError('deadline classes not matched to offered IDs')
                j.update(status='completed', successes=sum(int(r['status']) == 200 for r in rows))
                if instance: j['setup_sha256'] = sha(root/'setup.json')
            finally:
                if instance: instance.stop()
            save()
            print(f"{index+1}/{len(jobs)} {j['system']} rps={j['rps']} success={j['successes']}/{j['requests']}", flush=True)
    except BaseException as e:
        if j.get('status') == 'running': j.update(status='failed', error=str(e))
        manifest.update(status='failed', error=str(e), finished_unix_ns=time.time_ns()); save(); raise
    manifest.update(status='completed', finished_unix_ns=time.time_ns()); save()


if __name__ == '__main__': main()
