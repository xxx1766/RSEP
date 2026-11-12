"""Frozen Fig. 7 protocol for a two-vCPU driver and six application vCPUs.

This module only describes a new campaign; it never launches an experiment.
"""
import hashlib
import json
import os
from pathlib import Path
import random

IMAGE = os.environ.get('RSEP_NATIVE_IMAGE', 'docker.io/tide/fig5-piei-20261010:amd64')
DIGEST = os.environ.get('RSEP_NATIVE_IMAGE_DIGEST', 'sha256:defacfb3b2342c9a72fbdb6504844eccadb61a234647d33c34fc02b0019daa0a')
INPUT = '6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620'
MODEL = 'cd28f55fbbc1829f99d9ac9b38a16d259a22889739c8728ea877610201feff7b'
APP_CPUS = list(range(2, 8))
DRIVER_CPUS = list(range(2))
APP_MASK = '2-7'
DRIVER_MASK = '0-1'
MAPPING = [2, 4, 3, 5, 6, 7]
PAIRS = [[i, i+1] for i in range(2, 8, 2)]
CELLS = ('C1','C2','C3','C4','C5','C6','C7')
RATES = (1,2,4,8,16,32)
DEADLINE_SEC = 180
INFERENCES = 10
FORMAL_SEED = 2026110238
SMOKE_SEED = 2026110239
TIDE_BINARY = 'resptestv2-cpu-budget'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def cell_flags(cell):
    if cell not in CELLS:
        raise ValueError('unknown cell: ' + str(cell))
    return dict(cc=cell in ('C2', 'C4', 'C6','C7'), pi=cell in ('C5', 'C6'), ei=cell not in ('C1', 'C2'))


def workers(case):
    return 6 if case['cell']=='C7' else (3 if cell_flags(case['cell'])['cc'] else 6)


def demand(case):return 1 if case['cell']=='C7' else 2


def threads(case):return demand(case)


def manifest_fields(case):
    return dict(app_cpus=6,workers=workers(case),app_mask=APP_MASK,driver_mask=DRIVER_MASK,request_cpus=demand(case),torch_threads=threads(case),opencv_threads=1)


expected_manifest_fields = manifest_fields


def validate_manifest_fields(fields, case):
    expected = manifest_fields(case)
    assert all(fields.get(k) == v for k, v in expected.items()), ('case configuration mismatch', fields, expected)


def case_path(root, case):
    cell_flags(case['cell'])
    return Path(root) / case['cell'] / f"rps{case['rps']}" / f"run{case['repeat']}"


def make_plan(phase):
    if phase not in ('smoke','pilot','formal'):raise ValueError('invalid phase')
    seed={'smoke':202611034101,'pilot':202611034102,'formal':202611034103}[phase]
    rates={'smoke':(1,),'pilot':(1,2,4),'formal':RATES}[phase]
    cells=CELLS if phase=='smoke' else (('C3','C4','C7') if phase=='pilot' else CELLS[:6])
    repetitions={'smoke':1,'pilot':3,'formal':5}[phase]
    rng=random.Random(seed);cases=[]
    for repeat in range(1,repetitions+1):
        block=[dict(cell=cell,rps=rate,repeat=repeat,duration_sec=2 if phase=='smoke' else 20,inferences=10)
               for cell in cells for rate in rates]
        rng.shuffle(block);cases+=block
    return dict(schema=2,phase=phase,seed=seed,cases=cases,
        driver_cpus=DRIVER_CPUS,app_cpus=APP_CPUS,driver_mask=DRIVER_MASK,app_mask=APP_MASK,
        mapping=MAPPING,pairs=PAIRS,device_cpus=16,cpu_per_request='C7:1; C1-C6:2',
        torch_threads='C7:1; C1-C6:2',opencv_threads=1,torch_interop=8,
        deadline_sec=180,image=IMAGE,image_digest=DIGEST,input_sha256=INPUT,model_sha256=MODEL,
        inferences=10,repetitions=repetitions,expected_runs=len(cases),
        expected_measured=sum(c['rps']*c['duration_sec'] for c in cases),
        expected_warmups=sum(workers(c)*4 for c in cases),
        observation='20s open-loop arrivals,180s client deadline from submission,owned teardown after all client outcomes',
        estimands='valid-return mean/P95/SD; valid returns / full observed window including bounded drain; all-offer success yield and timeout/partial-work census')
