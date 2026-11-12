"""Owned pilot setup, immutable native fixture, real Services and clock gates."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shlex
import statistics
import time
import issue29_common as p
from issue29_start import ROOT,COMPUTE,launch

DRIVER='i23-d01'
BINARY='1f0e75ee76ca53fdf04555bbf4c3b5e23bfca1ff4ba053c63d70323a7b6fc00e'
def ip(vm):return p.GUEST_SUBNET+'.'+str((10 if vm[4]=='l' else 20)+int(vm[-2:]))
def guest_py(vm,code,timeout=30):return p.guest(vm,'python3 -c '+shlex.quote(code),timeout=timeout)

def qmp(host,vm,command,arguments=None):
 code=r'''import socket,json
s=socket.socket(socket.AF_UNIX);s.settimeout(5);s.connect(PATH);f=s.makefile('rwb',buffering=0);f.readline()
def call(cmd):
 f.write((json.dumps(cmd)+'\n').encode())
 while True:
  r=json.loads(f.readline())
  if 'return' in r or 'error' in r:return r
call(dict(execute='qmp_capabilities'));print(json.dumps(call(CMD)));f.close();s.close()'''.replace('PATH',repr(p.REMOTE+'/vms/'+vm+'/qmp.sock')).replace('CMD',repr(dict(execute=command,**({'arguments':arguments} if arguments else {}))))
 compile(code,'qmp-remote','exec')
 return json.loads(p.py(host,code,timeout=15))

def reference():
 folder=p.OUT/'clock';folder.mkdir(exist_ok=False)
 conf=(p.OLD/'validation/clock-repair/reference.conf').read_text().replace(p.VM_REMOTE,p.REMOTE)
 file=folder/'reference.conf';file.write_text(conf);p.copy('NJ-k3',file,p.REMOTE+'/reference.conf');p.ssh('NJ-k3','mkdir -p '+p.REMOTE+'/logs/chrony')
 argv=['setpriv','--bounding-set=-sys_time','--',p.VM_REMOTE+'/chrony-runtime/usr/sbin/chronyd','-x','-d','-u','root','-f',p.REMOTE+'/reference.conf']
 code="import socket,subprocess,json,time;from pathlib import Path;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('+repr(p.HOSTS['NJ-k3'])+',22423));s.close();f=Path("+repr(p.REMOTE+'/reference.log')+").open('x');q=subprocess.Popen("+repr(argv)+",stdout=f,stderr=f,start_new_session=True);time.sleep(.5);assert q.poll() is None;root=Path('/proc/'+str(q.pid));print(json.dumps(dict(pid=q.pid,start_ticks=(root/'stat').read_text().rsplit(')',1)[1].split()[19],status=(root/'status').read_text(),cmdline=(root/'cmdline').read_bytes().decode().replace('\\x00',' '),argv="+repr(argv)+")))"
 row=json.loads(p.py('NJ-k3',code));caps=next(x.split()[1] for x in row['status'].splitlines() if x.startswith('CapEff:'));assert not int(caps,16)&(1<<25) and '-x ' in row['cmdline']
 p.save(folder/'reference-job.json',row);print('READONLY_REFERENCE_READY_NO_CAP_SYS_TIME',flush=True)

def provision():
 scripts=['issue12_nj_job.py','issue22_service_client.py','issue12_k8s.py','issue20_domain_client.py','issue23_lf_client.py','issue29_capacity_client.py','issue29_capacity.py','issue22_clock_probe.py']
 for vm in [DRIVER,*COMPUTE]:
  p.guest_copy(vm,str(p.SOURCE_DIR/'issue12_nj_job.py'),ROOT+'/issue12_nj_job.py')
  code="from pathlib import Path;import shutil,hashlib,json,subprocess;src=Path(p.VM_REMOTE+'/tide-vm-test');dst=Path("+repr(ROOT+'/tide-vm-test')+");assert hashlib.sha256(src.read_bytes()).hexdigest()=="+repr(BINARY)+";shutil.copy2(src,dst) if not dst.exists() else None;assert hashlib.sha256(dst.read_bytes()).hexdigest()=="+repr(BINARY)+";print(json.dumps(dict(binary_sha256=hashlib.sha256(dst.read_bytes()).hexdigest(),clock=subprocess.check_output(['chronyc','tracking'],text=True),clocksource=Path('/sys/devices/system/clocksource/clocksource0/current_clocksource').read_text(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text())))"
  row=json.loads(guest_py(vm,code));assert row['binary_sha256']==BINARY and row['clocksource'].strip()=='kvm-clock';p.save(p.OUT/'preflight'/(vm+'-native-identity.json'),row)
 for script in scripts:p.guest_copy(DRIVER,str(p.SOURCE_DIR/script),ROOT+'/'+script)
 code="import shutil,hashlib;from pathlib import Path;src=Path(p.VM_REMOTE+'/inputs');dst=Path("+repr(ROOT+'/inputs')+");shutil.copytree(src,dst) if not dst.exists() else None;assert all(hashlib.sha256((src/('image-'+str(i)+'.jpg')).read_bytes()).digest()==hashlib.sha256((dst/('image-'+str(i)+'.jpg')).read_bytes()).digest() for i in range(4))"
 guest_py(DRIVER,code)
 p.save(p.OUT/'source/native-binary.json',dict(sha256=BINARY,source=str(p.OLD/'source/native-binary.json'),production_source_unchanged=True,overlays=['pkg/dev/resource.go','pkg/processor/physics_resource.go']))
 for vm in COMPUTE:
  p.guest_copy(vm,str(p.SOURCE_DIR/'issue22_clock_probe.py'),ROOT+'/issue22_clock_probe.py')
  jobfile=p.OUT/'jobs'/(vm+'-clock-server.json')
  if jobfile.exists():
   job=json.loads(jobfile.read_text());code="from pathlib import Path;f=Path('/proc/"+str(job['pid'])+"');assert(f/'stat').read_text().rsplit(')',1)[1].split()[19]=="+repr(job['start_ticks'])+";assert "+repr(ROOT+'/issue22_clock_probe.py')+" in (f/'cmdline').read_bytes().decode();print('owned-clock-server-verified')";guest_py(vm,code)
  else:launch(None,'clock-server',['python3',ROOT+'/issue22_clock_probe.py','serve','--host','0.0.0.0','--port','30923'],vm)
  host='NJ-k1' if vm[4]=='l' else 'NJ-k2';port=22400+int(vm[-2:])
  reply=qmp(host,vm,'human-monitor-command',{'command-line':f'hostfwd_add internet tcp:{p.HOSTS[host]}:{port}-:30923'})
  assert reply.get('return')=='',reply;p.save(p.OUT/'clock'/(vm+'-forward.json'),dict(host=host,port=port,reply=reply))
 print('SIX_NATIVE_BINARY_AND_INPUT_IDENTITIES_VERIFIED',flush=True)

def clocks(label):
 folder=p.OUT/'clock'/label;folder.mkdir(parents=True,exist_ok=False)
 code="import json,sys;sys.path.insert(0,"+repr(ROOT)+");from issue22_clock_probe import measure;items="+repr([(vm,p.HOSTS['NJ-k1' if vm[4]=='l' else 'NJ-k2'],22400+int(vm[-2:])) for vm in COMPUTE])+";print(json.dumps({vm:measure(host,port) for vm,host,port in items}))"
 rows=json.loads(guest_py(DRIVER,code,timeout=30));p.save(folder/'bounds.json',rows)
 assert all(r['consistent'] and r['maximum_absolute_offset_ms']<5 for r in rows.values()),'admission clock gate failed; do not score'
 print('CLOCK_GATE',label,round(max(r['maximum_absolute_offset_ms'] for r in rows.values()),3),flush=True)
 return rows

def services():
 folder=p.OUT/'services';folder.mkdir(exist_ok=False)
 pods=json.loads(p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf -n issue23 get pods -o json'));p.save(folder/'pods-before.json',pods)
 for enabled in [True,False]:
  names=[x['metadata']['name'] for x in pods['items'] if x['metadata'].get('labels',{}).get('experiment')=='issue23' and (x['metadata']['name'] in COMPUTE)==enabled]
  if names:p.guest('i23-c01',shlex.join(['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','-n','issue23','label','pod',*names,'active='+str(enabled).lower(),'--overwrite']))
 expected={ip(vm) for vm in COMPUTE};local={ip(vm) for vm in COMPUTE if vm[4]=='l'}
 until=time.monotonic()+90
 while time.monotonic()<until:
  slices=json.loads(p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf -n issue23 get endpointslices -o json'))
  ok=True
  for name in ['wan-default','wan-prefer-close']:
   eps=[e for s in slices['items'] if s['metadata'].get('labels',{}).get('kubernetes.io/service-name')==name for e in s.get('endpoints',[])]
   ok=ok and {a for e in eps for a in e['addresses']}==expected and len(eps)==6 and all(e.get('conditions',{}).get('ready') is True for e in eps)
   if name=='wan-prefer-close':ok=ok and all({z['name'] for z in e.get('hints',{}).get('forZones',[])}=={e.get('zone')} for e in eps)
  if ok:break
  time.sleep(1)
 else:raise RuntimeError('native Service endpoint/hint gate failed')
 p.save(folder/'endpointslices.json',slices)
 objects=json.loads(p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf -n issue23 get services wan-default wan-prefer-close -o json'));p.save(folder/'services.json',objects)
 for s in objects['items']:
  assert s['spec']['internalTrafficPolicy']=='Cluster' and s['spec']['sessionAffinity']=='None'
  if s['metadata']['name']=='wan-prefer-close':assert s['spec']['trafficDistribution']=='PreferClose'
 until=time.monotonic()+90
 while time.monotonic()<until:
  rules=p.guest(DRIVER,'iptables-save -t nat');actual={name:set(re.findall(r'issue23/'+name+r':infer -> ('+re.escape(p.GUEST_SUBNET)+r'\.\d+):19383',rules)) for name in ['wan-default','wan-prefer-close']}
  if actual['wan-default']==expected and actual['wan-prefer-close']==local:break
  time.sleep(1)
 else:raise RuntimeError('actual kube-proxy backend gate failed')
 (folder/'driver-iptables.txt').write_text(rules);p.save(folder/'actual-backends.json',{k:sorted(v) for k,v in actual.items()})
 endpoints={s['metadata']['name']:s['spec']['clusterIP']+':8080' for s in objects['items']};p.save(folder/'endpoints.json',endpoints);print('NATIVE_SERVICE_BACKENDS_VERIFIED_6_AND_3',flush=True);return endpoints

def shape(rtt,folder,qualify=True):
 folder.mkdir(parents=True,exist_ok=False)
 for dev in ['i23vwan1','i23vwan2']:
  if rtt=='unshaped':
   roots=[x for x in json.loads(p.ssh('NJ-k2','tc -j qdisc show dev '+dev)) if x.get('root')]
   for x in roots:
    if x['kind']=='netem' and x.get('handle')=='1:':p.ssh('NJ-k2','tc qdisc del dev '+dev+' root')
    elif x.get('handle') not in ['0:',None]:raise RuntimeError('unexpected owned qdisc')
  else:
   delay={'25':12,'65':32}[rtt]
   p.ssh('NJ-k2',shlex.join(['tc','qdisc','replace','dev',dev,'root','handle','1:','netem','limit','100000','delay',f'{delay}ms','5ms','loss','0.1%','rate','1gbit']))
   p.ssh('NJ-k2',shlex.join(['tc','qdisc','replace','dev',dev,'parent','1:','handle','10:','pfifo','limit','100000']))
 p.save(folder/'qdiscs.json',{dev:json.loads(p.ssh('NJ-k2','tc -s -j qdisc show dev '+dev)) for dev in ['i23vwan1','i23vwan2']})
 if qualify:
  cross=p.guest(DRIVER,'ping -n -i 0.1 -c 30 -W 2 '+p.GUEST_SUBNET+'.21');same=p.guest('i23-r01','ping -n -i 0.1 -c 30 -W 2 '+p.GUEST_SUBNET+'.22')
  (folder/'cross-ping.txt').write_text(cross);(folder/'same-domain-ping.txt').write_text(same)
  vals=lambda s:[float(x) for x in re.findall(r'time[=<]([0-9.]+) ms',s)]
  a,b=vals(cross),vals(same);bounds=(0,8) if rtt=='unshaped' else (15,35) if rtt=='25' else (55,75)
  row=dict(rtt=rtt,cross_received=len(a),cross_median_ms=statistics.median(a),same_received=len(b),same_median_ms=statistics.median(b));p.save(folder/'gate.json',row)
  assert len(a)>=29 and len(b)>=29 and bounds[0]<=row['cross_median_ms']<=bounds[1] and row['same_median_ms']<8,row
  print('RTT_GATE',json.dumps(row),flush=True)

def infrastructure(folder,label):
 folder.mkdir(parents=True,exist_ok=True)
 nodes=json.loads(p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes -o json'))
 pods=json.loads(p.guest('i23-c01','kubectl --kubeconfig=/etc/kubernetes/admin.conf -n issue23 get pods -o json'))
 p.save(folder/('nodes-'+label+'.json'),nodes);p.save(folder/('pods-'+label+'.json'),pods)
 for records in [nodes['items'],pods['items']]:
  selected=[x for x in records if x['metadata']['name'] in COMPUTE];assert len(selected)==6
  assert all(any(c['type']=='Ready' and c['status']=='True' for c in x['status'].get('conditions',[])) for x in selected),'active infrastructure not Ready'

def heartbeat_gate(folder,label,since_ns=None,until_ns=None):
 code="import json;from pathlib import Path;r=[]\nfor x in Path("+repr(ROOT+'/heartbeat.jsonl')+").read_text().splitlines():\n try:q=json.loads(x)\n except json.JSONDecodeError:continue\n if q.get('kind')=='tick' and ("+repr(since_ns)+" is None or q['unix_ns']>="+repr(since_ns)+") and ("+repr(until_ns)+" is None or q['unix_ns']<="+repr(until_ns)+"):r.append(q)\nprint(json.dumps(dict(samples=len(r),max_gap_ms=max([q['gap_ms'] for q in r],default=0),first_unix_ns=r[0]['unix_ns'] if r else None,last_unix_ns=r[-1]['unix_ns'] if r else None)))"
 def one(vm):return vm,json.loads(guest_py(vm,code,timeout=20))
 with ThreadPoolExecutor(4) as pool:rows=dict(pool.map(one,['i23-c01',DRIVER,*COMPUTE]))
 p.save(folder/('infrastructure-heartbeats-'+label+'.json'),rows)
 assert all(r['max_gap_ms']<=5000 for r in rows.values()),'VM heartbeat stall >5s: retain cell and stop pilot'
 if since_ns is not None and until_ns is not None:
  assert all(r['samples'] and r['first_unix_ns']<=since_ns+2000000000 and r['last_unix_ns']>=until_ns-2000000000 for r in rows.values()),'heartbeat coverage missing: preserve run and stop'
 return rows

if __name__=='__main__':
 import sys
 {'reference':reference,'provision':provision,'services':services}[sys.argv[1]]()
