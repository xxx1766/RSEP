"""Parent's independent, read-only phase and environment acceptance."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import time

from runtime_config import BASE, DEPENDENCY_ROOT, NERDCTL, KUBECONFIG


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(args):
    return subprocess.check_output(args, text=True, timeout=30)


def dead(owner):
    try:
        fields = Path(f"/proc/{owner['pid']}/stat").read_text().rsplit(') ', 1)[1].split()
    except FileNotFoundError:
        return True
    return fields[19] != owner['start_ticks'] or fields[0] == 'Z'


def validate_phase(phase):
    from protocol import make_plan,workers
    counts=make_plan(phase)
    count,measured,warmups=counts['expected_runs'],counts['expected_measured'],counts['expected_warmups']
    plan = read(BASE/'frozen'/f'{phase}-plan.json')
    progress = read(BASE/phase/'progress.json')
    terminal = read(BASE/'evidence'/f'{phase}-terminal.json')
    owners = read(BASE/'evidence'/f'{phase}-process.json')
    assert terminal['status'] == 'completed' and terminal['returncode'] == 0
    assert dead(owners['controller']) and dead(owners['child'])
    assert progress['status'] == 'completed' and progress['completed'] == progress['total'] == count
    assert read(BASE/phase/'plan.json') == plan
    assert len(plan['cases']) == len(progress['cases']) == count
    assert [{k: row[k] for k in case} for row, case in zip(progress['cases'], plan['cases'])] == plan['cases']
    expected = {f"{c['cell']}/rps{c['rps']}/run{c['repeat']}" for c in plan['cases']}
    actual = {str(p.relative_to(BASE/phase)) for p in (BASE/phase).glob('C*/rps*/run*') if p.is_dir()}
    assert actual == expected and len(expected) == count
    totals = dict(measured=0, warmup=0, failures=0)
    evidence_files = {}
    for case in plan['cases']:
        path = BASE/phase/case['cell']/f"rps{case['rps']}"/f"run{case['repeat']}"
        worker_count = workers(case)
        manifest = read(path/'manifest.json')
        audit = read(path/'audit.json')
        assert manifest['status'] == 'completed' and manifest['exit_code'] == 0
        assert all(manifest[k] == case[k] for k in ('cell', 'rps', 'repeat'))
        assert manifest['raw_sha256'] == digest(path/'raw.csv')
        assert audit['status'] == 'passed' and audit['case'] == case and audit['workers'] == worker_count
        assert read(path/'summary.json') == audit['stats']
        assert audit['raw_sha256'] == digest(path/'raw.csv')
        assert audit['warmup_sha256'] == digest(path/'warmup.csv')
        required = {'raw.csv', 'warmup.csv', 'manifest.json', 'case-config.json', 'launch.json', 'host-samples.jsonl'}
        if case['cell'] in ('C1', 'C2'):
            required |= {'http-worker.jsonl', 'backend-owner.json'}
        else:
            logs = list((path/'worker-logs').glob('*/stdout'))
            assert len(logs) == worker_count
            required |= {'runtime.jsonl'} | {str(log.relative_to(path)) for log in logs}
        assert required <= audit['evidence_sha256'].keys(), ('incomplete audit evidence hashes', path, sorted(required - audit['evidence_sha256'].keys()))
        for name, sha in audit['evidence_sha256'].items():
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
            assert digest(path/name) == sha, ('changed audit evidence', path/name)
            evidence_files[str((path/name).relative_to(BASE))] = sha
        for name, total, expected_rows in [('raw.csv', 'measured', case['rps']*case['duration_sec']), ('warmup.csv', 'warmup', worker_count*4)]:
            with (path/name).open() as stream:
                rows = list(csv.DictReader(stream))
            assert len(rows) == expected_rows
            assert {int(r['req_id']) for r in rows} == set(range(1, expected_rows+1))
            totals[total] += len(rows)
            if name == 'raw.csv':
                failures = sum(r['status'] != '200' for r in rows)
                totals['failures'] += failures
                assert audit['stats']['requests'] == len(rows) and audit['stats']['failures'] == failures
        if case['cell'] in ('C1', 'C2'):
            owner = read(path/'backend-owner.json')
            assert not Path(owner['cgroup_path']).exists(), 'HTTP owned cgroup remains'
            for kind in ('tasks', 'containers'):
                assert owner['backend_id'] not in command(['ctr', '-n', 'default', kind, 'ls', '-q']).splitlines()
        else:
            assert len(list((path/'worker-logs').glob('*/stdout'))) == worker_count
            if case['cell'] in ('C5', 'C6'):
                assert audit['binding']['binding_mismatches'] == 0
    assert totals['measured'] == measured and totals['warmup'] == warmups
    return dict(phase=phase, runs=count, **totals, audit_input_files=len(evidence_files))


def environment():
    pre = read(BASE/'evidence/preflight.json')
    settings = {p: Path(p).read_text() for p in pre['original_settings']}
    assert settings == pre['original_settings'], ('settings changed', settings)
    tasks = {ns: command(['ctr', '-n', ns, 'tasks', 'ls']) for ns in pre['tasks']}
    rows = lambda value: sorted(tuple(line.split()) for line in value.splitlines()[1:] if line.strip())
    assert {ns: rows(x) for ns, x in tasks.items()} == {ns: rows(x) for ns, x in pre['tasks'].items()}
    assert not command(['ctr', '-n', 'tide', 'containers', 'ls', '-q']).strip()
    assert not list(Path('/dev/mqueue').iterdir())
    assert not [p for p in Path('/sys/fs/cgroup/tide').iterdir() if p.is_dir()]
    services = {name: command(['systemctl', 'is-active', name]).strip() for name in pre['services']}
    assert services == pre['services']
    k = [NERDCTL, 'exec', 'tmc-ke-control-plane', 'kubectl', '--kubeconfig='+KUBECONFIG]
    pods = json.loads(command(k+['get', 'pods', '-A', '-o', 'json']))['items']
    nodes = json.loads(command(k+['get', 'nodes', '-o', 'json']))['items']
    shared = dict(pods=[dict(namespace=p['metadata']['namespace'], name=p['metadata']['name'], uid=p['metadata']['uid'], phase=p['status']['phase'], containers=sorted([dict(name=c['name'], ready=c['ready'], restarts=c['restartCount']) for c in p['status'].get('containerStatuses', [])], key=lambda c: c['name'])) for p in pods], nodes=[dict(name=n['metadata']['name'], uid=n['metadata']['uid'], ready=next(c['status'] for c in n['status']['conditions'] if c['type']=='Ready')) for n in nodes])
    for key in ('pods', 'nodes'):
        order = lambda item: (item.get('namespace', ''), item['name'])
        assert sorted(shared[key], key=order) == sorted(pre['shared_cluster'][key], key=order), ('shared state changed', key)
    from preflight_snapshot import outer_state
    assert outer_state() == pre['outer_cluster'], 'outer cluster state changed'
    identity = read(BASE/'frozen/identities.json')
    for name, sha in identity['files'].items():
        assert digest(BASE/name) == sha, ('frozen file changed', name)
    build = read(BASE/'frozen/build-identity.json')
    for name, sha in build['binaries'].items():
        assert digest(BASE/name) == sha
    dependency=read(BASE/'frozen/dependency-files.json')
    for name, expected in dependency.items():
        assert digest(DEPENDENCY_ROOT/name)==expected, ('dependency changed', name)
    return dict(settings_restored=True, settings=settings, tasks=tasks, services=services, shared_cluster=shared, owned_resources_absent=True, frozen_files_verified=len(identity['files']), binaries=build['binaries'], dependency_root=str(DEPENDENCY_ROOT), dependency_files_verified=len(dependency))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('smoke','pilot','formal'))
    parser.add_argument('--receipt-suffix', default='')
    args = parser.parse_args()
    phase = args.phase
    assert not args.receipt_suffix or all(c.isalnum() or c == '-' for c in args.receipt_suffix)
    suffix = '-'+args.receipt_suffix if args.receipt_suffix else ''
    path = BASE/'evidence'/f'{phase}-acceptance{suffix}.json'
    assert not path.exists(), 'preserve prior acceptance'
    result = dict(status='PASS', checked_ns=time.time_ns(), coverage=validate_phase(phase), environment=environment(), acceptance_source_sha256=digest(Path(__file__)))
    with path.open('x') as out:
        json.dump(result, out, indent=2)
        out.write('\n')
    print(json.dumps(dict(status='PASS', phase=phase, coverage=result['coverage'], frozen_files=result['environment']['frozen_files_verified'])))


if __name__ == '__main__':
    main()
