
FROM python:3.11.16-slim-trixie

ARG TORCH_WHL=torch-2.0.1+cpu-cp311-cp311-linux_x86_64.whl
ARG TORCH_WHL_URL=https://download.pytorch.org/whl/cpu/torch-2.0.1%2Bcpu-cp311-cp311-linux_x86_64.whl
ARG TORCH_WHL_SHA256=274d4acf486ef50ce1066ffe9d500beabb32bde69db93e3b71d0892dd148956c
ARG TORCHVISION_WHL=torchvision-0.15.2+cpu-cp311-cp311-linux_x86_64.whl
ARG TORCHVISION_WHL_URL=https://download.pytorch.org/whl/cpu/torchvision-0.15.2%2Bcpu-cp311-cp311-linux_x86_64.whl
ARG TORCHVISION_WHL_SHA256=1633c0b7ead69d2cd3fbe8aeaa146c76a68691bd3599facfffd2da925973e044

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gcc \
        libgomp1 \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o "/tmp/$TORCH_WHL" "$TORCH_WHL_URL" \
    && echo "$TORCH_WHL_SHA256  /tmp/$TORCH_WHL" | sha256sum -c - \
    && curl -fsSL -o "/tmp/$TORCHVISION_WHL" "$TORCHVISION_WHL_URL" \
    && echo "$TORCHVISION_WHL_SHA256  /tmp/$TORCHVISION_WHL" | sha256sum -c -

COPY requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir --no-warn-script-location \
        --target /legacy/deps \
        "/tmp/$TORCH_WHL" "/tmp/$TORCHVISION_WHL" \
        -r /tmp/requirements.txt

RUN apt-get purge -y gcc python3-dev && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* "/tmp/$TORCH_WHL" "/tmp/$TORCHVISION_WHL"

COPY context/source /legacy/source
COPY context/downloads /legacy/downloads
COPY context/workdir /legacy/workdir
COPY context/app/execinspb /app/execinspb

RUN mkdir -p /legacy/workdir/models /legacy/tmp/matplotlib \
    && cp /legacy/downloads/yolox_nano.pth /legacy/workdir/models/yolox_nano.pth \
    && chmod 1777 /legacy/tmp

COPY entrypoint.sh /usr/local/bin/pytorch-workload-entrypoint
RUN chmod 0755 /usr/local/bin/pytorch-workload-entrypoint

ENV LEGACY_ROOT=/legacy \
    PYTHONPATH=/legacy/deps:/legacy/source/workload:/legacy/source/YOLOX-0.3.0 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/legacy/tmp/matplotlib \
    TIDE_DETECTIONS_PER_REQ=10 \
    TIDE_TORCH_THREADS=2 \
    TIDE_TORCH_INTEROP_THREADS=8 \
    TIDE_OPENCV_THREADS=1 \
    WORKLOAD_MODE=native \
    PYTHONUNBUFFERED=1

WORKDIR /legacy
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/pytorch-workload-entrypoint"]
CMD ["native"]
