# RSEP

RSEP is a request-centric task scheduling and resource allocation model for the things-edge-cloud continuum. This repository provides the Tide runtime implementation, the workloads and configurations used in the evaluation, compact experiment data, and programs for reproducing the paper and supplementary results.

## Reproduce the results

Use Python 3.12 or later for offline analysis and plotting.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-plot.txt
make check
make figures
make tables
```

Generated figures are written to `figures/rendered/current/`; generated table values are written to `data/generated/tables/`. Offline reproduction uses the included data and requires no testbed connection.

[Results index](docs/RESULTS_INDEX.md) maps each paper and supplementary item to its data and reproduction entrypoint.

## Repository layout

| Directory | Contents |
|---|---|
| `code/tide/` | Tide runtime and experiment programs |
| `code/reproduction/runtime-issue42/Tide/` | Runtime source used by the native PyTorch ablation |
| `experiments/` | Workloads, experiment drivers and configuration templates |
| `containers/` | Image build recipes and worker entrypoints |
| `deploy/` | Baseline deployment configuration |
| `data/compact/current/` | Compact measurements, run statistics and dataset metadata |
| `figures/current/` | Plotting programs and historical figure assets |
| `tools/` | Analysis, validation and packaging entrypoints |

## Build and run

The runtime execution path uses Linux/amd64, cgroup v2, containerd and POSIX message queues. The measured native build used Go 1.26.2 with CGO enabled. Configure hosts, application CPU budgets and endpoints using the experiment templates before running live measurements.

See [environment settings](docs/CURRENT_ENVIRONMENT.md) and the experiment-specific instructions for build inputs and deployment settings. Data definitions retain the measurement windows, request outcomes and repeat counts used for each reported result.

## Data

The release contains the experiment cohorts used by the paper and supplement. Request records use logical node identities and relative times. Dataset metadata describes units, aggregation rules and cohort membership. Historical results retain their source identities. Simulated and native results are indexed separately.

## License and citation

RSEP code is licensed under [Apache-2.0](LICENSE). Included third-party components retain their license notices. Citation metadata is provided in [CITATION.cff](CITATION.cff).
