# E9 — Ten-level capacity sweep

**Paper item:** Supplementary capacity-sweep tables (main-text Fig. 5 uses only
the low/high subset from E1).

## Design

- 4 systems × 3 kernels × 3 arrival patterns × 10 offered-load levels
  (10–100% of `C_w`) × 5 runs.
- 1 800 published treatments / 3 600 origin runs in the export.
- Same worker budgets and deadlines as E1.

## Files

- `data/summaries/supplement_capacity_sweep/{cells,runs}.csv`
- `data/summaries/supplement_capacity_sweep/{audit,manifest}.json`
- Driver: `code/tide/scripts/run-matrix.sh`

## Note

The 72-cell low/high export used by the main figure is in
`data/summaries/fig05_cluster_workloads/`. The sweep is kept separate so that
the main figure and the supplementary range can be checked independently.
