# Native C4 fault-path validation

Goal: show that the revised native Tide request path enforces CPU C4 while a remote event endpoint is available, unavailable, and available again; document exactly which reviewer 1 item 3 claims it does and does not support.

The experiment uses two isolated Go processes, separate cluster-state singletons, the native Stub scheduler, real event protobuf and gRPC transport, and a controlled Executor that runs `revisionworkload.Run` CPU work. The origin has an actual local function Stub and the remote process has an actual receiving function Stub. Function-specific execution estimates make a remote-class request offload while a local-class request remains admissible locally. A gRPC interceptor on only the experiment's remote endpoint rejects calls during the fault phase; it is an endpoint reachability fault, not a whole-host network partition.

For each of three runs: verify one remote-class critical request completes before fault; deny remote endpoint calls; verify a local-class critical request completes and remote-class attempt does not return a fabricated success; restore endpoint; verify a new remote-class critical request completes with the correct result. Verify an ineligible critical request is rejected by C4 before any send. Record offered IDs, phase, actual route, status/result/checksum, timestamps, fault transitions, binary/source/config hashes, and worker mode. Check duplicates and absent outcomes. Do not claim in-flight request migration, exactly-once behavior, consistency, or a deadline guarantee.

1. Write the integration test in `pkg/stub/native_fault_test.go` and run it before implementation. Expected failure: the harness has no role setup or registered endpoints.
2. Implement process setup and request probe with test-scoped fake Executor; keep production code untouched unless a reproducible runtime fault is found.
3. Run on a Linux host in a fresh temporary copy. Keep existing experiment services and results intact. Inspect raw outcomes and process logs.
4. Add one concise paper paragraph, align the Major 3 response, and explicitly bound migration/state claims. Audit all active reply locations for the obsolete percentages/three-second recovery claim.
5. Recompile both PDFs, inspect affected pages, update issue 7 only after confirming evidence and stated limitations.
