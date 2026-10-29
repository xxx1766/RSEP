#!/usr/bin/env python3
"""Capture local source, binary and host identity before the ablation matrix."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess

root=Path(__file__).resolve().parents[2]
out=root/'evidence'
out.mkdir(exist_ok=True)
files={}
for base in ['pkg','cmd','scripts','workload','proto','bin']:
    for p in (root/base).rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and (p.suffix in ['.go','.py','.sh','.mod','.sum','.jpg'] or p.name in ['Dockerfile','httpbase','resptestv2']):
            files[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
for name in ['go.mod','go.sum']:
    files[name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
(out/'source-and-binary-sha256.json').write_text(json.dumps(files,indent=2)+'\n')
commands=[['uname','-a'],['lscpu','-J'],['ctr','version'],['ctr','-n','tide','images','ls'],['ctr','-n','default','images','ls'],['/usr/local/go/bin/go','version']]
host={'platform':platform.platform(),'commands':[]}
for cmd in commands:
    r=subprocess.run(cmd,text=True,capture_output=True)
    host['commands'].append({'command':cmd,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
(out/'host-and-images.json').write_text(json.dumps(host,indent=2)+'\n')
print('Captured',len(files),'source/binary hashes')
