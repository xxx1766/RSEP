"""Minimal YOLOX-Nano inference.

Decodes a JPEG from bytes, preprocesses to 416x416 letterbox, runs the ONNX
model, returns the raw output tensor shape. We do NOT post-process (no NMS, no
class mapping) — the user's requirement is "fire image recognition, no result
needed". The model run itself is the cost we're measuring.
"""
from __future__ import annotations

import os
import threading
from typing import Tuple

import cv2
import numpy as np
import onnxruntime as ort

_MODEL_PATH = os.environ.get("TIDE_MODEL", "/app/yolox_nano.onnx")
_INPUT_SIZE = (416, 416)

_session_lock = threading.Lock()
_session: ort.InferenceSession | None = None
_input_name: str | None = None


def _ensure_session() -> ort.InferenceSession:
    global _session, _input_name
    if _session is None:
        with _session_lock:
            if _session is None:
                # ORT thread count = per-task CPU allocation. Paper RSEP §5.1
                # specifies "每个计算任务的资源需求为 2 核心"; without intra_op=2
                # each task only uses 1 core, so CC=on (3 workers in 6-core
                # slice) ends up under-utilizing the 6 cores it's allocated
                # while CC=off (6 workers × 1 thread) saturates them — that
                # inverts paper Fig. 5-2 / Table 5-4. With intra_op=2 each
                # task uses 2 cores, matching paper's 6 cores/(2 cores/task)
                # = 3-concurrent semantics. Override via env if you need to
                # sweep workload weights (e.g. =1 for the light-workload
                # sensitivity comparison, =3 for 3-core/task experiments).
                intra = max(1, int(os.environ.get("ORT_INTRA_THREADS", "2")))
                inter = max(1, int(os.environ.get("ORT_INTER_THREADS", "1")))
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = intra
                opts.inter_op_num_threads = inter
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                _session = ort.InferenceSession(_MODEL_PATH, sess_options=opts,
                                                providers=["CPUExecutionProvider"])
                _input_name = _session.get_inputs()[0].name
    return _session


def preprocess(img_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("cv2.imdecode returned None (bad JPEG?)")
    ih, iw = img.shape[:2]
    th, tw = _INPUT_SIZE
    scale = min(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((th, tw, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    # HWC BGR → CHW RGB float32 matches Megvii YOLOX preprocessing for ONNX.
    x = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32)
    return x[None]  # add batch dim


def infer_once(img_bytes: bytes) -> Tuple[int, ...]:
    """Decode + preprocess + one forward pass. Returns output shape.

    Called per request; session is shared across calls inside one process.
    Thread-safe because ORT sessions are thread-safe for run().
    """
    sess = _ensure_session()
    x = preprocess(img_bytes)
    outs = sess.run(None, {_input_name: x})
    return tuple(outs[0].shape)
