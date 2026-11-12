import json,os,pathlib,subprocess,time
R=pathlib.Path(os.environ.get('RSEP_ISSUE30_ROOT','/var/lib/rsep/issue30'))
def run(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=30)
 return dict(command=a,rc=p.returncode,out=p.stdout,err=p.stderr)
assert not R.exists(),'preserve prior attempt'
assert os.geteuid()==0
assert not subprocess.check_output(['ctr','-n','tide','tasks','ls','-q']).strip()
assert not subprocess.check_output(['ctr','-n','tide','containers','ls','-q']).strip()
assert not list(pathlib.Path('/dev/mqueue').iterdir())
assert not [p for p in pathlib.Path('/sys/fs/cgroup/tide').iterdir() if p.is_dir()]
settings=['/proc/sys/fs/mqueue/msgsize_max','/proc/sys/fs/mqueue/msg_max','/sys/fs/cgroup/tide/cpuset.cpus','/sys/fs/cgroup/tide/cgroup.subtree_control','/sys/fs/cgroup/cgroup.subtree_control']
baseline=dict(time_ns=time.time_ns(),host=run(['hostname']),cpu=run(['lscpu','-J']),tasks={ns:run(['ctr','-n',ns,'tasks','ls']) for ns in ['default','tide','k8s.io']},settings={p:pathlib.Path(p).read_text() for p in settings},services={s:run(['systemctl','is-active',s]) for s in ['containerd','kubelet']},ports=run(['ss','-ltnp']),mq=[],cgroups=[])
R.mkdir(mode=0o700)
for x in ['evidence','runtime','runs','source','parent-control']: (R/x).mkdir()
(R/'evidence/baseline.json').write_text(json.dumps(baseline,indent=2))
print(json.dumps(dict(status='PASS',root=str(R),host=baseline['host']['out'].strip())))
