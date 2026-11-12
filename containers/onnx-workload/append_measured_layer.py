#!/usr/bin/env python3
"""Append audited application scripts to the frozen OCI image, preserving dependencies."""
import argparse, hashlib, io, json, tarfile
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--base',required=True);p.add_argument('--workload',required=True);p.add_argument('--out',required=True)
a=p.parse_args()
pack=lambda v:json.dumps(v,sort_keys=True,separators=(',',':')).encode()
with tarfile.open(a.base) as src:
    files={m.name:src.extractfile(m).read() for m in src.getmembers() if m.isfile()}
index=json.loads(files['index.json'])
d=index['manifests'][0]
def get(d):return json.loads(files['blobs/'+d['digest'].replace(':','/')])
m=get(d)
while 'manifests' in m:
    ds=[d for d in m['manifests'] if d.get('platform',{}).get('architecture')=='amd64']
    assert len(ds)==1,ds
    m=get(ds[0])
assert d['digest']=='sha256:bd16f9d2f955f90997886f627ce066e59587be93923fed0b197e0a48d8345363',d
cfg=get(m['config']);old_layers=list(m['layers'])
def desc(data,media):
    digest=hashlib.sha256(data).hexdigest();files['blobs/sha256/'+digest]=data
    return dict(mediaType=media,digest='sha256:'+digest,size=len(data))
layer=io.BytesIO();hashes={}
with tarfile.open(fileobj=layer,mode='w') as tar:
    for name in ['http_worker.py','paix_worker.py','request_kernel.py','gunicorn_audit.py']:
        data=(Path(a.workload)/name).read_bytes();hashes[name]=hashlib.sha256(data).hexdigest()
        info=tarfile.TarInfo('app/'+name);info.size=len(data);info.mode=0o644;tar.addfile(info,io.BytesIO(data))
ld=desc(layer.getvalue(),'application/vnd.oci.image.layer.v1.tar')
cfg['rootfs']['diff_ids'].append(ld['digest'])
cfg.setdefault('history',[]).append({'created_by':'Fig5 shared audited inference loop 20261010'})
m['config']=desc(pack(cfg),'application/vnd.oci.image.config.v1+json');m['layers'].append(ld)
m['mediaType']='application/vnd.oci.image.manifest.v1+json'
md=desc(pack(m),m['mediaType']);name='docker.io/tide/fig5-piei-20261010:amd64'
md['annotations']={'org.opencontainers.image.ref.name':name,'io.containerd.image.name':name}
md['platform']={'architecture':'amd64','os':'linux'}
files['index.json']=pack({'schemaVersion':2,'manifests':[md]})
files.pop('manifest.json',None)
with tarfile.open(a.out,'w') as tar:
    for name,data in sorted(files.items()):
        info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644;tar.addfile(info,io.BytesIO(data))
print(json.dumps(dict(image=md,unchanged_base_layers=old_layers,script_hashes=hashes),indent=2))
