# Measured host-mount recipe

The measured native and HTTP campaigns did not use a PyTorch image. They ran
`docker.io/tide/fig5-piei-20261010:amd64`, an ONNX base plus the audited
application-script layer, and bound a host directory at `/legacy`.

The host directory had this layout:

```
deps/                  dependency target installed by the recorded pip command
source/workload/       calculation program, adapter and native/http workers
source/YOLOX-0.3.0/    model source
downloads/yolox_nano.pth
workdir/assets/dog.jpg
workdir/models/yolox_nano.pth
tmp/matplotlib/
```

The dependency command recorded for the campaign installed into
`/evidence/deps`; at run time the same directory was visible as `/legacy/deps`.
The exact command and wheel hashes are in
`experiments/current/native_pytorch/DEPENDENCIES.md`.

## Native command shape

The measured launcher used:

```
env LEGACY_ROOT=/legacy \
    PYTHONPATH=/legacy/deps:/legacy/source/workload:/legacy/source/YOLOX-0.3.0 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/legacy/tmp/matplotlib \
    TIDE_DETECTIONS_PER_REQ=10 \
    python3 -u /legacy/source/workload/native_worker.py
```

The container also needs `TIDE_IN_MQ`, `TIDE_OUT_MQ`, `TIDE_TORCH_THREADS`
(two for C1-C6, one for C7) and one OpenCV thread. The Go side creates the
queues with a 16 MiB message size and 65535 maximum messages; the container
must share the host IPC namespace. Per-worker evidence is written under
`/legacy/native-evidence/`, so the bind mount must be writable.

`run-native.sh` reproduces this command with parameterized environment
variables. `run-http.sh` reproduces the HTTP variant, including the trace
directory mount and `TIDE_WORKERS` count.

## CPU layout

The measured application cpuset was CPUs 2-7, with CPUs 0-1 reserved for the
driver and sampling. The current worker source pins itself to 2-7. Reproduce
that cpuset; the container request/limit alone does not renumber the measured
affinity mask.
