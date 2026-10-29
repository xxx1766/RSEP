# Issue 8 completion plan (2026-10-05)

Final status: COMPLETED. See RESULTS.md and delivery-verification.json. Both PDFs compiled; new graph/table pages inspected.

Goal: finish the outstanding control-plane measurements, replace unsupported manuscript/response claims with measured evidence, and close issue 8 only after source/PDF verification.

Architecture: retain the completed three-system idle/DDL/pressure matrix without outcome-based reruns. Add opt-in, in-memory monotonic stage tracing in an isolated copy of Tide. On the same NJ-k1 VM and identical Go worker, compare an initially empty executor pool (first request creates a container) with a pre-created pool (first request reuses a container), randomized within 30 paired blocks. Keep DDL=1000 ms, CPU 0–1, GOMAXPROCS=1, identical 65536-byte ×100 kernel, and no concurrent requests. Include nine subsequent warm requests per session. Treat cached-image container initialization separately from request scheduling and from image downloading. Do not infer algorithm-only gains from a residual.

The user authorized the missing tests and manuscript changes; execute in this session without a further design approval gate. Source isolation is a copied benchmark tree because the current research worktree contains many untracked prior artifacts.

Alternatives considered: only deleting old unsupported claims would leave the requested measurement incomplete; only comparing whole framework boot times would mix controller bootstrap with application/container startup. Direct Tide path instrumentation plus the already completed three-system resource matrix answers the actual remaining scope.

- [x] Verify host idle state and freeze protocol/source hashes before pilot/formal runs.
- [x] Add and test trace recorder and strict stage-partition audit. Test missing/nonmonotonic stages, cold-vs-warm lifecycle attribution, and wrong work results.
- [x] Prepare isolated instrumented source; build/run tests on Linux. No scheduling-policy or kernel changes.
- [x] Pilot cold and warm sessions, audit phase nesting, executor identity, CPU affinity, worker hash, cleanup, and success. Pilot data excluded.
- [x] Run 30 randomized paired blocks (60 independent sessions, 600 requests); keep all failures. Formal results cannot be replaced to improve Tide's ranking.
- [x] Check tracing overhead using matched trace-enabled/disabled warm runs; report perturbation and keep time boundaries explicit.
- [x] Download raw results/provenance, independent audit, summary CSV and cold/warm stage figure/table. Verify stage sums against direct handler/client time.
- [x] Integrate completed control matrix table/figure and new lifecycle/decomposition results into revision_data/6_evaluation.tex. Resource scope and baseline DDL semantics must accompany claims.
- [x] Replace response_to_reviewers.tex Major 4 (English/Chinese), stale top pilot note and cross references; delete 17 MB/980000 msg/s/325–580 ms/<5% algorithm-attribution claims unless supported by the new exact metric.
- [x] Update revision_issue_log.md item 8 with measured evidence, bounded interpretation, and links.
- [x] Compile both PDFs, inspect revised pages/figures, check references/layout and stale claims, verify resource cleanup and complete goal only after all requirements pass.

Trace design: marks use process-monotonic time and are buffered until shutdown. Request marks cover HTTP entry, submit, enqueue, dispatch, resource acquisition, executor acquisition, execute, exit receipt, result availability, HTTP write. Container creation/task start/INIT-ready marks use executor IDs linked to each request. Worker execution duration remains the identical original kernel's own timer. IPC residual includes serialization, IPC, scheduling and return; never label it pure control CPU or all algorithm cost. Handler partitions telescope exactly; client-minus-handler is external transport/client residual.
