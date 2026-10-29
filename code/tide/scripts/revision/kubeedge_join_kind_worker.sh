#!/bin/sh
# Run inside the isolated kind worker container after its kubelet is stopped.
set -eu
IFS= read -r edge_join_token < /root/edge-join-token
exec /usr/local/bin/keadm join \
  --cloudcore-ipport=10.4.1.2:10000 \
  --edgenode-name=tmc-ke-worker \
  --kubeedge-version=v1.23.1 \
  --cgroupdriver=systemd \
  --remote-runtime-endpoint=unix:///run/containerd/containerd.sock \
  --tarballpath=/root \
  --token="$edge_join_token"
