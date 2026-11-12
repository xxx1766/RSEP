
FROM python:3.11.16-slim-trixie

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

RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev \
    && pip install --no-cache-dir posix_ipc==1.1.1 \
    && apt-get purge -y gcc python3-dev && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN curl -fsSL -o /app/yolox_nano.onnx \
        https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx \
    && test -s /app/yolox_nano.onnx

COPY context/ /app/

ENV WORKLOAD_MODE=http \
    TIDE_BIND=0.0.0.0:8080 \
    TIDE_WORKERS=3 \
    TIDE_WORKLOAD_MS=40 \
    TIDE_MODEL=/app/yolox_nano.onnx \
    PYTHONUNBUFFERED=1

EXPOSE 8080

ENTRYPOINT ["python3", "/app/entrypoint.py"]
