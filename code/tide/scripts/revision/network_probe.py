#!/usr/bin/env python3
"""Bounded single-stream TCP throughput probe for a dedicated experiment port.

The receiver counts bytes and times its own reads. This measures effective TCP
payload throughput for this probe, not a NIC line rate or an enforced rate.
"""

import argparse
import json
import socket
import time


BLOCK = b"0" * (256 * 1024)


def server(bind, port, accept_timeout):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bind, port))
        listener.listen(1)
        listener.settimeout(accept_timeout)
        print(json.dumps({"ready": True, "bind": bind, "port": port}), flush=True)
        with listener.accept()[0] as conn:
            conn.settimeout(10)
            count = 0
            start = None
            while True:
                chunk = conn.recv(256 * 1024)
                if not chunk:
                    break
                if start is None:
                    start = time.monotonic()
                count += len(chunk)
            seconds = time.monotonic() - start if start is not None else 0.0
            result = {"bytes": count, "seconds": seconds,
                      "mbps": count * 8 / seconds / 1e6 if seconds else 0.0}
            conn.sendall((json.dumps(result) + "\n").encode())
            print(json.dumps(result), flush=True)


def client(host, port, seconds, max_mib):
    limit = max_mib * 1024 * 1024
    with socket.create_connection((host, port), timeout=5) as conn:
        conn.settimeout(15)
        count = 0
        start = time.monotonic()
        while count < limit and time.monotonic() - start < seconds:
            count += conn.send(BLOCK[:min(len(BLOCK), limit - count)])
        conn.shutdown(socket.SHUT_WR)
        with conn.makefile("r") as stream:
            result = json.loads(stream.readline())
    if result["bytes"] != count:
        raise RuntimeError("sender and receiver byte counts disagree")
    result.update({"target": host, "port": port, "sent_bytes": count,
                   "duration_cap_s": seconds, "size_cap_mib": max_mib})
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("server", "client"):
        p = sub.add_parser(mode)
        p.add_argument("host")
        p.add_argument("--port", type=int, default=29485)
        if mode == "server":
            p.add_argument("--accept-timeout", type=float, default=60.0)
        else:
            p.add_argument("--seconds", type=float, default=2.0)
            p.add_argument("--max-mib", type=int, default=64)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be in [1024, 65535]")
    if args.mode == "server":
        if args.accept_timeout <= 0:
            parser.error("accept-timeout must be positive")
        server(args.host, args.port, args.accept_timeout)
    else:
        if args.seconds <= 0 or args.max_mib <= 0:
            parser.error("seconds and max-mib must be positive")
        client(args.host, args.port, args.seconds, args.max_mib)


if __name__ == "__main__":
    main()
