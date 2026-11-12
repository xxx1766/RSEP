#!/usr/bin/env python3
"""Auditable request statistics (standard library only).

The admitted cohort is selected by [measurement_start_ns, measurement_end_ns)
when both bounds exist in the manifest, then floor(cohort_size * warmup_frac)
leading submissions are removed BEFORE classifying outcomes. Manifest bounds
must use the raw CSV clock. With trimming, start advances to the first retained
submission. Rates count completions in [start, end); yields count all admitted
requests including their drained outcomes. Without bounds the explicit fallback
is first retained submission to last retained reply INCLUDING failures and drain;
that fallback is a cohort-drain rate, not a fixed-window offered-load rate.

status=200 is success; 408/504 are timeouts; every other status is failure.
Legacy HTTP status=0 conflates transport failures/timeouts, so is not inferred to
be a timeout. QoS must be explicitly supplied or positive finite qos_ms per row.
Missing QoS means unavailable goodput/late counts, never throughput-as-goodput.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

REQUIRED = {'req_id', 't_submit_ns', 't_reply_ns', 'latency_ms', 'status'}


def read_records(path, expected_count=None):
    with Path(path).open(newline='') as f:
        reader = csv.DictReader(f)
        if not REQUIRED.issubset(reader.fieldnames or []):
            raise ValueError('missing required raw CSV columns')
        rows = list(reader)
    ids = set()
    for row in rows:
        if None in row or any(row.get(k) in (None, '') for k in REQUIRED):
            raise ValueError('incomplete raw CSV row')
        if row['req_id'] in ids:
            raise ValueError('duplicate req_id')
        ids.add(row['req_id'])
        for name in ('t_submit_ns', 't_reply_ns', 'status'):
            row[name] = int(row[name])
        row['latency_ms'] = float(row['latency_ms'])
        latency = row['latency_ms']
        if (not math.isfinite(latency) or latency < 0 or row['t_submit_ns'] < 0
                or row['t_reply_ns'] < row['t_submit_ns']):
            raise ValueError('invalid timestamp or latency')
        if abs(latency - (row['t_reply_ns'] - row['t_submit_ns']) / 1e6) > .0011:
            raise ValueError('latency does not match timestamps')
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f'expected {expected_count} requests, got {len(rows)}')
    return rows


def aggregate_records(records, warmup_frac=0.0, qos_ms=None, manifest=None):
    if not 0 <= warmup_frac < 1:
        raise ValueError('warmup_frac must be in [0, 1)')
    if qos_ms is not None and (not math.isfinite(qos_ms) or qos_ms <= 0):
        raise ValueError('qos_ms must be positive and finite')
    manifest = manifest or {}
    start = manifest.get('measurement_start_ns')
    end = manifest.get('measurement_end_ns')
    explicit = start is not None or end is not None
    if explicit and (start is None or end is None or not isinstance(start, int)
                     or not isinstance(end, int) or end <= start):
        raise ValueError('manifest requires integer start/end with end > start')
    cohort = sorted((r for r in records if not explicit or start <= r['t_submit_ns'] < end),
                    key=lambda r: r['t_submit_ns'])
    before_trim = len(cohort)
    trimmed = int(before_trim * warmup_frac)
    cohort = cohort[trimmed:]
    if explicit:
        if trimmed and cohort:
            start = cohort[0]['t_submit_ns']
    elif cohort:
        start = cohort[0]['t_submit_ns']
        end = max(r['t_reply_ns'] for r in cohort)
    duration = (end - start) / 1e9 if start is not None and end is not None else None
    valid_duration = duration is not None and duration > 0
    ok = [r for r in cohort if r['status'] == 200]
    rate_ok = [r for r in ok if start <= r['t_reply_ns'] < end] if explicit else ok
    timeout = sum(r['status'] in (408, 504) for r in cohort)
    qos = []
    for r in cohort:
        value = qos_ms if qos_ms is not None else r.get('qos_ms')
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        qos.append(value if value is not None and math.isfinite(value) and value > 0 else None)
    known = bool(cohort) and all(v is not None for v in qos)
    ontime = [r for r, limit in zip(cohort, qos)
              if r['status'] == 200 and limit is not None and r['latency_ms'] <= limit]
    rate_ontime = [r for r in ontime if start <= r['t_reply_ns'] < end] if explicit else ontime
    n = len(cohort)
    latencies = sorted(r['latency_ms'] for r in ok)
    def percentile(p):
        if not latencies:
            return None
        pos = (len(latencies) - 1) * p
        low = int(pos)
        return latencies[low] + (latencies[math.ceil(pos)] - latencies[low]) * (pos - low)
    rate = lambda count: count / duration if valid_duration else None
    return {
        'n_total': len(records), 'n_eligible': n, 'denominator': n,
        'n_warmup': trimmed, 'n_outside_window': len(records) - before_trim,
        'n_ok': len(ok), 'n_failure': n - len(ok) - timeout, 'n_timeout': timeout,
        'n_ontime': len(ontime) if known else None,
        'n_on_time': len(ontime) if known else None,
        'n_late': len(ok) - len(ontime) if known else None,
        'n_qos_unknown': sum(v is None for v in qos),
        'n_completed_ok_in_window': len(rate_ok),
        'n_completed_ontime_in_window': len(rate_ontime) if known else None,
        'drained_successes': len(ok) - len(rate_ok),
        'success_yield': len(ok) / n if n else None,
        'on_time_yield': len(ontime) / n if n and known else None,
        'drop_pct': 100 * (n - len(ok)) / n if n else None,
        'avg_ms': statistics.mean(latencies) if latencies else None,
        'p95_ms': percentile(.95),
        'std_ms': statistics.pstdev(latencies) if latencies else None,
        'throughput': rate(len(rate_ok)),
        'ontime_goodput': rate(len(rate_ontime)) if known else None,
        'on_time_goodput': rate(len(rate_ontime)) if known else None,
        'window_start_ns': start, 'window_end_ns': end, 'duration_s': duration,
        'window_source': 'manifest' if explicit else 'retained_submission_to_last_reply',
        'rate_semantics': 'offered_cohort_completions_in_window' if explicit else 'cohort_including_drain',
        'qos_source': 'argument' if qos_ms is not None else 'row' if known else 'unavailable',
    }


def validate_completed(path, manifest_path, expected_count=None):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get('status') != 'completed' or manifest.get('exit_code') != 0:
        raise ValueError('manifest does not record successful completion')
    expected = expected_count if expected_count is not None else manifest.get('expected_count')
    if expected is None or manifest.get('expected_count') != expected:
        raise ValueError('missing or mismatched expected_count')
    rows = read_records(path, expected)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if manifest.get('raw_sha256') != digest:
        raise ValueError('raw CSV checksum mismatch')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    validate = sub.add_parser('validate')
    validate.add_argument('raw', type=Path)
    validate.add_argument('--expected-count', type=int, required=True)
    validate.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    try:
        if args.manifest:
            rows = validate_completed(args.raw, args.manifest, args.expected_count)
        else:
            rows = read_records(args.raw, args.expected_count)
        print(json.dumps({'n_total': len(rows), 'raw_sha256': hashlib.sha256(args.raw.read_bytes()).hexdigest()}))
    except (ValueError, OSError) as error:
        parser.exit(1, f'validation failed: {error}\n')


if __name__ == '__main__':
    main()
