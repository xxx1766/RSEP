# E6 — Dynamic binding and concurrency control

**Paper item:** Fig. 10, §VI-E.

## Question

Within Tide, and without changing physical isolation, how much do concurrency
control and Executor pre-creation contribute at increasing load?

## Design

- Six configurations C1–C6 spanning binding mode and concurrency control.
- Rates 1, 2, 4, 8, 12, 16 requests/s; 10 inferences; 180 s deadline.
- Five independent blocks per cell, all retained.
- Application CPUs 2–7, driver CPUs 0–1; 20 s measurement window with
  response drain included in throughput.

## Files

- Summary: `data/summaries/fig10_dynamic_binding/{per_cell,per_run,
  paired_summary,paired_effects,cc_paired_summary}.csv`
- Audit: `data/summaries/fig10_dynamic_binding/{audit,full-audit}.json`
- Design: `data/summaries/fig10_dynamic_binding/plan.json`
- Figure: `figures/rendered/evaluation/fig5_dynamic_binding_2x3.pdf`
- Driver: `code/tide/scripts/run-matrix.sh`,
  `code/tide/scripts/revision/run_fig5_confirmatory.py`

## Headline

Concurrency control reduces mean latency by 26.3% at 32 requests/s; Executor
pre-creation reduces first-request latency by 62.5%.

## Caveats

These are within-Tide ablations. They do not isolate algorithm from deployment
configuration, and the paper does not attribute the placement gain to any
single mechanism.
