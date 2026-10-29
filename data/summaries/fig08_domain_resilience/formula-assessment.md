# Domain resilience: formula and implementation assessment

Read this pre-experiment modeling assessment alongside the [qualified 36-cell results report](domain-resilience-results.md). The pilot figures below remain historical diagnostics; they are not the primary comparison. The domain/eligibility/service distinction and the implementation limits remain applicable to that comparison.

This assessment proposes a clearer interpretation and notation; it changes no paper, core implementation, experiment protocol, or result. The coordinator-defined local domain is **D_A = {A, B}**, with requests originating and returning at A. C is outside that domain and has no application allocation. At the assessment handoff, only a Tide pilot was available; formal comparisons had not started.

The main conclusion is that **structural capability eligibility and observed service resilience must remain separate**. The existing CPU C4 gate checks A's capacity. It does not implement a general A/B-domain eligibility test. With a fixed eligible-host inventory, R_cap cannot measure scheduler quality and contributes only a constant to a placement objective.

## Evidence and scope

| Evidence | What it establishes | Boundary |
|---|---|---|
| [Canonical model](../../../../TMC_slimmed/revision_data/3_modeling.tex), lines 44–51 and 68 | R is explicitly R_cap, a weighted compatible-local-option fraction; R_srv is separately measured | Capability eligibility does not assert service completion |
| Same model, lines 73–107 | One entire request executes at one selected host; capacity/reservations belong to that host | Device capacities are not pooled to execute an indivisible task |
| Same model, lines 123–140 | C4 retains the originating device's CPU capability and disclaims backup reservation/recovery guarantees | This is not a domain-wide runtime mechanism |
| [Canonical algebra](../../../../TMC_slimmed/revision_data/3_modeling_theory.tex), lines 19–44 and 89–113 | The ledger preserves ownership; degradation projection discards augmentation, assuming relevant local devices and links operate | The homomorphism does not prove deployment, routing, recovery, or deadline service |
| [Actual C4](../../pkg/c4/check.go) and [routine envelope](../../pkg/routine/routine.go) | Immutable LocalNodeID identifies the origin; C4 uses its total CPU capacity | No union of local nodes, free-capacity check, reservation, or deadline guarantee |
| [Domain native ingress](../../cmd/revision-domain-native/main_linux.go) and [configuration checks](../../cmd/revision-domain-native/api.go) | The study uses native submission, two-CPU requests/allocations, CPU C4, A/B execution budgets, and zero C application budget | The experiment does not replace C4 with the proposed domain notation |

The installed capability inventory, logical application budget, reachable data paths, running executor inventory, and completed service are different quantities. In this study, a two-CPU request is a declared allocation requirement. It is not a claim that the single-threaded Go kernel consumes two full physical CPUs. The model's capacity parameter must use the declared application budget rather than all physical CPUs available to control processes.

## A domain-explicit capability definition

For a request k arriving at A, let a_k be its frozen offered-arrival time, q_k its response-time budget, and p_k ≥ 0 its predeclared criticality weight. The weight sum for the evaluated cohort must be positive. Let I_k(v) denote execution compatibility at host v, including the required executable/architecture and declared resource dimensions. This denotes potential compatibility; it does not assert that an executor is already running.

An explicit candidate definition is

\[
\begin{aligned}
\mathcal E_k^{\mathcal D_A}(t)=\{v\in\mathcal D_A:\;&
\operatorname{operational}_v(t),\quad I_k(v)=1,\\
&S_k^{\rm req}\subseteq S_A,\quad A_k^{\rm req}\subseteq A_A,\\
&C_k^{\rm req}\preceq_{\mathcal C}C_v^{\rm total}(t),\\
&A\rightsquigarrow_{\mathcal D_A,t}v,\quad
v\rightsquigarrow_{\mathcal D_A,t}A\}.
\end{aligned}
\]

Here the two directed reachability predicates require a usable input/data route and a result-return route within the declared domain. A bidirectional-looking network drawing alone is insufficient. Routes that require transit through C cannot establish independence from C. Physical/domain reachability is a necessary condition; actual discovery, cached service metadata, protocol state, and return delivery must still be observed.

Sensing and actuation stay at the originating endpoint A in this formulation, following the canonical assignment semantics. A remote computing host B need not itself contain A's sensor or actuator. If an application relocates those operations or requires additional external dependencies, its compatibility/path predicate must explicitly describe that different execution model. The present compute-only kernel has empty sensing/actuation requirements.

