# Third-party inputs

The artifact's own code is Apache-2.0 (`../LICENSE`). The image recipes
install or vendor the following upstream components. License values are the
upstream package metadata; this file records them without interpreting
redistribution obligations.

| Component | Version | Upstream license |
|---|---|---|
| PyTorch | 2.0.1+cpu | BSD-3-Clause |
| torchvision | 0.15.2+cpu | BSD-3-Clause |
| NumPy | 1.26.4 | BSD-3-Clause |
| OpenCV (headless) | 4.10.0.84 | Apache-2.0 |
| ONNX Runtime | 1.18.1 | MIT |
| Flask | 3.0.3 | BSD-3-Clause |
| gunicorn | 22.0.0 | MIT |
| protobuf | 4.25.3 / 7.36.2 | BSD-3-Clause |
| posix_ipc | 1.1.1 | BSD |
| loguru | 0.7.2 | MIT |
| tqdm | 4.66.5 | MIT |
| thop | 0.1.1.post2209072238 | MIT |
| tabulate | 0.9.0 | MIT |
| psutil | 6.0.0 | BSD-3-Clause |
| pycocotools | 2.0.8 | FreeBSD |
| matplotlib | 3.8.4 | PSF |
| TensorBoard | 2.18.0 | Apache-2.0 |
| Pillow | 10.4.0 | HPND |
| YOLOX source | 0.3.0 | Apache-2.0 |
| YOLOX-Nano ONNX artifact | 0.1.1rc0 release | Apache-2.0 |

The vendored YOLOX 0.3.0 source snapshot includes its upstream `LICENSE` at
`pytorch-workload/context/source/YOLOX-0.3.0/LICENSE`. The Go worker image
vendors the Go module dependencies declared in `code/tide/go.mod`; their
license files remain in the upstream module sources.
