#!/usr/bin/env python3
"""Build a reproducible OCI archive around the locally built static worker.

No registry/base image is required. Runtime import is a separate explicit step.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

p = argparse.ArgumentParser()
p.add_argument('--binary', required=True)
p.add_argument('--out', required=True)
p.add_argument('--image', default='docker.io/tide/revision-worker:amd64')
a = p.parse_args()
binary = Path(a.binary).read_bytes()
layer = io.BytesIO()
with tarfile.open(fileobj=layer, mode='w') as t:
    for name, mode in [('tmp', 0o1777)]:
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        info.mode = mode
        t.addfile(info)
    info = tarfile.TarInfo('worker')
    info.size, info.mode = len(binary), 0o755
    t.addfile(info, io.BytesIO(binary))
blob = layer.getvalue()
blobs = {}
def desc(data, kind):
    digest = hashlib.sha256(data).hexdigest()
    blobs[digest] = data
    return {'mediaType': kind, 'digest': 'sha256:' + digest, 'size': len(data)}
def pack(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':')).encode()
ld = desc(blob, 'application/vnd.oci.image.layer.v1.tar')
cfg = {'architecture': 'amd64', 'os': 'linux',
       'config': {'Entrypoint': ['/worker'], 'Cmd': ['--mode=paix'],
                  'Env': ['PATH=/', 'GOMAXPROCS=1']},
       'rootfs': {'type': 'layers', 'diff_ids': [ld['digest']]}}
cd = desc(pack(cfg), 'application/vnd.oci.image.config.v1+json')
manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': cd, 'layers': [ld]}
md = desc(pack(manifest), 'application/vnd.oci.image.manifest.v1+json')
md['annotations'] = {'org.opencontainers.image.ref.name': a.image,
                     'io.containerd.image.name': a.image}
md['platform'] = {'architecture': 'amd64', 'os': 'linux'}
index = {'schemaVersion': 2, 'manifests': [md]}
with tarfile.open(a.out, mode='w') as t:
    files = {'oci-layout': pack({'imageLayoutVersion': '1.0.0'}),
             'index.json': pack(index),
             **{'blobs/sha256/' + k: v for k, v in blobs.items()}}
    for name, data in files.items():
        info = tarfile.TarInfo(name)
        info.size, info.mode = len(data), 0o644
        t.addfile(info, io.BytesIO(data))
print(json.dumps({'image': a.image, 'manifest': md['digest'],
                  'binary_sha256': hashlib.sha256(binary).hexdigest(),
                  'archive': a.out}))
