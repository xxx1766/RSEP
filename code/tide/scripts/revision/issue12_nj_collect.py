#!/usr/bin/env python3
"""Collect final image and network evidence after all paired jobs finish."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import time

import issue12_nj_matrix as m
from issue12_nj_postcheck import completed_guards


def collect_node(node, target):
    folder=target/node
    folder.mkdir(exist_ok=True)
    host=m.HOSTS[node]
    for source,destination in (("manifest.json","image-manifest.json"),("requests.jsonl","image-requests.jsonl")):
        m.copy(host,m.ROOT+"/image-service/v2/"+source,folder/destination,pull=True)
    m.copy(host,m.ROOT+"/image-service/v2/inputs",folder,pull=True)
    checks={"kernel":["uname","-a"],"cpu":["lscpu","-J"],"os":["cat","/etc/os-release"],
            "memory":["free","-b"],"load":["cat","/proc/loadavg"],
            "route_to_peer":["ip","-j","route","get",m.IPS["nj2" if node=="nj1" else "nj1"]],
            "interface":["ip","-j","addr","show","dev","eth0"],
            "qdisc":["tc","-s","qdisc","show","dev","eth0"],
            "tcp":["sysctl","net.ipv4.tcp_congestion_control","net.ipv4.tcp_slow_start_after_idle",
                   "net.ipv4.tcp_rmem","net.ipv4.tcp_wmem","net.core.rmem_max","net.core.wmem_max"],
            "containerd":["ctr","version"]}
    if node=="nj1":
        checks["go_binary_metadata"]=["/usr/local/go/bin/go","version","-m",m.BINARY]
        binary=folder/"tide-image-test"
        if not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest()!=m.SHA:
            m.copy(host,m.BINARY,binary,pull=True)
    program="import json,subprocess; checks="+repr(checks)+"; out={}\n"
    program+="for name,argv in checks.items():\n r=subprocess.run(argv,text=True,capture_output=True); out[name]=dict(argv=argv,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr)\nprint(json.dumps(out))"
    m.save(folder/"environment.json",json.loads(m.ssh(host,"python3 -c "+shlex.quote(program))))
    program="import json,sys; from pathlib import Path; sys.path.insert(0,"+repr(m.ROOT)+"); from issue12_nj_job import job_status; root=Path("+repr(m.ROOT)+"); out={}\n"
    program+="for family in ('formal01','validation01'):\n for path in (root/family).glob('*/*/state.json'):\n  out[str(path.parent.relative_to(root))]=job_status(path.parent)\nprint(json.dumps(out))"
    jobs=json.loads(m.ssh(host,"python3 -c "+shlex.quote(program)))
    m.save(folder/"application-job-status.json",jobs)
    expected=set()
    for family in ("formal01","validation01"):
        plan=json.loads((m.BASE/family/"plan.json").read_text())
        for cell in plan["cells"]:
            for job in (("worker-nj1","origin","http-client") if node=="nj1" else ("worker-nj2",)):
                expected.add(f"{family}/{cell['id']}/{job}")
    if set(jobs)!=expected or any(row["phase"]!="finished" for row in jobs.values()):
        raise RuntimeError("application job still live or missing its terminal record")
    if any(row["exit_code"]!=0 for name,row in jobs.items() if name.rsplit('/',1)[-1] in ("origin","http-client")):
        raise RuntimeError("scored application driver exited unsuccessfully")
    return node


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume",action="store_true")
    args=parser.parse_args()
    guards=completed_guards()
    try:
        target=m.BASE/"evidence"
        if args.resume:
            previous=json.loads((target/"collection.json").read_text())
            if previous["complete"]:
                raise RuntimeError("collection already complete")
            shutil.copytree(target,m.BASE/f"evidence-incomplete-{time.time_ns()}")
        else:
            target.mkdir(exist_ok=False)
        m.save(target/"collection.json",dict(started_unix_ns=time.time_ns(),complete=False))
        plan=json.loads((m.BASE/"formal01/plan.json").read_text())
        m.save(target/"final-preflight.json",m.preflight(plan))
        monitors=set()
        for family in ("formal01","validation01"):
            for path in (m.BASE/family).glob("monitor-segments-*.json"):
                monitors.update(tuple(job) for job in json.loads(path.read_text()))
        monitors.add(tuple(json.loads((m.BASE/"formal01/extended-monitor.json").read_text())["job"][:2]))
        for host,remote in sorted(monitors):
            family,name=remote.rsplit("/",2)[-2:]
            parent=m.BASE/family
            m.stop_collect((host,remote,parent/name),parent)
        with ThreadPoolExecutor(max_workers=2) as pool:
            collected=list(pool.map(lambda node:collect_node(node,target),("nj1","nj2")))
        for name in ("network-nj01.json","network-nj02.json"):
            m.copy("NJ-k1",m.ROOT+"/"+name,target/name,pull=True)
        m.save(target/"kubernetes-version.json",json.loads(m.ssh("NJ-k1","kubectl version -o json")))
        m.save(target/"configmaps.json",json.loads(m.ssh("NJ-k1","kubectl -n tmc-revision get configmap tmc-issue12-nj-image tmc-issue12-nj-image-v2 -o json")))
        m.save(target/"collection.json",dict(completed_unix_ns=time.time_ns(),complete=True,nodes=collected,
            sha256={str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(target.rglob('*')) if p.is_file() and p.name!="collection.json"}))
        print(target)
    finally:
        for guard in guards:
            guard.close()


if __name__=="__main__":
    main()
