# PyTorch workload image

Build kind: reconstructed, self-contained. This image is separately
identified from the measured ONNX runtime image.

```sh
docker build -t tmc/pytorch-workload:local containers/pytorch-workload
docker run --rm --ipc=host --ulimit msgqueue=67108864:67108864 \
  -e TIDE_IN_MQ=rsep_in -e TIDE_OUT_MQ=rsep_out \
  tmc/pytorch-workload:local native
```

## Build inputs

| Input | Value |
|---|---|
| Base | `python:3.11.16-slim-trixie` |
| torch | `torch-2.0.1+cpu-cp311-cp311-linux_x86_64.whl`, SHA-256 `274d4acf486ef50ce1066ffe9d500beabb32bde69db93e3b71d0892dd148956c` |
| torchvision | `torchvision-0.15.2+cpu-cp311-cp311-linux_x86_64.whl`, SHA-256 `1633c0b7ead69d2cd3fbe8aeaa146c76a68691bd3599facfffd2da925973e044` |
| YOLOX-Nano checkpoint | SHA-256 `cd28f55fbbc1829f99d9ac9b38a16d259a22889739c8728ea877610201feff7b` |
| Input image | SHA-256 `6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620` |
| YOLOX source | 0.3.0 snapshot, Apache-2.0; license included |

The Dockerfile verifies both CPU-wheel SHA-256 values before installing them.
`requirements.txt` pins the remaining Python packages. The checkpoint is
placed at `/legacy/downloads/yolox_nano.pth` and
`/legacy/workdir/models/yolox_nano.pth`; `user_program.init()` loads the latter.

## Runtime layout

```
/legacy/deps/                  Python packages
/legacy/source/workload/       calculation program, adapter and workers
/legacy/source/YOLOX-0.3.0/    model source
/legacy/downloads/yolox_nano.pth
/legacy/workdir/assets/dog.jpg
/legacy/workdir/models/yolox_nano.pth
/legacy/tmp/matplotlib/
/app/execinspb/proto_pb2.py    PAIX protobuf bindings
```

| Mode | Command | Runtime variables |
|---|---|---|
| `native` | `<image> native` | `TIDE_IN_MQ`, `TIDE_OUT_MQ` |
| `http` | `<image> http` | `FIG5_TRACE_PATH`, `TIDE_WORKERS` |

Both transports execute ten complete YOLOX-Nano detections per request.
Defaults: `TIDE_TORCH_THREADS=2`, `TIDE_OPENCV_THREADS=1`,
`TIDE_DETECTIONS_PER_REQ=10`. The workers pin themselves to CPUs 2-7, the
measured application slice; provide a cpuset that contains those CPUs.

`host-mount/` preserves the measured layout in which the ONNX image is used
with a host directory bound at `/legacy`; use those scripts when reproducing
the measured runtime rather than the self-contained image.

The launcher sets eight PyTorch inter-op threads. Native queues use262144-byte messages and10 slots;set the message-queue resource limit to64MiB and provision the corresponding IPC namespace limits. Queue environment values omit the leading slash.
