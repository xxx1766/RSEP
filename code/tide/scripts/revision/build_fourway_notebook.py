#!/usr/bin/env python3
"""Build a code-only measured-data notebook and matching Python plot program.

Run from any directory with the analysis dependencies installed::

    python scripts/revision/build_fourway_notebook.py

The notebook reads the audited four-system CSVs. This script creates no
experimental observations and never substitutes synthetic values.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "results-revision-20260928" / "fourway-analysis-01"
DEST = ROOT / "scripts" / "fourway_measured_figs.ipynb"
DEST_PY = ROOT / "scripts" / "fourway_measured_plot.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    audit = json.loads((DATA / "audit.json").read_text())
    mixed_audit = json.loads((DATA / "mixed_audit.json").read_text())
    if (audit.get("status"), audit.get("runs"), audit.get("raw_rows")) != ("passed", 144, 21600):
        raise SystemExit("The homogeneous four-system audit is missing or incomplete")
    if (mixed_audit.get("status"), mixed_audit.get("runs"), mixed_audit.get("raw_rows")) != ("passed", 36, 10080):
        raise SystemExit("The mixed-QoS four-system audit is missing or incomplete")
    hashes = {name: sha256(DATA / name) for name in ("per_cell.csv", "mixed_per_cell.csv")}

    cells: list[dict] = []

    def md(source: str) -> None:
        cells.append({"cell_type": "markdown", "metadata": {}, "source": source.strip("\n").splitlines(keepends=True)})

    def code(source: str) -> None:
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                      "outputs": [], "source": source.strip("\n").splitlines(keepends=True)})

    md("""# RSEP 大修 — 四系统实测图

沿用旧版 `gen_placeholder_notebook.py` 的 Matplotlib 双面板、120 DPI、英文图内标签及 PNG 导出格式。运行代码单元时**直接由 CSV 绘制**，不读取或插入现成图片；图中所有性能数值来自本次四系统实验的审核后 CSV，**不是占位数据**。

- 同质负载：Tide、Bare、Ray Serve、KubeEdge × CPU/内存访问/同步 I/O × 5/10/20/40 req/s，各 3 次，共 144 次运行和 21,600 个请求。
- 混合 QoS：四系统 × 总速率 20/40/80 req/s × 3 次；100 ms 与 500 ms 请求各占一半，共 36 次运行和 10,080 个请求。
- 图中误差棒是审计汇总表的运行间均值 95% 置信区间；`n=3`。按时 goodput 是在测量窗口内且符合该请求 deadline 的完成数除以窗口时长。

四系统均在 NJ-k1 单机上测量。Ray Serve 使用单个串行 replica；KubeEdge 的 EdgeCore 运行于隔离的 kind 集群。请勿据此推断跨节点扩缩容、故障恢复或完整系统资源占用。Bare/Ray 与 Tide/KubeEdge 的 I/O 存储路径不同。
""")

    setup = r'''import hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Start Jupyter from the repository tree, or run the Python file from anywhere.
anchors = [Path.cwd()]
if "__file__" in globals():
    anchors.append(Path(__file__).resolve().parent)
candidates = list(dict.fromkeys(p for anchor in anchors for p in (anchor, *anchor.parents)))
ROOT = next((p for p in candidates if (p / "results-revision-20260928/fourway-analysis-01").is_dir()), None)
if ROOT is None:
    ROOT = next((p / "code/Tide" for p in candidates
                 if (p / "code/Tide/results-revision-20260928/fourway-analysis-01").is_dir()), None)
if ROOT is None:
    raise FileNotFoundError("Open this notebook from the repository or code/Tide tree")
DATA = ROOT / "results-revision-20260928/fourway-analysis-01"
OUT = DATA / "notebook_figures"
OUT.mkdir(exist_ok=True)

expected_sha256 = __HASHES__
for name, expected in expected_sha256.items():
    actual = hashlib.sha256((DATA / name).read_bytes()).hexdigest()
    assert actual == expected, f"CSV changed since notebook generation: {name}"
audit = json.loads((DATA / "audit.json").read_text())
mixed_audit = json.loads((DATA / "mixed_audit.json").read_text())
assert (audit["status"], audit["runs"], audit["raw_rows"]) == ("passed", 144, 21600)
assert (mixed_audit["status"], mixed_audit["runs"], mixed_audit["raw_rows"]) == ("passed", 36, 10080)
cell = pd.read_csv(DATA / "per_cell.csv")
mixed = pd.read_csv(DATA / "mixed_per_cell.csv")
assert len(cell) == 288 and len(mixed) == 120

plt.rcParams.update({"figure.dpi": 120, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.3, "axes.axisbelow": True,
                     "axes.unicode_minus": False})
systems = ["bare", "kubeedge", "ray", "tide"]
labels = {"bare": "Bare", "kubeedge": "KubeEdge", "ray": "Ray Serve", "tide": "Tide"}
colors = {"bare": "tab:blue", "kubeedge": "tab:orange",
          "ray": "tab:green", "tide": "tab:red"}

def result(table, metric, **keys):
    part = table[table.metric == metric]
    for key, value in keys.items():
        part = part[part[key] == value]
    assert len(part) == 1, (metric, keys, len(part))
    return part.iloc[0]

def finish_figure(fig):
    if plt.get_backend().lower() == "agg":
        plt.close(fig)
    else:
        plt.show()

print(f"Verified: {audit['runs']} homogeneous and {mixed_audit['runs']} mixed runs; "
      f"{audit['raw_rows'] + mixed_audit['raw_rows']} raw requests")
'''.replace("__HASHES__", repr(hashes))
    code(setup)

    md("""## Fig D1 — 三类同质负载的按时 goodput

