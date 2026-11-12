"""HTTP mode: WSGI app served by gunicorn with TIDE_WORKERS sync workers.

Used by the EI=off baseline (cells C1, C2). The Go harness (cmd/httpbase)
runs this container and posts JPEG bytes to /infer. TIDE_WORKERS env sets
the gunicorn process count — 3 for CC=on, 6 for CC=off.

Why gunicorn multi-process (not Flask threaded):
  The earlier `app.run(threaded=True)` + `threading.Semaphore(N)` design
  serialized Python bytecode through the GIL (only released during ONNX
  C++ calls), so N "concurrent" worker threads effectively ran 1-at-a-time
  for Python preprocessing / protocol. This made CC=on slower than CC=off
  in our measurements — the opposite of the referenced §5.1.2 Table 5-4. Switching
  to N OS processes (gunicorn -w N --worker-class sync) gives true
  parallelism matching paper Fig. 5-1(b)'s "N-element queue".

TIDE_WORKLOAD_FORCE_CPUBURN=1 skips the model and spins CPU for
TIDE_WORKLOAD_MS milliseconds instead — dev smoke path.
"""
from __future__ import annotations

import os
import sys
import time

from flask import Flask, request

_BURN = os.environ.get("TIDE_WORKLOAD_FORCE_CPUBURN", "0") == "1"
_BURN_MS = int(os.environ.get("TIDE_WORKLOAD_MS", "40"))
_DETS_PER_REQ = max(1, int(os.environ.get("TIDE_DETECTIONS_PER_REQ", "10")))

app = Flask(__name__)


def _cpu_burn(ms: int) -> None:
    deadline = time.perf_counter() + ms / 1000.0
    x = 0
    while time.perf_counter() < deadline:
        for _ in range(1024):
            x = (x * 1103515245 + 12345) & 0x7fffffff


@app.route("/infer", methods=["POST"])
def infer():
    if _BURN:
        _cpu_burn(_BURN_MS * _DETS_PER_REQ)
        return "", 200
    from infer import infer_once
    body = request.get_data()
    for _ in range(_DETS_PER_REQ):
        infer_once(body)
    return "", 200


@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200


if not _BURN:
    try:
        from infer import _ensure_session
        _ensure_session()
    except Exception as _exc:  # noqa: BLE001
        print(f"[http_worker] warning: ORT warm-on-import failed: {_exc}",
              file=sys.stderr, flush=True)


def main() -> int:
    bind = os.environ.get("TIDE_BIND", "0.0.0.0:8080")
    workers = max(1, int(os.environ.get("TIDE_WORKERS", "3")))
    args = [
        "gunicorn",
        "-w", str(workers),
        "-b", bind,
        "--worker-class", "sync",
        "--timeout", "120",
        "--access-logfile", "-",
        "--log-level", "warning",
        "--graceful-timeout", "5",
        "http_worker:app",
    ]
    print(f"[http_worker] exec {' '.join(args)}", file=sys.stderr, flush=True)
    os.execvp("gunicorn", args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
