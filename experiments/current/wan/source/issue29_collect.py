"""Collect recorded evidence after the pilot controller terminates, then stop monitors."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import tarfile
import time
import issue29_common as p
from issue29_start import ROOT

def stop_code(job,expected):
 return '''import os,signal,json
from pathlib import Path
pid=PID;f=Path('/proc/'+str(pid));row=dict(pid=pid)
if not f.exists():row['state']='already_absent'
else:
 fields=(f/'stat').read_text().rsplit(')',1)[1].split();cmd=(f/'cmdline').read_bytes().decode(errors='replace')
 if fields[19]!=TICKS:raise RuntimeError('PIDreused; refuse signal')
 if fields[0]=='Z':row['state']='already_zombie'
 else:
  assert EXPECTED in cmd,'foreign process; refuse signal'
  os.kill(pid,signal.SIGTERM);row['state']='stop_requested'
print(json.dumps(row))'''.replace('PID',str(job['pid'])).replace('TICKS',repr(job['start_ticks'])).replace('EXPECTED',repr(expected))

def archive_code(root):
 return '''import tarfile,hashlib,json
from pathlib import Path
root=Path(ROOT);out=root/'collected-evidence.tar.gz';assert not out.exists()
files=[f for f in root.rglob('*') if f.is_file() and not f.is_symlink()]
with tarfile.open(out,'w:gz') as archive:
 for f in files:archive.add(f,arcname=str(f.relative_to(root)),recursive=False)
print(json.dumps(dict(files=len(files),sha256=hashlib.sha256(out.read_bytes()).hexdigest())))'''.replace('ROOT',repr(root))

FINAL='''import json,subprocess,re,time
from pathlib import Path
root=Path(ROOT)
journal=subprocess.check_output(['journalctl','-b','-o','short-precise','--no-pager'],text=True);(root/'journal.txt').write_text(journal)
lines=journal.splitlines();row=dict(unix_ns=time.time_ns(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text(),watchdog_lines=[x for x in lines if re.search(r'watchdog.*(timeout|timed out|failed|exceeded)|Failed with result .watchdog',x,re.I)],oom_lines=[x for x in lines if re.search(r'Out of memory|oom-kill|Killed process',x,re.I)],services=subprocess.check_output(['systemctl','show','systemd-networkd','systemd-journald','systemd-resolved','systemd-udevd','systemd-logind','-p','Id,MainPID,NRestarts,ActiveEnterTimestampMonotonic'],text=True),clock=Path('/sys/devices/system/clocksource/clocksource0/current_clocksource').read_text(),cpuinfo=Path('/proc/cpuinfo').read_text(),uptime=Path('/proc/uptime').read_text())
if COMPUTE:
 base=['crictl','--runtime-endpoint','unix:///run/containerd/containerd.sock'];cs=json.loads(subprocess.check_output(base+['ps','-o','json']))['containers'];cs=[x for x in cs if x['metadata']['name']=='infer'];assert len(cs)==1
 c=cs[0];info=json.loads(subprocess.check_output(base+['inspect',c['id']]))
 row.update(container=c,app_pid=info['info']['pid'])
 (root/'application-source.py').write_bytes(Path(LEGACY+'/code/issue23_image_server.py').read_bytes())
 (root/'server-executions.jsonl').write_bytes(Path(LEGACY+'/pod-output/requests.jsonl').read_bytes()) if Path(LEGACY+'/pod-output/requests.jsonl').exists() else None
(root/'final-runtime.json').write_text(json.dumps(row,indent=2))
print(json.dumps(dict(watchdogs=len(row['watchdog_lines']),oom=len(row['oom_lines']))))'''

def collect_guest(vm):
 for name,expected in [('heartbeat',ROOT+'/issue29_observe.py'),('clock-server',ROOT+'/issue22_clock_probe.py')]:
  file=p.OUT/'jobs'/(vm+'-'+name+'.json')
  if file.exists():p.save(p.OUT/'collection'/(vm+'-'+name+'-stop.json'),json.loads(p.guest(vm,'python3 -c '+shlex.quote(stop_code(json.loads(file.read_text()),expected)))))
 if vm in ['i23-l01','i23-l02','i23-l03','i23-r01','i23-r02','i23-r03']:
  job=json.loads((p.OUT/'jobs'/(vm+'-echo.json')).read_text())
  p.save(p.OUT/'collection'/(vm+'-echo-stop.json'),json.loads(p.guest(vm,'python3 -c '+shlex.quote(stop_code(job,ROOT+'/issue24_network_probe.py')))))
 code=FINAL.replace('ROOT',repr(ROOT)).replace('LEGACY',repr(p.VM_REMOTE)).replace('COMPUTE',repr(vm in ['i23-l01','i23-l02','i23-l03','i23-r01','i23-r02','i23-r03']))
 p.save(p.OUT/'collection'/(vm+'-final-status.json'),json.loads(p.guest(vm,'python3 -c '+shlex.quote(code),timeout=35)))
 meta=json.loads(p.guest(vm,'python3 -c '+shlex.quote(archive_code(ROOT)),timeout=90))
 target=p.OUT/'guest-logs'/vm;target.mkdir(parents=True,exist_ok=False);archive=target/'collected-evidence.tar.gz'
 p.guest_copy(vm,ROOT+'/collected-evidence.tar.gz',archive,pull=True);assert p.digest(archive)==meta['sha256']
 with tarfile.open(archive) as bundle:bundle.extractall(target,filter='data')
 p.save(p.OUT/'collection'/(vm+'-archive.json'),meta);print('GUEST_LOGS_COLLECTED',vm,flush=True)

def collect_host(host):
 for name,expected in [('underlay-probes','/issue24_network_probe.py'),('resources','/issue29_resources.py'),('underlay-server','/issue24_network_probe.py')]:
  job=json.loads((p.OUT/'jobs'/(host+'-'+name+'.json')).read_text())
  p.save(p.OUT/'collection'/(host+'-'+name+'-stop.json'),json.loads(p.py(host,stop_code(job,p.REMOTE+expected))))
 meta=json.loads(p.py(host,archive_code(p.REMOTE),timeout=90));target=p.OUT/'host-logs'/host;target.mkdir(parents=True,exist_ok=False);archive=target/'collected-evidence.tar.gz'
 p.copy(host,p.REMOTE+'/collected-evidence.tar.gz',archive,pull=True);assert p.digest(archive)==meta['sha256']
 with tarfile.open(archive) as bundle:bundle.extractall(target,filter='data')
 p.save(p.OUT/'collection'/(host+'-archive.json'),meta);print('HOST_LOGS_COLLECTED',host,flush=True)

def main(resume=False):
 assert (p.OUT/'campaign-final.json').exists(),'pilot controller must finish orrecordSTOPPED before collection'
 (p.OUT/'collection').mkdir(exist_ok=resume)
 from issue29_ops import qmp
 for file in sorted((p.OUT/'clock').glob('*-forward.json')):
  vm=file.name.removesuffix('-forward.json');meta=json.loads(file.read_text());record=p.OUT/'collection'/(vm+'-clock-forward-removed.json')
  if record.exists():continue
  address=f"tcp:{p.HOSTS[meta['host']]}:{meta['port']}";r=qmp(meta['host'],vm,'human-monitor-command',{'command-line':'hostfwd_remove internet '+address});assert r.get('return') in ['', 'host forwarding rule for '+address+' removed\r\n'],r;p.save(record,r)
 with ThreadPoolExecutor(4) as pool:list(pool.map(collect_guest,['i23-c01','i23-d01','i23-l01','i23-l02','i23-l03','i23-r01','i23-r02','i23-r03']))
 reference=p.OUT/'clock/reference-job.json'
 if reference.exists():p.save(p.OUT/'collection/reference-stop.json',json.loads(p.py('NJ-k3',stop_code(json.loads(reference.read_text()),p.REMOTE+'/reference.conf'))))
 with ThreadPoolExecutor(3) as pool:list(pool.map(collect_host,p.HOSTS))
 p.save(p.OUT/'collection/complete.json',dict(unix_ns=time.time_ns(),guests=8,hosts=3))

if __name__=='__main__':main()
