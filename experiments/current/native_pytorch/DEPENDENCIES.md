# Native workload dependencies

The calculation program and transport adapters are in `source/workload/`. The standalone image recipe is `../../../containers/pytorch-workload/Dockerfile`.

| Component | Version |
|---|---|
| Python |3.11 |
| PyTorch CPU |2.0.1+cpu |
| torchvision CPU |0.15.2+cpu |
| NumPy |1.26.4 |
| OpenCV |4.10.0 |
| loguru |0.7.2 |
| pycocotools |2.0.8 |
| YOLOX |0.3.0 |

The Dockerfile verifies the CPU-wheel hashes and includes the matching model and input. `requirements.txt` pins the remaining packages.

For the host-mount layout,`LEGACY_ROOT` contains `deps/`,`source/workload/`,`source/YOLOX-0.3.0/`,`downloads/yolox_nano.pth`,`workdir/assets/dog.jpg` and `workdir/models/yolox_nano.pth`. Install pinned packages into `deps/` and set `PYTHONPATH` to that directory and the released source directories. The native launcher uses two intra-op threads,eight inter-op threads and one OpenCV thread. Application CPUs are2-7.

Native queues have10 slots and262144-byte messages. Configure a64MiB message-queue resource limit and the matching namespaced queue limits. Queue environment names omit the leading slash. Model/input hashes and timing parameters are listed in `../../../docs/CURRENT_ENVIRONMENT.md`.
