import json,sys,hashlib,time,os,pathlib
sys.path.insert(0,'/app')
from threefig_worker import execute
payload=pathlib.Path('/data/dog.jpg').read_bytes()
warm=[execute(payload,'warm-'+str(i)) for i in range(3)]
rows=[execute(payload,'cal-'+str(i)) for i in range(12)]
print(json.dumps(dict(schema='threefig-calibration-v1',warm=warm,rows=rows,affinity=sorted(os.sched_getaffinity(0)),input_sha256=hashlib.sha256(payload).hexdigest())))
