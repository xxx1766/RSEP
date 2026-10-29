# RSEP / Tide — Artifact for the TMC Submission

This repository is the curated artifact accompanying the manuscript **"RSEP: A
Request-Centric Task Scheduling and Resource Allocation Model for the
Things–Edge–Cloud Computing Continuum"** (IEEE Transactions on Mobile
Computing, major revision). It contains the Tide runtime source, the experiment
drivers, and the processed summary tables behind every reported figure and
number.

The artifact is designed for three levels of use:

| Level | What you can do | Needs |
|---|---|---|
| **L1 — Inspect** | Check every number in the paper against an audited CSV/JSON export | nothing |
| **L2 — Redraw** | Regenerate the data figures from the published exports | Python 3.11+, matplotlib, numpy |
| **L3 — Re-run** | Re-execute an experiment | Linux hosts, cgroup v2, containerd, Kubernetes/KubeEdge (see `docs/ENVIRONMENT.md`) |

L1 and L2 are self-contained. L3 requires hardware comparable to the original
three-node testbed; `docs/PROTOCOL.md` records the exact design so a re-run can
be matched cell by cell.

---

## 1. Layout

```
.
├── code/tide/                 Tide runtime (Go), revision drivers, tests
│   ├── cmd/                   daemons, CLI, calibration and revision binaries
│   ├── pkg/                   scheduler, procmgr, stub, executor, dev, ...
│   ├── proto/                 gRPC/protobuf definitions
│   ├── scripts/               experiment drivers and analyzers
│   │   ├── revision/          per-figure drivers (fig5/fig6/fig7, issue12, ...)
│   │   ├── tests/             unit/integration tests for the drivers
│   │   ├── run-matrix.sh      capacity-sweep driver
│   │   └── setup-paper-cgroup.sh
│   └── workload/              kernel definitions
├── experiments/               one directory per experiment family (see §3)
├── data/summaries/            processed exports: the source of every figure/table
├── figures/                   plotting scripts + rendered PDFs
├── paper/                     supplementary table sources and validation inputs
├── docs/                      environment, protocol, results index
├── MANIFEST.sha256            SHA-256 of every tracked file
└── CITATION.cff, LICENSE
```

`data/summaries/<experiment>/` holds the processed export used for plotting:
the CSV/JSON files whose hashes are checked by the plotting scripts, together
with the frozen design (`plan.json`, `manifest.json`) and the audit record.
Unaggregated per-request logs and the complete per-run campaign trees are not
shipped; see §6.

---

## 2. Quick start

### 2.1 Build Tide

```bash
cd code/tide
go build ./...                 # Go 1.22+ (toolchain 1.22.2 in go.mod)
go test ./pkg/... ./cmd/...
```

The Go module path is `github.com/ict/tide` (548 internal imports); it is kept
as-is so the tree builds without a rewrite. If you vendor the code under a new
organization, either keep the module path or add a `replace` directive.

Linux is required for the runtime path (cgroup v2, containerd, cpuset); build
with `CGO_ENABLED=1` on the test host. The portable packages and the offline
calibration binary also build and test on macOS:

```bash
go test ./pkg/schedpolicy ./cmd/revision-calibrate
```

The runtime binaries are:

```bash
go build -o bin/tide ./cmd/tide
go build -o bin/revision-worker ./cmd/revision-worker
go build -o bin/revision-calibrate ./cmd/revision-calibrate
```

### 2.2 Redraw the data figures

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install matplotlib numpy pandas

cd figures
python calibration_panels.py                 # Fig. 16
python goodput_yield.py                      # Fig. 9
python compact_remaining_figures.py          # Figs. 6, 11, 12
python fig6_cluster.py \
    --cells    ../data/summaries/fig05_cluster_workloads/cells.csv \
    --manifest ../data/summaries/fig05_cluster_workloads/manifest.json \
    --audit    ../data/summaries/fig05_cluster_workloads/audit.json \
    --capacity-labels \
    --output   rendered/evaluation/figD1_cluster.pdf     # Fig. 5
