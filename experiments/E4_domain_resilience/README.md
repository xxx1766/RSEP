# E4 — Domain resilience under cloud disconnection

**Paper item:** Fig. 8, §VI-D.

## Question

When the cloud control path is interrupted but Edge1–Edge2 connectivity is
retained, can a domain activate an initially idle peer and keep returning
within the deadline?

## Design

| Factor | Values |
|---|---|
| Topology | Edge1 = NJ-k2 (ingress), Edge2 = NJ-k3 (idle peer), cloud = NJ-k1 |
| Systems | Tide (native), KubeEdge/EdgeMesh, Ray Serve |
| Initial state | Only Edge1 hosts an application executor |
| Arrival | 2 req/s for 0–30 s, ramp to 8 req/s during 30–60 s, 8 req/s to 150 s |
| Outage | 20–110 s, packet-drop on configured cloud paths; Edge1–Edge2 retained |
| Deadline | 500 ms |
| Repetitions | 3 per condition, paired with an uninterrupted run |
| Resources | 2 vCPU / 512 MiB per edge; control on vCPUs 8–15 |

## Metric

`R_srv` = fraction of *all* requests scheduled during the stable interruption
that return correctly at Edge1 within 500 ms of planned arrival, including
failures in the denominator. Panel (a) bins actual timely returns; panel (b)
reports per-cohort deadline yield in non-overlapping 5-s bins.

## Files

- Summary: `data/summaries/fig08_domain_resilience/comparison-v3-results.csv`
- Report: `data/summaries/fig08_domain_resilience/domain-resilience-results.md`
- Design: `data/summaries/fig08_domain_resilience/experiment-design.md`
- Figure script: `figures/compact_remaining_figures.py` (cold-domain panel),
  `data/summaries/fig08_domain_resilience/plot_cold_domain_returns.py`
- Driver: `code/tide/scripts/revision/` domain-resilience campaign scripts

## Results

Mean `R_srv` across the interruption: 88.77% Tide, 14.97% KubeEdge/EdgeMesh,
14.27% Ray Serve. High-load interruption cohort (60–110 s): 87.03% / 0.00% /
0.00%. Uninterrupted: 88.13% / 49.77% / 99.12%. Tide's peer starts returning at
47.44–47.62 s in all three interrupted runs.

## Caveats

The advantage concerns activating an initially idle peer. With both edges
predeployed, outage yields are 87.95% / 100.00% / 99.94%, so the baselines can
share existing replicas without cloud. All systems have structural
`R_cap = 1` under the fixed compatible inventory; differences are operational,
not structural.
