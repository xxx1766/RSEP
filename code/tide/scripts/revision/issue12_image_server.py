#!/usr/bin/env python3
"""Single-inference YOLOX-Nano service with serialized compute and raw evidence."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time


def verify_output(shape, finite, actual_digest, reference_digest):
    if list(shape) != [1, 3549, 85] or not finite or actual_digest != reference_digest:
        raise ValueError("YOLOX output shape, finiteness, or deterministic reference mismatch")
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=19283)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    os.sched_setaffinity(0, {8, 9})
    import cv2
    import numpy as np
    from infer import _ensure_session, preprocess
    session = _ensure_session()
    input_name = session.get_inputs()[0].name
    a.output.mkdir(parents=True, exist_ok=True)
    input_dir = a.output / "inputs"
    input_dir.mkdir(exist_ok=True)
    source = cv2.imdecode(np.frombuffer(a.input.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if source is None:
        raise RuntimeError("invalid source JPEG")
    references, inputs, manifest = {}, [], []

    def infer(body):
        tensor = session.run(None, {input_name: preprocess(body)})[0]
        return list(tensor.shape), bool(np.isfinite(tensor).all()), hashlib.sha256(tensor.tobytes()).hexdigest()

    for index, size in enumerate((224, 416, 640, 960)):
        scale = size / max(source.shape[:2])
        resized = cv2.resize(source, (round(source.shape[1] * scale), round(source.shape[0] * scale)))
        ok, data = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        body = data.tobytes()
        digest = hashlib.sha256(body).hexdigest()
        shape, finite, reference = infer(body)
        verify_output(shape, finite, reference, reference)
        # Repeat inference independently to verify deterministic results before serving.
        start = time.monotonic_ns()
        shape, finite, repeated = infer(body)
        service_ns = time.monotonic_ns() - start
        verify_output(shape, finite, repeated, reference)
        inputs.append(body)
        references[digest] = reference
        (input_dir / f"image-{index}.jpg").write_bytes(body)
        manifest.append(dict(index=index, long_side=size, bytes=len(body), input_sha256=digest,
                             output_sha256=reference, shape=shape, warm_service_ns=service_ns,
                             checksum=str(int.from_bytes(bytes.fromhex(digest)[:8], "little"))))
    metadata = dict(model_sha256=hashlib.sha256(Path(os.environ["TIDE_MODEL"]).read_bytes()).hexdigest(),
                    source_sha256=hashlib.sha256(a.input.read_bytes()).hexdigest(), inputs=manifest,
                    affinity=sorted(os.sched_getaffinity(0)), intra_threads=os.environ.get("ORT_INTRA_THREADS"),
                    compute_concurrency=1, detections_per_request=1, tcp_nodelay=True,
                    description="four resolutions of the same archived dog image; not recovered conference inputs")
    (a.output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    compute_lock, log_lock = threading.Lock(), threading.Lock()
    log = (a.output / "requests.jsonl").open("a", buffering=1)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, *_):
            pass

        def reply(self, status, body, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, b'{"ready":true}')
            elif self.path == "/manifest":
                self.reply(200, json.dumps(metadata).encode())
            elif self.path.startswith("/input/") and self.path[7:] in ("0", "1", "2", "3"):
                self.reply(200, inputs[int(self.path[7:])], "image/jpeg")
            else:
                self.reply(404, b"{}")

        def do_POST(self):
            received_ns = time.time_ns()
            row = dict(request_id=self.headers.get("X-Request-ID", ""), received_unix_ns=received_ns,
                       peer=self.client_address[0])
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.path != "/infer" or not 1 <= size <= 1048576:
                    raise ValueError("bounded JPEG body required")
                body = self.rfile.read(size)
                digest = hashlib.sha256(body).hexdigest()
                if len(body) != size or digest not in references:
                    raise ValueError("input does not match frozen image set")
                queued = time.monotonic_ns()
                with compute_lock:
                    started = time.monotonic_ns()
                    shape, finite, output_digest = infer(body)
                    verify_output(shape, finite, output_digest, references[digest])
                    completed = time.monotonic_ns()
                row.update(status="success", input_sha256=digest, output_sha256=output_digest,
                           checksum=str(int.from_bytes(bytes.fromhex(digest)[:8], "little")),
                           shape=shape, finite=finite, bytes=size,
                           queue_ns=started-queued, service_ns=completed-started)
                status = 200
            except Exception as error:
                row.update(status="error", error=f"{type(error).__name__}: {error}")
                status = 400
            row["completed_unix_ns"] = time.time_ns()
            with log_lock:
                log.write(json.dumps(row) + "\n")
            try:
                self.reply(status, json.dumps(row).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

    class ImageHTTPServer(ThreadingHTTPServer):
        request_queue_size = 256

    server = ImageHTTPServer(("0.0.0.0", a.port), Handler)
    print(json.dumps(dict(ready=True, port=a.port, metadata=metadata)), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
