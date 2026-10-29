#!/usr/bin/env python3
"""Unscored CPU-only calibration for the three-node mixed-deadline study."""
import argparse
import json
import os
from pathlib import Path
import statistics
import time
import urllib.request

from fig6_cluster_driver import expected_result

WORK = {'cpu': {'kind': 'cpu', 'bytes': 65536, 'iterations': 100}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True, choices=('nj1', 'nj2', 'nj3'))
    parser.add_argument('--system', required=True, choices=('bare', 'tide'))
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.samples < 3:
        raise ValueError('fresh output and at least three samples required')
    os.sched_setaffinity(0, set(range(8, 16)))
    url = 'http://127.0.0.1:%d/run' % (29810 if args.system == 'bare' else 29801)
    body = {**WORK['cpu'], 'deadline_ms': 500}
    reference = expected_result(WORK['cpu'])
    rows, warmups = [], []
    for i in range(args.samples + 3):
        request = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
        started = time.monotonic_ns()
        with urllib.request.urlopen(request, timeout=12) as reply:
            headers, response = dict(reply.headers), json.load(reply)
        elapsed = time.monotonic_ns() - started
        if response.get('result') != reference or response.get('node') != args.node:
            raise ValueError('kernel checksum or local calibration node mismatch')
        if args.system == 'tide':
            lower = {k.lower(): v for k, v in headers.items()}
            if (lower.get('x-tide-function') != 'cpu-qos500'
                    or lower.get('x-tide-effective-deadline-ms') != '500'):
                raise ValueError('native calibration class mapping mismatch')
        row = {'kind': 'cpu', 'sample': i - 3, 'elapsed_ns': elapsed,
               'response': response, 'response_headers': headers, 'request': body}
        (warmups if i < 3 else rows).append(row)
        time.sleep(.025)
    summary = {'cpu': {'median_service_ms': statistics.median(r['response']['service_ns'] / 1e6 for r in rows),
                       'median_path_ms': statistics.median(r['elapsed_ns'] / 1e6 for r in rows),
                       'samples': args.samples}}
    result = {'node': args.node, 'system': args.system, 'workloads': WORK, 'rows': rows,
              'summary': summary, 'time_ns': time.time_ns(), 'warmup_per_kernel': 3,
              'warmup_rows': warmups, 'client_cpus': sorted(os.sched_getaffinity(0)),
              'unscored': True, 'request': body}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'node': args.node, 'system': args.system, 'summary': summary}), flush=True)


if __name__ == '__main__':
    main()
