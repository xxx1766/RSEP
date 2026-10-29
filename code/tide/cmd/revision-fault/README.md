# B: measured-kernel HTTP fault/local-eligibility adapter

This is a **real execution experiment through a scoped HTTP adapter**, not Tide's native distributed gRPC/event-return path. It executes the same `revisionworkload.Run` CPU kernel in separate worker processes, measures actual submission/reply/service times, and interrupts only its own remote HTTP gate. It does not simulate service latency or fabricate outcomes. Native Tide multi-node recovery remains unverified by this experiment.

## Why an adapter

The normal runtime's `StubFn.SubmitRoutine` first admits locally when its queue estimate meets the deadline, then invokes remote scheduling. Normal distributed completion returns through stub events. Exercising two complete runtime nodes during other container/cgroup experiments would require additional discovery and event-return integration. This command isolates the narrower experimental question: does keeping a feasible local execution path preserve measured local work while an external execution endpoint is inaccessible?

## Policies, budgets, and fault

Both policies allocate the same two workers. Each has one serial execution slot, `GOMAXPROCS=1`, and a verified two-CPU affinity set. Default affinity sets are local CPUs 0,1 and remote CPUs 2,3. These are affinity budgets, **not cgroup quotas or exclusive physical-core ownership**. The launcher freezes a copy of the binary in the result directory and records every worker thread's affinity before and after each run and hashes the executable through `/proc/PID/exe`.

- `-local-eligible=true`: use the local worker first when predicted deadline-feasible; otherwise consider the remote worker.
- `-local-eligible=false`: local worker is ineligible, an explicit entry-only-origin ablation. Its allocated process remains present but unused. This is not a claim that another complete scheduler intrinsically lacks local execution.

Prediction uses pending request count times the fixed service estimate, including one full estimated service for a currently running task. Default estimate is 22 ms, rounded from the measured CPU100 warm p95 calibration. The local network prediction is zero and remote prediction is 5 ms; actual loopback transport times are measured, not forced to these predictions. Remote candidates use shared `pkg/schedpolicy.Score` with weights `(0.3,0.5,0.2)`, latency scale 100 ms, free fraction zero for the fully reserved two-CPU budget, and device weight 1. This two-node experiment has only one remote candidate, so it does not test relative remote ranking. No eligible predicted-feasible node yields explicit admission rejection (429).

There are three nominally three-second phases: connected, disconnected, recovered. A controller-owned localhost gate forwards remote requests during connected/recovered phases. At disconnection it cancels its active upstream HTTP contexts and rejects new remote calls with 503. Recovery reopens that gate. It does not alter host network rules, container networking, unrelated endpoints, or the local worker path. Events record transition start/completion times and the IDs targeted while in flight. An in-flight target may race with successful response delivery; it is not automatically counted as failed.

HTTP cancellation does **not** cancel an already-running kernel. The worker retains its serial semaphore until actual computation ends, even if the response is lost. Worker JSONL therefore separately records completed kernels, request identities, start/finish times, checksums, and whether the client context was canceled at completion. There are no retries and no exactly-once claim. The launcher waits for actual worker idleness before terminating its own processes.

## Input trace and metrics

Default workload: CPU, 65,536 bytes, 100 SHA-256 iterations. Default offered rate is 70 requests/s, QoS 200 ms, external response wait two seconds, and three paired seeds. The 70 requests/s input intentionally offers more work than one calibrated serial worker can sustain, so the local-enabled policy can exercise both routes while connected. It is not an expected throughput or success target.

A recorded jittered open-loop trace is generated as `(i + U[0,0.2)) / rate`. There are `round(3 * phase_duration * rate)` arrivals. Each pair uses the identical saved trace and kernel. Policy order alternates by seed. Actual submission times and dispatch times remain in the raw data, so scheduling jitter and queue delay are observable. No requests are filtered by outcome.

`raw.csv` records every offered request, chosen route, planned/actual submission, dispatch, reply, worker timestamps, status, QoS outcome, phase at submission/reply, and fault-cancel targeting. `events.json` contains actual gate transitions. `summary.json` distinguishes:

- per-submission-phase cohort outcomes, including late replies or failures after a transition;
- deadline-goodput counted by **response completion within each actual phase window**, divided by that phase's actual duration;
- successful cohort requests draining after the full observation window.

All observation windows are half-open `[start,end)`: a response exactly at the full endpoint belongs to drain. Phase boundaries use gate transition completion timestamps; transition start/end brackets document the small state-change timing interval. Connected/disconnected/recovered labels describe endpoint reachability, not physical distributed network health. Success is measured; deadline success is not guaranteed by prediction. The local-eligibility ablation does not establish a universal C4 guarantee.

## Build and run on the Linux experiment host

From the Tide module directory, after other performance matrices finish:

```sh
go build -o /tmp/revision-fault ./cmd/revision-fault
python3 cmd/revision-fault/run_matrix.py \
  --binary /tmp/revision-fault \
  --out results-revision-20260928/fault-01 \
  --source-root . --local-cpus 0,1 --remote-cpus 2,3
```

The default six runs take about 54 seconds plus setup and drain. The output directory must be new. The launcher fails if affinity, endpoint PID, worker binary identity, paired trace hash, or controller exit status is inconsistent. It captures source/binary/artifact hashes and each run's full command. It creates no container or cgroup and terminates only the processes it launched. Do not run this concurrently with the baseline matrix when making performance comparisons.

For a shorter launcher smoke test, use `--repetitions 1 --phase-seconds 0.2 --rps 30`. Formal runs should retain the frozen default input design or explicitly report changed settings. Worker and controller modes are independently accessible via `-mode=worker` and the documented flags.

## Validation

```sh
env GOMODCACHE=/tmp/tmc-go-mod GOCACHE=/tmp/tmc-go-cache go test -race -timeout=15s ./cmd/revision-fault
```

Tests verify local eligibility and deadline feasibility; in-flight cancellation, disconnected rejection and restoration; actual kernel result identity; retention of the serial slot after client cancellation; deterministic input traces; a bounded real connected/disconnected/recovered run under both policies; and cohort versus completion-window accounting. These tests execute loopback HTTP endpoints, which require local socket permission in a restrictive sandbox.
