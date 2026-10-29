#!/usr/bin/env python3
"""Draw the audited four-system figures from CSV data.
"""

# %%
import hashlib, json
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

expected_sha256 = {'per_cell.csv': '521adb50bf1fb81c7ce240760a9254040f7ddee6601f83c782dd1038323be9ca', 'mixed_per_cell.csv': 'f0a37adb236835664067fd990a14d101da2f450bc96fe9a9baacfd4bf92e4586'}
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

# %%
workloads = ["cpu", "memory", "io"]
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

# %%
rates = [20, 40, 80]
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

# %%
rates = [5, 10, 20, 40]
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
