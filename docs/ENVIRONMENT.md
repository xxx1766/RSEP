# Environment

This file records the testbed as it was at measurement time. Values marked
"campaign" differ between experiment families and are listed in the table.

## 1. Hosts

| Role | Label | Spec |
|---|---|---|
| Cloud / controller | NJ-k1 | 16 vCPU Intel Xeon Platinum 8163 @ 2.50 GHz, KVM guest, 1 NUMA node |
| End-side | NJ-k2 | Same class as NJ-k1; slower relative to NJ-k3 in the cluster experiments |
| End-side | NJ-k3 | Same class as NJ-k1; faster processor than NJ-k2 |
| Optional WAN peer | BJ-k1 | Beijing site used for cross-region checks; not used for the main WAN cell |

- Kernel: `Linux 6.12.95+deb13-amd64` (Debian 13), x86_64.
- Virtualization: KVM, SMT disabled; L3 16 MiB.
- CPU affinity is set explicitly per experiment (`taskset`), because the
  controller and the application workers must not share cores.

> The paper's "4-CPU edge node" and "2-vCPU worker" statements refer to
> *cgroup quotas and affinity masks*, not to the physical core count of the
> host. A host has 16 vCPUs; a worker is limited to two.

## 2. Software

| Component | Version | Used in |
|---|---|---|
| Go | 1.22 (toolchain 1.22.2) | Tide and all workers |
| containerd | 1.6.x | container lifecycle |
| cgroup | v2 (unified hierarchy) | CPU/PSS accounting, cpuset binding |
| Kubernetes | **v1.32.2** in the Oct-14/15 cluster campaigns; **v1.34.2** in the WAN campaign | E1/E9; E5 |
| KubeEdge | 1.23.1 (EdgeCore, CloudCore) | E1, E2, E4, E9 |
| EdgeMesh | 1.17.0 | E4 domain routing |
| Ray Serve | 2.58.0 | E1, E2, E4, E9 |
| Python | 3.11–3.14 | analysis and plotting |
| matplotlib / numpy / pandas | 3.11.2 / 2.5.3 / 3.0.6 (artifact snapshot) | figures |

Kubernetes version evidence is shipped as
`data/summaries/fig09_wan_goodput/kubernetes-version.json`; the cluster
campaigns' API-server image tags (`k8s.io/kube-apiserver:v1.32.2`) appear in
their frozen preflight records.

## 3. Network shaping

WAN cell (E5), applied only to the NJ-k1 ↔ NJ-k2 path:

| Parameter | Value |
|---|---|
| Rate cap | 1 Gbit/s per direction |
| Delay | 32 ms ± 5 ms |
| Loss | 0.1% |
| Median RTT | 64.2–65.2 ms |
| Measured 8-stream TCP | 144–168 Mb/s |

Domain-resilience cell (E4): packet-drop rules on the configured cloud paths
from 20 s to 110 s; Edge1–Edge2 path untouched. The rule and restore audits
are summarized in
`data/summaries/fig08_domain_resilience/domain-resilience-results.md`.

## 4. Workload kernels

All systems execute the same Go binary/kernel:

| Kernel | Definition |
|---|---|
| CPU | 100 iterations × 65 536 B |
| CPU (cost study) | 29 / 77 req/s variant |
| Memory-access | 8 / 21 req/s variant |
| Synchronous I/O | host-backed ext4, 10-s windows |
| Inference | YOLOX-Nano, one inference per worker at a time |

Worker budgets: 2 vCPU quota + 512 MiB limit unless stated; Go parallelism 1.

## 5. Reproducing the host side

`code/tide/scripts/setup-paper-cgroup.sh` prepares cgroup v2 delegation and
the cpuset layout. `code/tide/scripts/run-matrix.sh` is the generic matrix
driver. Both expect root and a Linux host; read the header comments before
running, because they modify host cgroup state.

## 6. Non-reproducible details

- The three hosts are VMs on a shared provider; absolute throughput is not
  portable, and the paper's claims are relative within a campaign.
- Container image digests are recorded in `plan.json`; the images themselves
  are not shipped.
- KubeEdge carries persistent deployment history across campaigns; the control
  cost paper states this and does not claim a clean-slate comparison.