For a predeclared degraded window W, define the **offered-arrival cohort**

\[
\mathcal K_W=\{k:a_k\in W\},\qquad
R_{\rm cap}^{\mathcal D_A}(W)=
\frac{\sum_{k\in\mathcal K_W}p_k\,
\mathbb I[\mathcal E_k^{\mathcal D_A}(a_k)\ne\varnothing]}
{\sum_{k\in\mathcal K_W}p_k}.
\]

This makes the domain, connectivity time, and cohort explicit. It is a suggested clarification, not an implemented new formula or a new formal theorem. If the intended structural metric excludes connectivity and considers only installed capability, name and report that narrower variant explicitly instead of silently changing its predicate between experiments.

The existential host check is essential. For example, A and B with two CPUs each cannot execute a single indivisible three-CPU request merely because 2 + 2 ≥ 3. At least one compatible execution host must independently satisfy the request. Cross-host capacity addition can describe an aggregate ledger or multiple concurrently placed tasks; it cannot establish single-host feasibility for this request model.

For cold B, distinguish a compatible, image-cached host from a ready service instance. An optional policy-dependent set

\[
\mathcal E_{k,\rm ready}^{\mathcal D_A,\pi}(t)
=\{v\in\mathcal E_k^{\mathcal D_A}(t):
\operatorname{executorReady}_{k,v}^{\pi}(t)
\land\operatorname{usableRequestReturnProtocol}_{k,A,v}^{\pi}(t)\}
\]

can describe deployment/readiness. It must not be substituted for the fixed capability set without changing the metric's stated meaning. B's application activation time, cached-routing state, and control dependence belong in the operational evidence.

## What the current C4 implementation checks

For a flagged request, `c4.Check` rejects missing/nonpositive CPU demand and nonzero GPU demand; looks up `r.LocalNodeID`; requires a things/edge role; and compares that node's **total** CPU against the request. It does not inspect the node's currently remaining CPUs. Origin initialization and the immutable envelope preserve that identity while the request is forwarded. Separate destination checks still enforce the selected executor's capacity.

Consequently, in this experiment A = 2 CPUs and request = 2 CPUs passes C4 in both warm and cold conditions, irrespective of B's application state. A capable but busy A also passes. The gate neither discovers B nor makes B ready, and it does not guarantee that a result returns to A by its deadline.

The distinction is substantive: hypothetically, A with one CPU and B with three CPUs could offer a domain-compatible host for a two-CPU task, while the current originating-node C4 would reject it. That is an explanatory counterexample, not a newly executed experiment. Conversely, successful B execution during C isolation does not prove that the code implements domain-general C4.

The general paper model contains sensing/actuation and resource-vector conditions. The runtime gate is the documented CPU projection. The study must not be presented as validating general sensing, actuation, GPU, or memory-capability screening through C4.

## Operational outage service metric

For policy/system π, let Y_k^π = 1 only when A receives a correct, request-ID-matched result from an eligible physical execution host within D_A by a_k + q_k. Establish the execution host by joining client outcomes to journals collected from that physical host. Worker labels alone are insufficient. A completed kernel, an enqueued native routine, an HTTP acknowledgment, and an eventual result after the deadline do not satisfy this indicator.

\[
R_{\rm srv}^{\mathcal D_A,\pi}(W)=
\frac{\sum_{k\in\mathcal K_W}p_k Y_k^\pi}
{\sum_{k\in\mathcal K_W}p_k}.
\]

The denominator contains **every offered request in the declared cohort**, including admission rejection, connection failure, timeout, missing outcome, and missing usable return. Do not condition it on accepted requests, successful dispatches, or completed worker executions. Preserve zero-weight offers in raw counts, and freeze weights before outcomes. If all weights are one, report this as an unweighted outage-arrival deadline yield. The runtime's C4 classification flag is not evidence that numeric outcome weights were implemented.

Use scheduled offer time and A's monotonic clock for deadline accounting, including driver scheduling delay; retain separate transport latency if useful. Define W from the recorded fault brackets and document clock conversion. Report rule-install/removal transition cohorts separately. Offers arriving during isolation whose deadlines extend past restoration remain in the primary offered-arrival cohort; flag their cross-phase returns. A secondary cohort whose entire deadline lies within stable isolation may be useful, but must be predeclared and cannot replace the primary denominator after results are seen.

