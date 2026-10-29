# E10 — Application resource occupancy and cost

**Paper item:** §VI-F, Supplementary Tables S16–S27.

## Question

At fixed worker budgets, what CPU and resident memory does Tide use relative to
cloud-only execution, and what is the cost per *timely* versus per *valid*
return?

## Design

- CPU kernel at 29 and 77 requests/s; memory-access kernel at 8 and 21
  requests/s.
- 40 runs total; each with 5 s warm-idle, 20 s offered load, 25 s post-offer.
- Cohort goodput = timely cohort returns / 20 s arrival window.
- Owned worker cgroups for CPU time; PSS sampled every 100 ms.
- `U_obs = ½(C_L + M_L) + ½(C_C + M_C)`, with CPU and PSS fractions over fixed
  two-vCPU / 512-MiB denominators; `α_full = 0.5`.

## Files

- Summary: `data/summaries/fig_resource_cost/{cells,per-run,paired-blocks,
  all-attempts,pi-comparisons,alternative-pi-comparisons,
  failed-attempt-statistics}.csv`
- Provenance: `data/summaries/fig_resource_cost/provenance.json`
- Design: `data/summaries/fig_resource_cost/plan.json` (B6/B8/B12 CPU budgets)
- Rendered panel: `data/summaries/fig_resource_cost/cpu-budget.pdf`
- Driver: `code/tide/scripts/revision/` cost campaign scripts

## Headline numbers

At 21 requests/s memory-access:
- Timely returns: 199.8 vs 9.4.
- CPU cost per *timely* return: 0.165 vs 3.522 CPU-s.
- CPU cost per *valid* return: ≈0.078 CPU-s under both policies.
- Total CPU: 32.86 vs 32.81 CPU-s; mean PSS 78.3 vs 49.7 MiB.
- `U_obs`: 0.487 vs 0.272.

## Caveats

The per-timely gap reflects the number of timely returns, not lower total CPU.
The CPU cost per valid return does not establish a consistent reduction. These
measurements estimate `U` at fixed `α_full = 0.5`; they do not validate
model-wide memory management or estimate/vary `α_full`.
