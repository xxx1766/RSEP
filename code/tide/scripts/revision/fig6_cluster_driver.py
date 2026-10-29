#!/usr/bin/env python3
"""Deterministic two-origin traces and open-loop stdlib HTTP Fig. 6 collector.

trace --pattern balanced --duration-s 30 --rate 40 --workload cpu --bytes 4096
      --iterations 100 --output trace.json
run --trace trace.json --origin k2 --system bare --route direct
    --urls http://k1:29801/run http://k1:29802/run http://k2:29801/run http://k3:29801/run
    --start-unix 1800000000 --timeout-s 10 --output run-k2

One new HTTP connection per offered request; no client concurrency limit,
retries, or semaphore. Application execution is serialized; the framework or
endpoint owns queueing. The direct
baseline cycles the four fixed two-CPU slots (cloud twice, each edge once).
Gateway routes send every origin request to that origin's Tide/Ray gateway.
"""
import argparse
import asyncio
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import ssl
import time
from urllib.parse import urlsplit
import zlib

from fig6_cluster_analyze import outcome, summarize_runs


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def ensure_nofile():
    """Reserve collector FD headroom before offering any requests; never alter hard limits."""
    minimum = 65536
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard < minimum:
        raise RuntimeError(f"RLIMIT_NOFILE hard limit {hard} is below required {minimum}")
    target = soft if soft == resource.RLIM_INFINITY else max(soft, minimum)
    if target != soft:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    actual, actual_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    return {"soft_before": soft, "soft": actual, "hard": actual_hard, "minimum": minimum}


