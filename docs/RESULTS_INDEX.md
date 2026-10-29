# Results index

Every reported figure and table, the file that contains its values, the raw
sample that shows the aggregation format, and the script that renders it.

Paths are relative to the repository root.

## Figures

| Fig. | Topic | Processed values | Renderer |
|---|---|---|---|
| 1 | TEC³ / CoT–CoW architecture schematic | no measurement | `figures/rendered/intro/Machine-Thing.pdf` |
| 2 | Motivation: KubeEdge HPA, pod timeline, CPU | illustrative traces from the HPA campaign; not a controlled comparison | `figures/rendered/motivation/` |
| 3 | Connectivity state transition | formal model, no measurement | `figures/rendered/design/state-trans.pdf` |
| 4 | RSEP architecture | formal model, no measurement | `figures/rendered/design/scheduling modle.pdf` |
| 5 | Cluster workloads, low/high load | `data/summaries/fig05_cluster_workloads/{cells,runs}.csv` + `audit.json`, `manifest.json` | `figures/fig6_cluster.py` |
| 6 | Management/runtime CPU and PSS | `data/summaries/fig06_control_pressure/publication/control_fig14_regular.csv` | `figures/compact_remaining_figures.py`, `figures/fig14_management.py` |
| 7 | Request-path stage durations | `data/summaries/fig07_lifecycle/formal/summary/lifecycle.csv` | `figures/rendered/evaluation/issue8-20261005/lifecycle_paper.pdf` (source driver `plot_issue8_paper.py`) |
| 8 | Domain resilience | `data/summaries/fig08_domain_resilience/comparison-v3-results.csv` | `data/summaries/fig08_domain_resilience/plot_cold_domain_returns.py` |
| 9 | WAN placement goodput and deadline yield | `data/summaries/fig09_wan_goodput/summary.json`; `paper/validation/before-goodput-yield-table.tex` | `figures/goodput_yield.py` |
| 10 | Dynamic binding / concurrency | `data/summaries/fig10_dynamic_binding/per_cell.csv` | `figures/rendered/evaluation/fig5_dynamic_binding_2x3.pdf` |
| 11 | Historical ablation | `data/summaries/fig11_12_historical/HISTORICAL_ABLATION_MAIN_VALUES.json` | `figures/historical_ablation_main.py` |
| 12 | Historical latency | `data/summaries/fig11_12_historical/HISTORICAL_LATENCY_MAIN_VALUES.json` | `figures/historical_latency_main.py` |
| 13 | Historical placement shares | `data/summaries/fig11_12_historical/historical_sources/` | `figures/historical_redraw.py` |
| 14 | Historical allocation | same | `figures/historical_redraw.py` |
| 15 | Historical goodput | same | `figures/historical_redraw.py`, `figures/plot_native_threefig.py` |
| 16 | Objective-weight calibration | `data/summaries/fig16_objective_calibration/plot_data.json` | `figures/calibration_panels.py` |

## Tables

| Table | Topic | Values |
|---|---|---|
| I | Comparison with the conference version | `data/summaries/fig09_wan_goodput/summary.json`; full ledger in `paper/supplement_tables/` |
| II | Control/runtime resources | `data/summaries/fig06_control_pressure/publication/control_fig14_regular.csv`, `role-classification/role_summary.csv` |
| III | Held-out scenario means | `data/summaries/fig16_objective_calibration/heldout_summary.csv` |
| IV | Native two-candidate score sensitivity | `data/summaries/fig16_objective_calibration/per_scenario.csv` |
| Notation | Symbols | `paper/supplement_tables/` (not a measurement table) |

## Supplementary tables

`paper/supplement_tables/` contains the LaTeX sources of the 21 supplementary
tables. Their values are drawn from the same summary exports:

| Supplementary group | Source |
|---|---|
| S1, S31–S33 (control totals/components) | `data/summaries/fig06_control_pressure/` |
| S5 (conference ledger) | `data/summaries/fig09_wan_goodput/` |
| S11 (calibration profiles) | `data/summaries/fig16_objective_calibration/` |
| S16–S27 (resource cost) | `data/summaries/fig_resource_cost/` |
| S28–S30 (burst/capacity sweep) | `data/summaries/supplement_capacity_sweep/` |
| S34–S35 (resilience matrix) | `data/summaries/fig08_domain_resilience/` |

## Verifying a number

1. Locate the row in the table above.
2. Open the published CSV/JSON and find the cell/system/rate.
3. Compare with the manuscript value.
4. Optionally re-render the figure; the script checks the SHA-256 of the input
   against `audit.json` or the embedded `*_sha256` field.

For the aggregation schema, `samples.jsonl` in the original runs is one JSON
object per sampling tick, `requests.csv` is one row per request, and
`measurement.json` records the window, rate, deadline and block identity. Those
unaggregated logs are not part of this repository; the processed exports above
are the inputs to every plotted number.
