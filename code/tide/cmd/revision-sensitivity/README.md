# Weight selection sensitivity: restricted online simulation (C weights)

This experiment computes outcomes from recorded synthetic arrival/workload traces, using measured workload p95 only as a service-time scale. It **does not execute Tide requests**, measure network behavior, learn weights, or validate runtime guarantees. TTL is excluded and requires its separate runtime experiment.

## Model and question

The origin is entry-only: every candidate is a remote node and goes through the shared `pkg/schedpolicy.Score` selection rule. Three nodes each have an existing stub and one FIFO serial executor with a fixed 2-CPU quota. Their stipulated total CPU capacities are 4, 6, and 8, giving fixed remaining CPU fractions `(total - 2) / total = (0.5, 2/3, 0.75)`. Device weights are `(0.65, 0.8, 1.0)`. These score inputs are held fixed; the model does not simulate background CPU use, resizing, or additional execution capacity. Consequently the sweep tests selection sensitivity with different fixed features, not resource allocation or the full runtime admission path.

At decision time `a`, node j's predicted response time is:

`response_j = max(a, previous_service_finish_j) + service[i,j] + network[j]`.

A request is admitted iff some node has `response_j <= absolute_deadline_i`. Among feasible nodes, choose the maximum shared score, with the lowest node index breaking exact ties. Queue state advances to the selected node's service finish, excluding network time. Thus network is a per-request response budget after service, not serialized executor work or an actual network simulation. The latency score feature is response minus request arrival, in milliseconds; its scale is 100 ms. Decision times equal arrival times in generated traces; the implementation and tests also support explicit later decision times and reject expired requests.

Actual simulated service equals its predicted deterministic service, with exact queue state and network budget. Every accepted request therefore completes on time by construction. This is an **ideal-estimation selection experiment**, and its acceptance yield must not be interpreted as measured runtime deadline reliability.

## Inputs fixed before evaluation

- Full simplex: 66 triples `(resource, latency, device)` of nonnegative weights in steps of 0.1, summing to 1. Includes all vertices and edges.
- Offered Poisson arrival rates: 10, 20, 40 requests/s; observation duration 20 seconds; five recorded seeds per rate. Fifteen saved traces are reused byte-for-byte as inputs to all weights and both network profiles.
- Seed formula: `202609280000 + offered_rate_rps*100 + repetition`, with repetitions 0..4. Arrivals come from accumulated exponential intervals and are rounded to 0.1 ms ticks. All arrivals lie in `[0,20s)`.
- Workload kind independently uniform over CPU/memory/I/O; size multiplier uniform over integers 75..125 divided by 100. The input trace and all per-node service durations are saved, so replay does not require regenerating random inputs.
- Measured warm service p95 references: CPU 21.283347 ms; memory 78.898112 ms; I/O 59.614709 ms. The exact input file and hash appear in the manifest. These references do not assert measured service at the model's node factors or quotas.
- Stipulated synthetic service factors (CPU, memory, I/O): node0 `(1.3, 0.9, 1.15)`; node1 `(0.9, 1.2, 0.85)`; node2 `(0.7, 0.85, 1.1)`.
- `service_ticks[i,j] = ceil(p95[kind_i] * size_i * node_factor[j,kind_i] / 0.1)`.
- Low network response budgets: `(2,12,35)` ms; high: `(10,40,100)` ms. These are controlled model inputs, not measured network delays.
- Common per-request QoS budget: 200 ms from arrival. No warmup is removed; all requests in the observation cohort are retained.

The complete grid has `66 * 3 * 2 * 5 = 1,980` runs. Outcomes are calculated by FIFO simulation; no desired percentages or response shapes are used, and no seeds or outcomes are filtered.

## Metrics and saved artifacts

Window goodput is the number of responses completed in `[0,20s)` divided by 20 seconds. Cohort yield is accepted requests divided by all arrivals in `[0,20s)`, including admitted requests that drain at or after the endpoint. These latter requests are separately counted as `drained_after_window`. This half-open window matches the runtime metrics; a response exactly at its request deadline still counts as on time. Thus:

- `arrivals = accepted + rejected`;
- `accepted = completed_within_window + drained_after_window`.

Rejected requests stay in the cohort denominator. Conditional mean/p95 latency uses all accepted requests, including drain; rejected latency is undefined and left blank in raw CSV. p95 uses nearest rank. These model outputs are not steady-state throughput measurements. Across-seed variation reflects generated input variability, not measured runtime uncertainty.

`requests.csv.gz` contains every request decision, selected node, deadline, executor start/finish, response time, status, score, and observation-window flag. `traces.json` contains complete reusable arrival/workload inputs. `runs.jsonl` contains each run's weights, nodes, network, latency scale, trace identity/hash, and summary. `summary.csv` / `summary.json` support analysis. `manifest.json` records the experiment scope, command, environment, source/calibration hashes, and artifact hashes. `validation.json` records independent replay checks.

## Reproduction from the Tide module directory

```sh
env GOMODCACHE=/tmp/tmc-go-mod GOCACHE=/tmp/tmc-go-cache go test -race ./cmd/revision-sensitivity ./pkg/schedpolicy
env GOMODCACHE=/tmp/tmc-go-mod GOCACHE=/tmp/tmc-go-cache go run ./cmd/revision-sensitivity -traces results-revision-20260928/weights-02/traces.json -out /tmp/weights-reproduction
env GOMODCACHE=/tmp/tmc-go-mod GOCACHE=/tmp/tmc-go-cache go run ./cmd/revision-sensitivity -traces results-revision-20260928/weights-02/traces.json -out /tmp/weights-replay
python3 cmd/revision-sensitivity/verify.py
```

Output directories must be absent or empty. Replay produces the same decision and summary values; only provenance fields such as invocation and creation time change. The Python validator independently recomputes every feasible candidate, selected score, FIFO state, count, rate, and latency statistic from saved inputs. Unit tests cover a known weight-dependent node flip, FIFO/deadline boundary, expired request rejection, deterministic trace generation/replay, the complete simplex, raw-row completeness, and denominator invariants.

## Observed finite-grid results

`results-revision-20260928/weights-02` is authoritative. It replays the unchanged saved traces from `weights-01` with strict `response < T` window accounting. `weights-01` is preserved as the earlier inclusive-endpoint result. Exactly three endpoint decisions move from window completions to drain; arrivals, accepted/rejected counts, cohort yields, selected nodes, and response times remain unchanged.

The default batch records 934,560 decisions. At 10 requests/s, five-seed mean cohort yield is 1 for every weight. At 40 requests/s, the range across weights of five-seed mean cohort yield is 0.989114–0.998990 for low network and 0.980785–0.992329 for high network. The modest effect and the flat low-load result are part of the evidence. They neither establish a generally optimal weight nor justify online learning claims. Plot the actual per-run values and label this evidence **restricted online simulation**.
