#!/usr/bin/env python3
"""Targeted Fig.6 KE teardown: run cloud FIRST, then nj2 and nj3.

Leaves pre-existing kind cluster, its old edge node, shared host kubelet and
containerd intact. Retains archives, logs and runtime storage for reproducibility.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess

ROOT = Path('/root/tmc-fig6-cluster-20261014/ke')

def run(*args):
    subprocess.run(list(map(str, args)), check=True)

def main(node):
    if node == 'cloud':
        n = '/root/rsep-revision-20260928/kubeedge-tools/bin/nerdctl'
        k = [n, 'exec', 'tmc-fig6-ke-control-plane', 'kubectl', '--kubeconfig=/etc/kubernetes/admin.conf']
        run(*k, 'delete', 'namespace', 'fig6-cluster-20261014', '--ignore-not-found', '--timeout=60s')
        run(*k, 'delete', 'nodes', 'tmc-fig6-ke-nj2', 'tmc-fig6-ke-nj3', '--ignore-not-found')
        pidfile = ROOT / 'cloud-proxy.pid'
        if pidfile.exists():
            pid = int(pidfile.read_text())
            cmdline = Path('/proc', str(pid), 'cmdline')
            if cmdline.exists() and b'fig6_cluster_kubeedge_proxy.py' in cmdline.read_bytes():
                os.kill(pid, signal.SIGTERM)
            pidfile.unlink()
        env = dict(os.environ, KIND_EXPERIMENTAL_PROVIDER='nerdctl')
        tools = '/root/rsep-revision-20260928/kubeedge-tools/bin'
        env['PATH'] = tools + ':' + env['PATH']
        subprocess.run([tools + '/kind', 'delete', 'cluster', '--name', 'tmc-fig6-ke',
                        '--kubeconfig', str(ROOT / 'dedicated-kubeconfig')], env=env, check=True)
    else:
        n = [str(ROOT / 'nerdctl'), '-n', 'tmc-fig6-ke']
        name = 'tmc-fig6-ke-' + node
        existing = subprocess.check_output([*n, 'ps', '-a', '--format', '{{.Names}}'], text=True).splitlines()
        if name in existing:
            run(*n, 'rm', '-f', name)
        networks = subprocess.check_output([*n, 'network', 'ls', '--format', '{{.Name}}'], text=True).splitlines()
        if 'tmc-fig6-ke' in networks:
            run(*n, 'network', 'rm', 'tmc-fig6-ke')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', required=True, choices=['cloud', 'nj2', 'nj3'])
    main(parser.parse_args().node)
