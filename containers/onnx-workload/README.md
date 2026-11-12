# ONNX workload image

Build kind: reconstructed. The measured image is an ONNX base plus an audited
script layer, recorded in `MEASURED_IMAGE.md`.

```sh
docker build -t tmc/onnx-workload:local containers/onnx-workload
```

The build uses `python:3.11.16-slim-trixie`, the pinned ONNX Runtime/OpenCV
stack from the measured base, and the upstream `yolox_nano.onnx` release asset.

| Mode | Command | Use |
|---|---|---|
| `http` | default entrypoint | Flask/gunicorn HTTP `/infer` |
| `paix` | `--mode=paix` | POSIX-mqueue Tide executor |
| `cpuburn` | `--mode=cpuburn` | pipeline smoke test without the model |

Campaign settings: `TIDE_DETECTIONS_PER_REQ=10`, one OpenCV thread, and the
application cpuset supplied by the launcher.

## Measured-image append

`append_measured_layer.py` is the recovered builder for
`docker.io/tide/fig5-piei-20261010:amd64`. It asserts the measured base
manifest and appends the four audited application scripts.

```sh
python3 containers/onnx-workload/append_measured_layer.py \
  --base /path/to/base.oci.tar \
  --workload containers/onnx-workload/context \
  --out /path/to/fig5-piei-audited.oci.tar
```
