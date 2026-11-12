"""Fail-closed audits of new Fig. 7 cases; original evidence is read-only.

Go CSV times are relative to the measured phase. Workload times and host-sample
intervals are host epoch times and are compared only with one another.
"""
import collections
import csv
import json
import math
import re
from pathlib import Path
import shutil
import statistics

from audit_binding import cpu_list, verify_binding
from audit_fig5_runtime import audit as runtime_audit
from protocol import (APP_CPUS, APP_MASK, DEADLINE_SEC, DIGEST, DRIVER_CPUS,
                      DRIVER_MASK, IMAGE, INFERENCES, INPUT, MAPPING, MODEL,
                      PAIRS, TIDE_BINARY, cell_flags, save, sha,
                      validate_manifest_fields, workers, demand, threads)

HERE = Path(__file__).resolve().parent


def read_events(path):
    for line in Path(path).read_text().splitlines():
        if line.startswith('{'):
            yield json.loads(line)


def metrics(rows, duration):
    assert rows, 'empty request trace'
    assert math.isfinite(float(duration)) and duration > 0, 'invalid duration'
    ids = [int(row['req_id']) for row in rows]
    assert len(ids) == len(set(ids)) and set(ids) == set(range(1, len(rows)+1)), 'missing/duplicate IDs'
    good = []
    for row in rows:
        submit, reply = int(row['t_submit_ns']), int(row['t_reply_ns'])
        latency = float(row['latency_ms'])
        assert submit >= 0 and reply >= submit, 'negative timestamp/latency'
        assert math.isfinite(latency) and abs((reply-submit)/1e6-latency) <= .002, 'invalid latency or timestamp mismatch'
        assert str(row['status']).strip(), 'empty status'
        if str(row['status']) == '200':
            good.append(latency)
    ordered = sorted(good)
    p95 = None
    if ordered:
        rank = (len(ordered)-1)*.95
        left = int(rank)
        right = min(left+1, len(ordered)-1)
        p95 = ordered[left]+(ordered[right]-ordered[left])*(rank-left)
    window = max(float(duration), max(int(row['t_reply_ns']) for row in rows)/1e9)
    return dict(requests=len(rows), successes=len(good), failures=len(rows)-len(good),
                mean_ms=statistics.mean(good) if good else None, p95_ms=p95,
                sd_ms=statistics.pstdev(good) if good else None,
                throughput_rps=len(good)/window, observation_sec=window,
                success_yield=len(good)/len(rows),
                first_submission_s=min(int(row['t_submit_ns']) for row in rows)/1e9,
                last_submission_s=max(int(row['t_submit_ns']) for row in rows)/1e9)


def _identity_mapping(expected_binaries):
    if expected_binaries is None:
        frozen = json.loads((HERE/'frozen/build-identity.json').read_text())
        assert frozen.get('status') == 'PASS', 'build identity did not pass'
        expected_binaries = frozen['binaries']
    if 'binaries' in expected_binaries:
        expected_binaries = expected_binaries['binaries']
    assert isinstance(expected_binaries, dict) and expected_binaries, 'missing frozen binary identities'
    return expected_binaries


def _one_flag(command, expected):
    key = expected.split('=', 1)[0] + '='
    assert [arg for arg in command[1:] if arg == key[:-1] or arg.startswith(key)] == [expected], ('missing/duplicate/wrong command flag', expected, command)


