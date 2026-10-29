# TMC revision experiments implementation plan

> Execute continuously under the user's approved small-patch scope. Use targeted failing tests for behavior changes and review implementation against this checklist. Code is on `codex/tmc-revision-experiments`; preserve the original paper and unrelated files. No additional design approval is needed: the preceding conversation and active goal authorize implementation and remote experiments.

**Goal:** Small scheduling/runtime patches, remote execution with saved raw evidence, and replacement of every A–F placeholder with reproducible measured or explicitly simulated figures and tables.

**Architecture:** Keep the Routine–Stub–Executor–Processor pipeline. Parameterize existing admission/ranking, repair execution and measurement defects, and add thin experiment drivers. Share ranking logic between runtime and any restricted-model experiment; simulated outcomes must never be presented as runtime measurements.

**Tech stack:** Existing Go/containerd/cgroup v2/PAIX runtime; Python analysis; SSH NJ-k1 initially, NJ-k2 if useful; existing Kubernetes installation for an explicitly identified baseline. NJ-k3 is currently NotReady and is not required.

## Scope and evidence inventory

Source: `scripts/gen_placeholder_notebook.py` and `scripts/placeholder_new_figs.ipynb`.

| ID | Required evidence | Output / acceptance |
|---|---|---|
| A | Daemon CPU/RSS under load; stub creation; executor cold/warm; scheduling cost; PAIX throughput; measured control-path/e2e timing | `figA_ctrl_plane.{pdf,png}`, per-run and per-event records. No derived latency share without matched events. |
| B | Full/disconnected/recovered execution; local versus remote request outcomes; explicit local-preservation policy comparison and failure model | `figB_degradation.{pdf,png}`, timestamped connectivity events and all requests. No automatic C4 completion guarantee. |
| C | Weight sweeps over load/network settings and TTL sweep with idle allocated CPU time | `figC_sensitivity.{pdf,png}`, configuration for every point, repetitions, empirical variation without expected target. |
| D | CPU, memory-access, I/O and differing QoS cases on actual execution paths; fair resource budgets for real available baselines | `figD_hetero.{pdf,png}`, workload calibration and complete raw records. GEMM is not assumed memory-bound; Kubernetes must not be mislabeled KubeEdge. |
| E | CPU time and reservation samples with declared denominators, under matched loads and baselines | `figE_utilization.{pdf,png}`, resource time series and summary. CPU allocation is not CPU use. |
| F | Restricted small-instance heuristic vs independently verified optimum, identical feasibility/admission/objective | `figF_optgap.{pdf,png}`, instance inputs, solver certificates/status, exhaustive tiny-case checks. Explicit restricted-model label. |

All six categories remain required. Scope adaptations must preserve the experimental question, identify the actual system/model, and document any limits; do not replace missing measurements with plausible numbers. Existing preselected performance targets are discarded.

## Batch 1: trustworthy execution and measurements

- [x] Preserve baseline source and environment metadata in the new isolated remote directory `/root/rsep-revision-20260928`.
- [x] `cmd/tide/app/serve/tide.go`: use existing v2 initialization, honor configured CPU capacity, report GPU=0 for CPU-only experiments. Verify allowed CPU placement and daemon lifecycle.
- [x] `pkg/stub/sched/default.go`: compare available capacity to requested stub quota; equal capacity and CPU-only hosts must work. Tests cover insufficient/incomparable resources.
- [x] `pkg/dev/`: configure QoS, calibrated service time, TTL, weights and controlled network estimates. Reject invalid values and save effective configuration.
- [x] `pkg/stub/stub_op.go`: inactivity-based TTL, no pending/running requests at reclamation, admission/stop synchronized. Validate a used idle stub releases exactly once.
- [x] `scripts/analyze.py`: measurement-window selection before outcome filtering; QoS-goodput distinct from success throughput; no warmup-as-failure. Regression fixture has 10 requests, warmup success plus genuine failures and late successes, with independently computed expected rates.
- [x] `scripts/run-matrix.sh`: explicit success/failure manifests, completeness checks, only run-owned cleanup; never global `/dev/mqueue/*` deletion.

