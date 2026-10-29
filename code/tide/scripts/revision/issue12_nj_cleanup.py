#!/usr/bin/env python3
"""Remove only the verified Pods, ConfigMaps and owned emulation network."""
import json
import shlex
import time

import issue12_nj_matrix as m
from issue12_nj_postcheck import completed_guards


def main():
    guards=completed_guards()
    try:
        if not json.loads((m.BASE/"analysis/summary.json").read_text())["complete"]:
            raise RuntimeError("independent experiment audit is not complete")
        if not json.loads((m.BASE/"evidence/collection.json").read_text())["complete"]:
            raise RuntimeError("raw evidence has not been collected")
        if json.loads((m.BASE/"postcheck01/progress.json").read_text())["phase"]!="postcheck_complete":
            raise RuntimeError("network postcheck is incomplete")
        plan=json.loads((m.BASE/"formal01/plan.json").read_text())
        target=m.BASE/"cleanup"
        target.mkdir(exist_ok=False)
        before=m.preflight(plan)
        m.save(target/"preflight.json",before)
        cms=json.loads(m.ssh("NJ-k1","kubectl -n tmc-revision get configmap tmc-issue12-nj-image tmc-issue12-nj-image-v2 -o json"))
        expected=json.loads((m.BASE/"evidence/configmaps.json").read_text())
        def identities(items):
            result={}
            for item in items:
                metadata=item["metadata"]
                if metadata["labels"].get("experiment")!="issue12-nj-20261009":
                    raise RuntimeError("experiment ownership label differs")
                result[metadata["name"]]=metadata["uid"]
            return result
        if identities(cms["items"])!=identities(expected["items"]):
            raise RuntimeError("ConfigMap UID drift")
        m.save(target/"identities.json",dict(pods=plan["pod_uids"],configmaps=identities(cms["items"])))
        for kind,names in (("pod",sorted(plan["pod_uids"])),("configmap",sorted(identities(cms["items"])))):
            argv=["kubectl","-n","tmc-revision","delete",kind,*names,"--wait=true","--timeout=45s"]
            result=m.ssh("NJ-k1",shlex.join(argv))
            m.save(target/(kind+"-deletion.json"),dict(argv=argv,stdout=result,completed_unix_ns=time.time_ns()))
        result=m.ssh("NJ-k1",f"python3 {m.ROOT}/issue12_nj_netns.py teardown --run-id nj02")
        m.save(target/"network-deletion.json",json.loads(result))
        m.copy("NJ-k1",m.ROOT+"/network-nj02.json",target/"network-nj02.json",pull=True)
        journal=json.loads((target/"network-nj02.json").read_text())
        if journal["status"]!="removed":
            raise RuntimeError("owned network removal is incomplete")
        commands={"pods":["kubectl","-n","tmc-revision","get","pods",*sorted(plan["pod_uids"]),"--ignore-not-found","-o","name"],
                  "configmaps":["kubectl","-n","tmc-revision","get","configmap",*sorted(identities(cms["items"])),"--ignore-not-found","-o","name"],
                  "namespaces":["ip","netns","list"],"links":["ip","-o","link","show"],
                  "route":["ip","route","show","10.212.13.0/30"],"firewall":["iptables-save"],
                  "shared_eth0":["tc","qdisc","show","dev","eth0"]}
        after={key:m.ssh("NJ-k1",shlex.join(argv)) for key,argv in commands.items()}
        m.save(target/"after.json",after)
        if (after["pods"] or after["configmaps"] or after["route"] or
                "t13-nj02" in after["namespaces"] or "h13nj02" in after["links"] or
                "tide-issue12-nj-nj02" in after["firewall"] or
                after["shared_eth0"].strip()!=before["network"]["live"]["shared_eth0"].strip()):
            raise RuntimeError("owned resources remain or shared eth0 changed")
        m.save(target/"result.json",dict(complete=True,completed_unix_ns=time.time_ns(),
            retained="raw artifacts and imported image caches; no active experiment Pods or emulation network"))
        print(target/"result.json")
    finally:
        for guard in guards:
            guard.close()


if __name__=="__main__":
    main()
