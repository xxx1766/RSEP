#!/bin/sh
set -eu

: "${IMAGE:=docker.io/tide/fig5-piei-20261010:amd64}"
: "${HOST_ROOT:?set HOST_ROOT to the directory containing deps, source, workdir}"
: "${TRACE_DIR:?set TRACE_DIR to a writable host directory}"
: "${CONTAINER_ID:=tmc-http}"
: "${CTR_NAMESPACE:=tide}"
: "${TIDE_BIND:=0.0.0.0:8080}"
: "${TIDE_WORKERS:=1}"
: "${WORKLOAD_CPUSET:=2-7}"

exec ctr -n "$CTR_NAMESPACE" run --rm --net-host \
    --mount "type=bind,src=$TRACE_DIR,dst=/trace,options=rbind:rw" \
    --mount "type=bind,src=$HOST_ROOT,dst=/legacy,options=rbind:rw" \
    --env LEGACY_ROOT=/legacy \
    --env PYTHONPATH=/legacy/deps:/legacy/source/workload:/legacy/source/YOLOX-0.3.0 \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --env MPLCONFIGDIR=/legacy/tmp/matplotlib \
    --env FIG5_TRACE_PATH=/trace/http-worker.jsonl \
    --env TIDE_BIND="$TIDE_BIND" \
    --env TIDE_WORKERS="$TIDE_WORKERS" \
    --env WORKLOAD_CPUSET="$WORKLOAD_CPUSET" \
    --env TIDE_TORCH_THREADS=2 \
    --env TIDE_OPENCV_THREADS=1 \
    --env TIDE_DETECTIONS_PER_REQ=10 \
    "$IMAGE" "$CONTAINER_ID" \
    python3 -u -c 'import torch,runpy; torch.set_num_interop_threads(8); runpy.run_path("/legacy/source/workload/http_worker.py",run_name="__main__")'
