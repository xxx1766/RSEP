# Tide ablation workload: YOLOX-Nano image recognition worker.
#
# Modes (selected by --mode / TIDE_MODE env):
#   http    — Flask server; POST /infer accepts JPEG bytes, runs inference, returns 200 empty.
#             Used for EI=off baseline (C1, C2).
#   cpuburn — CPU-bound sleep for TIDE_WORKLOAD_MS; skips the model entirely.
#             Dev-time smoke testing for harness pipeline.
#   paix    — Linux POSIX mqueue PAIX protocol. Stub for now; filled in Phase 3.
#             Used for EI=on Tide runs (C3–C6).

FROM python:3.11-slim

# Build deps we drop after install
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        onnxruntime==1.18.1 \
        opencv-python-headless==4.10.0.84 \
        numpy==1.26.4 \
        flask==3.0.3 \
        gunicorn==22.0.0 \
        protobuf==4.25.3

# Paix mode (EI=on, cells C3-C6) needs posix_ipc which builds from source.
# Install gcc only to build the wheel, then uninstall to keep layer small.
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev \
    && pip install --no-cache-dir posix_ipc==1.1.1 \
    && apt-get purge -y gcc python3-dev && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# YOLOX-Nano ONNX weights. License: Apache-2.0 (Megvii-BaseDetection/YOLOX).
# v0.1.1rc0 (2021-08-18) is where the ONNX artifacts live; the v0.3.0 release
# only ships source. The Nano architecture is identical across these tags.
WORKDIR /app
RUN curl -fsSL -o /app/yolox_nano.onnx \
        https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx \
    && test -s /app/yolox_nano.onnx

COPY infer.py         /app/infer.py
COPY entrypoint.py    /app/entrypoint.py
COPY http_worker.py   /app/http_worker.py
COPY paix_worker.py   /app/paix_worker.py
COPY execinspb        /app/execinspb

# Note: WORKLOAD_MODE (not TIDE_MODE) because the Tide Go side injects
# TIDE_MODE=fn|entry into executor containers (see
# pkg/executor/instance/container_ins.go), which would otherwise clash with
# our {http,cpuburn,paix} dispatcher.
ENV WORKLOAD_MODE=http \
    TIDE_BIND=0.0.0.0:8080 \
    TIDE_WORKERS=3 \
    TIDE_WORKLOAD_MS=40 \
    TIDE_MODEL=/app/yolox_nano.onnx \
    PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["python3", "/app/entrypoint.py"]
