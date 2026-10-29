#!/usr/bin/env python3
"""Summarize independent Fig. 5 runs, including the complete response drain."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics


def percentile(values, q):
    values = sorted(values)
    index = (len(values)-1)*q
    lo = int(index)
    hi = min(lo+1, len(values)-1)
    return values[lo] + (values[hi]-values[lo])*(index-lo)


def summarize_rows(rows, duration_sec):
    if not rows:
        raise ValueError("empty request trace")
    if len({r['req_id'] for r in rows}) != len(rows):
        raise ValueError("duplicate request IDs")
    for r in rows:
        start, end = int(r['t_submit_ns']), int(r['t_reply_ns'])
        if start < 0 or end < start or abs(float(r['latency_ms'])-(end-start)/1e6) > .002:
            raise ValueError("inconsistent request timestamp/latency")
    lat = [float(r['latency_ms']) for r in rows if int(r['status']) == 200]
    if not lat:
        raise ValueError("no successful request: report this failure, do not draw an empty latency bar")
    window = max(float(duration_sec), max(int(r['t_reply_ns']) for r in rows)/1e9)
    return dict(n_total=len(rows), n_success=len(lat), n_failed=len(rows)-len(lat),
                observation_sec=window, avg_ms=statistics.mean(lat),
                p95_ms=percentile(lat,.95), std_ms=statistics.pstdev(lat),
                throughput=len(lat)/window, success_yield=len(lat)/len(rows))


def write_csv(path, rows):
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    plan=json.loads((args.root/'plan.json').read_text())
    results=[]
    for case in plan['cases']:
        path=args.root/case['cell']/f"rps{case['rps']}"/f"run{case['repeat']}"
        manifest=json.loads((path/'manifest.json').read_text())
        assert manifest['status']=='completed'
        assert hashlib.sha256((path/'raw.csv').read_bytes()).hexdigest()==manifest['raw_sha256']
        rows=list(csv.DictReader((path/'raw.csv').open()))
        assert len(rows)==case['rps']*plan['duration_sec']
        result=dict(case)
        result.update(summarize_rows(rows,plan['duration_sec']))
        results.append(result)
    cells=[]
    # Student-t .975 quantiles for df=1..4. The protocol fixes five repetitions;
    # smaller pilot summaries remain identifiable through the runs column.
    critical={2:12.706204736,3:4.302652730,4:3.182446305,5:2.776445105}
    for rate in [1,2,4,8,16,32]:
        for cell in [f'C{i}' for i in range(1,7)]:
            runs=[r for r in results if (r['rps'],r['cell'])==(rate,cell)]
            assert len(runs)==plan['repeats']
            row=dict(cell=cell,rps=rate,runs=len(runs),n_total=sum(r['n_total'] for r in runs),n_failed=sum(r['n_failed'] for r in runs))
            for metric in ['avg_ms','p95_ms','std_ms','throughput','success_yield']:
                vals=[r[metric] for r in runs]
                row[metric+'_mean']=statistics.mean(vals)
                row[metric+'_ci95_half']=critical[len(vals)]*statistics.stdev(vals)/math.sqrt(len(vals)) if len(vals)>1 else 0
            cells.append(row)
    out=args.root/'summary'
    out.mkdir(exist_ok=True)
    write_csv(out/'per_run.csv',results)
    write_csv(out/'per_cell.csv',cells)
    save={'runs':len(results),'requests':sum(r['n_total'] for r in results),'failed':sum(r['n_failed'] for r in results),'definition':plan['throughput_window']}
    (out/'audit.json').write_text(json.dumps(save,indent=2)+'\n')
    print(json.dumps(save))


if __name__=='__main__': main()