```

The scripts verify the SHA-256 of their inputs against the recorded audit
before plotting and abort on any mismatch. Outputs are written to
`figures/rendered/evaluation/`. A `Makefile` wraps the same commands:

```bash
make figures      # all figures that are runnable from the shipped exports
make check        # verify MANIFEST.sha256
```

> The 2026-10-29 snapshot in `figures/rendered/` was produced by these
> commands. `fig6_cluster.py` follows the published 72-cell export; the
> 10-level capacity sweep is kept separately under
> `data/summaries/supplement_capacity_sweep/` and is not used by Fig. 5.

### 2.3 Inspect a processed table

```bash
cd data/summaries/fig06_control_pressure
column -s, -t < publication/control_fig14_regular.csv | head
python3 -m json.tool plan.json | head -40
```

---

## 3. Experiment map

| Directory | Paper item | Design (short) |
|---|---|---|
| `experiments/E1_cluster_workloads` | Fig. 5 | 4 systems × 3 kernels × 2 load levels × 3 arrival patterns, 5 runs |
| `experiments/E2_control_overhead` | Figs. 6, 13, 14; Table II | 3 systems × 4 rates × 3 randomized blocks at 200 ms |
| `experiments/E3_executor_lifecycle` | Fig. 7 | 30 cold/pre-created pairs + 10 timing-toggle pairs |
| `experiments/E4_domain_resilience` | Fig. 8 | 3 runs/condition, 2→8 req/s ramp, cloud outage 20–110 s |
| `experiments/E5_wan_placement` | Fig. 9; Table I | 3 paired runs, gradual/burst, ~65 ms RTT, 135 ms deadline |
| `experiments/E6_dynamic_binding` | Fig. 10 | C1–C6 binding/concurrency configs, 5 runs per cell |
| `experiments/E7_historical` | Figs. 11–15 | Re-derived values from prior publications; no new runs |
| `experiments/E8_objective_calibration` | Fig. 16; Tables III–IV | Offline grid over 6 preference profiles, restricted model |
| `experiments/E9_capacity_sweep` | Supplementary S-tables | 10 load levels × 3 kernels × 3 patterns × 4 systems |
| `experiments/E10_resource_cost` | §VI-F tables | CPU/memory kernels at 29/77 and 8/21 req/s, 40 runs |

Each experiment directory contains a single `README.md` with the question, the
frozen design (rates, deadlines, repeats), the exact file map into
`data/summaries/`, the driver/plotting commands, and the caveats stated in the
paper.

---

## 4. What each claim rests on

The mapping from paper statement to file is in
[`docs/RESULTS_INDEX.md`](docs/RESULTS_INDEX.md). In brief:

- **Placement gain (1.45–1.62×, Table I, Fig. 9)** —
  `data/summaries/fig09_wan_goodput/summary.json` (per-cell offered/success/
  on-time/goodput/RTT audit).
- **Capacity and workload behaviour (Fig. 5)** —
  `data/summaries/fig05_cluster_workloads/{cells,runs}.csv` with the matching
  `audit.json` and `manifest.json`; the 10-level sweep is in
  `supplement_capacity_sweep/`.
- **Control/runtime cost (Figs. 6, 13, 14; Table II)** —
  `fig06_control_pressure/publication/control_fig14_regular.csv` plus the
  post-hoc role reclassification; original October-4 totals are retained under
  `oct04_summary/`.
- **Executor reuse and lifecycle (Fig. 7)** —
  `fig07_lifecycle/formal/summary/{lifecycle,perturbation,requests}.csv`.
- **Domain resilience (Fig. 8)** —
  `fig08_domain_resilience/comparison-v3-results.csv` and the derived panels.
- **Objective calibration (Fig. 16; Tables III–IV)** —
  `fig16_objective_calibration/{plot_data.json,heldout_summary.csv,per_scenario.csv}`.
- **Historical re-derivation (Figs. 11–15)** — `fig11_12_historical/` with the
  original value extracts and `HISTORICAL_*_PROVENANCE.json`.

No result in this repository was regenerated or smoothed for the artifact; the
files are the audited exports used by the manuscript.

---

## 5. Environment and protocol

- `docs/ENVIRONMENT.md` — hosts, kernel, cgroup, Kubernetes/KubeEdge/Ray
  versions, network shaping parameters, kernel binaries.
- `docs/PROTOCOL.md` — per-experiment design, inclusion/exclusion rules, and
  the known limitations that the paper also states.
- `docs/RESULTS_INDEX.md` — figure/table → processed export → renderer.

---

## 6. What is *not* in this repository

| Excluded | Size | Reason |
|---|---|---|
| Per-request logs and full per-run campaign trees (`results-*`) | ~16 GB | Not needed to verify any reported number; the repository ships the processed exports used for plotting. |
| Calibration decision dump (`decisions.csv.gz`) | 91 MB | Inputs and selected outputs are shipped. |
| Full supplementary PDF/TeX build | — | Supplementary table *sources* are in `paper/supplement_tables/`. |
| Container images and model weights | multi-GB | Public upstream images; digests are recorded in each experiment's `plan.json`. |

---

## 7. Integrity

```bash
shasum -a 256 -c MANIFEST.sha256
```

The manifest covers every file except itself. Release state and remaining
publication steps are tracked in [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md). Campaign-level source hashes
(e.g. the worker binary `7a7a6f3a…`) are recorded inside the per-experiment
`plan.json`/`audit.json` files and are cross-checked by the plotting scripts.

---

## 8. License and citation

All code, measurement data and figures in this repository are released under
the Apache License 2.0 (`LICENSE`). If you use this artifact,
please cite the manuscript; see `CITATION.cff`.

This is a single-blind TMC submission, so author identities are not removed.
Internal hostnames, registry addresses and absolute paths have nevertheless
been replaced with documentation placeholders (`<REPO>`, `registry.example.org`,
`198.51.100.0/24`).