def expected_result(config):
    """Independent exact reference for revisionworkload.Run, using uint64 arithmetic."""
    size, iterations = config["bytes"], config["iterations"]
    if type(size) is not int or type(iterations) is not int or not 8 <= size <= 256 << 20 or not 1 <= iterations <= 100000:
        raise ValueError("invalid workload size or iteration count")
    kind = config["kind"]
    if kind == "memory":
        # Index classes repeat every 256 bytes; iteration states every 512.
        weights = [size // 256 + int(i < size % 256) for i in range(256)]
        def sum_steps(count):
            return sum(weight * ((t * (i + 1) + t * (t - 1) // 2) % 256)
                       for t in range(1, count + 1) for i, weight in enumerate(weights))
        cycles, remainder = divmod(iterations, 512)
        checksum = cycles * sum_steps(512) + sum_steps(remainder) if cycles else sum_steps(remainder)
    elif kind in ("cpu", "io"):
        pattern = bytes((i * 17 + 3) % 256 for i in range(256))
        buffer = bytearray((pattern * ((size + 255) // 256))[:size])
        if kind == "cpu":
            for _ in range(iterations):
                digest = hashlib.sha256(buffer).digest()
                buffer[:min(size, 32)] = digest[:min(size, 32)]
            checksum = int.from_bytes(digest[:8], "little")
        else:
            checksum = zlib.crc32(buffer) * iterations
    else:
        raise ValueError("unsupported workload " + kind)
    return {"bytes_processed": size * iterations, "checksum": checksum % (1 << 64)}


def generate_trace(pattern, duration_s, rate, workload, deadline_ms=500, burst_period_s=1):
    duration_ns = int(Decimal(str(duration_s)) * 1_000_000_000)
    count = int((Decimal(str(duration_s)) * Decimal(str(rate))).to_integral_value(rounding=ROUND_HALF_UP))
    period_ns = int(Decimal(str(burst_period_s)) * 1_000_000_000)
    if pattern not in ("balanced", "hotspot", "burst") or duration_ns <= 0 or count < 1 or period_ns <= 0 or deadline_ms <= 0:
        raise ValueError("invalid pattern, duration, rate, deadline, or burst period")
    reference = expected_result(workload)
    requests = []
    for i in range(count):
        offset = i * duration_ns // count
        if pattern == "burst":
            offset = offset // period_ns * period_ns
        origin = ("k3" if i % 10 == 9 else "k2") if pattern == "hotspot" else ("k2" if i % 2 == 0 else "k3")
        requests.append({"id": f"r{i:08d}", "origin": origin, "offset_ns": offset,
                         "deadline_ms": deadline_ms})
    return {"schema_version": 1, "pattern": pattern, "duration_ns": duration_ns,
            "requested_rate_rps": rate, "actual_rate_rps": count / (duration_ns / 1e9),
            "burst_period_ns": period_ns if pattern == "burst" else None,
            "workload": dict(workload), "expected_result": reference, "requests": requests}


def validate_trace(trace):
    if trace["duration_ns"] <= 0:
        raise ValueError("invalid trace duration")
    ids = set()
    for request in trace["requests"]:
        if request["id"] in ids or request["origin"] not in ("k2", "k3"):
            raise ValueError("duplicate ID or invalid origin")
        if not 0 <= request["offset_ns"] < trace["duration_ns"] or request["deadline_ms"] <= 0:
            raise ValueError("invalid request offset or deadline")
        ids.add(request["id"])
    reference = expected_result(trace["workload"])
    if "expected_result" in trace and trace["expected_result"] != reference:
        raise ValueError("trace reference result differs from independent kernel reference")
    return reference


async def http_post(url, payload):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("HTTP(S) endpoint without credentials required")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    reader, writer = await asyncio.open_connection(parsed.hostname, port,
                                                  ssl=ssl.create_default_context() if parsed.scheme == "https" else None)
    try:
        target = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")
        header = (f"POST {target} HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
                  f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n")
        writer.write(header.encode("ascii") + payload)
        await writer.drain()
        raw_headers = await reader.readuntil(b"\r\n\r\n")
        lines = raw_headers.decode("iso-8859-1").split("\r\n")
        status = int(lines[0].split()[1])
        headers = {k.strip().lower(): v.strip() for k, v in
                   (line.split(":", 1) for line in lines[1:] if line)}
        if "chunked" in headers.get("transfer-encoding", "").lower():
            chunks = []
            while True:
                size = int((await reader.readline()).split(b";", 1)[0].strip(), 16)
                if not size:
                    while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                        pass
                    break
                chunks.append(await reader.readexactly(size))
                if await reader.readexactly(2) != b"\r\n":
                    raise ValueError("invalid HTTP chunk terminator")
            raw = b"".join(chunks)
        elif "content-length" in headers:
            raw = await reader.readexactly(int(headers["content-length"]))
        else:
            raw = await reader.read()
        return status, headers, raw
    finally:
        writer.close()


async def collect(trace, origin, system, route, urls, start_unix, timeout_s, record_sink=None):
    nofile = ensure_nofile()
    reference = validate_trace(trace)
    if origin not in ("k2", "k3") or route not in ("direct", "gateway") or timeout_s <= 0:
        raise ValueError("invalid origin, route, or timeout")
    if len(urls) != (4 if route == "direct" else 1):
        raise ValueError("direct route requires four fixed slots; gateway requires one URL")
    # Capture a bracketed local wall/monotonic mapping; never subtract remote clocks.
    before = time.monotonic_ns()
    now_unix_ns = time.time_ns()
    after = time.monotonic_ns()
    now_mono_ns = (before + after) // 2
    target_ns = round(start_unix * 1e9)
    if target_ns <= now_unix_ns:
        raise ValueError("start-unix must be in the future after validation")
    start_ns = now_mono_ns + target_ns - now_unix_ns
    selected = sorted((r for r in trace["requests"] if r["origin"] == origin), key=lambda r: r["offset_ns"])
    meta = {"schema_version": 1, "origin": origin, "system": system, "route": route, "urls": urls,
            "rlimit_nofile": nofile,
            "trace_sha256": hashlib.sha256(canonical(trace)).hexdigest(),
            "payload_sha256": hashlib.sha256(canonical(trace["workload"])).hexdigest(),
            "workload": trace["workload"], "pattern": trace.get("pattern"),
            "duration_ns": trace["duration_ns"], "start_unix": start_unix,
            "window_start_monotonic_ns": start_ns, "timeout_s": timeout_s,
            "expected_ids": [r["id"] for r in selected], "expected_result": reference,
            "clock_sample": {"unix_ns": now_unix_ns, "monotonic_ns": now_mono_ns,
                             "bracket_ns": after - before},
            "hostname": platform.node(), "pid": os.getpid(), "python": platform.python_version(),
            "transport": "new HTTP/1.1 connection per attempt; no retries; no client concurrency limit",
            "routing": "per-origin round robin over four equal two-CPU slots; k3 phase=2" if route == "direct" else "origin-local gateway"}
    payload = canonical(trace["workload"])
    records = []

    async def request_one(request, ordinal):
        planned = start_ns + request["offset_ns"]
        url = urls[(ordinal + (2 if origin == "k3" else 0)) % 4] if route == "direct" else urls[0]
        record = {"id": request["id"], "origin": origin, "deadline_ms": request["deadline_ms"],
                  "offset_ns": request["offset_ns"], "url": url, "request": trace["workload"],
                  "planned_submit_monotonic_ns": planned,
                  "actual_submit_monotonic_ns": time.monotonic_ns(),
                  "http_status": None, "response": None, "error": None}
        try:
            status, headers, raw = await asyncio.wait_for(http_post(url, payload), timeout=timeout_s)
            record["return_monotonic_ns"] = time.monotonic_ns()
            record["http_status"] = status
            record["response_headers"] = headers
            record["response_body"] = raw.decode("utf-8", errors="replace")
            try:
                record["response"] = json.loads(raw)
            except (ValueError, UnicodeError):
                record["error"] = "invalid_json"
            if status != 200:
                record["error"] = "http_error"
            record["error"] = outcome(record, reference)
        except asyncio.TimeoutError:
            record["error"] = "timeout"
        except Exception as error:
            record["error"] = type(error).__name__
            record["error_detail"] = str(error)
        record.setdefault("return_monotonic_ns", time.monotonic_ns())
        records.append(record)
        if record_sink:
            record_sink(record)

    tasks = []
    for ordinal, request in enumerate(selected):
        target = start_ns + request["offset_ns"]
        while time.monotonic_ns() < target:
            await asyncio.sleep((target - time.monotonic_ns()) / 1e9)
        tasks.append(asyncio.create_task(request_one(request, ordinal)))
        # Yield even for simultaneous bursts: no bounded queue or semaphore.
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    meta["collection_end_monotonic_ns"] = time.monotonic_ns()
    meta["collection_end_unix_ns"] = time.time_ns()
    meta["clock_mapping_drift_ns"] = (meta["collection_end_unix_ns"] - now_unix_ns
                                      - (meta["collection_end_monotonic_ns"] - now_mono_ns))
    return meta, records


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def write_run(prefix, meta, records):
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    raw = Path(str(prefix) + ".jsonl")
    temporary = Path(str(raw) + ".partial")
    summary = summarize_runs([(meta, records)])
    with temporary.open("w") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(raw)
    meta = {**meta, "raw_file": raw.name, "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            "complete": True}
    atomic_json(str(prefix) + ".summary.json", summary)
    # Meta is committed last and is the completion marker.
    atomic_json(str(prefix) + ".meta.json", meta)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    trace_parser = sub.add_parser("trace")
    trace_parser.add_argument("--pattern", choices=("balanced", "hotspot", "burst"), required=True)
    trace_parser.add_argument("--duration-s", type=float, required=True)
    trace_parser.add_argument("--rate", type=float, required=True, help="total offered requests/s across both origins")
    trace_parser.add_argument("--workload", choices=("cpu", "memory", "io"), required=True)
    trace_parser.add_argument("--bytes", type=int, required=True)
    trace_parser.add_argument("--iterations", type=int, required=True)
    trace_parser.add_argument("--deadline-ms", type=float, default=500)
    trace_parser.add_argument("--burst-period-s", type=float, default=1)
    trace_parser.add_argument("--output", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--trace", required=True)
    run_parser.add_argument("--origin", choices=("k2", "k3"), required=True)
    run_parser.add_argument("--system", required=True)
    run_parser.add_argument("--route", choices=("direct", "gateway"), required=True)
    run_parser.add_argument("--urls", nargs="+", required=True)
    run_parser.add_argument("--start-unix", type=float, required=True)
    run_parser.add_argument("--timeout-s", type=float, default=10)
    run_parser.add_argument("--output", required=True, help="output filename prefix")
    args = parser.parse_args()
    if args.command == "trace":
        trace = generate_trace(args.pattern, args.duration_s, args.rate,
                               {"kind": args.workload, "bytes": args.bytes, "iterations": args.iterations},
                               args.deadline_ms, args.burst_period_s)
        atomic_json(args.output, trace)
        print(json.dumps({"output": args.output, "requests": len(trace["requests"]),
                          "sha256": hashlib.sha256(canonical(trace)).hexdigest()}))
        return
    prefix = Path(args.output)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if any(Path(str(prefix) + suffix).exists() for suffix in (".meta.json", ".jsonl", ".jsonl.partial")):
        raise ValueError("output already exists; choose a new run prefix")
    trace = json.loads(Path(args.trace).read_text())
    with Path(str(prefix) + ".jsonl.partial").open("x") as stream:
        def sink(record):
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
        meta, records = asyncio.run(collect(trace, args.origin, args.system, args.route,
                                            args.urls, args.start_unix, args.timeout_s, sink))
    write_run(prefix, meta, records)
    print(json.dumps(summarize_runs([(meta, records)]), sort_keys=True))


if __name__ == "__main__":
    main()
