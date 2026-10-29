This isolated command supplies measurement ingress for campaign `r-u-20261020`.
Actual work uses native Tide Fn admission, ProcessorManager, containerd and PAIX.
It does not change the scheduler, add retries, or create an HTTP execution backend.

Run one process on each owned host, with identical policy and kernel:

```
revision-ru-native --node ru-local --address 11.0.4.48:32040 \
  --http 11.0.4.48:32041 --policy tide --kernel cpu \
  --root /root/OWNED_UNIQUE_RUN --config /root/OWNED_CONFIG.json \
  --request-timeout 5s
```

`ru-local` is EDGE and `ru-cloud` is CLOUD. Each reserves two logical CPUs
and initializes one real warm Fn executor from
`docker.io/tide/fig6-cluster-20261014:amd64` (native code adds the architecture).
The worker command is `/worker --mode=paix --node=<name> --tmpdir=/owned`.
The parent must apply and verify the worker leaf's 512 MiB memory limit,
two-CPU quota and CPU set before calibration or traffic. This command never
changes shared host limits, sysctls or network settings.

`--config` is loaded once. Require native `qos_ms=500`, `ttl_ms=86400000`,
`c4_functions` enabling CPU and memory, and positive `node_execution_ms`
entries for both nodes. The selected `--kernel cpu|memory` gets its own
immutable config with independently calibrated estimates. There is no config
mutation endpoint. Policies are `tide` and `native-cloud-only`; the latter
uses the genuine native cloud submission/wait path with no edge scheduling.
Both policies retain the same local and cloud worker inventory.

- `GET /health`: complete process/stub/image/worker argv/root identity.
- `POST /peers`: exactly the two `/health` objects, once on each node.
  Unknown fields, trailing JSON, differing policies/worker profiles, incorrect
  budgets and mismatched self identities fail validation.
- `GET /clock`: Linux `CLOCK_MONOTONIC` nanoseconds, a wall-clock midpoint
  and wall-clock before/after bracket. Driver-side RPC brackets bound offsets;
  wall timestamps alone do not establish cross-host timing accuracy.
- `GET /state`: self, locked native PM snapshot, node capacity snapshots,
  immutable config/kernel, owned cgroups/executors, gate state and observer
  submitted/completed/unresolved counters. The counters describe observation,
  not executor occupancy or backend drain.
- `POST /gate {"enabled":true|false}`: update only this process's owned flag;
  return actual Unix and monotonic timestamp. Enabled gates reject inbound
  nonself native event RPC batches with `Unavailable`, retaining event details.
  HTTP stays reachable. This is controlled event-endpoint failure, not a physical
  network partition. Gate changes and rejections are durably journaled.
- `POST /run`: exactly `{"kind":"cpu","bytes":65536,"iterations":100}`
  or `{"kind":"memory","bytes":16777216,"iterations":4}` matching
  `--kernel`. Optional `X-Request-ID` is echoed. Returns request ID, policy,
  workload/input hash, Unix and origin-monotonic submission/return times,
  latency, native status, receiver, raw base64 result and parsed worker JSON.
  Worker `worker_start_ns`, `worker_end_ns`, `service_ns`, node, PID, instance,
  checksum and processed bytes are retained unchanged in `result_json`.

Only local ingress is used for scored requests. Cloud `/run` directly admits
to its real native cloud Fn for excluded standalone calibration after peers
are registered. It does not apply the scored policy comparison.

HTTP success/failure/timeout/submission-error codes are 200/503/504/503.
Transport acceptance is an asynchronous dispatcher acknowledgement, not proof
of execution. No retries or replay are added; lost receive/wait/result events
can leave work unresolved after gate release. Recovered cohorts contain new
arrivals. Retain every client outcome and any native late result evidence.

Artifacts are exclusively created under `<root>/native/<node>/`: startup,
runtime JSONL, atomic owned manifest, gate journal/flag and shutdown report.
Existing process directories are refused. Ownership hooks capture successful
native cgroup creation, executor/container intent and actual warm INIT.
On SIGTERM/SIGINT, ingress drains, native SM/PM close, then only manifest-owned
cgroup leaves are removed. Shutdown records remaining leaves and errors;
the parent independently verifies tasks, process identities and shared state.
There are no namespace-wide or `DeleteAll*` cleanup calls.

Portable API/ownership contracts: `go test -race ./cmd/revision-ru-native`.
Native cloud protocol/refusal tests: `go test -count=1 -run '^TestSubmitExternalCloud' ./pkg/stub`
on Linux with native headers/dependencies. Those controlled protocol tests
are not container-runtime experiment evidence.
