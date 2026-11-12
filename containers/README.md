# Container recipes

| Recipe | Build kind | Output |
|---|---|---|
| `onnx-workload/` | Reconstructed ONNX Runtime image | `tmc/onnx-workload:local` |
| `pytorch-workload/` | Reconstructed self-contained PyTorch image | `tmc/pytorch-workload:local` |
| `revision-worker/` | Reconstructed Go worker images | `tmc/revision-worker:local`, `tmc/revision-domain-worker:local` |

The measured identities are recorded in `onnx-workload/MEASURED_IMAGE.md` and
`revision-worker/README.md`. Third-party license metadata is in
`LICENSE_INPUTS.md`.

```sh
docker build -t tmc/onnx-workload:local containers/onnx-workload
docker build -t tmc/pytorch-workload:local containers/pytorch-workload
docker build -f containers/revision-worker/Dockerfile --target worker \
  -t tmc/revision-worker:local code/tide
```

`deploy/` contains parameterized Kubernetes, KubeEdge, Ray and WAN baselines.
See `../docs/REPRODUCTION.md` for the end-to-end sequence.
