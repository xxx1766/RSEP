#!/usr/bin/env python3
"""Estimate interhost clock offset through a bounded TCP timestamp exchange."""

import argparse
import json
import socket
import struct
import time
from datetime import datetime, timezone
from pathlib import Path


def estimate(client_send_ns: int, server_ns: int, client_receive_ns: int) -> dict:
    rtt = client_receive_ns - client_send_ns
    return {"rtt_ms": rtt / 1e6,
            "server_minus_client_midpoint_ms": (server_ns - (client_send_ns + client_receive_ns) / 2) / 1e6,
            "offset_uncertainty_ms_at_least": rtt / 2e6}


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
                try:
                    if len(conn.recv(8)) == 8:
                        conn.sendall(struct.pack("!Q", time.time_ns()))
                except OSError:
                    pass


def client(address: str, port: int, count: int) -> dict:
    rows = []
    for index in range(count):
        row = {"index": index}
        try:
            with socket.create_connection((address, port), timeout=2) as conn:
                conn.settimeout(2)
                start = time.time_ns()
                conn.sendall(struct.pack("!Q", index))
                body = bytearray()
                while len(body) < 8:
                    chunk = conn.recv(8 - len(body))
                    if not chunk:
                        raise ConnectionError("short timestamp reply")
                    body.extend(chunk)
                end = time.time_ns()
                row.update(estimate(start, struct.unpack("!Q", body)[0], end))
        except OSError as error:
            row["error"] = f"{type(error).__name__}: {error}"
        rows.append(row)
    clean = [r for r in rows if "rtt_ms" in r]
    best = min(clean, key=lambda r: r["rtt_ms"]) if clean else None
    return {"utc": datetime.now(timezone.utc).isoformat(),
            "destination": f"{address}:{port}", "attempts": count,
            "best_min_rtt_estimate": best, "rows": rows}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("server", "client"))
    p.add_argument("address")
    p.add_argument("--port", type=int, default=29625)
    p.add_argument("--seconds", type=int, default=180)
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    if a.mode == "server":
        serve(a.address, a.port, a.seconds)
    else:
        result = client(a.address, a.port, a.count)
        if a.out:
            a.out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result["best_min_rtt_estimate"]))


if __name__ == "__main__":
    main()
