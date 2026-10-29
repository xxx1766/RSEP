#!/usr/bin/env python3
"""Verify OCI blob chain and worker identity against all observed Kubernetes Pods."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile


def sha(data): return hashlib.sha256(data).hexdigest()


def verify(archive, matrix):
    with tarfile.open(archive) as f:
        def blob(desc):
            data = f.extractfile('blobs/'+desc['digest'].replace(':','/')).read()
            if desc['digest'] != 'sha256:'+sha(data) or len(data) != desc['size']:
                raise ValueError('OCI descriptor mismatch')
            return data
        index = json.load(f.extractfile('index.json'))
        if len(index['manifests']) != 1: raise ValueError('expected one image')
        manifest = json.loads(blob(index['manifests'][0]))
        config = json.loads(blob(manifest['config']))
        if len(manifest['layers']) != 1: raise ValueError('expected fixed single-layer worker image')
        layer = blob(manifest['layers'][0])
        if manifest['layers'][0]['mediaType'].endswith('+gzip'): layer = gzip.decompress(layer)
        if config['rootfs']['diff_ids'] != ['sha256:'+sha(layer)]: raise ValueError('layer diffID mismatch')
        with tarfile.open(fileobj=io.BytesIO(layer)) as inner:
            worker_sha = sha(inner.extractfile('worker').read())
    m = json.loads((matrix/'matrix.json').read_text())
    if m['status'] != 'completed': raise ValueError('matrix incomplete')
    if isinstance(m['binary_sha256'], dict):
        bare = matrix/'bin/worker'; expected = m['binary_sha256']['worker']
    else:
        bare = matrix/'revision-worker'; expected = m['worker_sha256']
    if worker_sha != expected or sha(bare.read_bytes()) != expected:
        raise ValueError('image worker differs from bare frozen worker')
    pods = []
    for job in m['jobs']:
        if job.get('system') != 'kubernetes': continue
        p = matrix/job['path']/'setup.json'
        if sha(p.read_bytes()) != job['setup_sha256']: raise ValueError('setup changed')
        observed = json.loads(p.read_text())['pod']
        image_id = observed['status']['containerStatuses'][0]['imageID']
        if image_id != manifest['config']['digest']: raise ValueError('Pod imageID differs from OCI config')
        pods.append(dict(name=observed['metadata']['name'], uid=observed['metadata']['uid'],
                         image_id=image_id, setup_sha256=sha(p.read_bytes())))
    if not pods: raise ValueError('no Kubernetes observations')
    return dict(status='passed', archive_sha256=sha(archive.read_bytes()),
                matrix_sha256=sha((matrix/'matrix.json').read_bytes()), worker_sha256=worker_sha,
                manifest_digest=index['manifests'][0]['digest'], config_digest=manifest['config']['digest'],
                config=config, checked_pods=len(pods), pods=pods,
                scope='Observed Pod config digest matches verified OCI archive; layer /worker matches frozen Bare worker.')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--matrix',type=Path,required=True)
    a=p.parse_args()
    result=verify(a.archive,a.matrix)
    result['checker_sha256']=sha(Path(__file__).read_bytes())
    (a.matrix/'image_provenance.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('status','worker_sha256','checked_pods')}))
