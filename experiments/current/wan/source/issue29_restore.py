"""Restore 6 original compute guests and two support guests, unshaped."""
import json
import shlex
import time
import issue29_common as p

VMS=['i23-c01','i23-d01','i23-l01','i23-l02','i23-l03','i23-r01','i23-r02','i23-r03']

def main():
 root=p.OUT/'restore';root.mkdir(exist_ok=False)
 assert (p.OUT/'preflight/passed.json').exists()
 for host in p.HOSTS:
  p.ssh(host,'mkdir '+p.REMOTE)
  commands=json.loads((p.OLD/'validation/network'/(host+'-commands.json')).read_text())
  p.save(root/(host+'-overlay-commands.json'),commands)
  for cmd in commands:p.ssh(host,shlex.join(cmd))
 commands=json.loads((p.OLD/'preflight/aggregate-wan/plan.json').read_text())['commands']
 p.save(root/'aggregate-boundary-commands.json',commands)
 for cmd in commands:p.ssh('NJ-k2',shlex.join(cmd))
 for vm in VMS:
  role=vm[4];host={'c':'NJ-k3','d':'NJ-k3','l':'NJ-k1','r':'NJ-k2'}[role]
  file=p.OLD/('validation/boot/'+vm+'/launch.json' if role in 'cd' else 'validation/storage-thin/'+vm+'/launch.json' if vm.endswith('01') else 'preflight/expanded-vms/'+vm+'/launch.json')
  argv=list(json.loads(file.read_text())['argv'])
  number=99 if role=='c' else 100 if role=='d' else (10 if role=='l' else 20)+int(vm[-2:]);tap='i23tap'+str(number)
  for cmd in [['ip','tuntap','add',tap,'mode','tap'],['ip','link','set',tap,'master','i23br1'],['ip','link','set',tap,'mtu','1350'],['ip','link','set',tap,'up']]:p.ssh(host,shlex.join(cmd))
  folder=p.REMOTE+'/vms/'+vm;p.ssh(host,'mkdir -p '+folder)
  for flag,value in [('-serial','file:'+folder+'/console.log'),('-qmp','unix:'+folder+'/qmp.sock,server=on,wait=off'),('-pidfile',folder+'/pid')]:argv[argv.index(flag)+1]=value
  p.save(root/(vm+'-launch.json'),dict(vm=vm,host=host,argv=argv,source=str(file),source_sha256=p.digest(file),qmp_folder=folder))
  p.ssh(host,shlex.join(argv));print('ORIGINAL_VM_STARTED',vm,flush=True)
 p.save(root/'restored.json',dict(vms=VMS,other10_compute_off=True,guest_resources_cpu_binding_clock_unchanged=True,unshaped=True,host_clock_not_changed=True,unix_ns=time.time_ns()))

if __name__=='__main__':main()
