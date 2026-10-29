# E5 — Cross-region WAN placement

**Paper item:** Fig. 9, Table I, abstract headline (1.45–1.62×).

## Question

With inference capacity held fixed, what does request-level placement buy over
fixed client round robin when the remote path has a real WAN RTT?

## Design

| Factor | Values |
|---|---|
| Nodes | NJ-k1 = origin + local worker; NJ-k2 = remote worker |
| Systems | Tide (native Stub placement), K8s RR over the same two workers |
| Workers | 2 prestarted, node-pinned YOLOX-Nano Pods; 2 CPU / 512 MiB requested, 2 CPU / 1 GiB limit |
| Remote shaping | 1 Gbit/s cap each direction, 32 ms delay ± 5 ms, 0.1% loss |
| Median RTT | 64.2–65.2 ms |
| Deadline | 135 ms |
| Arrivals | gradual and burst |
| Repetitions | 3 paired runs per arrival pattern |

The shaper is a configured limit; measured eight-stream TCP throughput with
jitter/loss is 144–168 Mb/s.

## Files

- Summary: `data/summaries/fig09_wan_goodput/summary.json` — per-cell
  offered/success/on-time/goodput, execution audit, and RTT coverage (NJ1/NJ2)
- Network records: `network-nj01.json`, `network-nj02.json`,
  `tcp-rtt-fifo.json`
- Kubernetes version evidence: `kubernetes-version.json` (v1.34.2)
- Figure script: `figures/goodput_yield.py` (8 published table values)
- Original table input: `paper/validation/before-goodput-yield-table.tex`

## Headline

Tide achieves 1.45–1.62× the on-time goodput of K8s RR under matched fixed
inference resources, a 135 ms deadline and ≈65 ms RTT.

## Caveats

The comparison isolates placement, not the scheduler alone: both arms share
the worker image and capacity, but the request paths differ by design. The cap
is configured, not line-rate; measured throughput is reported above.