def _manifest(path, case, identities):
    validate_manifest_fields(json.loads((path/'case-config.json').read_text()), case)
    launch = json.loads((path/'launch.json').read_text())
    assert launch['actual_driver_affinity'] == DRIVER_CPUS, 'wrong actual driver affinity'
    m = json.loads((path/'manifest.json').read_text())
    assert m['status'] == 'completed' and m['exit_code'] == 0, 'incomplete or failed manifest'
    assert (m['cell'], m['rps'], m['repeat']) == (case['cell'], case['rps'], case['repeat']), 'manifest case identity mismatch'
    assert float(m['duration_sec']) == case['duration_sec'] and float(m['deadline_sec']) == DEADLINE_SEC, 'wrong duration/deadline'
    assert int(m['expected_count']) == case['rps']*case['duration_sec'], 'wrong expected offer count'
    assert case['inferences'] == INFERENCES, 'wrong inference count'
    assert m['raw_sha256'] == sha(path/'raw.csv'), 'raw hash mismatch'
    flags = cell_flags(case['cell'])
    binary = TIDE_BINARY if flags['ei'] else 'httpbase'
    command = m['command']
    assert isinstance(command, list) and Path(command[0]).name == binary, 'wrong executable'
    matches = [value for key, value in identities.items() if Path(key).name == binary]
    assert len(matches) == 1 and m['binary_sha256'] == matches[0], 'binary identity mismatch'
    expected_env = dict(PAPER_CPUSET=APP_MASK, WORKLOAD_CPUSET=APP_MASK,
                        TIDE_COMB_CPUIDS=','.join(map(str, MAPPING)), TIDE_COMB_DEV_CPUNUM='16',
                        TIDE_DETECTIONS_PER_REQ='10',CGROUP_ROOT='/sys/fs/cgroup',TIDE_LEGACY_HOST_ROOT=str(HERE.parent),TIDE_REQUEST_CPUS=str(demand(case)),TIDE_TORCH_THREADS=str(threads(case)),TIDE_OPENCV_THREADS='1')
    assert all(m['environment'].get(key) == value for key, value in expected_env.items()), 'environment/CPU mapping mismatch'
    assert set(m['environment']) <= set(expected_env)|{'WORKLOAD_IMAGE'}, 'unexpected environment override'
    if 'WORKLOAD_IMAGE' in m['environment']:
        assert m['environment']['WORKLOAD_IMAGE'] == IMAGE, 'wrong workload image'
    for key, expected in [('image', IMAGE), ('image_digest', DIGEST)]:
        if key in m:
            assert m[key] == expected, 'wrong '+key
    for flag in [f"-duration={case['duration_sec']}s", '-deadline=180s', '-cpu-num=6']:
        _one_flag(command, flag)
    warmup_flags = [arg for arg in command[1:] if arg == '-warmup' or arg.startswith('-warmup=')]
    assert warmup_flags in ([], ['-warmup=true']), 'missing default or duplicate/wrong warmup flag'
    out = [arg.split('=', 1)[1] for arg in command[1:] if arg.startswith('-outfile=')]
    assert len(out) == 1 and Path(out[0]).parts[-4:] == (case['cell'], f"rps{case['rps']}", f"run{case['repeat']}", 'raw.csv'), 'wrong output case path'
    if flags['ei']:
        _one_flag(command,f'-request-cpus={demand(case)}')
        _one_flag(command,'-stop-after-observation=true')
        for flag in [f"-concurr-ctrl={str(flags['cc']).lower()}", f"-cpusets={str(flags['pi']).lower()}", f"-fps={case['rps']}"]:
            _one_flag(command, flag)
    else:
        for flag in [f"-cc={'on' if flags['cc'] else 'off'}", f"-rps={case['rps']}"]:
            _one_flag(command, flag)
        assert not any(arg in ('-skip-container', '-skip-container=true') for arg in command[1:]), 'HTTP skipped its dedicated backend'
        backend_ids = [arg.split('=',1)[1] for arg in command[1:] if arg.startswith('-backend-id=')]
        assert len(backend_ids) == 1 and re.fullmatch(r'tide-matrix-[0-9a-f]{32}', backend_ids[0]), 'missing/duplicate/non-unique HTTP backend ID'
        owner = json.loads((path/'backend-owner.json').read_text())
        assert owner['backend_id'] == backend_ids[0] and owner['namespace'] == 'default' and owner['role'] == 'http', 'HTTP backend ownership mismatch'
        assert owner['path'] == owner['cgroup_path'] and owner['path'].startswith('/sys/fs/cgroup/'), 'HTTP backend cgroup ownership mismatch'
        assert int(owner['pid']) > 0 and owner['task_row'][0] == backend_ids[0] and int(owner['task_row'][1]) == int(owner['pid']), 'HTTP backend task identity mismatch'
        unified = [line.split('::',1)[1] for line in owner['proc_cgroup'].splitlines() if line.startswith('0::')]
        assert len(unified) == 1 and '/sys/fs/cgroup'+unified[0] == owner['path'], 'HTTP backend /proc cgroup ownership mismatch'
    return m


def _cgroup_chain(value):
    root = Path('/sys/fs/cgroup')
    current = Path(value)
    assert current.is_absolute() and '..' not in current.parts and (current == root or root in current.parents), 'invalid ancestor cgroup path'
    paths = set()
    while True:
        paths.add(str(current))
        if current == root:
            return paths
        current = current.parent


def _ancestor_quotas(sample, flags, owner):
    expected = {('driver', path) for path in _cgroup_chain(sample['driver_cgroup'])}
    expected.update(('tide', path) for path in _cgroup_chain('/sys/fs/cgroup/tide'))
    backend_owner_path = sample.get('backend_owner_path')
    if owner is not None and float(sample['time'])*1e9 >= int(owner['discovered_ns']):
        assert backend_owner_path == owner['path'], 'missing ancestor HTTP owner after discovery'
    if backend_owner_path is not None:
        assert owner is not None and backend_owner_path == owner['path'], 'ancestor HTTP owner path mismatch'
        expected.update(('http', path) for path in _cgroup_chain(str(Path(backend_owner_path).parent)))
    elif sample.get('backend_groups'):
        raise AssertionError('HTTP backend has no ancestor owner path')
    observed = set()
    for record in sample['ancestor_quotas']:
        role, path = record['role'], record['path']
        assert role in ('driver','tide','http'), 'unexpected ancestor role'
        _cgroup_chain(path)
        assert (role,path) not in observed, 'duplicate ancestor quota observation'
        observed.add((role,path))
        if record['applicable']:
            quota = str(record['cpu_max']).split()
            assert len(quota) == 2 and quota[0] == 'max' and quota[1].isdigit() and int(quota[1]) > 0, 'limited or invalid ancestor CPU quota'
        else:
            assert path == '/sys/fs/cgroup' and record['cpu_max'] is None and record.get('reason') == 'cgroup_v2_root_has_no_cpu_max', 'missing ancestor CPU quota without root exception'
    assert expected <= observed, ('missing ancestor quota coverage', sorted(expected-observed))
    return observed


