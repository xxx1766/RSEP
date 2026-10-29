# Experiment protocols

This document is the compact protocol record. The per-experiment README files
carry the same information with file pointers.

## Common rules

1. **Fixed inventory.** Autoscaling is disabled in all controlled comparisons.
   Worker counts and CPU/memory budgets are set before the first request.
2. **Warmup excluded.** Each loaded cell discards its warmup requests; the
   number is recorded per campaign (90 requests in E2).
3. **Paired blocks.** Where the design is paired (E2, E3, E5), systems are run
   in randomized order within a block and the analysis uses paired intervals.
4. **All outcomes retained.** Failed, timed-out and late requests remain in
   the denominator unless a metric explicitly says "successful returns".
5. **No outcome-based cell selection.** Rates and loads were fixed before the
   formal runs. Excluded attempts and their reasons are kept in the campaign
   trees (`excluded-attempts/`, `failed-*`).
6. **Audited exports.** Every published CSV is hashed in an `audit.json`; the
   plotting scripts verify the hash and abort on mismatch.

## E1 / E9 — Cluster workloads

- 4 systems, 3 kernels, 10 load levels, 3 arrival patterns, 5 runs.
- Low/high subset = 72 cells (E1, Fig. 5); full sweep = 1 800 treatments
  (E9, supplementary).
- 10-s windows, 500 ms deadline, 4 workers (2/1/1), 2 vCPU + 512 MiB each.
- Offered rates are integer-rounded from calibrated fractions of `C_w`;
  `--capacity-labels` checks the rounding against the manifest.

## E2 — Control overhead

- 3 systems, 4 rates, 200 ms deadline, 3 randomized blocks, 30-s loaded and
  20-s idle windows, 40 500 formal requests, 90 warmups.
- CPU and PSS are sampled per process and attributed by role; application
  workers are excluded.
- The October-27 role split is post-hoc and does not change the samples.

## E3 — Executor lifecycle

- 30 cold/pre-created blocks and 10 timing-toggle blocks.
- Same cached image in both arms; no image pull is included.
- Stage completeness, monotonicity, additivity, identity, startup state,
  affinity, worker hash and cleanup are checked per block.

## E4 — Domain resilience

- Two edge nodes + cloud; only Edge1 initially holds an executor.
- 2→8 req/s ramp, 150 s run, 500 ms deadline, outage 20–110 s.
- 3 interrupted runs per system, paired with uninterrupted controls.
- `R_srv` includes failures in the denominator; cohort bins are 5 s.

## E5 — WAN placement

- Two workers fixed on NJ-k1 and NJ-k2; Tide vs K8s RR.
- 135 ms deadline, ≈65 ms RTT, gradual and burst arrivals, 3 paired runs.
- The shaper values are configured limits; measured TCP throughput is
  reported alongside.

## E6 — Dynamic binding

- C1–C6, rates 1/2/4/8/12/16, 5 blocks, 10 inferences, 180 s deadline.
- Throughput window includes response drain.

## E7 — Historical re-derivation

- No new runs. Values are extracted from published tables/vectors and hashed;
  `HISTORICAL_*_PROVENANCE.json` records the source and extraction method.

## E8 — Objective calibration

- Restricted simulation, 6 preference profiles, grid over online weights.
- Selection uses calibration traces only; held-out scenarios are evaluation
  only. Ties prefer weights closest to equal, then grid order.

## E10 — Resource cost

- 40 runs, 5 s warm-idle + 20 s offered + 25 s post-offer.
- Cohort goodput over the 20-s arrival window; CPU from owned cgroups, PSS at
  100 ms.
- `U_obs` uses fixed two-vCPU/512-MiB denominators and `α_full = 0.5`.

## Known limitations (stated in the paper)

- Control/runtime cost scopes differ by system and mix management with request
  handling; no algorithm-only ranking is claimed.
- The resilience gain is about activating an idle peer; with both peers
  predeployed the baselines also survive the outage.
- The calibration is a restricted model, not native execution.
- Historical sources have unverified timing scopes and are not pooled with the
  new measurements.
