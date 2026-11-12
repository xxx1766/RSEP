# Deployment baselines

The templates reproduce the measured CPU, memory, network and placement
settings for the systems baselines. Addresses and node names are placeholders;
set them in `hosts.example.yaml` and render into a separate output directory.

```sh
python3 deploy/render.py --hosts deploy/hosts.example.yaml --out deploy/rendered
```

`render.py` substitutes `${NAME}` placeholders and leaves unknown placeholders
visible rather than dropping them. The output directory can be reapplied
without touching the tracked templates.

## Baselines

| Baseline | Directory | Key settings |
|---|---|---|
| Pinned Kubernetes | `kubernetes/pinned-worker-pods.yaml.tmpl` | 4 Pods, two on cloud and one on each edge, 2 CPU / 512 MiB each, host network |
| Elastic Kubernetes | `kubernetes/elasticity-*.yaml.tmpl` | CPU HPA at 60 %, 1 replica minimum, 18 maximum, 2 CPU request/limit, 512 MiB request / 1 GiB limit, namespace cap 36 CPU / 18 Pods |
| KubeEdge | `kubeedge/` | KubeEdge 1.23.1, one cloud-core node and one edge node, same Go worker |
| Ray Serve | `ray/` | Ray2.58.0, four replicas placed2/1/1 with one serial Go worker each |
| WAN | `wan/` | `tc netem` 65 ms RTT shape |

The Go worker images come from `../containers/revision-worker`. The measured
`docker.io/tide/revision-worker:amd64` identity is recorded there. Replace the
`worker_image` and `inference_image` values in `hosts.example.yaml` when using
locally built tags.

## Kubernetes order

```sh
kubectl apply -f deploy/rendered/kubernetes/namespace.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-quota.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-source.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-deployment.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-service.yaml
kubectl apply -f deploy/rendered/kubernetes/elasticity-hpa.yaml
```

For the pinned baseline, apply `worker-service.yaml` and then
`pinned-worker-pods.yaml`. The Pods use `imagePullPolicy: Never`, matching the
preloaded image on each measured node. Load the worker image into each
container runtime before applying the manifests.

The elasticity Deployment mounts `${EVIDENCE_PATH}` at `/evidence` and the
rendered `issue30-source` ConfigMap at `/issue30`. The ConfigMap contains the
measured `issue30_http_worker.py` unchanged except for the namespace
placeholder.

## Measured resource shape

| Item | Value |
|---|---|
| Application container | 2 CPU request and limit, 512 MiB request, 1 GiB limit |
| Namespace quota | 36 CPU request/limit, 18 Pods |
| HPA | CPU utilization 60 %, min 1, max 18 |
| Worker Pods | 2 CPU request and limit, 512 MiB request and limit |
| Go worker environment | `GOMAXPROCS=1`, `--mode=http --listen=0.0.0.0:19280` |
| Elastic inference ports | 19384 work, 19385 control/health |

The measured runner pinned each Go worker process to CPUs 0 and 1 after
startup. The Pod resource values alone do not replace that affinity step. The
PyTorch request workers pin themselves to CPUs 2-7; use a cpuset containing
those CPUs when running the inference workload.

For the Fig.9 and mixed-QoS comparisons,use the `cluster` worker-image target and the four-worker drivers in `ray/cluster.py`,`bare/cluster.py` and `kubeedge/cluster.py`. Configure the logical host-address mapping and node names before deployment.

The HPA experiment uses Metrics Serverv0.9.1;the official manifest is `kubernetes/metrics-server-v0.9.1.yaml`. Configure trust for the testbed kubelet certificates before enabling metric collection.
