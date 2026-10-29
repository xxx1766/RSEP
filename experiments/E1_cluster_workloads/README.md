# E1 — Cluster workloads: four systems × three kernels × ten load levels

**Paper item:** Fig. 5 (main text), Supplementary S-tables for the full sweep.

## Question

Under matched application CPU budgets and a fixed deadline, how much on-time
goodput do Tide, Bare, Ray Serve and KubeEdge deliver across CPU, memory-access
and synchronous-I/O kernels, at low and high offered load, under balanced,
hotspot and burst arrivals?

## Design

| Factor | Values |
|---|---|
| Systems | Tide, Bare (direct Go HTTP), Ray Serve 2.58.0, KubeEdge 1.23.1 |
| Kernels | CPU (100 iterations × 65 536 B), memory-access, synchronous I/O |
| Offered load | 10–100% of the calibrated capacity `C_w`, integer-rounded |
| Arrival pattern | balanced, hotspot, burst |
| Repetitions | 5 per cell |
| Window | 10 s measurement windows, 500 ms deadline |
| Workers | 4 prestarted serial workers, 2/1/1 on NJ-k1/NJ-k2/NJ-k3 |
| Budgets | 2 vCPU + 512 MiB per worker; Go parallelism 1 |

The main-text figure uses the low (≈30% of `C_w`) and high (≈80% of `C_w`)
levels; the 10-level sweep is in `data/summaries/supplement_capacity_sweep/`.

## Files

- Design: `data/summaries/fig05_cluster_workloads/manifest.json`
- Published export: `data/summaries/fig05_cluster_workloads/{cells,runs}.csv`
- Audit: `data/summaries/fig05_cluster_workloads/audit.json` (360 treatments,
  720 origin runs, SHA-256 of both exports)
- Full 10-level sweep: `data/summaries/supplement_capacity_sweep/`
- Figure script: `figures/fig6_cluster.py`
- Driver: `code/tide/scripts/run-matrix.sh`,
  `code/tide/scripts/revision/fig6_cluster_*.py`

## Reproduce the figure

```bash
cd figures
python fig6_cluster.py \
  --cells    ../data/summaries/fig05_cluster_workloads/cells.csv \
  --manifest ../data/summaries/fig05_cluster_workloads/manifest.json \
  --audit    ../data/summaries/fig05_cluster_workloads/audit.json \
  --capacity-labels \
  --output   rendered/evaluation/figD1_cluster.pdf
```

## Caveats

The comparison is an integrated-path comparison: control/runtime footprints
differ between systems, and the four systems do not share an identical routing
implementation. The paper states this explicitly. Burst cells are retained in
the supplementary tables, including non-monotonic fixed-window responses.
