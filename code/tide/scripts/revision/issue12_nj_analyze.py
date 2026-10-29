#!/usr/bin/env python3
"""Independent request, inference-output and RTT audit for NJ image pairs."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from issue12_audit import audit_rows, percentile


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify_execution(row, expected):
    if (row.get("status") != "success" or row.get("finite") is not True or
            row.get("shape") != [1,3549,85] or any(row.get(k)!=expected[k] for k in
            ("input_sha256","output_sha256","bytes","checksum")) or
            row.get("service_ns",0)<=0 or row.get("completed_unix_ns",0)<row.get("received_unix_ns",1)):
        raise ValueError("server execution does not match the frozen image/tensor reference")


def rtt_window(samples, start, end):
    points=sorted((stamp,value) for stamp,value in samples if start<=stamp<=end)
    times=[start]+[p[0] for p in points]+[end]
    gap=max(b-a for a,b in zip(times,times[1:]))
    values=[p[1] for p in points]
    return dict(samples=len(points),median_ms=statistics.median(values) if values else None,
                p95_ms=percentile(values,0.95),max_observation_gap_s=gap,
                coverage_complete=bool(points) and gap<=5 and len(points)>=max(1,math.floor((end-start)*0.8)))


def read_rtts(base):
    result={"nj1":[],"nj2":[]}
    pattern=re.compile(r"^\[([0-9.]+)\].*time[=<]([0-9.]+) ms")
    for path in base.glob("*/monitor-*/process.log"):
        node="nj1" if path.parent.name.startswith("monitor-nj1") else "nj2"
        for line in path.read_text().splitlines():
            match=pattern.search(line)
            if match:
                result[node].append(tuple(map(float,match.groups())))
    for node in result:
        result[node]=sorted(set(result[node]))
    return result


def read_servers(base):
    index=defaultdict(list)
    for node in ("nj1","nj2"):
        manifest=json.loads((base/"evidence"/node/"image-manifest.json").read_text())
        references={row["input_sha256"]:row for row in manifest["inputs"]}
        for row in jsonl(base/"evidence"/node/"image-requests.jsonl"):
            ident=row.get("request_id","")
            if not ident.startswith("nj-image-"):
                continue
            system="tide" if row["peer"] in ("127.0.0.1","::1") else "k8s"
            if row["status"]=="success":
                verify_execution(row,references[row["input_sha256"]])
            index[(system,ident)].append(dict(node=node,record=row))
    return index


def request_breakdown(rows, pattern, start_unix_ns):
    """Describe replay fidelity and deadline outcomes without assuming causation."""
    drift = [(r["submit_unix_ns"]-start_unix_ns-r["scheduled_offset_ns"])/1e6 for r in rows]
    boundaries = (0,300,600,900,1000) if pattern == "gradual" else (0,400,600,1000)
    segments = []
    for low, high in zip(boundaries, boundaries[1:]):
        selected = [r for r in rows if low <= int(r["id"].rsplit("i",1)[1]) < high]
        segments.append(dict(first_index=low,end_index_exclusive=high,offered=len(selected),
                             success=sum(r["status"]=="success" for r in selected),
                             on_time=sum(r["on_time"] for r in selected)))
    groups = defaultdict(list)
    for row in rows:
        if row["status"] == "success":
            groups[(row.get("receiver",row.get("endpoint")),row["input_bytes"])].append(row)
    completions = []
    for (destination,size), selected in sorted(groups.items()):
        service = [r["worker_response"]["service_ns"]/1e6 for r in selected if "worker_response" in r]
        queue = [r["worker_response"]["queue_ns"]/1e6 for r in selected if "worker_response" in r]
        completions.append(dict(destination=destination,input_bytes=size,success=len(selected),
            on_time=sum(r["on_time"] for r in selected),
            latency_median_ms=statistics.median(r["latency_ms"] for r in selected),
            service_median_ms=statistics.median(service) if service else None,
            queue_median_ms=statistics.median(queue) if queue else None))
    return dict(arrival_drift_ms=dict(min=min(drift),median=statistics.median(drift),
                                     p95=percentile(drift,0.95),max=max(drift)),
                segments=segments,successful_completions_by_destination_and_image=completions)


def analyze_cell(family, cell, plan, servers, rtts):
    folder=family/"raw"/cell["id"]
    trace_path=family/"traces"/(cell["id"]+".jsonl")
    trace=jsonl(trace_path)
    result=dict(family=family.name,cell=cell,systems={})
    windows=[]
    for system,path in (("tide",folder/"tide-output"/cell["id"]),("k8s",folder/"k8s-output")):
        rows=jsonl(path/"requests.jsonl")
        summary=json.loads((path/"run.json").read_text())
        by_id={r["id"]:r for r in rows}
        if len(rows)!=len(trace) or len(by_id)!=len(rows):
            raise ValueError("duplicate/missing request records")
        for image_index in range(4):
            subset=[row for i,row in enumerate(trace) if (i//2)%4==image_index]
            actual=[by_id[row["id"]] for row in subset]
            expected=plan["image_calibration"][0]["manifest"]["inputs"][image_index]
            audit_rows(subset,actual,cell["window_ms"],int(expected["checksum"]),135)
            if any(row["input_sha256"]!=expected["input_sha256"] or row["input_bytes"]!=expected["bytes"] for row in actual):
                raise ValueError("wire image identity/size mismatch")
        on_time=sum(row["on_time"] for row in rows)
        success=sum(row["status"]=="success" for row in rows)
        if (summary["on_time"]!=on_time or summary["success"]!=success or
                summary["trace_sha256"]!=hashlib.sha256(trace_path.read_bytes()).hexdigest() or
                not math.isclose(summary["on_time_goodput_rps"],on_time*1000/cell["window_ms"],rel_tol=1e-10)):
            raise ValueError("published run summary differs from raw request accounting")
        execution_counts=Counter()
        for row in rows:
            executions=servers.get((system,row["id"]),[])
            valid=[entry for entry in executions if entry["record"]["status"]=="success"]
            execution_counts["server_successes"]+=len(valid)
            execution_counts["duplicate_executions"]+=max(0,len(executions)-1)
            for entry in valid:
                if entry["record"]["input_sha256"]!=row["input_sha256"]:
                    raise ValueError("server executed a different image for this request")
                execution_counts["executed_"+entry["node"]]+=1
            if row["status"]=="success":
                expected_node=("nj1" if row.get("receiver")=="issue5-worker-nj1" else "nj2") if system=="tide" else (
                    "nj1" if row["endpoint"].startswith("11.0.4.47:") else "nj2")
                matching=[entry for entry in valid if entry["node"]==expected_node and entry["record"]["checksum"]==row["checksum"]]
                if not matching:
                    raise ValueError(f"client success has no correct server execution: {system} {row['id']}")
                if system=="k8s" and not any(entry["record"]==row["worker_response"] for entry in matching):
                    raise ValueError("HTTP result differs from the worker's retained record")
                execution_counts["verified_client_successes"]+=1
            elif valid:
                execution_counts["executed_but_client_not_successful"]+=1
            else:
                execution_counts["no_successful_server_execution"]+=1
        start,end=summary["start_unix_ns"]/1e9,summary["end_unix_ns"]/1e9
        windows.append((start,end))
        latency=[r["latency_ms"] for r in rows if r["status"]=="success"]
        scheduled_on_time=sum(r["status"]=="success" and
            (r["reply_unix_ns"]-summary["start_unix_ns"]-r["scheduled_offset_ns"])<=135000000 for r in rows)
        result["systems"][system]=dict(offered=len(rows),success=success,on_time=on_time,
            yield_fraction=on_time/len(rows),goodput_rps=on_time*1000/cell["window_ms"],
            scheduled_origin_on_time=scheduled_on_time,
            scheduled_origin_goodput_rps=scheduled_on_time*1000/cell["window_ms"],
            drain_goodput_rps=on_time/(end-start),start_unix_s=start,end_unix_s=end,
            success_latency_median_ms=statistics.median(latency) if latency else None,
            success_latency_p95_ms=percentile(latency,0.95),statuses=dict(Counter(r["status"] for r in rows)),
            execution_audit=dict(execution_counts),rtt={node:rtt_window(samples,start,end) for node,samples in rtts.items()},
            breakdown=request_breakdown(rows,cell["pattern"],summary["start_unix_ns"]))
    windows.sort()
    result["treatment_gap_s"]=windows[1][0]-windows[0][1]
    return result


def aggregate(cells):
    result={}
    for pattern in ("gradual","burst"):
        group=[row for row in cells if row["cell"]["pattern"]==pattern]
        systems={}
        for system in ("tide","k8s"):
            rows=[row["systems"][system] for row in group]
            if not rows:
                continue
            systems[system]=dict(repeats=len(rows),offered=sum(r["offered"] for r in rows),
                on_time_counts=[r["on_time"] for r in rows],
                mean_goodput_rps=statistics.mean(r["goodput_rps"] for r in rows),
                mean_scheduled_origin_goodput_rps=statistics.mean(r["scheduled_origin_goodput_rps"] for r in rows),
                mean_yield=statistics.mean(r["yield_fraction"] for r in rows),
                goodput_min=min(r["goodput_rps"] for r in rows),goodput_max=max(r["goodput_rps"] for r in rows))
        ratio=systems["tide"]["mean_goodput_rps"]/systems["k8s"]["mean_goodput_rps"] if systems.get("k8s",{}).get("mean_goodput_rps") else None
        result[pattern]=dict(systems=systems,ratio_of_mean_goodput=ratio)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",type=Path,required=True)
    p.add_argument("--families",nargs="+",default=["formal01","validation01"])
    p.add_argument("--partial",action="store_true")
    a=p.parse_args()
    servers,rtts=read_servers(a.base),read_rtts(a.base)
    addendum=json.loads((a.base/"recovery-addendum.json").read_text())
    excluded={(r["family"],r["cell"]) for r in addendum["excluded_from_primary"]}
    cells=[]
    for name in a.families:
        family=a.base/name
        plan=json.loads((family/"plan.json").read_text())
        for cell in plan["cells"]:
            folder=family/"raw"/cell["id"]
            if a.partial and not all(path.exists() for path in
                    (folder/"k8s-output"/"run.json",folder/"tide-output"/cell["id"]/"run.json")):
                continue
            row=analyze_cell(family,cell,plan,servers,rtts)
            row["primary"]=(name,cell["id"]) not in excluded
            cells.append(row)
    primary=[r for r in cells if r["primary"]]
    coverage=all(v["coverage_complete"] for r in primary for s in r["systems"].values() for v in s["rtt"].values())
    repeats=Counter(r["cell"]["pattern"] for r in primary)
    complete=(repeats=={"gradual":3,"burst":3} and coverage)
    output=dict(complete=complete,cells=cells,primary=aggregate(primary),all_retained=aggregate(cells),
                primary_network_coverage_complete=coverage,exclusion_addendum=addendum)
    out=a.base/"analysis"
    out.mkdir(exist_ok=True)
    (out/"summary.json").write_text(json.dumps(output,indent=2)+"\n")
    print(json.dumps(dict(complete=complete,primary=output["primary"],network_coverage=coverage),indent=2))
    if not a.partial and not complete:
        raise SystemExit("completion audit not satisfied")


if __name__=="__main__":
    main()
