# Reproduction sequence

This document is the end-to-end entry point for rebuilding the workload
images and running the preserved baselines. `docs/PROTOCOL.md` fixes the
experiment settings; this document covers the container and deployment steps.

## Prerequisites

| Item | Minimum |
|---|---|
| Host | Linux amd64, Docker or containerd |
| Go | 1.26.2 for the measured worker build |
| Python | 3.11 for the workload image |
| Cluster tools | `kubectl`, `kind` for the KubeEdge baseline |
| Network shaping | `tc` with `netem`, `CAP_NET_ADMIN` |

The measured worker ELF used Go 1.26.2 with CGO enabled and a static external
link. `code/tide/go.mod` separately declares `go 1.22.0` and
`toolchain go1.22.2`; the container builds with the measured compiler without
changing the module declaration.

## Build images

```sh
docker build -t tmc/onnx-workload:local containers/onnx-workload
docker build -t tmc/pytorch-workload:local containers/pytorch-workload
docker build -f containers/revision-worker/Dockerfile --target worker \
  -t tmc/revision-worker:local code/tide
docker build -f containers/revision-worker/Dockerfile --target domain \
  -t tmc/revision-domain-worker:local code/tide
```

| Recipe | Build kind | Recorded identity |
|---|---|---|
| `containers/onnx-workload` | Recovered append step | `docker.io/tide/fig5-piei-20261010:amd64`, `sha256:defacfb3...` |
| `containers/pytorch-workload` | Reconstructed | New local image identity |
| `containers/revision-worker` | Reconstructed | `docker.io/tide/revision-worker:amd64`, `sha256:3d7cf3a3...` |

`containers/onnx-workload/append_measured_layer.py` reproduces the measured
ONNX base plus script layer when the recorded base OCI archive is available.
The Dockerfile in the same directory builds the equivalent runtime from the
base Python image, the pinned packages and the final audited scripts.

Verify the workload inputs before a run:

```sh
sha256sum containers/pytorch-workload/context/downloads/yolox_nano.pth
sha256sum containers/pytorch-workload/context/workdir/assets/dog.jpg
```

Expected values are in `containers/pytorch-workload/README.md`. The YOLOX
0.3.0 source tree is vendored under `context/source/YOLOX-0.3.0/` with its
Apache-2.0 license.

## Native PyTorch workload

The measured layout uses the ONNX image and a host directory at `/legacy`:

```sh
HOST_ROOT=/srv/tmc/legacy TRACE_DIR=/srv/tmc/traces \
  containers/pytorch-workload/host-mount/run-http.sh
```

The host tree must contain `deps/`, `source/workload/`,
`source/YOLOX-0.3.0/`, `downloads/yolox_nano.pth`,
`workdir/assets/dog.jpg` and `workdir/models/yolox_nano.pth`. Install the
dependency tree with the recorded command in
`experiments/current/native_pytorch/DEPENDENCIES.md`.

For PAIX, provide the queue names and share the host IPC namespace:

```sh
HOST_ROOT=/srv/tmc/legacy TIDE_IN_MQ=rsep_in TIDE_OUT_MQ=rsep_out \
  containers/pytorch-workload/host-mount/run-native.sh
```

The self-contained image uses the same program and layout:

```sh
docker run --rm --ipc=host --ulimit msgqueue=67108864:67108864 \
  -e TIDE_IN_MQ=rsep_in -e TIDE_OUT_MQ=rsep_out \
  tmc/pytorch-workload:local native
```

Both transports run one request at a time and execute ten YOLOX-Nano
detections on `dog.jpg`. `TIDE_TORCH_THREADS=2` for C1-C6;
`TIDE_OPENCV_THREADS=1`; `TIDE_DETECTIONS_PER_REQ=10`. The workers pin
themselves to CPUs 2-7. Run with a cpuset that contains those CPUs.

## Kubernetes baselines

Set node names, image tags and the evidence path in `deploy/hosts.example.yaml`,
then render:

```sh
python3 deploy/render.py --hosts deploy/hosts.example.yaml --out deploy/rendered
```

Load the worker image into each node and apply the manifests:

```sh
kubectl apply -f deploy/rendered/kubernetes/namespace.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-quota.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-source.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-deployment.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-service.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-hpa.yaml
```

The elasticity baseline requests 2 CPU and 512 MiB per replica, limits
memory to 1 GiB, targets 60 % CPU utilization with 1 to 18 replicas, and caps
the namespace at 36 CPU and 18 Pods. For the pinned baseline, apply
`worker-service.yaml` and `pinned-worker-pods.yaml` instead.

## Other baselines

- KubeEdge 1.23.1: `deploy/kubeedge/README.md`.
- Ray Serve 2.58.0: `deploy/ray/README.md`.
- WAN delay: `deploy/wan/README.md`.

Each baseline uses the same measured Go worker and the same 2 CPU / 512 MiB
request and limit. Apply the recorded process affinity after startup:
`taskset -c 0,1` for the Go worker, CPUs 2-7 for the PyTorch workload.

## Run checks

Before collecting a campaign, confirm:

- the checkpoint, input and source hashes;
- `torch.__version__ == "2.0.1+cpu"`,
  `torchvision.__version__ == "0.15.2+cpu"`;
- ten completed detections for one request;
- INIT, ROUTINE_SUBMIT and EXIT over the configured PAIX queues;
- the thread and affinity witnesses emitted by `worker_ready`;
- `tc -s qdisc show` matches the configured WAN shape.

Keep the per-run evidence directory, trace files and manifest hashes with the
result set.
