"""Delete only explicitly owned IDs, after observing containerd's stopped state."""
import re
import subprocess
import time


def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=15)
    return dict(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)


def remove_owned(namespace,ids,command=run,pause=time.sleep):
    assert namespace in ['tide','default']
    assert all(re.fullmatch(r'[0-9a-f-]{36}|tide-matrix-[0-9a-f]{32}',eid) for eid in ids)
    journal=[]
    def cmd(tail,required=True):
        args=['ctr','-n',namespace,*tail];result=command(args)
        journal.append(dict(command=args,**result))
        if required and result['returncode']:raise RuntimeError(journal[-1])
        return result
    def listing(kind):
        result=cmd([kind,'ls'])
        return {r[0]:r for r in [line.split() for line in result['stdout'].splitlines()[1:]] if r}
    for eid in sorted(ids):
        tasks=listing('tasks')
        if eid in tasks:
            if tasks[eid][-1]!='STOPPED':cmd(['tasks','kill','-s','SIGKILL',eid],required=False)
            deadline=time.monotonic()+20
            while True:
                tasks=listing('tasks')
                if eid not in tasks:break
                if tasks[eid][-1]=='STOPPED':
                    cmd(['tasks','rm',eid],required=False)
                    if eid not in listing('tasks'):break
                assert time.monotonic()<deadline,('owned task failed to stop/delete',eid,journal)
                pause(.1)
        containers=listing('containers')
        if eid in containers:
            result=cmd(['containers','rm',eid],required=False)
            if eid in listing('containers'):raise RuntimeError(('owned container deletion failed',eid,result))
    tasks=listing('tasks');containers=listing('containers')
    assert not set(ids)&(tasks.keys()|containers.keys())
    return dict(verified_absent=True,ids=sorted(ids),namespace=namespace,commands=journal)
