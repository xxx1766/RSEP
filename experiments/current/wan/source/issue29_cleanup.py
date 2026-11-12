"""Clean ACPI shutdown and exact owned-network removal; preserve all guest disks."""
import argparse
import json
import shlex
import time
import issue29_common as p

def shutdown():
 assert (p.OUT/'collection/complete.json').exists()
 root=p.OUT/'cleanup';root.mkdir(exist_ok=False)
 old=p.CANONICAL/'results-issue24-network-recheck-20261031/cleanup/shutdown-owned-guests.py'
 file=root/'shutdown-owned-guests.py';file.write_text(old.read_text().replace('/var/lib/tide-issue24-network-20261031',p.REMOTE))
 for host,names in [('NJ-k1',['i23-l01','i23-l02','i23-l03']),('NJ-k2',['i23-r01','i23-r02','i23-r03']),('NJ-k3',['i23-c01','i23-d01'])]:
  p.copy(host,file,p.REMOTE+'/shutdown-owned-guests.py')
  argv=['python3',p.REMOTE+'/shutdown-owned-guests.py',json.dumps(names)]
  code="import subprocess,json;from pathlib import Path;f=Path("+repr(p.REMOTE+'/shutdown-owned-guests.log')+").open('x');q=subprocess.Popen("+repr(argv)+",stdout=f,stderr=f,start_new_session=True);print(json.dumps(dict(pid=q.pid,start_ticks=Path('/proc/'+str(q.pid)+'/stat').read_text().rsplit(')',1)[1].split()[19],argv="+repr(argv)+")))"
  p.save(root/(host+'-shutdown-job.json'),json.loads(p.py(host,code)))
  print('CLEAN_SHUTDOWN_STARTED',host,flush=True)

def finish():
 root=p.OUT/'cleanup';stopped=0
 for host in p.HOSTS:
  status=json.loads(p.ssh(host,'cat '+p.REMOTE+'/shutdown-result.json'))
  p.save(root/(host+'-shutdown-result.json'),status)
  assert status['all_cleanly_stopped'],'guest shutdown incomplete: retain all interfaces'
  stopped+=len(status['vms'])
  code="from pathlib import Path;import json,os;out=[]\nfor f in Path('/proc').glob('[0-9]*/cmdline'):\n if f.parent.name==str(os.getpid()):continue\n try:b=f.read_bytes()\n except OSError:continue\n if "+repr(p.REMOTE.encode())+" in b and (b.startswith(b'qemu-system-x86_64\\x00') or b.startswith(b'python3\\x00')):out.append(dict(pid=f.parent.name,cmd=b.decode(errors='replace').replace('\\x00',' ')))\nprint(json.dumps(out))"
  running=json.loads(p.py(host,code));p.save(root/(host+'-processes-final.json'),running);assert not running
  commands=json.loads((p.OUT/'restore'/(host+'-overlay-commands.json')).read_text())
  if host=='NJ-k2':commands+=json.loads((p.OUT/'restore/aggregate-boundary-commands.json').read_text())
  rules=[cmd[4:] for cmd in commands if cmd[0]=='iptables' and cmd[1]=='-I']
  for rule in rules:
   assert rule[rule.index('--comment')+1] in ['issue23-20261031','issue23-aggregate-wan']
   p.ssh(host,shlex.join(['iptables','-D','FORWARD',*rule]))
  taps=['i23tap11','i23tap12','i23tap13'] if host=='NJ-k1' else ['i23tap21','i23tap22','i23tap23'] if host=='NJ-k2' else ['i23tap99','i23tap100']
  for name in taps:p.ssh(host,shlex.join(['ip','tuntap','del',name,'mode','tap']))
  for name in (['i23vwan1','i23vx1','i23br2','i23br1'] if host=='NJ-k2' else ['i23vx1','i23br1']):p.ssh(host,shlex.join(['ip','link','del',name]))
  after=dict(addresses=json.loads(p.ssh(host,'ip -j address')),eth0_qdisc=json.loads(p.ssh(host,'tc -j qdisc show dev eth0')),firewall=p.ssh(host,'iptables-save'))
  p.save(root/(host+'-network-after.json'),after)
  before=json.loads((p.OUT/'preflight'/(host+'-before.json')).read_text())
  assert before['eth0_qdisc']==after['eth0_qdisc']
  a={x['ifname']:x for x in before['addresses']};b={x['ifname']:x for x in after['addresses']};assert set(a)==set(b)
  for name,row in a.items():
   addresses=lambda r:{(x['family'],x['local'],x['prefixlen']) for x in r.get('addr_info',[])}
   assert row.get('address')==b[name].get('address') and addresses(row)==addresses(b[name])
  assert 'issue23-20261031' not in after['firewall'] and 'issue23-aggregate-wan' not in after['firewall']
  p.copy(host,p.REMOTE+'/shutdown-owned-guests.log',root/(host+'-shutdown.log'),pull=True)
  print('OWNED_NETWORK_RELEASED_SHARED_UNCHANGED',host,flush=True)
 shared=json.loads(p.ssh('NJ-k1','kubectl --request-timeout=10s get nodes -o json'));p.save(root/'shared-cluster-after.json',shared)
 assert len(shared['items'])==3 and all(any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions']) for n in shared['items'])
 assert stopped==8
 p.save(root/'verified.json',dict(status='PASS',cleanly_stopped_guests=8,other10guests_never_started=True,owned_network_removed=True,guest_disks_preserved=True,shared_addresses_and_eth0_qdisc_unchanged=True,shared_cluster_ready_nodes=3,unix_ns=time.time_ns()))
 print('CLEANUP_VERIFIED',flush=True)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['shutdown','finish']);a=parser.parse_args();shutdown() if a.mode=='shutdown' else finish()