两面板分别固定为 20 和 40 req/s；每组四根柱子对应同一工作负载和相同请求速率。40 req/s 内存访问的 goodput 接近零；请结合 CSV 数值阅读，不要从柱高估计精确差值。置信区间可能因三次运行的 t 区间而跨过零，图面从零起显示。
""")
    code(r'''workloads = ["cpu", "memory", "io"]
workload_labels = ["CPU compute", "Memory access", "Sync I/O"]
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
for panel, rate, letter in zip(ax, [20, 40], "ab"):
    x = np.arange(len(workloads))
    width = 0.2
    for k, system in enumerate(systems):
        rows = [result(cell, "on_time_goodput", system=system, workload=workload, rps=rate)
                for workload in workloads]
        mean = [r["mean"] for r in rows]
        yerr = np.array([[max(0.0, r["mean"] - r["ci95_low"]) for r in rows],
                         [max(0.0, r["ci95_high"] - r["mean"]) for r in rows]])
        panel.bar(x + (k - 1.5) * width, mean, width, yerr=yerr, capsize=2,
                  color=colors[system], label=labels[system])
    panel.set_xticks(x)
    panel.set_xticklabels(workload_labels)
    panel.set_ylim(bottom=0)
    panel.set_ylabel("On-time goodput (req/s)")
    panel.set_title(f"({letter}) Offered rate: {rate} req/s")
ax[0].legend(ncol=2, fontsize=8)
fig.suptitle("Fig D1  Heterogeneous workloads [MEASURED]", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "figD1_heterogeneous_4way.png", bbox_inches="tight")
fig.savefig(OUT / "figD1_heterogeneous_4way.pdf", bbox_inches="tight")
finish_figure(fig)
print("40 req/s CPU goodput:", {labels[s]: round(result(cell, "on_time_goodput",
      system=s, workload="cpu", rps=40)["mean"], 2) for s in systems})
''')

    md("""## Fig D2 — 混合 QoS 的两类请求

