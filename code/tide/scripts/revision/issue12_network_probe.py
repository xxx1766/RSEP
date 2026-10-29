#!/usr/bin/env python3
"""Bounded TCP echo RTT probe for the isolated Beijing–Nanjing path."""

import argparse
import json
import math
import socket
import statistics
import struct
import time
from datetime import datetime, timezone
from pathlib import Path


def summarize(rows: list[dict]) -> dict:
    values = sorted(float(r["rtt_ms"]) for r in rows if "rtt_ms" in r)
    n = len(rows)
    return {
        "sent": n,
        "received": len(values),
        "error_fraction": (n - len(values)) / n if n else None,
        "min_ms": values[0] if values else None,
        "mean_ms": statistics.mean(values) if values else None,
        "median_ms": statistics.median(values) if values else None,
        "p95_ms": values[math.ceil(0.95 * len(values)) - 1] if values else None,
        "max_ms": values[-1] if values else None,
        "stdev_ms": statistics.stdev(values) if len(values) > 1 else (0.0 if values else None),
    }


def receive_exact(conn: socket.socket, size: int) -> bytes:
    received = bytearray()
    while len(received) < size:
        chunk = conn.recv(size - len(received))
        if not chunk:
            raise ConnectionError("peer closed")
        received.extend(chunk)
    return bytes(received)


def serve(address: str, port: int, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    with socket.create_server((address, port), backlog=4) as listener:
        listener.settimeout(1)
        print(json.dumps({"ready": True, "address": address, "port": port}), flush=True)
        while time.monotonic() < deadline:
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(2)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                while time.monotonic() < deadline:
                    try:
                        payload = receive_exact(conn, 8)
                        conn.sendall(payload)
                    except (OSError, ConnectionError):
                        break


def probe(address: str, port: int, count: int, timeout: float) -> dict:
    rows = []
    conn = None
    started = datetime.now(timezone.utc).isoformat()
    try:
        for index in range(count):
            row = {"index": index, "utc": datetime.now(timezone.utc).isoformat()}
            try:
                if conn is None:
                    conn = socket.create_connection((address, port), timeout=timeout)
                    conn.settimeout(timeout)
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                payload = struct.pack("!Q", index)
                start = time.perf_counter_ns()
                conn.sendall(payload)
                echoed = receive_exact(conn, 8)
                row["rtt_ms"] = (time.perf_counter_ns() - start) / 1e6
                if echoed != payload:
                    raise ValueError("echo checksum mismatch")
            except (OSError, ValueError) as error:
                row.pop("rtt_ms", None)
                row["error"] = f"{type(error).__name__}: {error}"
                if conn is not None:
                    conn.close()
                    conn = None
            rows.append(row)
    finally:
        if conn is not None:
            conn.close()
    return {"destination": f"{address}:{port}", "started_utc": started,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
            "probe_bytes": 8, "connection_reused": True,
            "summary": summarize(rows), "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("server", "client"))
    parser.add_argument("address")
    parser.add_argument("--port", type=int, default=29612)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not 20000 <= args.port <= 60000 or not 1 <= args.count <= 10000 or not 1 <= args.seconds <= 3600:
        parser.error("invalid port, count or server duration")
    if args.mode == "server":
        serve(args.address, args.port, args.seconds)
    else:
        result = probe(args.address, args.port, args.count, args.timeout)
        body = json.dumps(result, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(body)
        print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
