#!/usr/bin/env python3
"""Owned KubeEdge Fig. 6 deployment helpers; run on the indicated physical host.

Uses the archived isolated kind cloud and NEW nested nodes on NJ-k2/NJ-k3.
Never stops host kubelets/containerd. Bootstrap archives contain secrets and must
remain private; evidence deliberately excludes tokens, keys and kubeconfigs.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('/root/tmc-fig6-cluster-20261014')
KE = ROOT / 'ke'
IMAGE = 'docker.io/kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f'

def run(*args, check=True):
    return subprocess.run(list(map(str, args)), check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout

def nerd(*args):
    return run(KE / 'nerdctl', '--namespace', 'tmc-fig6-ke', *args)

def prepare(node):
    KE.mkdir(parents=True, exist_ok=True)
    os.chmod(KE, 0o700)
    (ROOT / 'io-data').mkdir(exist_ok=True)
    run('tar', '-xf', ROOT / 'bootstrap.tar', '-C', KE)
    os.chmod(KE / 'join-token', 0o600)
    name = 'tmc-fig6-ke-' + node
    index = int(node[-1])
    (KE / 'node-var').mkdir(exist_ok=True)
    if subprocess.run([str(KE / 'nerdctl'), '-n', 'tmc-fig6-ke', 'image', 'inspect', IMAGE],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        print(nerd('load', '-i', ROOT / 'kind-node.tar'))
    if not (KE / 'node-var/lib/dpkg/alternatives/iptables').exists():
        # Bind mounts do not copy image contents, unlike kind's anonymous volume.
        print(nerd('run', '--rm', '--network', 'none', '--entrypoint', '/bin/cp',
                   '-v', str(KE / 'node-var') + ':/seed', IMAGE, '-a', '/var/.', '/seed/'))
    if 'tmc-fig6-ke' not in nerd('network', 'ls'):
        print(nerd('network', 'create', '--subnet', f'172.29.{40+index}.0/24', 'tmc-fig6-ke'))
    if name not in nerd('ps', '-a', '--format', '{{.Names}}'):
        print(nerd('run', '-d', '--name', name, '--hostname', name,
                   '--privileged', '--cgroupns', 'private', '--network', 'tmc-fig6-ke',
                   '--tmpfs', '/run', '--tmpfs', '/tmp',
                   '-v', str(KE / 'node-var') + ':/var',
                   '-v', '/lib/modules:/lib/modules:ro',
                   '-v', str(ROOT / 'io-data') + ':/fig6-io',
                   '-v', str(KE) + ':/fig6-setup',
                   '-p', f'11.0.4.{46+index}:29890:29890', IMAGE))
    elif name not in nerd('ps', '--format', '{{.Names}}'):
        print(nerd('start', name))
    for _ in range(30):
        probe = subprocess.run([str(KE / 'nerdctl'), '--namespace', 'tmc-fig6-ke',
                                'exec', name, 'ctr', 'version'], capture_output=True)
        if probe.returncode == 0:
            break
        time.sleep(2)
    print(nerd('exec', name, 'systemctl', 'disable', '--now', 'kubelet'))
    # A real local CNI bridge makes the isolated CRI network ready. Workers use
    # hostNetwork inside this nested node and therefore do not cross this bridge.
    cni = {'cniVersion': '0.4.0', 'name': 'fig6-local', 'plugins': [
        {'type': 'bridge', 'bridge': 'fig6pod0', 'isGateway': True, 'ipMasq': True,
         'ipam': {'type': 'host-local', 'ranges': [[{'subnet': f'172.30.{40+index}.0/24'}]],
                  'routes': [{'dst': '0.0.0.0/0'}]}}, {'type': 'portmap', 'capabilities': {'portMappings': True}}]}
    (KE / '10-fig6.conflist').write_text(json.dumps(cni))
    print(nerd('exec', name, 'sh', '-c', 'mkdir -p /etc/cni/net.d; cp /fig6-setup/10-fig6.conflist /etc/cni/net.d/'))
    # Preserve cloud certificate's original IP SAN by proxying that IP inside
    # the new nested network namespace. No physical-interface shaping/routing.
    script = '''set -eu
mkdir -p /etc/kubeedge/config /var/lib/kubeedge
cp /fig6-setup/edgecore /usr/local/bin/edgecore
ip address add 10.4.1.2/32 dev lo 2>/dev/null || true
cp /fig6-setup/edgecore-template.yaml /etc/kubeedge/config/edgecore.yaml
'''
    print(nerd('exec', name, 'sh', '-c', script))
    config = (KE / 'edgecore-template.yaml').read_text()
    config = config.replace('hostnameOverride: tmc-ke-worker', 'hostnameOverride: ' + name)
    config = config.replace('token: ""', 'token: "' + (KE / 'join-token').read_text().strip() + '"')
    config = config.replace('podSandboxImage: kubeedge/pause:3.6', 'podSandboxImage: registry.k8s.io/pause:3.10')
    # Token stays in the private host setup directory; never emit this file.
    (KE / 'edgecore-node.yaml').write_text(config)
    os.chmod(KE / 'edgecore-node.yaml', 0o600)
    print(nerd('exec', name, 'cp', '/fig6-setup/edgecore-node.yaml', '/etc/kubeedge/config/edgecore.yaml'))
    proxy_unit = '''[Unit]
Description=Fig6 isolated CloudCore transport proxy
After=network.target
[Service]
ExecStart=/usr/bin/python3 /fig6-setup/fig6_cluster_kubeedge_proxy.py edge
CPUAffinity=8 9 10 11 12 13 14 15
Restart=always
[Install]
WantedBy=multi-user.target
'''
    edge_unit = '''[Unit]
Description=Fig6 owned EdgeCore
After=containerd.service fig6-proxy.service
[Service]
ExecStart=/usr/local/bin/edgecore
CPUAffinity=8 9 10 11 12 13 14 15
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
    (KE / 'fig6-proxy.service').write_text(proxy_unit)
    (KE / 'edgecore.service').write_text(edge_unit)
    print(nerd('exec', name, 'sh', '-c', 'cp /fig6-setup/*.service /etc/systemd/system/; systemctl daemon-reload; systemctl enable --now fig6-proxy edgecore'))
    print(json.dumps({'node': node, 'container': name, 'worker_port': 29890,
                      'runtime_namespace': 'tmc-fig6-ke', 'host_io': str(ROOT / 'io-data')}))

def cloud():
    KE.mkdir(parents=True, exist_ok=True)
    path = KE / 'cloud-proxy.pid'
    if path.exists():
        try:
            os.kill(int(path.read_text()), 0)
            print('cloud proxy already running')
            return
        except ProcessLookupError:
            pass
    log = open(KE / 'cloud-proxy.log', 'ab')
    proc = subprocess.Popen(['taskset', '-c', '8-15', 'python3',
                             str(KE / 'fig6_cluster_kubeedge_proxy.py'), 'cloud'],
                            stdout=log, stderr=log, start_new_session=True)
    path.write_text(str(proc.pid))
    print(json.dumps({'cloud_proxy_pid': proc.pid, 'ports': [29880, 29882, 29890, 29891]}))

def cloud_mount():
    """Attach a cloned host bind mount to the existing isolated cloud node."""
    import ctypes
    lib = ctypes.CDLL(None, use_errno=True)
    n = '/root/rsep-revision-20260928/kubeedge-tools/bin/nerdctl'
    pid = int(run(n, 'inspect', 'tmc-ke-control-plane', '--format', '{{.State.Pid}}'))
    source = ROOT / 'io-data'
    source.mkdir(exist_ok=True)
    target_root = os.open(f'/proc/{pid}/root', os.O_PATH)
    namespace = os.open(f'/proc/{pid}/ns/mnt', os.O_RDONLY)
    # Linux x86-64 open_tree(OPEN_TREE_CLONE|O_CLOEXEC), then move_mount.
    mount_fd = lib.syscall(428, -100, os.fsencode(source), 1 | os.O_CLOEXEC)
    if mount_fd < 0:
        raise OSError(ctypes.get_errno(), 'open_tree')
    if lib.setns(namespace, 0) < 0:
        raise OSError(ctypes.get_errno(), 'setns')
    os.fchdir(target_root)
    os.chroot('.')
    os.chdir('/')
    os.makedirs('/fig6-io', exist_ok=True)
    if lib.syscall(429, mount_fd, b'', -100, b'/fig6-io', 4) < 0:
        raise OSError(ctypes.get_errno(), 'move_mount')
    print('host I/O directory mounted at cloud node /fig6-io')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare-node', 'cloud-forward', 'cloud-mount'])
    parser.add_argument('--node', choices=['nj2', 'nj3'])
    args = parser.parse_args()
    if args.action == 'prepare-node':
        prepare(args.node)
    elif args.action == 'cloud-mount':
        cloud_mount()
    else:
        cloud()
