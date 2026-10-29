# Four-objective offline calibration

> Execute inline using executing-plans and targeted failing tests. User approved “四个 λ 离线选参” after reviewing the assessment. Preserve runtime and existing results; no further execution-mode approval is necessary.

**Goal:** Actually use four objective preferences to select existing three-feature score weights, evaluate independent traces, and deliver reproducible figures/notebook and truthful paper/reply updates.

**Architecture:** New `cmd/revision-calibrate` calls the unchanged `pkg/schedpolicy.Score`. A restricted discrete-event model records predicted/actual service, deadline rejection, remote outage losses, and CPU/memory resource-time occupancy. Offline selection uses calibration traces only; held-out traces never alter selection. This is not native Tide recovery or a solution of full Problem 1.

**Stack:** Go standard library + shared score; Python standard library, matplotlib, nbformat/nbclient; tectonic.

## Approved scope and fixed experiment design

- Keep structural R_cap unchanged, report service-level R_srv separately. Explain that R_cap is constant for a fixed capability topology.
- Two local serial executors and one remote serial executor. Existing calibrated CPU/memory/I/O p95 scales and explicit synthetic heterogeneous factors; per-request actual service multiplier uniform [0.8,1.2], hidden from prediction.
- 30 s input interval; remote gate unavailable [10,20) s. Gate loss applies to remote responses whose dispatch-to-response interval intersects the outage. Already queued remote work still consumes modeled resources; no retries.
- Candidate filtering uses estimated completion and observed connectivity. Actual FIFO finishes are maintained independently. Predictions reset when a node is observably idle. No future outage information is used in selection.
- Rates 10/30/60 RPS; low/high network budgets; steady and burst arrivals; 3 calibration + 3 independent held-out seeds for each condition. Randomness generates saved inputs only.
- 66 step-0.1 score triples plus (1/3,1/3,1/3) and archived-placeholder (0.45,0.35,0.20): 68 candidates. Same complete inputs for all candidates.
- Six predeclared lambda vectors: equal; prior-draft ratio 1:1:0.5:1 (candidate preference, not historical evidence); throughput-heavy; resilience-heavy; utilization-heavy; latency-heavy. Also evaluate one-coordinate ±30% lambda perturbations using cached calibration outcomes. Do not optimize lambda against its own score.
- Normalize window deadline goodput by offered cohort count/window, R_srv by all arrivals during outage, U_model by CPU and memory capacity-time (average of fractions), latency by common 200 ms deadline with cap 1; no successful responses gives latency cost 1. Report uncapped conditional mean/P95 separately and all outcome counts. U_model is modeled execution-time occupancy, not measured runtime reservation/RSS or full paper U.
- Select maximum mean calibration reward across equally weighted scenarios/repetitions; exact numerical ties prefer score weights closest to equal, then lexicographic order. Freeze before accessing held-out metrics. Report held-out four-vector, default comparison and descriptive best-grid regret without reselecting.
- Preserve triangle/viridis visual style; triangle axes are online score weights, color is fixed-lambda calibration objective. Additional panels show held-out raw metrics, empirical sensitivity and default comparisons. Embed plotting data/code in notebook; no external-image notebook cells.

## Tasks and validation

- [x] Add `cmd/revision-calibrate/model_test.go`: hand-computed deadline equality, outage start/end and crossing response loss, queued failed work occupancy, predictable score-dependent assignment, disjoint seeds and 68 unique triples, 4-term reward and training-only selection cases. Run `go test ./cmd/revision-calibrate` and confirm missing implementation fails.
- [x] Add `model.go`: explicit trace/node/request/result types, bounded input validation, shared-score selection and raw decisions; model metrics as above. Add `main.go`: deterministic complete inputs, gzip decisions, summary CSV, selection JSON, configuration/source/artifact hashes. Reject nonempty output directories.
- [x] Run targeted Go tests plus existing schedpolicy/sensitivity tests. Run full fixed matrix into `results-objective-20261005`; preserve all outcomes, calculate hashes and retain commands.
- [x] Add `scripts/revision/verify_objective.py`: independently replay candidate rankings, FIFO/service/outage outcomes, occupancy and aggregate metrics from saved inputs and raw decisions. Check exact row counts, hashes, training-only winners and held-out summaries. Save verification JSON.
- [x] Add `scripts/revision/plot_objective.py`: aggregate saved summaries, embed data and matplotlib program into `scripts/objective_calibration.ipynb`, export PDF/PNG to result folder and `revision-figs/evaluation/`. Execute notebook; inspect rendered images.
- [x] Add Chinese results/readme with calibration scope, actual chosen weights, measured simulation sensitivity and explicit limits. Update approved assessment status. Add scoped experimental text/table/figure to revision manuscript; replace unsupported Major 2 draft parameter claims in response; update only supported issue5 checkboxes. Compile paper, response, assessment PDFs and inspect relevant pages.
- [x] Completion audit: four lambda preferences actually used; all four modeled metrics vary or inactivity reported; no test-set tuning; notebook runnable with data; chart style retained; paper and reply consistent; no runtime changes; full objective remains active until all outputs verified.

The line-count assessment is an estimate, not a cap: tests, evidence verification and reporting are counted separately from algorithm changes. No native runtime or protocol redesign is planned.
