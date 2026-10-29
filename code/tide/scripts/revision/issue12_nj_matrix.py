#!/usr/bin/env python3
"""Frozen NJ image pairs; detached remote jobs survive SSH observation loss."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import math
from pathlib import Path
import random
import shlex
import subprocess
import time

from issue12_trace import generate
from issue12_audit import audit_rows

ROOT = "/tmp/tide-issue12-nj-20261009"
BASE = Path(__file__).resolve().parents[2] / "results-issue12-nj-20261009"
LOCAL = BASE / "formal01"
REMOTE = ROOT + "/formal01"
BINARY = ROOT + "/tide-image-test"
SHA = "8a49418e4853576e1be0de7affbed022191a44ffa5a6cee28ef1dc531bceefa4"
IMAGE_CONFIG = "sha256:08f7264a1a218832429b41b86ce3a5a11acf50fea42ab1977e4460fa09b7a4c0"
HOSTS = {"nj1": "NJ-k1", "nj2": "NJ-k2"}
IPS = {"nj1": "11.0.4.47", "nj2": "11.0.4.48"}


def command(argv, timeout=60):
    r = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"command failed ({r.returncode}): {shlex.join(argv)}\n{r.stderr[-2000:]}")
    return r.stdout.strip()


def options(host):
    opts = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]
    if host == "NJ-k2":
        opts += ["-o", "UserKnownHostsFile=/private/tmp/tide-issue12-known-hosts", "-o", "StrictHostKeyChecking=yes"]
    return opts


def ssh(host, cmd):
    return command(["ssh", *options(host), host, cmd])


def copy(host, source, destination, *, pull):
    args = ["scp", *options(host), "-r"]
    args += [f"{host}:{source}", str(destination)] if pull else [str(source), f"{host}:{destination}"]
    for attempt in range(3):
        try:
            return command(args, timeout=120)
        except (RuntimeError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2)


def save(path, row):
    path.write_text(json.dumps(row, indent=2)+"\n")


def progress(**row):
    row["observed_unix_ns"] = time.time_ns()
    save(LOCAL / "progress.json", row)
    print(json.dumps(row), flush=True)


def preflight(plan=None):
    pods = json.loads(ssh("NJ-k1", "kubectl -n tmc-revision get pods tmc-issue12-njimg1v2 tmc-issue12-njimg2v2 -o json"))
    if len(pods["items"]) != 2:
        raise RuntimeError("two image Pods required")
    identities = {}
    for pod in pods["items"]:
        status = pod["status"]["containerStatuses"][0]
        if not status["ready"] or status["restartCount"] != 0 or status["imageID"] != IMAGE_CONFIG:
            raise RuntimeError("image Pod readiness/digest changed")
        identities[pod["metadata"]["name"]] = pod["metadata"]["uid"]
    if plan and identities != plan["pod_uids"]:
        raise RuntimeError("Pod UID drift")
    network = json.loads(ssh("NJ-k1", f"python3 {ROOT}/issue12_nj_netns.py status --run-id nj02"))
    for key in ("host_qdisc", "origin_qdisc"):
        qdisc = network["live"][key]
        if any(value not in qdisc for value in ("delay 32ms", "5ms", "loss 0.1%", "rate 1Gbit", "pfifo")):
            raise RuntimeError("network configuration drift")
    if "fq_codel" not in network["live"]["shared_eth0"]:
        raise RuntimeError("shared interface changed")
    for host in HOSTS.values():
        if ssh(host, f"sha256sum {BINARY}").split()[0] != SHA:
            raise RuntimeError("binary drift")
    if plan:
        for name in ("issue12_image_client.py","issue12_nj_job.py","issue12_nj_netns.py"):
            if ssh("NJ-k1",f"sha256sum {ROOT}/{name}").split()[0] != plan["source_sha256"][name]:
                raise RuntimeError("experiment script drift: "+name)
    return dict(pods=pods, pod_uids=identities, network=network)


def prepare():
    checked = preflight()
    LOCAL.mkdir(exist_ok=False)
    (LOCAL/"traces").mkdir()
    (LOCAL/"raw").mkdir()
    calibration = json.loads((BASE/"image-calibration-fifo.json").read_text())
    execution = {f"nj{i+1}":math.ceil(row["summary"]["service_median_ms"]) for i,row in enumerate(calibration)}
    cells = [dict(id=f"{pattern}-r{repeat}", pattern=pattern, repeat=repeat,
                  window_ms=270000 if pattern=="gradual" else 240000)
             for pattern in ("gradual","burst") for repeat in (1,2,3)]
    rng = random.Random(20261009)
    rng.shuffle(cells)
    order = ["tide","k8s"] * 3
    rng.shuffle(order)
    for cell, first in zip(cells, order):
        cell["order"] = [first, "k8s" if first=="tide" else "tide"]
        rows, _ = generate(cell["pattern"], 1, cell["repeat"])
        for row in rows:
            row["id"] = "nj-image-"+row["id"]
        (LOCAL/"traces"/(cell["id"]+".jsonl")).write_text("".join(json.dumps(r)+"\n" for r in rows))
        (LOCAL/"raw"/cell["id"]).mkdir()
    source_hashes = {name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in
                     ("issue12_nj_matrix.py","issue12_nj_job.py","issue12_image_client.py","issue12_image_server.py","issue12_nj_netns.py")}
    plan = dict(cells=cells, seed=20261009, qos_ms=135, sources=1,
                worker_execution_ms=execution, image_calibration=calibration,
                application="one YOLOX-Nano inference, four resolutions of the archived dog image",
                image_order="(request_index // 2) % 4", channels_per_endpoint=16,
                tide_path="native Stub scheduling and receipt/result RPC; local HTTP image executor adapter",
                k8s_path="same image service in prestarted Pods; static RR with LIFO HTTP connection pools",
                binary_sha256=SHA, source_sha256=source_hashes, pod_uids=checked["pod_uids"],
                image_config_digest=IMAGE_CONFIG,
                image_manifest_digest="sha256:bd16f9d2f955f90997886f627ce066e59587be93923fed0b197e0a48d8345363")
    save(LOCAL/"plan.json", plan)
    save(LOCAL/"preflight.json", checked)
    for host in HOSTS.values():
        ssh(host, f"mkdir -p {REMOTE}")
        copy(host, LOCAL/"traces", REMOTE+"/", pull=False)
    return plan


def launch(host, name, spec, folder):
    remote = f"{REMOTE}/{name}"
    local = folder / name.rsplit("/",1)[-1]
    local.mkdir(exist_ok=True)
    if (local/"spec.json").exists() and json.loads((local/"spec.json").read_text()) != spec:
        raise RuntimeError("refusing to replace an existing job specification")
    save(local/"spec.json",spec)
    ssh(host, f"mkdir -p {shlex.quote(remote)}")
    copy(host, local/"spec.json", remote+"/spec.json", pull=False)
    # Do not retry launch after an SSH error: inspect this exact job instead.
    try:
        wrapper = ssh(host, f"nohup python3 {ROOT}/issue12_nj_job.py run {shlex.quote(remote)} > {shlex.quote(remote+'/wrapper.log')} 2>&1 < /dev/null & echo $!")
        save(local/"launch.json", dict(wrapper=wrapper))
    except Exception as error:
        save(local/"launch.json", dict(observation_error=str(error)))
    return host, remote, local


def status(job):
    return json.loads(ssh(job[0], f"python3 {ROOT}/issue12_nj_job.py status {shlex.quote(job[1])}"))


def recovery_action(state):
    if state["phase"] == "absent":
        return "launch"
    if state["phase"] in ("running", "starting"):
        return "observe"
    if state["phase"] == "finished" and state["exit_code"] == 0:
        return "collect"
    raise RuntimeError(f"preserving unsuccessful or missing job; automatic rerun forbidden: {state}")


def stop_collect(job, parent):
    current = status(job)
    if current["phase"] in ("running", "starting"):
        ssh(job[0],f"python3 {ROOT}/issue12_nj_job.py stop {shlex.quote(job[1])}")
        for _ in range(15):
            current = status(job)
            if current["phase"] == "finished":
                break
            time.sleep(0.2)
    if current["phase"] != "finished":
        raise RuntimeError(f"stop not verified: {job[:2]} {current}")
    copy(job[0], job[1], parent, pull=True)


def stop_workers(cell, folder):
    errors=[]
    for node in ("nj1", "nj2"):
        remote=f"{REMOTE}/{cell['id']}"
        job=(HOSTS[node], remote+"/worker-"+node, folder/("worker-"+node))
        try:
            if status(job)["phase"] == "absent":
                continue
            stop_collect(job, folder)
            copy(job[0], remote+"/"+node+"-resources.jsonl", folder, pull=True)
            copy(job[0], remote+"/"+node+"-ready.json", folder, pull=True)
        except Exception as error:
            errors.append(str(error))
    if errors:
        save(folder/"worker-cleanup-errors.json",errors)
        raise RuntimeError(errors)


def collect_treatment(cell, folder, system, job):
    copy(job[0],job[1],folder,pull=True)
    remote=f"{REMOTE}/{cell['id']}"
    copy("NJ-k1",remote+("/tide-output" if system=="tide" else "/k8s-output"),folder,pull=True)
    if system=="tide":
        copy("NJ-k1",remote+"/origin-resources.jsonl",folder,pull=True)


def wait(job, cell, system):
    missing = 0
    while True:
        try:
            state = status(job)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            progress(phase="observation_retry", cell=cell, system=system, job=job[:2], error=str(error))
            time.sleep(10)
            continue
        save(job[2]/"last-observation.json", state)
        if state["phase"] == "finished":
            return state
        if state["phase"] in ("missing_process","absent"):
            missing += 1
            if missing >= 3:
                raise RuntimeError(f"job has no live process or terminal record: {job[:2]}")
        else:
            missing = 0
        progress(phase="running", cell=cell, system=system, job=job[:2], pid=state.get("pid"))
        time.sleep(10)


def native(cell, folder, plan):
    name=cell["id"]
    remote=f"{REMOTE}/{name}"
    work=dict(kind="cpu",bytes=65536,iterations=30)  # unused fallback; image adapter is explicit in job env.
    workers=[]
    try:
        for node in ("nj1","nj2"):
            config=dict(node_id=node,listen=f"{IPS[node]}:{29721 if node=='nj1' else 29722}",
                        origin_addr=("10.212.13.2" if node=="nj1" else "11.0.4.47")+":29682",
                        total_cpu=4, execution_ms=plan["worker_execution_ms"][node], qos_ms=135,warmup_qos_ms=2000,
                        workload=work,ready_path=f"{remote}/{node}-ready.json",resource_path=f"{remote}/{node}-resources.jsonl")
            save(folder/(node+".json"),config)
            ssh(HOSTS[node],f"mkdir -p {remote}")
            copy(HOSTS[node],folder/(node+".json"),remote+"/"+node+".json",pull=False)
            job=launch(HOSTS[node],name+"/worker-"+node,dict(
                argv=["taskset","-c","10,11",BINARY,"-test.run=^TestIssue5WorkerProcess$","-test.timeout=12m","-test.v"],
                env=dict(TMC_ISSUE5_WORKER_CONFIG=remote+"/"+node+".json",TMC_ISSUE12_IMAGE_ENDPOINT="http://127.0.0.1:19283",GOMAXPROCS="1")),folder)
            workers.append(job)
            for _ in range(15):
                if ssh(HOSTS[node],f"test -s {remote}/{node}-ready.json && echo ready || true")=="ready":
                    break
                if status(job)["phase"]=="finished":
                    raise RuntimeError("worker exited before readiness")
                time.sleep(1)
            else:
                raise RuntimeError("worker readiness not established")
        config=dict(listen="10.212.13.2:29682",advertise_addr="11.0.4.47:29682",
                    candidates=[dict(node_id=n,address=f"{IPS[n]}:{29721 if n=='nj1' else 29722}",total_cpu=4,network_ms=1 if n=='nj1' else 65) for n in ("nj1","nj2")],
                    workload=work,output_dir=remote+"/tide-output",resource_path=remote+"/origin-resources.jsonl",
                    qos_ms=135,warmup_qos_ms=2000,execution_ms=26,node_execution_ms=plan["worker_execution_ms"],
                    runs=[dict(id=name,rate=1,pattern="steady",repeat=cell["repeat"],seed=20261009,duration_ms=cell["window_ms"],payload_bytes=0,weight_name="equal",weights=[1,1,1],trace_path=REMOTE+"/traces/"+name+".jsonl")])
        save(folder/"origin.json",config)
        copy("NJ-k1",folder/"origin.json",remote+"/origin.json",pull=False)
        job=launch("NJ-k1",name+"/origin",dict(
            argv=["ip","netns","exec","t13-nj02","taskset","-c","12,13",BINARY,"-test.run=^TestIssue5OriginProcess$","-test.timeout=12m","-test.v"],
            env=dict(TMC_ISSUE5_ORIGIN_CONFIG=remote+"/origin.json",TMC_ISSUE12_IMAGE_DIR=ROOT+"/image-service/v2/inputs",TMC_ISSUE12_PRECONNECT="1",GOMAXPROCS="2")),folder)
        outcome=wait(job,name,"tide")
        copy("NJ-k1",job[1],folder,pull=True)
        if outcome["exit_code"]:
            raise RuntimeError(f"Tide exited {outcome['exit_code']}")
        collect_treatment(cell,folder,"tide",job)
    finally:
        stop_workers(cell,folder)


def kubernetes(cell,folder):
    name=cell["id"]
    remote=f"{REMOTE}/{name}"
    job=launch("NJ-k1",name+"/http-client",dict(argv=[
        "ip","netns","exec","t13-nj02","taskset","-c","12,13","python3",ROOT+"/issue12_image_client.py",
        "--trace",REMOTE+"/traces/"+name+".jsonl","--inputs",ROOT+"/image-service/v2/inputs",
        "--window-ms",str(cell["window_ms"]),"--channels","16","--out",remote+"/k8s-output"],env={}),folder)
    outcome=wait(job,name,"k8s")
    copy("NJ-k1",job[1],folder,pull=True)
    if outcome["exit_code"]:
        raise RuntimeError(f"K8s client exited {outcome['exit_code']}")
    collect_treatment(cell,folder,"k8s",job)


def audit(cell,folder,plan):
    trace=[json.loads(s) for s in (LOCAL/"traces"/(cell["id"]+".jsonl")).read_text().splitlines()]
    results={}
    for system,path in (("tide",folder/"tide-output"/cell["id"]),("k8s",folder/"k8s-output")):
        rows=[json.loads(s) for s in (path/"requests.jsonl").read_text().splitlines()]
        by_id={r["id"]:r for r in rows}
        if len(by_id)!=len(trace) or len(rows)!=len(trace):
            raise RuntimeError("request count or uniqueness mismatch")
        for image_index in range(4):
            subset=[r for i,r in enumerate(trace) if (i//2)%4==image_index]
            actual=[by_id[r["id"]] for r in subset]
            meta=plan["image_calibration"][0]["manifest"]["inputs"][image_index]
            audit_rows(subset,actual,cell["window_ms"],int(meta["checksum"]),135)
            if any(r["input_sha256"]!=meta["input_sha256"] or r["input_bytes"]!=meta["bytes"] for r in actual):
                raise RuntimeError("image payload mismatch")
        on_time=sum(r["on_time"] for r in rows)
        summary=json.loads((path/"run.json").read_text())
        if (summary["on_time"]!=on_time or summary["success"]!=sum(r["status"]=="success" for r in rows)
                or summary["trace_sha256"]!=hashlib.sha256((LOCAL/"traces"/(cell["id"]+".jsonl")).read_bytes()).hexdigest()):
            raise RuntimeError("reported summary/trace hash differs from raw evidence")
        results[system]=dict(offered=len(rows),success=sum(r["status"]=="success" for r in rows),on_time=on_time,
                             yield_fraction=on_time/len(rows),goodput_rps=on_time*1000/cell["window_ms"],
                             statuses=dict(Counter(r["status"] for r in rows)),
                             destinations=dict(Counter(r.get("receiver",r.get("endpoint","none")) for r in rows)))
    save(folder/"audit.json",results)
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("prepare","run"))
    p.add_argument("--resume",action="store_true")
    a=p.parse_args()
    plan=prepare() if not (LOCAL/"plan.json").exists() else json.loads((LOCAL/"plan.json").read_text())
    if a.action=="prepare":
        print(LOCAL/"plan.json")
        return
    controller_lock=(LOCAL/"controller.flock").open("a")
    fcntl.flock(controller_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    if a.resume:
        if not (LOCAL/"launch.lock").exists():
            raise RuntimeError("no previous launch to resume")
        save(LOCAL/f"controller-recovery-{time.time_ns()}.json",dict(
            controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            previous_progress=json.loads((LOCAL/"progress.json").read_text())))
    else:
        with (LOCAL/"launch.lock").open("x") as f:
            f.write(str(time.time_ns()))
    results=[]
    monitors=[]
    try:
        for node in ("nj1","nj2"):
            name="monitor-"+node
            job=("NJ-k1",REMOTE+"/"+name,LOCAL/name)
            current=status(job)
            if current["phase"] == "running":
                monitors.append(job)
                continue
            if current["phase"] == "finished":
                copy(job[0],job[1],LOCAL,pull=True)
                name+=f"-recovery-{int(time.time())}"
            elif current["phase"] != "absent":
                raise RuntimeError("monitor state requires inspection")
            monitors.append(launch("NJ-k1",name,dict(argv=["ip","netns","exec","t13-nj02",
                "ping","-D","-n","-i","1","-w","7200",IPS[node]],env={}),LOCAL))
        save(LOCAL/f"monitor-segments-{time.time_ns()}.json",[list(job[:2]) for job in monitors])
        for cell in plan["cells"]:
            folder=LOCAL/"raw"/cell["id"]
            for system in cell["order"]:
                job_name="origin" if system=="tide" else "http-client"
                job=("NJ-k1",f"{REMOTE}/{cell['id']}/{job_name}",folder/job_name)
                current=status(job)
                action=recovery_action(current)
                if action!="launch":
                    save(folder/(system+"-recovery-check.json"),dict(job_status=current,environment=preflight(plan)))
                    try:
                        if action=="observe":
                            current=wait(job,cell["id"],system)
                        recovery_action(current)
                        collect_treatment(cell,folder,system,job)
                    finally:
                        if system=="tide":
                            stop_workers(cell,folder)
                    save(folder/(system+"-recovery.json"),dict(retained_original=True,exit=current,
                        recovered_unix_ns=time.time_ns(),late_post_network_snapshot=True,
                        reason="control-side transfer direction corrected; application not rerun"))
                    continue
                save(folder/(system+"-preflight.json"),preflight(plan))
                progress(phase="starting",cell=cell["id"],system=system)
                native(cell,folder,plan) if system=="tide" else kubernetes(cell,folder)
                save(folder/(system+"-after-network.json"),json.loads(ssh("NJ-k1",f"python3 {ROOT}/issue12_nj_netns.py status --run-id nj02")))
                time.sleep(10)
            results.append(dict(cell=cell,results=audit(cell,folder,plan)))
            save(LOCAL/"completed-pairs.json",results)
            progress(phase="pair_complete",cell=cell["id"],results=results[-1]["results"])
        progress(phase="matrix_complete",pairs=len(results))
    except BaseException as error:
        progress(phase="controller_stopped",error=f"{type(error).__name__}: {error}")
        raise
    finally:
        errors=[]
        for job in monitors:
            try:
                stop_collect(job,LOCAL)
            except Exception as error:
                errors.append(str(error))
        if errors:
            save(LOCAL/"monitor-cleanup-errors.json",errors)


if __name__=="__main__":
    main()