每个总速率下，100 ms 和 500 ms 请求各占 50%。因此每一类的理论最大 goodput 为总速率的一半。单独画两类，避免把严格和宽松 deadline 的效果合并成一个看似更好的总量。图中 100 ms 类的损失同样是实测结果。
""")
    code(r'''rates = [20, 40, 80]
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
for panel, qos_ms, letter in zip(ax, [100, 500], "ab"):
    for system in systems:
        rows = [result(mixed, "on_time_goodput", system=system, rps=rate, qos_ms=qos_ms)
                for rate in rates]
        means = np.array([r["mean"] for r in rows])
        yerr = np.array([[max(0.0, r["mean"] - r["ci95_low"]) for r in rows],
                         [max(0.0, r["ci95_high"] - r["mean"]) for r in rows]])
        panel.errorbar(rates, means, yerr=yerr, marker="o", linewidth=1.8,
                       capsize=3, color=colors[system], label=labels[system])
    panel.plot(rates, np.array(rates) / 2, ":", color="black", label="Offered / 2")
    panel.set_xticks(rates)
    panel.set_ylim(bottom=0)
    panel.set_xlabel("Total offered rate (req/s)")
    panel.set_ylabel("On-time goodput (req/s)")
    panel.set_title(f"({letter}) {qos_ms} ms deadline")
ax[1].legend(ncol=2, fontsize=8)
fig.suptitle("Fig D2  Mixed-QoS request classes [MEASURED]", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "figD2_mixed_qos_4way.png", bbox_inches="tight")
fig.savefig(OUT / "figD2_mixed_qos_4way.pdf", bbox_inches="tight")
finish_figure(fig)
for qos_ms in [100, 500]:
    print(f"{qos_ms} ms, total 80 req/s:", {labels[s]: round(result(mixed,
          "on_time_goodput", system=s, rps=80, qos_ms=qos_ms)["mean"], 2)
          for s in systems})
''')

    md("""## Fig D3 — CPU 负载的吞吐与完成请求延迟

左图为按时 goodput，右图为**成功请求条件下**的 p95 延迟。失败、拒绝及未完成请求不进入右图，所以判断 deadline 效果必须同时看左图。p95 的 95% 置信区间按三个运行的汇总 p95 计算。
""")
    code(r'''rates = [5, 10, 20, 40]
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
for panel, metric, ylabel, letter in zip(
        ax, ["on_time_goodput", "p95_ms"],
        ["On-time goodput (req/s)", "Successful-request p95 (ms)"], "ab"):
    for system in systems:
        rows = [result(cell, metric, system=system, workload="cpu", rps=rate)
                for rate in rates]
        means = np.array([r["mean"] for r in rows])
        yerr = np.array([[max(0.0, r["mean"] - r["ci95_low"]) for r in rows],
                         [max(0.0, r["ci95_high"] - r["mean"]) for r in rows]])
        panel.errorbar(rates, means, yerr=yerr, marker="o", linewidth=1.8,
                       capsize=3, color=colors[system], label=labels[system])
    panel.set_xticks(rates)
    panel.set_ylim(bottom=0)
    panel.set_xlabel("Offered rate (req/s)")
    panel.set_ylabel(ylabel)
    panel.set_title(f"({letter}) {ylabel}")
ax[0].plot(rates, rates, ":", color="black", label="Offered rate")
ax[0].legend(ncol=2, fontsize=8)
fig.suptitle("Fig D3  CPU workload: deadline and latency [MEASURED]", fontsize=12)
fig.tight_layout()
fig.savefig(OUT / "figD3_cpu_4way.png", bbox_inches="tight")
fig.savefig(OUT / "figD3_cpu_4way.pdf", bbox_inches="tight")
finish_figure(fig)
''')

    md("""## 数据与图的复现

运行时两个审计 JSON 和两个按单元汇总的 CSV 会被验证；CSV SHA-256 固定在首个代码单元。逐个运行 notebook 代码单元，或执行同源的 `scripts/fourway_measured_plot.py`，可直接绘图并将 PNG 保存到 `results-revision-20260928/fourway-analysis-01/notebook_figures/`。`scripts/revision/build_fourway_notebook.py` 仅重新生成 notebook 和 Python 绘图程序，不预先运行或嵌入图片。若更换实验数据，先重新审计并重新生成 notebook。

`per_cell.csv` 和 `mixed_per_cell.csv` 的误差棒是基于三次独立运行的 95% 置信区间，绝非每请求的置信区间。I/O 对照的宿主机与容器存储路径不同，因此 I/O 图仅代表本次配置。
""")

    for index, cell_item in enumerate(cells):
        cell_item["id"] = f"cell-{index:02d}"
    notebook = {
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python", "version": "3"}},
        "nbformat": 4, "nbformat_minor": 5,
    }

    script = '#!/usr/bin/env python3\n"""Draw the audited four-system figures from CSV data.\n"""\n\n'
    script += '\n\n'.join('# %%\n' + ''.join(cell_item['source'])
                          for cell_item in cells if cell_item['cell_type'] == 'code')
    script += '\n'
    DEST_PY.write_text(script)
    DEST.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n")
    print(f"Wrote code-only {DEST} and {DEST_PY}")


if __name__ == "__main__":
    main()