def _host(path, case, events):
    flags = cell_flags(case['cell'])
    quota_samples = client_samples = backend_samples = backend_thread_samples = tide_thread_samples = 0
    owner = json.loads((path/'backend-owner.json').read_text()) if not flags['ei'] else None
    backend_cpus = set()
    ancestor_paths = set()
    ancestor_samples = 0
    for sample in read_events(path/'host-samples.jsonl'):
        assert sample['sampler_affinity'] == DRIVER_CPUS, 'wrong sampler affinity'
        ancestors = _ancestor_quotas(sample, flags, owner)
        ancestor_paths.update(ancestors)
        ancestor_samples += len(ancestors)
        end = float(sample['time'])*1e9
        start = float(sample.get('started_at', sample['time']-.025))*1e9
        assert start <= end, 'invalid host sampling interval'
        for driver in sample['driver_processes']:
            assert driver['thread_affinities'], 'driver process has no thread evidence'
            for mask in driver['thread_affinities']:
                assert set(cpu_list(mask)) and set(cpu_list(mask)) <= set(DRIVER_CPUS), 'driver thread outside declared CPUs'
            expected_comm = 'resptestv2' if flags['ei'] else 'httpbase'
            if driver['comm'].startswith(expected_comm):
                client_samples += 1
        for kind, groups in [('tide', sample['groups']), ('backend', sample.get('backend_groups', []))]:
            for group in groups:
                assert group['quota'].strip() == 'max 100000', 'unexpected CPU quota'
                counters = dict(line.split() for line in group['cpu_stat'].splitlines() if line.strip())
                assert int(counters['nr_throttled']) == 0 and int(counters.get('throttled_usec', 0)) == 0, 'CPU quota throttled'
                quota_samples += 1
                if kind == 'tide':
                    assert set(cpu_list(group['cpus'])) and set(cpu_list(group['cpus'])) <= set(APP_CPUS), 'Tide cgroup outside application CPUs'
                    if group['thread_affinities']:
                        tide_thread_samples += 1
                    for mask in group['thread_affinities']:
                        assert set(cpu_list(mask)) and set(cpu_list(mask)) <= set(APP_CPUS), 'Tide thread outside application CPUs'
                else:
                    assert owner is not None, 'unexpected HTTP backend in Tide case'
                    assert group['path'] == owner['path'] and group['backend_id'] == owner['backend_id'], 'backend group ownership mismatch'
                    backend_samples += 1
                    backend_cpus.add(group['cpus'])
                    eligible = [event for _, event in events.values() if event['start_ns'] < start and end < event['finish_ns']]
                    if eligible:
                        assert group['thread_affinities'], 'backend execution has no thread samples'
                        assert all(set(cpu_list(mask)) and set(cpu_list(mask)) <= set(APP_CPUS) for mask in group['thread_affinities']), 'backend inference thread outside application CPUs'
                        observed_pids = {int(pid) for ids in group['ns_pids'].values() for pid in ids}
                        assert all(int(event['pid']) in observed_pids for event in eligible), 'backend inference worker PID was not observed'
                        for event in eligible:
                            actual = [process for process in group['processes'] if int(event['pid']) in [int(pid) for pid in process['ns_pids']]]
                            assert len(actual) == 1 and actual[0]['thread_affinities'], 'backend inference process/thread evidence missing'
                            assert all(set(cpu_list(mask)) and set(cpu_list(mask)) <= set(APP_CPUS) for mask in actual[0]['thread_affinities']), 'backend inference process thread outside application CPUs'
                        backend_thread_samples += 1
    if not flags['ei']:
        assert backend_samples > 0 and backend_thread_samples > 0, 'no eligible backend execution observations'
    else:
        assert tide_thread_samples > 0, 'no actual Tide worker thread observations'
    assert quota_samples > 0, 'no CPU quota observations'
    assert client_samples > 0, 'no actual inference-client process observations'
    return dict(quota_samples=quota_samples, client_affinity_samples=client_samples,
                backend_samples=backend_samples, backend_execution_thread_samples=backend_thread_samples, tide_thread_samples=tide_thread_samples,
                backend_cgroup_cpu_masks=sorted(backend_cpus), ancestor_quota_samples=ancestor_samples,
                ancestor_quota_paths=[dict(role=role,path=path) for role,path in sorted(ancestor_paths)])


