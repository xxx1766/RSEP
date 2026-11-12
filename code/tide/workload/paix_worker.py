"""PAIX mode: POSIX-mqueue worker speaking the Tide Stub↔Executor protocol.

Used for EI=on runs (cells C3-C6). One-to-one with the Go side in
pkg/executor/instance/base_instance.go and pkg/interfaces/executor.go.

Queue names — passed via env:
  TIDE_IN_MQ       <execId>-in   — Stub → this container (we read)
  TIDE_OUT_MQ      <execId>-out  — this container → Stub (we write)

Wire format: raw protobuf bytes of execins.Event, one message per mq_send.
Queues are created by the Go side (O_CREAT|O_EXCL) with
attr={MsgSize: 1<<24, MaxMsg: 65535}; we open them read-write without creating.

Protocol:
  1. Send {type: INIT} on TIDE_OUT_MQ to signal readiness.
  2. Loop: receive on TIDE_IN_MQ → decode Event
       - ROUTINE_SUBMIT: run inference on routine.args (JPEG bytes), send
         {type: EXIT, status: SUCC} on TIDE_OUT_MQ
       - GET_WAIT / other: ignore (the StubFn executor path never sees them)
  3. Exit cleanly on SIGTERM / SIGINT.

Environment:
  TIDE_WORKLOAD_FORCE_CPUBURN=1  skip ONNX, sleep TIDE_WORKLOAD_MS instead.
"""
from __future__ import annotations

import os
import signal
import sys
import time

try:
    import posix_ipc
except ImportError:
    print("paix_worker: posix_ipc not installed; build image with the paix layer.",
          file=sys.stderr)
    sys.exit(3)

sys.path.insert(0, "/app")
from execinspb import proto_pb2 as pb  # type: ignore  # noqa: E402


MQ_MSG_MAX = 65535
MQ_MSG_SIZE = 1 << 24
BURN = os.environ.get("TIDE_WORKLOAD_FORCE_CPUBURN", "0") == "1"
BURN_MS = int(os.environ.get("TIDE_WORKLOAD_MS", "40"))
DETS_PER_REQ = max(1, int(os.environ.get("TIDE_DETECTIONS_PER_REQ", "10")))


def _open_mq(name: str) -> "posix_ipc.MessageQueue":
    full = "/" + name if not name.startswith("/") else name
    return posix_ipc.MessageQueue(
        full,
        max_messages=MQ_MSG_MAX,
        max_message_size=MQ_MSG_SIZE,
    )


def _cpu_burn(ms: int) -> None:
    deadline = time.perf_counter() + ms / 1000.0
    x = 0
    while time.perf_counter() < deadline:
        for _ in range(1024):
            x = (x * 1103515245 + 12345) & 0x7fffffff


def _run_inference(args_bytes: bytes) -> None:
    if BURN:
        _cpu_burn(BURN_MS * DETS_PER_REQ)
        return
    from infer import infer_once
    for _ in range(DETS_PER_REQ):
        infer_once(args_bytes)


def _send_event(out_mq: "posix_ipc.MessageQueue", evt: pb.Event) -> None:
    out_mq.send(evt.SerializeToString(), priority=0)


def main() -> int:
    in_name = os.environ.get("TIDE_IN_MQ")
    out_name = os.environ.get("TIDE_OUT_MQ")
    if not in_name or not out_name:
        print(
            f"paix_worker: TIDE_IN_MQ/TIDE_OUT_MQ not set (got in={in_name!r} out={out_name!r})",
            file=sys.stderr,
        )
        return 2

    print(f"paix_worker: opening in={in_name} out={out_name} burn={BURN} burn_ms={BURN_MS}",
          file=sys.stderr, flush=True)
    in_mq = _open_mq(in_name)
    out_mq = _open_mq(out_name)

    if not BURN:
        from infer import _ensure_session
        _ensure_session()

    init = pb.Event()
    init.type = pb.Event.INIT
    _send_event(out_mq, init)
    print("paix_worker: INIT sent, entering event loop", file=sys.stderr, flush=True)

    stop = {"flag": False}

    def _graceful(signum, _frame):
        stop["flag"] = True
        print(f"paix_worker: signal {signum} received, draining", file=sys.stderr, flush=True)
    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    served = 0
    while not stop["flag"]:
        try:
            data, _prio = in_mq.receive(timeout=0.5)
        except posix_ipc.BusyError:
            continue
        except posix_ipc.SignalError:
            continue
        except OSError as e:
            if getattr(e, "errno", None) in (11, 35, 110):
                continue
            raise

        evt = pb.Event()
        try:
            evt.ParseFromString(data)
        except Exception as e:
            print(f"paix_worker: parse event failed: {e}", file=sys.stderr)
            continue

        if evt.type == pb.Event.ROUTINE_SUBMIT:
            _run_inference(evt.routine.args)
            exit_evt = pb.Event()
            exit_evt.type = pb.Event.EXIT
            exit_evt.status = pb.Event.SUCC
            _send_event(out_mq, exit_evt)
            served += 1
        else:
            print(f"paix_worker: ignoring event type={evt.type}", file=sys.stderr)

    print(f"paix_worker: exiting after serving {served} routines",
          file=sys.stderr, flush=True)
    try:
        in_mq.close()
        out_mq.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
