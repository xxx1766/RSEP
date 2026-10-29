#!/usr/bin/env python3
"""Bounded bidirectional network checks, strictly after both image matrices."""
import fcntl
import json
from pathlib import Path
import time

import issue12_nj_matrix as m


def completed_guards():
    guards = []
    for family, count in (("formal01", 6), ("validation01", 1)):
        folder = m.BASE / family
        guard = (folder / "controller.flock").open("a")
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        guards.append(guard)
        progress = json.loads((folder / "progress.json").read_text())
        pairs = json.loads((folder / "completed-pairs.json").read_text())
        expected = {cell["id"] for cell in json.loads((folder / "plan.json").read_text())["cells"]}
        if (progress["phase"] != "matrix_complete" or len(pairs) != count or
                len(expected) != count or {row["cell"]["id"] for row in pairs} != expected):
            raise RuntimeError(f"{family} has not completed")
    return guards


def network():
    return json.loads(m.ssh("NJ-k1", f"python3 {m.ROOT}/issue12_nj_netns.py status --run-id nj02"))


def shape(jitter, loss):
    return m.ssh("NJ-k1", f"python3 {m.ROOT}/issue12_nj_netns.py shape --run-id nj02 --delay-ms 32 --jitter-ms {jitter} --loss-pct {loss}")


def probe(name, reverse):
    folder = m.LOCAL / name
    folder.mkdir()
    remote = m.REMOTE + "/" + name
    prefix = ["ip", "netns", "exec", "t13-nj02"]
    server_host, client_host = ("NJ-k1", "NJ-k2") if reverse else ("NJ-k2", "NJ-k1")
    port = 29682 if reverse else 29684
    bind, target = ("10.212.13.2", "11.0.4.47") if reverse else ("11.0.4.48", "11.0.4.48")
    for host in (server_host, client_host):
        m.ssh(host, f"mkdir -p {remote}")
    common = ["--port", str(port), "--streams", "8", "--seconds", "15", "--max-mib", "256"]
    server_argv = (prefix if reverse else []) + ["python3", m.ROOT + "/issue12_nj_bandwidth.py", "server", bind] + common
    client_argv = ([] if reverse else prefix) + ["python3", m.ROOT + "/issue12_nj_bandwidth.py", "client", target] + common
    server_job = m.launch(server_host, name + "/receiver", dict(argv=server_argv+["--out",remote+"/receiver.json"],env={}),folder)
    try:
        for _ in range(10):
            output = m.ssh(server_host, f"test ! -f {server_job[1]}/process.log || cat {server_job[1]}/process.log")
            if '"ready": true' in output:
                break
            if m.status(server_job)["phase"] == "finished":
                raise RuntimeError("receiver exited before readiness")
            time.sleep(0.5)
        else:
            raise RuntimeError("receiver readiness not observed")
        client_job = m.launch(client_host,name+"/sender",dict(argv=client_argv+["--out",remote+"/sender.json"],env={}),folder)
        for job in (client_job, server_job):
            outcome = m.wait(job,name,"network")
            m.copy(job[0],job[1],folder,pull=True)
            if outcome["exit_code"]:
                raise RuntimeError(f"network probe failed: {job[:2]} {outcome}")
        for host, filename in ((server_host,"receiver.json"),(client_host,"sender.json")):
            m.copy(host,remote+"/"+filename,folder/filename,pull=True)
        receiver = json.loads((folder/"receiver.json").read_text())
        sender = json.loads((folder/"sender.json").read_text())
        if receiver["summary"] != sender["summary"]:
            raise RuntimeError("sender and receiver summaries differ")
        m.save(folder/"network-after.json",network())
        return receiver["summary"]
    finally:
        m.stop_collect(server_job,folder)


def main():
    guards = completed_guards()
    try:
        original_plan=json.loads((m.BASE/"formal01/plan.json").read_text())
        checked=m.preflight(original_plan)
        m.LOCAL=m.BASE/"postcheck01"
        m.REMOTE=m.ROOT+"/postcheck01"
        m.LOCAL.mkdir(exist_ok=False)
        m.save(m.LOCAL/"preflight.json",checked)
        script=Path(__file__).with_name("issue12_nj_bandwidth.py")
        for host in m.HOSTS.values():
            m.copy(host,script,m.ROOT+"/issue12_nj_bandwidth.py",pull=False)
        results={}
        for name,reverse in (("shaped-forward",False),("shaped-reverse",True)):
            results[name]=probe(name,reverse)
            m.save(m.LOCAL/"results.json",results)
        try:
            shape(0,0)
            m.save(m.LOCAL/"capacity-network.json",network())
            for name,reverse in (("capacity-forward",False),("capacity-reverse",True)):
                results[name]=probe(name,reverse)
                m.save(m.LOCAL/"results.json",results)
        finally:
            shape(5,0.1)
            m.save(m.LOCAL/"restored-network.json",network())
        m.save(m.LOCAL/"final-preflight.json",m.preflight(original_plan))
        m.progress(phase="postcheck_complete",results=results)
    finally:
        for guard in guards:
            guard.close()


if __name__ == "__main__":
    main()