## Batch 2: normal-path experiment support

- [x] Add the measured local normal-submission harness with real timestamps and every offered outcome. Native originating-stub cross-node return is outside the completed evidence; the explicit adapter scope is documented below.
- [x] Implement ranking in existing selection hook with declared units, bounded features and deterministic ties; handle expired requests and failed submissions without claiming distributed exactly-once semantics.
- [x] Add minimal CPU/memory/I/O workers through the real executor protocol; build an isolated container image. Cross-process PAIX benchmark records individual round-trip durations.
- [x] Instrument lifecycle and scheduling timestamps and sample process/resource usage; benchmark logging level fixed and captured.
- [x] Fault tests use controlled loss of the owned HTTP adapter endpoint, with real workers, paired eligibility policies, in-flight outcomes and recovery. This is not native Tide distributed recovery and does not modify host networking.

## Batch 3: experiments and artifacts

- [x] Calibrate workloads and timing on the actual remote host. Freeze configuration before evaluation; reuse input traces across policies.
- [x] Run A–E with independent repetitions; record code hash, binary/image identity, environment, command, exit status, raw requests/events/samples. Failure records retained.
- [x] Run F with saved restricted instances and proven optimal results; cross-check tiny cases by enumeration.
- [x] Generate six figures, CSV/LaTeX summary tables, and data-driven narrative with dispersion and explicit limits. No expected superiority or target percentage in plotting code.
- [x] Replace the placeholder notebook with an executable data-loading notebook; archive the original synthetic design as non-results. Remove active synthetic generator/output references so reruns cannot silently restore fake figures.
- [x] Provide a figure-to-data manifest and results report; verify each figure can be regenerated from saved inputs without random outcome generation.
- [x] Inspect rendered figures and compile a standalone experiment supplement PDF. Paper/rebuttal result insertion only uses verified values and labels.

## Verification gates

1. Targeted unit tests reproduce capacity, TTL and statistics bugs before fixes.
2. Remote single request then bounded concurrent load completes with correct resource release; no unrelated service changes.
3. Every reported run has exit status and completeness evidence, every aggregate links to raw data.
4. A–F coverage checked individually against this inventory, including subpanels, baseline identities and data provenance.
5. Goal remains active until all formal artifacts replace placeholders and the preceding gates pass.

## Current observations (2026-09-28)

NJ-k1 reachable, 16 allowed logical CPUs, low load, cgroup v2, Go 1.26.2, containerd 2.2.3; no workload images in default namespace and no system Python pip. Kubernetes has two Ready nodes; no proof of KubeEdge components yet. No runtime behavior has been certified by the initial read-only checks.

## Evidence checkpoint 02

See `results-revision-20260928/PROGRESS.md` for tests, calibration, binary/image identities and remaining scope. This was the intermediate checkpoint. It is superseded by the final checkpoint below.


## Final checkpoint and scope adaptations

A–F artifacts are complete and replace the active synthetic notebook. `results-revision-20260928/README.md`, `figure_data_manifest.json`, `verification.json`, and the 12-page `revision_experiments.pdf` are the final entry points. All figure generation and eight notebook code cells were rerun from saved data; relevant Linux race tests and twenty Python analysis tests passed. Experiment-owned worker tasks, Pods and named IPC queues were cleaned up.

The completed experimental question has explicit limits. B uses a real-kernel HTTP scheduling/fault adapter, not native Tide distributed recovery. C weights and F are restricted simulations. A measures named control-path components and matched response/kernel times, not a complete asynchronous scheduling-cost decomposition. TTL reclaims logical reservation while retaining the container. Actual available baselines are Bare and a single hostNetwork Kubernetes Pod, not Ray/KubeEdge; I/O storage paths differ. The report retains unfavorable outcomes.

Native originating-stub/result-return recovery and high-load deadline guarantees remain unproven. Those are claim-dependent follow-up work, not silently completed functionality. The submitted manuscript and rebuttal have not been overwritten. This checkpoint completes the recorded experiment-artifact objective; it does not certify the entire journal revision as submission-ready.
