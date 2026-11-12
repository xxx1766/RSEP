"""Freeze the observed identities, then start independent 30-minute diagnostics."""
from concurrent.futures import ThreadPoolExecutor
import json
import shlex
import time
import issue29_common as p

ROOT=p.GUEST_ROOT
COMPUTE=['i23-l01','i23-l02','i23-l03','i23-r01','i23-r02','i23-r03']

def launch(host,name,args,vm=None):
 root=ROOT if vm else p.REMOTE
 code="import subprocess,json,time;from pathlib import Path;f=Path("+repr(root+'/'+name+'.log')+").open('x');q=subprocess.Popen("+repr(args)+",stdout=f,stderr=f,start_new_session=True);print(json.dumps(dict(pid=q.pid,start_ticks=Path('/proc/'+str(q.pid)+'/stat').read_text().rsplit(')',1)[1].split()[19],argv="+repr(args)+",unix_ns=time.time_ns())))"
 row=json.loads(p.guest(vm,'python3 -c '+shlex.quote(code),timeout=20) if vm else p.py(host,code,timeout=20))
 p.save(p.OUT/'jobs'/((vm or host)+'-'+name+'.json'),row);return row

def prepare(vm):
 p.guest(vm,'mkdir '+ROOT,timeout=20)
 for name in ['issue24_network_probe.py','issue29_observe.py']:
  p.guest_copy(vm,str(p.SOURCE_DIR/name),ROOT+'/'+name)
 if vm in COMPUTE:
  manifest=json.loads(p.guest(vm,"python3 -c 'import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:19383/manifest\",timeout=2).read().decode())'",timeout=15))
  want=json.loads((p.OLD/'validation/application/i23-l01/manifest.json').read_text())
  for key in ['model_sha256','source_sha256','affinity','intra_threads','compute_concurrency','tcp_nodelay']:assert manifest[key]==want[key],key
  for a,b in zip(manifest['inputs'],want['inputs']):
   for key in ['input_sha256','output_sha256','checksum','bytes','shape']:assert a[key]==b[key],key
  p.save(p.OUT/'guests'/(vm+'-manifest.json'),manifest)
  p.guest_copy(vm,p.OUT/'guests'/(vm+'-manifest.json'),ROOT+'/manifest.json')
  code="""import subprocess,json,os
from pathlib import Path
base=['crictl','--runtime-endpoint','unix:///run/containerd/containerd.sock']
cs=json.loads(subprocess.check_output(base+['ps','-o','json']))['containers'];cs=[x for x in cs if x['metadata']['name']=='infer'];assert len(cs)==1
c=cs[0];info=json.loads(subprocess.check_output(base+['inspect',c['id']]))
pid=info['info']['pid'];cg=Path('/proc/'+str(pid)+'/cgroup').read_text().split(':',2)[2].strip();group=Path('/sys/fs/cgroup')/cg.lstrip('/')
assert (group/'cpu.max').read_text().split()==['100000','100000'];assert int((group/'memory.max').read_text())==1073741824
assert c['imageRef']=='sha256:08f7264a1a218832429b41b86ce3a5a11acf50fea42ab1977e4460fa09b7a4c0'
assert os.cpu_count()==1
print(json.dumps(dict(container=c,app_pid=pid,cgroup=cg,cpu_max=(group/'cpu.max').read_text(),memory_max=(group/'memory.max').read_text(),cpu_count=os.cpu_count(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text(),services=subprocess.check_output(['systemctl','show','systemd-networkd','systemd-journald','systemd-resolved','systemd-udevd','systemd-logind','-p','Id,MainPID,NRestarts,ActiveEnterTimestampMonotonic'],text=True))))"""
  p.save(p.OUT/'guests'/(vm+'-runtime.json'),json.loads(p.guest(vm,'python3 -c '+shlex.quote(code),timeout=20)))
  launch(None,'echo',['python3',ROOT+'/issue24_network_probe.py','serve','--out',ROOT+'/echo.jsonl'],vm)
  code="from issue24_network_probe import probe;import json;print(json.dumps([probe('127.0.0.1:19383','preflight-app'),probe('127.0.0.1:30924','preflight-echo',4096)]))"
  health=json.loads(p.guest(vm,'cd '+ROOT+' && python3 -c '+shlex.quote(code),timeout=15));p.save(p.OUT/'guests'/(vm+'-initial-health.json'),health)
  assert all(x['ok'] for x in health)
 print('PREPARED',vm,flush=True)

