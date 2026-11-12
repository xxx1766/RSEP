#!/usr/bin/env python3
"""Prepare and freeze a new native RSEP campaign without starting a benchmark."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

RELEASE_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_SOURCE = RELEASE_ROOT / 'experiments/current/native_pytorch/source/campaign'
WORKLOAD_SOURCE = RELEASE_ROOT / 'experiments/current/native_pytorch/source/workload'
LAUNCHER_SOURCE = RELEASE_ROOT / 'experiments/current/native_pytorch/source/launcher'
RUNTIME_SOURCE = RELEASE_ROOT / 'code/reproduction/runtime-issue42/Tide'
METRICS_SOURCE = RELEASE_ROOT / 'code/tide/scripts/revision/metrics.py'
RUNTIME_FILES = ('go.mod', 'go.sum')
RUNTIME_DIRS = ('cmd/httpbase', 'cmd/resptestv2', 'pkg', 'proto')
RUNTIME_SCRIPTS = ('scripts/setup-paper-cgroup.sh',)
EXPECTED_GO = 'go1.26.2'
HEX64 = re.compile(r'[0-9a-f]{64}')
DIGEST_FORMAT = re.compile(r'sha256:[0-9a-f]{64}')


def fail(message):
    raise SystemExit('error: ' + message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def absolute(label, value):
    path = Path(value)
    if not path.is_absolute():
        fail(label + ' must be an absolute path')
    return path


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')


def tree_files(root, skip_dirs=()):
    root = Path(root)
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if path.is_file() and relative.parts[0] not in skip_dirs:
            yield path


def copy_required(source, runtime):
    missing = [str(source / name) for name in RUNTIME_FILES + RUNTIME_DIRS + RUNTIME_SCRIPTS
               if not (source / name).exists()]
    if missing:
        fail('runtime snapshot is incomplete: ' + ', '.join(missing))
    runtime.mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_FILES + RUNTIME_SCRIPTS:
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    for name in RUNTIME_DIRS:
        shutil.copytree(source / name, runtime / name)
    script = LAUNCHER_SOURCE / 'run-matrix.sh'
    if not script.is_file():
        fail('campaign launcher run-matrix.sh is missing: ' + str(script))
    target = runtime / 'scripts/run-matrix.sh'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script, target)
    if METRICS_SOURCE.is_file():
        target = runtime / 'scripts/revision/metrics.py'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(METRICS_SOURCE, target)


def check_legacy(root):
    required = {
        'deps directory': root / 'deps',
        'workload source': root / 'source/workload/user_program.py',
        'YOLOX source': root / 'source/YOLOX-0.3.0',
        'model checkpoint': root / 'downloads/yolox_nano.pth',
        'input image': root / 'workdir/assets/dog.jpg',
    }
    missing = [label + ': ' + str(path) for label, path in required.items() if not path.exists()]
    if missing:
        fail('legacy root is incomplete: ' + '; '.join(missing))
    return root / 'deps', root / 'downloads/yolox_nano.pth', root / 'workdir/assets/dog.jpg'


def find_go(explicit):
    if explicit:
        path = absolute('--go-bin', explicit)
        if not path.is_file() or not os.access(path, os.X_OK):
            fail('Go executable is not executable: ' + str(path))
        return path
    candidates = [os.environ.get('RSEP_GO_BIN'), shutil.which('go'), '/usr/local/go/bin/go']
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return path
    fail('Go 1.26.2 is required; set RSEP_GO_BIN or use /usr/local/go/bin/go')


def check_go(go):
    try:
        output = subprocess.check_output([str(go), 'version'], text=True, stderr=subprocess.STDOUT).strip()
    except OSError as exc:
        fail('cannot execute Go: ' + str(exc))
    except subprocess.CalledProcessError as exc:
        fail('go version failed: ' + exc.output.strip())
    if output.split()[2] != EXPECTED_GO:
        fail('Go ' + EXPECTED_GO + ' is required, found: ' + output)
    return output


def build(runtime, go, go_version):
    env = dict(os.environ, CGO_ENABLED='1', GOOS='linux', GOARCH='amd64')
    commands = [
        [str(go), 'build', '-o', 'bin/resptestv2-cpu-budget', './cmd/resptestv2'],
        [str(go), 'build', '-o', 'bin/httpbase', './cmd/httpbase'],
    ]
    for command in commands:
        try:
            subprocess.run(command, cwd=runtime, env=env, check=True)
        except subprocess.CalledProcessError as exc:
            fail('Go build failed (' + ' '.join(command[1:]) + '): ' + str(exc))
    binaries = {
        'runtime-source/Tide/bin/resptestv2-cpu-budget': sha(runtime / 'bin/resptestv2-cpu-budget'),
        'runtime-source/Tide/bin/httpbase': sha(runtime / 'bin/httpbase'),
    }
    return {
        'status': 'PASS',
        'binaries': binaries,
        'core': 'new public native source; scheduling and workload code unchanged',
        'go_version': go_version,
        'cgo_enabled': 1,
    }


def freeze_plan(base, protocol, image, digest):
    plan = protocol.make_plan('formal')
    if plan['phase'] != 'formal' or plan['seed'] != 202611034103:
        fail('formal plan changed fixed phase or seed')
    if plan['image'] != image or plan['image_digest'] != digest:
        fail('protocol.py did not apply RSEP_NATIVE_IMAGE/RSEP_NATIVE_IMAGE_DIGEST')
    if plan['expected_runs'] != 180 or plan['expected_measured'] != 37800:
        fail('formal plan does not contain 180 runs and 37800 offers')
    if len(plan['cases']) != 180:
        fail('formal plan case count is not 180')
    write_json(base / 'frozen/formal-plan.json', plan)
    return plan


def load_protocol(base):
    path = base / 'protocol.py'
    if not path.is_file():
        fail('campaign protocol is missing: ' + str(path))
    spec = importlib.util.spec_from_file_location('rsep_native_protocol', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dependency_hashes(deps):
    result = {}
    for root, dirs, files in os.walk(deps, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for name in sorted(files):
            path = Path(root) / name
            if path.is_file():
                result[path.relative_to(deps).as_posix()] = sha(path)
    if not result:
        fail('dependency directory contains no files: ' + str(deps))
    return result


def verify_assets(protocol, model, input_image, runtime):
    if not HEX64.fullmatch(protocol.MODEL) or not HEX64.fullmatch(protocol.INPUT):
        fail('protocol model/input hash format is invalid')
    if sha(model) != protocol.MODEL:
        fail('model checkpoint hash mismatch: ' + str(model))
    if sha(input_image) != protocol.INPUT:
        fail('input image hash mismatch: ' + str(input_image))
    for name in ('cmd/httpbase/dog.jpg', 'cmd/resptestv2/dog.jpg'):
        if sha(runtime / name) != protocol.INPUT:
            fail('copied runtime input mismatch: ' + str(runtime / name))


def campaign_files(base, campaign_names, runtime, plan):
    files = {}
    names = [path.relative_to(base).as_posix() for path in tree_files(runtime, skip_dirs={'bin'})]
    names += [path.relative_to(base).as_posix() for path in tree_files(base / 'source/workload')]
    names += list(campaign_names)
    names.append(plan.relative_to(base).as_posix())
    for name in sorted(set(names)):
        files[name] = sha(base / name)
    return {'status': 'PASS', 'files': files}


def write_environment(base, legacy, deps, image, digest):
    runtime = base / 'runtime-source/Tide'
    lines = [
        'export RSEP_NATIVE_EVIDENCE_ROOT=' + shlex_quote(str(base)),
        'export RSEP_RUNTIME_SOURCE=' + shlex_quote(str(runtime)),
        'export RSEP_NATIVE_LEGACY_ROOT=' + shlex_quote(str(legacy)),
        'export RSEP_NATIVE_DEPENDENCY_ROOT=' + shlex_quote(str(deps)),
        'export RSEP_NATIVE_IMAGE=' + shlex_quote(image),
        'export RSEP_NATIVE_IMAGE_DIGEST=' + shlex_quote(digest),
    ]
    (base / 'runtime-env.sh').write_text('\n'.join(lines) + '\n')


def shlex_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def parse_args():
    parser = argparse.ArgumentParser(description='Prepare a new frozen native RSEP campaign; does not start an experiment.')
    parser.add_argument('--workdir', required=True, help='new absolute campaign BASE directory; must not exist')
    parser.add_argument('--legacy-root', required=True, help='absolute prepared model/deps/YOLOX host tree')
    parser.add_argument('--image', required=True, help='workload image reference')
    parser.add_argument('--image-digest', required=True, help='sha256:<64 lowercase hex> image manifest digest')
    parser.add_argument('--go-bin', help='absolute Go 1.26.2 executable (default: RSEP_GO_BIN or go on PATH)')
    return parser.parse_args()


def main():
    args = parse_args()
    workdir = absolute('--workdir', args.workdir)
    legacy = absolute('--legacy-root', args.legacy_root)
    if workdir.exists():
        fail('workdir already exists; choose a new path: ' + str(workdir))
    if not legacy.is_dir():
        fail('legacy root is not a directory: ' + str(legacy))
    if not DIGEST_FORMAT.fullmatch(args.image_digest):
        fail('--image-digest must be sha256:<64 lowercase hex>')
    if not args.image.strip():
        fail('--image must not be empty')
    for label, path in (('runtime source', RUNTIME_SOURCE), ('campaign source', CAMPAIGN_SOURCE),
                        ('workload source', WORKLOAD_SOURCE), ('launcher source', LAUNCHER_SOURCE)):
        if not path.is_dir():
            fail(label + ' is missing: ' + str(path))
    deps, model, input_image = check_legacy(legacy)
    if sys.platform != 'linux':
        fail('the native build requires Linux')
    go = find_go(args.go_bin)
    go_version = check_go(go)

    workdir.mkdir(parents=True)
    (workdir / "evidence").mkdir()
    runtime = workdir / 'runtime-source/Tide'
    copy_required(RUNTIME_SOURCE, runtime)
    for path in sorted(CAMPAIGN_SOURCE.glob('*.py')):
        shutil.copy2(path, workdir / path.name)
    shutil.copytree(WORKLOAD_SOURCE, workdir / 'source/workload')

    os.environ['RSEP_NATIVE_IMAGE'] = args.image
    os.environ['RSEP_NATIVE_IMAGE_DIGEST'] = args.image_digest
    protocol = load_protocol(workdir)
    verify_assets(protocol, model, input_image, runtime)
    plan_path = workdir / 'frozen/formal-plan.json'
    freeze_plan(workdir, protocol, args.image, args.image_digest)

    identity = build(runtime, go, go_version)
    write_json(workdir / 'frozen/build-identity.json', identity)
    write_json(workdir / 'frozen/dependency-files.json', dependency_hashes(deps))
    campaign_names = [path.name for path in sorted(CAMPAIGN_SOURCE.glob('*.py'))]
    write_json(workdir / 'frozen/identities.json', campaign_files(workdir, campaign_names, runtime, plan_path))
    write_json(workdir / 'frozen/image-identity.json', {
        'status': 'PREPARED',
        'image': args.image,
        'digest': args.image_digest,
    })
    write_environment(workdir, legacy, deps, args.image, args.image_digest)
    summary = {
        'status': 'PREPARED',
        'workdir': str(workdir),
        'runtime': str(runtime),
        'legacy_root': str(legacy),
        'dependency_root': str(deps),
        'image': args.image,
        'image_digest': args.image_digest,
        'go_version': go_version,
        'formal_runs': 180,
        'formal_offers': 37800,
    }
    write_json(workdir / 'preparation.json', summary)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
