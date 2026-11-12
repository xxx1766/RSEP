"""Read-only collision/resource/shared-cluster check before owned restoration."""
from concurrent.futures import ThreadPoolExecutor
import json
import time
import issue29_common as p

CODE=r'''from pathlib import Path
import json,subprocess,os
q=[]
for f in Path('/proc').glob('[0-9]*/cmdline'):
 try:b=f.read_bytes()
 except OSError:continue
 if b.startswith(b'qemu-system-x86_64\x00'):q.append(dict(pid=f.parent.name,argv=b.decode().split('\x00')))
run=lambda a:subprocess.check_output(a,text=True)
print(json.dumps(dict(qemu=q,links=json.loads(run(['ip','-j','link'])),addresses=json.loads(run(['ip','-j','address'])),routes=json.loads(run(['ip','-j','route'])),eth0_qdisc=json.loads(run(['tc','-j','qdisc','show','dev','eth0'])),firewall=run(['iptables-save']),cpuinfo=run(['lscpu']),meminfo=Path('/proc/meminfo').read_text(),loadavg=Path('/proc/loadavg').read_text(),pressure={x:Path('/proc/pressure/'+x).read_text() for x in ['cpu','memory','io']},kernel=run(['uname','-a']),kvm=Path('/sys/module/kvm/parameters/halt_poll_ns').read_text(),disk=run(['df','-B1','/var/lib/tide-issue23-20261031']),br_netfilter=Path('/sys/module/br_netfilter').exists(),clock_ticks=os.sysconf('SC_CLK_TCK'))))'''

def collect(host):
 row=json.loads(p.py(host,CODE,timeout=30));p.save(p.OUT/'preflight'/(host+'-before.json'),row)
 assert not row['qemu'],'QEMU already active: stop before restoration'
 assert not any(x['ifname'].startswith('i23') for x in row['links'])
 assert not any(x.get('dst')==p.GUEST_SUBNET+'.0/24' for x in row['routes'])
 assert row['br_netfilter']
 print(host,'PRECHECK_OK',row['loadavg'].strip(),flush=True)

def main():
 assert '\x00' not in CODE
 compile(CODE,'remote-preflight','exec')
 with ThreadPoolExecutor(3) as pool:list(pool.map(collect,p.HOSTS))
 shared=json.loads(p.ssh('NJ-k1','kubectl --request-timeout=10s get nodes -o json',timeout=20));p.save(p.OUT/'preflight/shared-cluster-before.json',shared)
 assert len(shared['items'])==3 and all(any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions']) for n in shared['items'])
 p.save(p.OUT/'preflight/passed.json',dict(status='PASS',unix_ns=time.time_ns()))

if __name__=='__main__':main()
