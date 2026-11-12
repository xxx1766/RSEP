# Ray cluster baseline

`cluster.py` is the fixed-capacity Ray Serve driver used by the three-node workload comparisons. Four replicas are placed2/1/1,each wrapping the same Go cluster worker. Ray Serve handles request routing;each replica permits one ongoing request.

Build `cmd/revision-cluster-worker` as `worker` in `RSEP_CLUSTER_ROOT`. Install Ray Serve2.58.0 in the active Python environment. Configure `RSEP_CLUSTER_NODES` as a JSON mapping of logical IDs `nj1`,`nj2`,`nj3` to testbed addresses. Each node starts with four,two,two Ray CPU units respectively;each replica reserves two units.

```sh
python deploy/ray/cluster.py node-start --node nj1
python deploy/ray/cluster.py deploy
python deploy/ray/cluster.py audit
```

Run `node-start` on each configured node before deploying on the head. The driver maintains process identities and worker cgroups under its configured root. `stop-serve` and `stop` clean up its owned processes. The Bare driver is `../bare/cluster.py`;the KubeEdge four-slot manifest and affinity driver is `../kubeedge/cluster.py`.