Report correct-return count, timely-return count, rejection/timeout/missing counts, physical A/B execution attribution, and readiness timeline alongside R_srv. Connected-control episodes should use the same offered traces and comparable time windows. Repeats, rather than individual requests from one episode, are the units for comparative uncertainty. This note asserts no universal equality or numerical bound between differently timed structural and service metrics.

## Consequence for the optimization objective

When the domain, physical reachability, compatibility inventory, capacities, weights, and offers are fixed independently of placement policy X, R_cap is constant with respect to X. Therefore the canonical objective term −λ₂R_cap cannot distinguish schedulers or justify an optimization benefit from choosing a different assignment. If C4 already guarantees the relevant origin capability for every admitted critical request, conditioning the evaluation on those admissions would make that structural score especially uninformative about lost offers.

Two defensible choices are:

1. Keep R_cap as an explicit structural descriptor/eligibility condition and use separately measured R_srv to evaluate actual service under degradation. This is the recommendation supported by the present implementation and experiment.
2. If a policy genuinely chooses deployments, retained resources, or available communication paths, define those decision variables and their costs/time dependence. Then a policy-dependent readiness/capability objective may be meaningful, but it is a different optimization scope and requires corresponding implementation and evidence.

A modeled service-probability objective is another possible future formulation:

\[
\widehat R_{\rm service}^{\pi}
=\frac{\sum_k p_k\,
\widehat{\Pr}[\text{correct return to A by }a_k+q_k
\mid\text{state, workload, degradation, policy }\pi]}
{\sum_k p_k}.
\]

Use that substitution only if the probabilities are actually estimated, calibrated, and used by the proposed decision procedure. No such validated estimator or optimization result is established by this assessment. Relabeling the observed fraction as a probability optimized online would be unsupported.

## Pilot evidence and limits

The available [pilot specification](../runs/pilot-tide-cold-drop/A/spec.json) schedules cloud packet dropping at 5–35s within a 45s episode, with 300 offered requests, 500ms deadlines, and 2s client timeouts. The coordinator reports B ready at approximately 10.6s; that reported readiness must retain its event definition and clock basis rather than being equated with arbitrary process birth or a worker's self-reported label.

Reading [client records](../runs/pilot-tide-cold-drop/A/requests.jsonl) gives 299 driver-correct returns and 266 timely returns over **the whole pilot**. Joining each correct return's `(request_id, worker_instance)` to RESULT records under physically collected [A journals](../runs/pilot-tide-cold-drop/A/native-journals) and [B journals](../runs/pilot-tide-cold-drop/B/native-journals) attributes 179 to A and 120 to B, with zero unjoined/ambiguous correct-return matches. All 299 client `claimed_node` values say A, illustrating why journal-based physical attribution is required.

Thus 299/300 and 266/300 are whole-pilot descriptive fractions. They are **not** the outage-arrival R_srv unless that exact cohort is reconstructed from offers and fault brackets. The pilot demonstrates an observed native B execution path in one setup; it supplies no formal comparison, repeated-run effect estimate, universal guarantee, or proof that C4 causes the expansion.

## Do not overclaim

- Do not call fixed R_cap an empirical ranking of scheduler resilience; A's compatible option can make it one while service deadlines are missed.
- Do not sum A/B CPU to claim feasibility of an indivisible task or treat a pooled ledger as a cross-host executor.
- Do not call the proposed domain set a feature implemented by originating-node C4.
- Do not equate cached image, idle preforked process, reserved allocation, ready function/Pod/replica, discovery, and successful client return.
- Do not infer return-path independence from B's local completion alone; use A's matching correct response, physical journals, and the actual scoped fault evidence.
- Do not drop unsuccessful offers or reweight them after inspection; do not count duplicate execution as additional useful service.
- Do not derive outage yield, comparative superiority, causal attribution, or formal guarantees from the whole-pilot totals.
- Do not edit the canonical objective or claim an estimated service-probability optimizer without a separate justified modeling and implementation change.

An evidence-consistent eventual statement would be: “The declared A/B domain retained compatible single-host computing capability during withdrawal of C. Under the recorded offered workload and failure window, the systems' measured ability to activate/use that capability and return correct results within deadlines was evaluated separately.” Any stronger comparative statement must wait for the qualified formal runs.
