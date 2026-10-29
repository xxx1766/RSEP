#!/usr/bin/env python3
"""Plot F only from saved, independently checked restricted-model results."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.model / "results.csv").open() as f:
        records = list(csv.DictReader(f))
    manifest = json.loads((args.model / "manifest.json").read_text())
    assert len(records) == manifest["instance_count"]
    assert len({r["instance"] for r in records}) == len(records)
    assert all(r["proven_optimal"] == "true" for r in records)
    nodes = sorted({int(r["nodes"]) for r in records})
    sizes = sorted({int(r["tasks"]) for r in records})
    summary = []
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.1), constrained_layout=True)
    for m, color in zip(nodes, ("#2463a6", "#c46b19", "#279368")):
        hm, om, rm, low, high = [], [], [], [], []
        offset = (nodes.index(m) - (len(nodes)-1)/2)*.25
        for n in sizes:
            group = [r for r in records if int(r["nodes"]) == m and int(r["tasks"]) == n]
            h = [int(r["heuristic_accepted"]) for r in group]
            o = [int(r["optimum_accepted"]) for r in group]
            ratios = [float(r["heuristic_over_optimum"]) for r in group]
            assert all(0 <= a <= b for a, b in zip(h, o))
            hm.append(statistics.mean(h)); om.append(statistics.mean(o)); rm.append(statistics.mean(ratios)*100)
            low.append(min(ratios)*100); high.append(max(ratios)*100)
            x = [n+offset+(i-(len(group)-1)/2)*.014 for i in range(len(group))]
            axes[1].scatter(x, [r*100 for r in ratios], s=14, color=color, alpha=.5, zorder=2)
            summary.append(dict(tasks=n,nodes=m,instances=len(group),heuristic_mean=hm[-1],optimum_mean=om[-1],
                                ratio_mean=rm[-1]/100,ratio_min=min(ratios),ratio_max=max(ratios),
                                maximum_gap=max(b-a for a,b in zip(h,o))))
        axes[0].plot(sizes,om,"o--",color=color,markerfacecolor="white",label=f"Exact optimum, {m} nodes")
        axes[0].plot(sizes,hm,"s-",color=color,label=f"Shared-score heuristic, {m} nodes")
        axes[1].plot([n+offset for n in sizes],rm,"D-",color=color,label=f"{m} nodes: mean",zorder=3)
    axes[0].set_ylabel("Mean admitted tasks")
    axes[0].set_title("(a) Identical cardinality objective")
    axes[0].legend(fontsize=8)
    axes[1].set_ylabel("Heuristic / exact optimum (%)")
    axes[1].set_ylim(0,105)
    axes[1].set_title("(b) Every instance and group means")
    axes[1].legend(fontsize=8,loc="lower left")
    for ax in axes:
        ax.set_xlabel("Tasks in batch")
        ax.set_xticks(sizes)
        ax.grid(alpha=.25)
    fig.suptitle(f"Restricted offline model — simulated, {len(records)} instances",fontsize=12)
    for extension in ("pdf","png"):
        fig.savefig(args.out/f"figF_optgap.{extension}",dpi=200,bbox_inches="tight")
    plt.close(fig)
    with (args.out/"figF_summary.csv").open("w") as f:
        w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
    with (args.out/"figF_table.tex").open("w") as f:
        f.write("\\begin{tabular}{rrrrrr}\n\\hline\nTasks & Nodes & Instances & Heuristic & Optimum & Ratio (\\%) \\\\\n\\hline\n")
        for r in summary:
            f.write(f"{r['tasks']} & {r['nodes']} & {r['instances']} & {r['heuristic_mean']:.2f} & {r['optimum_mean']:.2f} & {r['ratio_mean']*100:.1f} \\\\\n")
        f.write("\\hline\n\\end{tabular}\n")
    ratios=[float(r['heuristic_over_optimum']) for r in records]
    caption=(f"Restricted offline batch model, {len(records)} instances. Each task has unit importance; all arrivals are zero and deadlines common within an instance. "
             "Each node has one serial executor with a fixed two-CPU quota and fixed response-network budget. The exact and heuristic methods use identical feasibility constraints and maximize admitted task count. "
             "Service-time scales use measured calibration; node factors, network budgets, task-size variation and deadlines are stipulated model inputs. "
             "Each task-count/node-count group contains 15 generated instances (three capacity-pressure settings and five seeds). Points show individual ratios; lines show means. "
             "The common horizon scales inversely with node count; the two- and three-node curves describe different instance families, not a scaling comparison. "
             f"Mean per-instance ratio is {statistics.mean(ratios)*100:.2f}%, with minimum {min(ratios)*100:.1f}%. These are finite-instance observations, not a general approximation bound or runtime measurements.\n")
    (args.out/"figF_caption.txt").write_text(caption)
    sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in args.model.iterdir() if p.suffix in ('.json','.csv','.md')}
    (args.out/"figF_manifest.json").write_text(json.dumps(dict(evidence_type="restricted offline simulation",sources_sha256=sources,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        regenerate="python scripts/revision/plot_optimum.py --model results-revision-20260928/model-01 --out results-revision-20260928/figures"),indent=2)+'\n')
    print(caption)


if __name__ == "__main__":
    main()
