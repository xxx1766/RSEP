# E3 — Executor lifecycle: cold start versus pre-creation

**Paper item:** Fig. 7 (request-path stage durations), §VI-C.

## Question

How much of the first-request latency is container creation and task setup, and
how much is removed by pre-creating Executors?

## Design

- 30 cold/pre-created paired blocks, 600 requests total.
- 10 timing-toggle paired blocks, 1 020 requests; the first request of each
  session is excluded from the perturbation mean.
- Same cached image in both arms: the comparison isolates request-path and
  lifecycle intervention, not image pull.
- All 30 blocks pass stage-completeness, monotonic-time, stage-additivity,
  Executor-identity, startup-state, CPU-affinity, worker-hash and cleanup
  checks.

## Files

- Summary: `data/summaries/fig07_lifecycle/formal/summary/{lifecycle,
  perturbation,summary,requests}.csv`
- Audit: `data/summaries/fig07_lifecycle/formal/summary/audit.json`
- Per-run traces: `data/summaries/fig07_lifecycle/formal/<block>/trace.json`
- Analysis: `code/tide/scripts/revision/analyze_issue8_latency.py`,
  `plot_issue8_paper.py`

## Results (mean, ms)

| Metric | Value |
|---|---:|
| On-demand first request | 163.41 |
| Pre-created first request | 61.21 |
| Later warm reuse | 31.32 |
| On-demand first container create / task setup | 91.96 |
| task start call | 9.95 |
| create → INIT ready | 104.81 |
| Pre-created first bind / prepare | 38.94 |
| Later bind / prepare | 9.27 |

Paired on-demand − pre-created difference: 102.20 ms, 95% *t* interval
[95.27, 109.12] ms. Probe-on − probe-off: 0.016 ms [−0.25, 0.28].

## Caveats

These are same-image, already-cached measurements. They do not include image
pull, and they are not comparable with first Stub creation, a single bind API,
or historical pilot timings.
