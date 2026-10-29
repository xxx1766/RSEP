#!/usr/bin/env python3
"""Bounded multi-stream TCP probe with a common receiver observation window."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import time


def summarize(rows):
    if not rows or any(r.get("error") or not r.get("bytes") or r.get("first_ns") is None
                       or r.get("last_ns", 0) <= r["first_ns"] for r in rows):
        raise ValueError("complete nonempty receiver records required")
    duration = (max(r["last_ns"] for r in rows) - min(r["first_ns"] for r in rows)) / 1e9
    size = sum(r["bytes"] for r in rows)
    return dict(streams=len(rows), bytes=size, window_s=duration, mbps=size * 8 / duration / 1e6)


def receive(conn):
    with conn:
        conn.settimeout(30)
        row = dict(bytes=0, first_ns=None, receive_buffer=conn.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
        while True:
            block = conn.recv(262144)
            now = time.monotonic_ns()
            if not block:
                break
            if row["first_ns"] is None:
                row["first_ns"] = now
            row["bytes"] += len(block)
            row["last_ns"] = now
        conn.sendall((json.dumps(row) + "\n").encode())
        return row


def server(address, port, streams):
    with socket.create_server((address, port), backlog=streams) as listener, ThreadPoolExecutor(streams) as pool:
        listener.settimeout(60)
        print(json.dumps(dict(ready=True, address=address, port=port, streams=streams)), flush=True)
        jobs = [pool.submit(receive, listener.accept()[0]) for _ in range(streams)]
        rows = [job.result() for job in jobs]
    return dict(unix_ns=time.time_ns(), receiver_rows=rows, summary=summarize(rows))


def send(address, port, seconds, max_mib):
    with socket.create_connection((address, port), timeout=10) as conn:
        conn.settimeout(30)
        block = b"0" * 262144
        sent, cap = 0, max_mib * 1048576
        start = time.monotonic_ns()
        while sent < cap and (time.monotonic_ns() - start) / 1e9 < seconds:
            sent += conn.send(block[:min(len(block), cap - sent)])
        conn.shutdown(socket.SHUT_WR)
        with conn.makefile("r") as stream:
            result = json.loads(stream.readline())
        if result["bytes"] != sent:
            raise RuntimeError("sender/receiver byte mismatch")
        result.update(sent_bytes=sent, sender_duration_s=(time.monotonic_ns() - start) / 1e9)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("server", "client"))
    p.add_argument("address")
    p.add_argument("--port", type=int, default=29684)
    p.add_argument("--streams", type=int, default=8)
    p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--max-mib", type=int, default=128)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.streams <= 16 or not 0 < a.seconds <= 15 or not 1 <= a.max_mib <= 256 or not 20000 <= a.port <= 60000:
        p.error("bounded experiment parameters required")
    if a.out.exists():
        raise RuntimeError("refusing to replace a probe result")
    if a.mode == "server":
        result = server(a.address, a.port, a.streams)
    else:
        with ThreadPoolExecutor(a.streams) as pool:
            jobs = [pool.submit(send, a.address, a.port, a.seconds, a.max_mib) for _ in range(a.streams)]
            rows = [job.result() for job in jobs]
        result = dict(unix_ns=time.time_ns(), receiver_rows=rows, summary=summarize(rows))
    a.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"]), flush=True)


if __name__ == "__main__":
    main()