def main():
 for folder in ['jobs','guests']: (p.OUT/folder).mkdir(exist_ok=False)
 p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf wait --for=condition=Ready '+shlex.join(['node/'+vm for vm in COMPUTE])+' --timeout=120s',timeout=130)
 pods=json.loads(p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf -n issue23 get pods -o json'))
 p.save(p.OUT/'preflight/private-pods-before.json',pods)
 existing={x['metadata']['name'] for x in pods['items']};missing=[vm for vm in COMPUTE if vm not in existing]
 if missing:
  from issue23_deploy import manifests
  body=manifests(missing,False);body['items']=[x for x in body['items'] if x['kind']=='Pod']
  file=p.OUT/'preflight/restored-pods.json';p.save(file,body)
  p.guest_copy('i23-c01',file,ROOT+'/restored-pods.json')
  p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf create -f '+ROOT+'/restored-pods.json')
 p.save(p.OUT/'preflight/pod-restore-record.json',dict(missing_pods_restored=missing,unchanged_original_manifest=True))
 p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf -n issue23 wait --for=condition=Ready '+shlex.join(['pod/'+vm for vm in COMPUTE])+' --timeout=120s',timeout=130)
 for resource in ['nodes','pods -A']:
  p.save(p.OUT/'preflight'/('private-'+resource.replace(' ','_')+'.json'),json.loads(p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf get '+resource+' -o json')))
 with ThreadPoolExecutor(6) as pool:list(pool.map(prepare,['i23-c01','i23-d01',*COMPUTE]))
 for host in p.HOSTS:
  pids=json.loads(p.py(host,"import json;from pathlib import Path;print(json.dumps([int(f.read_text()) for f in Path("+repr(p.REMOTE+'/vms')+").glob('*/pid')]))"))
  assert len(pids)==(2 if host=='NJ-k3' else 3)
  for name in ['issue29_resources.py','issue24_network_probe.py']:p.copy(host,str(p.SOURCE_DIR/name),p.REMOTE+'/'+name)
  launch(host,'resources',['python3',p.REMOTE+'/issue29_resources.py','--pids',*map(str,pids),'--owned',p.REMOTE,'--seconds','14400','--out',p.REMOTE+'/resources.jsonl'])
  launch(host,'underlay-server',['python3',p.REMOTE+'/issue24_network_probe.py','serve','--port','30925','--out',p.REMOTE+'/underlay-server.jsonl'])
 for host in p.HOSTS:
  endpoints=[ip+':30925' for other,ip in p.HOSTS.items() if other!=host]
  launch(host,'underlay-probes',['python3',p.REMOTE+'/issue24_network_probe.py','probe','--endpoints',*endpoints,'--seconds','14400','--echo','--label','issue29-underlay','--out',p.REMOTE+'/underlay.jsonl'])
 for vm in ['i23-c01','i23-d01',*COMPUTE]:
  launch(None,'heartbeat',['python3',ROOT+'/issue29_observe.py','heartbeat','--vm',vm,'--seconds','14400','--out',ROOT+'/heartbeat.jsonl'],vm)
  print('INFRA_HEARTBEAT_STARTED',vm,flush=True)
 for host in p.HOSTS:
  p.save(p.OUT/'preflight'/(host+'-unshaped-qdiscs.json'),json.loads(p.ssh(host,'tc -j qdisc show')))
  assert 'netem' not in json.dumps(json.loads(p.ssh(host,'tc -j qdisc show')))
 p.save(p.OUT/'started.json',dict(unix_ns=time.time_ns(),seconds_per_guest_process=14400,compute=COMPUTE,support=['i23-c01','i23-d01'],other10off=True,network='unshaped',no_tide_processes=True))
 print('CAMPAIGN_STARTED',time.time_ns(),flush=True)

if __name__=='__main__':main()
