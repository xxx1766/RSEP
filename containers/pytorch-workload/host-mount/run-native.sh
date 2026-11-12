#!/bin/sh
set -eu

: "${IMAGE:=docker.io/tide/fig5-piei-20261010:amd64}"
: "${HOST_ROOT:?set HOST_ROOT to the directory containing deps, source, workdir}"
: "${TIDE_IN_MQ:?set TIDE_IN_MQ to the Stub-to-executor queue name}"
: "${TIDE_OUT_MQ:?set TIDE_OUT_MQ to the executor-to-Stub queue name}"
: "${CONTAINER_ID:=tmc-native}"
: "${CTR_NAMESPACE:=tide}"

exec ctr -n "$CTR_NAMESPACE" run --rm --ipc-host \
    --mount "type=bind,src=$HOST_ROOT,dst=/legacy,options=rbind:rw" \
    --env LEGACY_ROOT=/legacy \
    --env PYTHONPATH=/legacy/deps:/legacy/source/workload:/legacy/source/YOLOX-0.3.0 \
    --env PYTHONDONTWRITEBYTECODE=1 \
    --env MPLCONFIGDIR=/legacy/tmp/matplotlib \
    --env TIDE_IN_MQ="$TIDE_IN_MQ" \
    --env TIDE_OUT_MQ="$TIDE_OUT_MQ" \
    --env TIDE_TORCH_THREADS="${TIDE_TORCH_THREADS:-2}" \
    --env TIDE_OPENCV_THREADS=1 \
    --env TIDE_DETECTIONS_PER_REQ=10 \
    "$IMAGE" "$CONTAINER_ID" \
    python3 -u -c 'import torch,runpy; torch.set_num_interop_threads(8); runpy.run_path("/legacy/source/workload/native_worker.py",run_name="__main__")'
