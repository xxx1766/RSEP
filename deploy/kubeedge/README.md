# KubeEdge baseline

The KubeEdge baseline runs KubeEdge 1.23.1 on an isolated single-cloud,
single-edge cluster. The cloud side is an in-cluster KubeEdge control plane;
the edge node is a kind worker container converted to EdgeCore while the host
kubelet and containerd remain intact.

## Setup sequence

1. Render the templates and create the kind cluster from `kind-cluster.yaml`.
2. Install KubeEdge 1.23.1 on the control-plane node. Patch the `cloudcore`
   Deployment with `cloudcore-patch.json` and the `cloudcore` cloud manager
   Deployment with `cloud-manager-patch.json`, so both are scheduled on the
   control-plane node.
3. Generate the edge join token on the cloud node and copy it to the worker
   container as `/root/edge-join-token`. Run the rendered
   `join-edge-worker.sh` after stopping that container's kubelet.
4. Apply the rendered `edge-worker-pod.yaml` to start the measured Go worker
   on the edge node.

```sh
python3 deploy/render.py --hosts deploy/hosts.example.yaml --out deploy/rendered
sh deploy/rendered/kubeedge/join-edge-worker.sh
kubectl apply -f deploy/rendered/kubeedge/edge-worker-pod.yaml
```

| Setting | Measured value |
|---|---|
| KubeEdge | 1.23.1 |
| Edge node name | `tmc-ke-worker` |
| Edge namespace | `tmc-ke-revision` |
| Cloud-core endpoint | documentation placeholder in `hosts.example.yaml` |
| Edge worker | `docker.io/tide/revision-worker:amd64`, 2 CPU / 512 MiB |
| Cloud-core / cloud-manager placement | kind control-plane node |

The join token is generated at setup time and is not part of the artifact. Do
not commit it. The cloud-core address in `hosts.example.yaml` is a
documentation placeholder; set it to the isolated cluster's service address.
