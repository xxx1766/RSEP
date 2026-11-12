"""Shared inference loop and per-request execution evidence for both transports."""
import hashlib
import json
import os
import time


def run_request(data, count, infer, *, request_id="", path=""):
    if type(count) is not int or count <= 0:
        raise ValueError("inference count must be a positive integer")
    event = dict(request_id=request_id, path=path, pid=os.getpid(),
                 input_sha256=hashlib.sha256(data).hexdigest(),
                 inferences_requested=count, inferences_completed=0,
                 affinity=sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else [],
                 start_ns=time.time_ns(), success=False)
    try:
        for _ in range(count):
            shape = infer(data)
            event["inferences_completed"] += 1
        event.update(shape=list(shape), success=True)
        return shape
    finally:
        event["finish_ns"] = time.time_ns()
        line = json.dumps(event, separators=(",", ":")) + "\n"
        trace = os.environ.get("FIG5_TRACE_PATH")
        if trace:
            fd = os.open(trace, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)
        else:
            print(line, end="", flush=True)


def record_startup(path):
    event = dict(event="worker_ready", path=path, pid=os.getpid(),
                 affinity=sorted(os.sched_getaffinity(0)),
                 inferences_requested=int(os.environ.get("TIDE_DETECTIONS_PER_REQ", "10")),
                 model_sha256=hashlib.sha256(open("/app/yolox_nano.onnx", "rb").read()).hexdigest())
    line = json.dumps(event) + "\n"
    trace = os.environ.get("FIG5_TRACE_PATH")
    if trace:
        fd = os.open(trace, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try: os.write(fd, line.encode())
        finally: os.close(fd)
    else:
        print(line, end="", flush=True)
