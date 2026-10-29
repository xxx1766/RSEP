# Independent workload/estimator reconstruction diagnostic

The original distributed YOLOX function and sliding-window implementation were not recovered. These results use a declared single-application reconstruction and do not reproduce or refute the historical cross-platform figures. All 24 frozen treatments and the earlier 33-run campaign are retained.

Both modes use the same native source, matched 100-ms feedback transport, 350-ms deadline, twelve application plus four control vCPUs per node, one origin, unshaped LAN and cold function inventory. One/ten actual inferences are workload contrasts, not assertions about the original function.

| Inferences/request | Offered req/s | Estimator | Goodput req/s (mean ±95% half-width) | Yield % | Mean latency ms | Final 90%-arrival goodput |
|---:|---:|---|---:|---:|---:|---:|
| 1 | 25 | fixed | 25.000 ± 0.000 | 100.00 | 31.47 | 25.000 |
| 1 | 25 | sliding | 25.000 ± 0.000 | 100.00 | 33.52 | 25.000 |
| 1 | 40 | fixed | 40.000 ± 0.000 | 100.00 | 37.97 | 40.000 |
| 1 | 40 | sliding | 40.000 ± 0.000 | 100.00 | 35.51 | 40.000 |
| 10 | 25 | fixed | 9.789 ± 31.706 | 39.16 | 374.00 | 9.679 |
| 10 | 25 | sliding | 20.444 ± 8.588 | 81.78 | 299.88 | 20.864 |
| 10 | 40 | fixed | 6.411 ± 13.366 | 16.03 | 685.54 | 6.704 |
| 10 | 40 | sliding | 19.311 ± 6.818 | 48.28 | 406.16 | 19.914 |

## Paired estimator effects

| Inferences/request | Offered req/s | Sliding − fixed goodput (req/s, mean ±95% half-width) | All three paired differences |
|---:|---:|---:|---|
| 1 | 25 | 0.000 ± 0.000 | 0.000, 0.000, 0.000 |
| 1 | 40 | 0.000 ± 0.000 | 0.000, 0.000, 0.000 |
| 10 | 25 | 10.656 ± 23.475 | -0.033, 14.100, 17.900 |
| 10 | 40 | 12.900 ± 16.705 | 13.567, 5.867, 19.267 |

## Interpretation

All one-inference runs have 100% observed yield at both inputs. Ten-inference mean goodput is 9.789/6.411 req/s with fixed estimates and 20.444/19.311 with sliding estimates at 25/40 req/s. All three paired differences at 40 req/s are positive; the first at 25 req/s is slightly negative. Both ten-inference pointwise paired 95% intervals include zero. The three blocks therefore do not establish a stable population-level benefit. Large run variation is retained, and zero observed variation is not a future guarantee.

The reconstructed workload and estimate-update mechanism materially change these declared conditions, but neither inference count identifies the original distributed function. Existing Fig. 6 and 4+12 ablation data remain unchanged. No new Tide-only ratio against historical KubeEdge/Ray values is constructed.

Block 2, ten inferences, 25 req/s, fixed: the SSH control receipt was lost after measurement completed. The original 750 valid requests, execution samples and complete measurement records were recovered and verified; goodput 3.90 req/s is retained. No measured cohort was replayed. The underlying transport cause is unresolved and is not asserted to explain the low goodput. Only SSH keepalives changed for the originally unstarted remainder; see protocol/transport-recovery-addendum.json.

## Accounting and limits

```json
{
  "runs": 24,
  "offers": 23400,
  "valid_successes": 23400,
  "failures": 0,
  "timely": 16736,
  "validated_forward_passes": 128700,
  "estimate_observations": 23400,
  "feedback_error_rounds": 0,
  "invalid_processor_observations": 0
}
```

Every request/forward count is checked against output hashes and per-execution witnesses. Each seeded mean is rederived from ordered native completion samples; remote feedback and CPU sampling failures are retained. Failed offers remain in denominators. The final 90% view selects by planned arrival index and divides by its matching offered duration; the full cohort remains primary. Effective completion throughput includes all client terminal drain.

The reconstructed arithmetic window has 20 seeded slots and 100-ms experiment-owned transport, not recovered original heartbeat/probe code. It does not implement interference recovery probes, multitenant windows or cloud admission control. The finite 12-vCPU cloud and unconditional native fallback remain. The results cannot alone establish CPU/memory-kernel effects, physical-SMT isolation benefits or a current KubeEdge/Ray comparative ratio.

Full logs, hashes, cleanup and all case-level audits are in evidence/. The source/configuration identities and randomized order were frozen before outcomes in protocol/formal-plan.json.
