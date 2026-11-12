"""Verify each request's allocated Processor against its observed CPU set."""
import argparse
import collections
import json
from pathlib import Path


def cpu_list(value):
    result = set()
    for part in value.split(','):
        if '-' in part:
            a, b = map(int, part.split('-')); result.update(range(a, b + 1))
        elif part:
            result.add(int(part))
    return sorted(result)


def verify_binding(path, cell, workers=None):
    path = Path(path)
    if cell in ('C1', 'C2'):
        return {'cell': cell, 'applicable': False}
    m = json.loads((path / 'manifest.json').read_text())
    mapping = list(map(int, m['environment']['TIDE_COMB_CPUIDS'].split(',')))
    physical = lambda r: sorted(mapping[i] for i, used in enumerate(r['CpuSet']) if used)
    pi = cell in ('C5', 'C6')
    observed = {}
    by_worker = collections.defaultdict(list)
    for source in sorted((path / 'worker-logs').glob('*/stdout')):
        for line in source.read_text().splitlines():
            if not line.startswith('{'): continue
            e = json.loads(line)
            if 'request_id' not in e: continue
            key = (source.parent.name, e['request_id'])
            assert key not in observed, ('duplicate workload request', key)
            observed[key] = e
            by_worker[key[0]].append(e)
    resources, acquired, active, binding, cgroups = {}, {}, {}, {}, {}
    matched, executed, ended = set(), set(), set()
    target_by_request = {}
    for line in (path / 'runtime.jsonl').read_text().splitlines():
        e = json.loads(line)
        msg, ex, proc, rid = (e.get(k) for k in ('msg', 'ExecutorId', 'ProcessorId', 'RoutineId'))
        if msg == 'Containerxv2: start creating new container':
            cgroups[ex] = '/sys/fs/cgroup' + e['CgroupPath']
        elif msg == 'ablation oversubscribed worker slot':
            resources['ablation-' + ex] = physical(e['PhysicRes'])
        elif msg == 'Stub: got a processor':
            resources[proc] = physical(e['physics resource']); acquired[rid] = proc
        elif msg == 'Exec: bind processor':
            assert ex not in active, ('overlapping binding', ex)
            active[ex] = proc
            binding.pop(ex, None)
        elif msg in ('Containerxv2: bound container CPU set', 'Containerxv2: move a container'):
            assert pi and ex in active
            assert physical(e['phyRes']) == resources[active[ex]]
            binding[ex] = e
        elif msg == 'Exec: bind routine':
            key = (ex, rid)
            assert key not in matched and ex in active
            if cell in ('C4', 'C6'): assert active[ex] == acquired[rid]
            else: assert active[ex] == 'ablation-' + ex
            target = resources[active[ex]] if pi else cpu_list(m['environment']['WORKLOAD_CPUSET'])
            worker = observed[key]
            assert worker['affinity'] == target, (path, rid, ex, 'requested', target, 'observed', worker['affinity'])
            if pi:
                event = binding[ex]
                assert event['pids'], ('empty PID binding', path, key)
                if event['msg'] == 'Containerxv2: bound container CPU set':
                    assert cpu_list(event['cpus']) == target
                    assert '/sys/fs/cgroup' + event['cgPath'] == cgroups[ex]
            else: assert ex not in binding
            assert worker['success'] and worker['inferences_completed'] == 10
            matched.add(key); target_by_request[key] = target
        elif msg == 'Stub: notice executor to execute':
            assert (ex, rid) in matched
            assert proc == (active[ex] if cell in ('C4', 'C6') else acquired[rid])
            executed.add((ex, rid))
        elif msg == 'Exec: unbind routine':
            assert (ex, rid) in executed
            ended.add((ex, rid))
        elif msg == 'Exec: unbind processor':
            assert active.pop(ex) == proc
    assert not active and matched == executed == ended == set(observed)
    expected = int(m['rps']) * int(m['duration_sec']) + ((workers * 4) if workers is not None else (12 if cell in ('C4', 'C6') else 24))
    assert len(matched) == expected
    switches = {}
    for ex, events in by_worker.items():
        events.sort(key=lambda e: e['start_ns'])
        switches[ex] = sum(a['affinity'] != b['affinity'] for a, b in zip(events, events[1:]))
    thread_samples = 0
    if pi:
        for sample in map(json.loads, (path / 'host-samples.jsonl').read_text().splitlines()):
            stamp = sample['time'] * 1e9
            started = sample.get('started_at', sample['time'] - .025) * 1e9
            groups = {g['path']: g for g in sample['groups']}
            for ex, events in by_worker.items():
                group = groups.get(cgroups[ex])
                if group is None: continue
                for e in events:
                    if e['start_ns'] < started and stamp < e['finish_ns']:
                        target = target_by_request[ex, e['request_id']]
                        assert group['thread_affinities'], ('no thread samples', ex)
                        assert all(cpu_list(a) == target for a in group['thread_affinities']), (path, e['request_id'], group, target)
                        thread_samples += 1
    return dict(cell=cell, applicable=True, requests_with_warmup=len(matched),
                measured_requests=sum(rid.startswith('measured-') for _, rid in matched),
                binding_mismatches=0, execution_thread_samples=thread_samples,
                cpu_set_switches_by_executor=switches)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--cell', required=True)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    result = verify_binding(a.run, a.cell)
    if a.out: a.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
