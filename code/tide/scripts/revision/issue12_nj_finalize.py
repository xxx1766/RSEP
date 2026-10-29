#!/usr/bin/env python3
"""Wait for paired runs, then perform postchecks, collection and independent audit."""
import json
from pathlib import Path
import subprocess
import sys
import time

import issue12_nj_matrix as m
from issue12_nj_postcheck import completed_guards


def report(phase, **fields):
    row=dict(phase=phase,observed_unix_ns=time.time_ns(),**fields)
    m.save(m.BASE/"finalization-status.json",row)
    print(json.dumps(row),flush=True)


def main():
    while True:
        ready=True
        states={}
        for family in ("formal01","validation01"):
            path=m.BASE/family/"progress.json"
            state=json.loads(path.read_text()) if path.exists() else {"phase":"queued"}
            states[family]=state
            if state["phase"]=="controller_stopped":
                raise RuntimeError(f"{family} stopped; inspect its existing job records")
            ready=ready and state["phase"]=="matrix_complete"
        if ready:
            try:
                guards=completed_guards()
            except BlockingIOError:
                pass
            else:
                for guard in guards:
                    guard.close()
                break
        report("waiting_for_matrices",matrices=states)
        time.sleep(30)
    scripts=Path(__file__).parent
    stages=[("postcheck",[str(scripts/"issue12_nj_postcheck.py")]),
            ("collection",[str(scripts/"issue12_nj_collect.py")]),
            ("audit",[str(scripts/"issue12_nj_analyze.py"),"--base",str(m.BASE)])]
    for name,argv in stages:
        report(name)
        with (m.BASE/("final-"+name+".log")).open("x") as output:
            subprocess.run([sys.executable,*argv],stdout=output,stderr=subprocess.STDOUT,check=True)
    report("audit_complete",remaining=["review final evidence","owned-resource cleanup","paper and reviewer results","final PDF checks"])


if __name__=="__main__":
    try:
        main()
    except BaseException as error:
        report("finalization_stopped",error=f"{type(error).__name__}: {error}")
        raise
