# Restricted offline optimum experiment (F)

This command generates **simulated instances**, whose service-time scale uses the measured warm workload p95 in `results-revision-20260928/calibration-01/summary.json`. It does not run the workload, reproduce measured task outcomes, or solve the paper's full Problem 1.

## Restricted model

All tasks arrive at time zero and share one deadline H. Each node has one serial executor with a fixed 2-CPU quota, and each admitted task runs on exactly one node. Every task has importance 1. Service times s[i,j] are known deterministic integer ticks (0.1 ms). Node j has a fixed network response budget d[j], incurred once in each task's completion time, not serialized as executor work. For each used node, feasibility is:

`sum_i assigned(i,j) * s[i,j] + d[j] <= H`.

Any order of the accepted tasks on a node is feasible under this condition, because all service times are positive and every deadline is H. Maximize the total number of accepted tasks. Rejection is allowed and contributes zero. There are no online arrivals, dependencies, preemption, variable quotas, per-task deadlines, stochastic execution times, or bandwidth contention.

The heuristic preserves input order, admits a task whenever some node is feasible, and selects the maximum shared `pkg/schedpolicy.Score`. Features are fixed free CPU fraction 1, predicted completion `(queued service + own service + network) * tick_ms`, and the stipulated device weight. Weights are `(0.3, 0.5, 0.2)` and latency scale is 100 ms. Ties select the lowest node index. Free fraction stays 1 because a fixed executor quota is reused serially; this experiment cannot assess dynamic CPU allocation or the resource feature's effect.

The exact solver independently enumerates assignment and rejection decisions with branch-and-bound. Task sorting changes only search order. Its admissible upper bound gives each remaining task its cheapest currently feasible service cost, then packs those costs into the sum of residual node capacities. This relaxes placement constraints, so it can only overestimate further admissions. There is no timeout or target gap. `proven_optimal=true` is emitted only after the complete search returns. The assignment itself certifies feasibility; optimality additionally relies on the exhaustive algorithm and valid bound, not on the assignment alone.

## Instance design fixed before evaluation

- Task counts: 6, 8, 10, 12; nodes: 2, 3; deadline multipliers: 0.55, 0.75, 0.95; five seeds per combination: 120 instances.
- Seed: `202609280000 + tasks*10000 + nodes*1000 + pressure_index*100 + repetition`, indices zero based. No instances are filtered by result.
- Each task independently draws a workload kind uniformly from CPU/memory/I/O and a size multiplier uniformly from integers 75..125 divided by 100, using Go `math/rand` with the saved seed.
- Measured p95 calibration: CPU 21.283347 ms; memory 78.898112 ms; I/O 59.614709 ms. These are scalar scale references, not claims of measured service under the model's quota or simulated device heterogeneity.
- Node service factors (CPU, memory, I/O): node0 `(1.3, 0.9, 1.15)`; node1 `(0.9, 1.2, 0.85)`; node2 `(0.7, 0.85, 1.1)`. These are stipulated synthetic factors to exercise heterogeneous placement.
- Node network budgets: 2, 12, 35 ms. Device weights: 0.65, 0.8, 1.0. These are stipulated model inputs, not measurements or automatic calibration.
- `s[i,j] = ceil(p95[kind_i] * size_i * factor[j,kind_i] / 0.1)`.
- `H = ceil(multiplier * sum_i(p95[kind_i] * size_i) / nodes / 0.1)` ticks. This sets differing capacity pressure without selecting desired outcomes.

## Reproduce from the Tide module directory

```sh
env GOMODCACHE=/tmp/tmc-go-mod GOCACHE=/tmp/tmc-go-cache go test -race ./cmd/revision-model ./pkg/schedpolicy
env GOMODCACHE=/tmp/tmc-go-mod GOCACHE=/tmp/tmc-go-cache go run ./cmd/revision-model -out /tmp/model-reproduction
env GOMODCACHE=/tmp/tmc-go-mod GOCACHE=/tmp/tmc-go-cache go run ./cmd/revision-model -instances results-revision-20260928/model-01/instances.json -out /tmp/model-replay
```

Run `python3 scripts/revision/model_verify.py` to independently recheck saved certificates, source hashes, and every six-task optimum by complete Python enumeration.

Output directories must be absent or empty. JSON includes all instance inputs, heuristic and optimal task-to-node assignments, search statistics, status, and objectives. CSV supports plotting; summary JSON groups by task and node counts. The manifest records calibration and Go source hashes, command, environment, fixed design, and limitations. Compute nanoseconds are individual local calls including allocation and possible GC noise, not a controlled runtime-overhead benchmark; do not use them as runtime request latency.

Tests first check known failure cases and then cross-check 150 tiny instances against independent enumeration of every possible assignment without solver pruning. They also test infeasible certificates, deterministic ties, deadline boundaries, seeded generation, aggregation, and saved-instance replay.

## Interpretation

For model-01, the heuristic matches the exact cardinality optimum in 60/120 instances. The arithmetic mean of per-instance `heuristic accepted / optimum accepted` is 0.9153688672; the observed minimum is 0.5. Absolute gap is 0..3 admitted tasks. These are observations about this finite restricted test set, not a constant-factor approximation guarantee or a result for general Problem 1. A zero-optimum case would receive ratio 1 by convention; none occur in model-01. Variation between seeds is variation of generated inputs, not measured runtime uncertainty.
