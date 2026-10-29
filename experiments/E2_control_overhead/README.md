# E2 — Control/runtime overhead at four rates

**Paper item:** Fig. 6 (control pressure), Fig. 13 (request-path stages),
Fig. 14 (management/runtime split), Table II.

## Question

What management and runtime-support cost (CPU, PSS) does each system incur to
deliver the same serial Go kernel at 10/20/40/80 requests/s and a 200 ms
deadline, and how is the request path spent?

## Design

| Factor | Values |
|---|---|
| Systems | Tide, Ray Serve 2.58.0 (serial), KubeEdge 1.23.1 (direct Pod) |
| Host | NJ-k1 only, cgroup v2 |
| Rates | 10, 20, 40, 80 requests/s |
| Deadline | 200 ms |
| Blocks | 3 randomized blocks per cell |
| Window | 30 s loaded; 20 s idle windows |
| Requests | 40 500 formal + 90 warmups |
| Kernel | 100 iterations × 65 536 B, pinned to CPUs 0–1, Go parallelism 1 |

Application workers are excluded from the cost accounting. Tide includes its
integrated daemon, the current worker shim and sampled shared containerd;
KubeEdge includes CloudCore, EdgeCore, API server, scheduler, state and node
management and internal runtimes; Ray includes Serve Controller, GCS,
autoscaler monitor, raylet, launcher and agents. Process-role attribution is
necessarily mixed for agents that combine admission and request handling.

## Files

- Published export: `data/summaries/fig06_control_pressure/publication/`
  (`control_fig14_regular.csv`, `runtime-contrasts.json`, `audit.json`)
- Post-hoc role classification:
  `data/summaries/fig06_control_pressure/role-classification/`
- October-4 original totals: `data/summaries/fig06_control_pressure/oct04_summary/`
- Frozen design: `data/summaries/fig06_control_pressure/plan.json`
- Figure scripts: `figures/compact_remaining_figures.py`,
  `figures/fig14_management.py`, `figures/control_runtime_scope.py`
- Drivers: `code/tide/scripts/revision/{run_control_comparison.py,
  analyze_control_comparison.py, control_metrics.py, ray_serve_baseline.py}`

## Reproduce the figure

```bash
cd figures
python compact_remaining_figures.py     # writes control_pressure_compact.pdf
```

## Caveats

- The valid-return fractions at 80 req/s are 42.44% / 98.25% / 22.93%
  (Tide/Ray/KubeEdge). Failed requests remain in the cost denominator.
- The October-27 samples were reclassified by process role after the fact; no
  measurement was re-run and all original totals are retained.
- Ray's 40 req/s aggregation includes 283.06 percentage points from
  Proxy/Replica, which are reported separately; its queueing anomaly remains
  unexplained.
