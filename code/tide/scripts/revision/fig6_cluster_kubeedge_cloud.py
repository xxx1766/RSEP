#!/usr/bin/env python3
"""Build a NEW dedicated hostNetwork-only KE control plane on NJ-k1.

Copies real installed KubeEdge resources and cached images; no mock components.
Private copied CA/token material stays mode 0600 under the remote owned ke root.
The old cluster is only read until the explicit migration/cleanup phase.
"""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/root/tmc-fig6-cluster-20261014/ke')
TOOLS = Path('/root/rsep-revision-20260928/kubeedge-tools')
N = str(TOOLS / 'bin/nerdctl')
OLD = 'tmc-ke-control-plane'
NEW = 'tmc-fig6-ke-control-plane'

def run(*args):
    return subprocess.check_output(list(map(str, args)), text=True)

def kub(node, *args):
    return run(N, 'exec', node, 'kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', *args)

def clean(obj):
    obj.pop('status', None)
    meta = obj['metadata']
    for key in ['uid', 'resourceVersion', 'creationTimestamp', 'generation', 'managedFields', 'ownerReferences']:
        meta.pop(key, None)
    meta.pop('annotations', None)
    return obj

def main():
    os.umask(0o077)
    config = '''kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  disableDefaultCNI: true
  kubeProxyMode: none
  apiServerAddress: 127.0.0.1
  apiServerPort: 29950
nodes:
- role: control-plane
  extraMounts:
  - hostPath: /root/tmc-fig6-cluster-20261014/io-data
    containerPath: /fig6-io
  - hostPath: /root/tmc-fig6-cluster-20261014/ke
    containerPath: /fig6-setup
'''
    (ROOT / 'dedicated-kind.yaml').write_text(config)
    if NEW not in run(N, 'ps', '-a', '--format', '{{.Names}}').splitlines():
        env = dict(os.environ, KIND_EXPERIMENTAL_PROVIDER='nerdctl')
        env['PATH'] = str(TOOLS / 'bin') + ':' + env['PATH']
        subprocess.run([str(TOOLS / 'bin/kind'), 'create', 'cluster', '--name', 'tmc-fig6-ke',
                        '--config', str(ROOT / 'dedicated-kind.yaml'),
                        '--image', 'docker.io/kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f',
                        '--kubeconfig', str(ROOT / 'dedicated-kubeconfig')], env=env, check=True)
    # No cross-node Pod network is needed: every application and CloudCore uses
    # the nested node's hostNetwork. A local CNI keeps the CRI runtime healthy.
    cni = {'cniVersion': '0.4.0', 'name': 'fig6-cloud-local', 'plugins': [
        {'type': 'bridge', 'bridge': 'fig6pod0', 'isGateway': True, 'ipMasq': True,
         'ipam': {'type': 'host-local', 'ranges': [[{'subnet': '172.30.41.0/24'}]],
                  'routes': [{'dst': '0.0.0.0/0'}]}}]}
    (ROOT / 'cloud-local.conflist').write_text(json.dumps(cni))
    print(run(N, 'exec', NEW, 'sh', '-c', 'mkdir -p /etc/cni/net.d; cp /fig6-setup/cloud-local.conflist /etc/cni/net.d/10-fig6.conflist'))
    print(kub(NEW, '-n', 'kube-system', 'delete', 'deployment', 'coredns', '--ignore-not-found'))
    print(kub(NEW, 'delete', 'namespace', 'local-path-storage', '--ignore-not-found', '--timeout=60s'))
    print(kub(NEW, 'delete', 'storageclass', 'standard', '--ignore-not-found'))
    crds = json.loads(kub(OLD, 'get', 'crds', '-o', 'json'))['items']
    for item in crds:
        clean(item)
    (ROOT / 'dedicated-crds.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':crds}))
    print(kub(NEW, 'apply', '-f', '/fig6-setup/dedicated-crds.json'))
    items = [{'apiVersion':'v1','kind':'Namespace','metadata':{'name':'kubeedge'}}]
    for kind, names in [('clusterrole', ['cloudcore', 'cloudcore-common']),
                        ('clusterrolebinding', ['cloudcore']),
                        ('serviceaccount', ['cloudcore']),
                        ('secret', ['casecret', 'cloudcore', 'cloudcoresecret', 'tokensecret']),
                        ('configmap', ['cloudcore'])]:
        for name in names:
            scope = [] if kind.startswith('cluster') else ['-n', 'kubeedge']
            items.append(clean(json.loads(kub(OLD, *scope, 'get', kind, name, '-o', 'json'))))
    deployment = clean(json.loads(kub(OLD, '-n', 'kubeedge', 'get', 'deployment', 'cloudcore', '-o', 'json')))
    spec = deployment['spec']['template']['spec']
    spec['nodeSelector'] = {'kubernetes.io/hostname': NEW}
    spec['hostNetwork'] = True
    spec['dnsPolicy'] = 'Default'
    spec['tolerations'] = [{'operator':'Exists'}]
    env = spec['containers'][0].setdefault('env', [])
    env[:] = [x for x in env if x['name'] not in ['KUBERNETES_SERVICE_HOST','KUBERNETES_SERVICE_PORT']]
    env += [{'name':'KUBERNETES_SERVICE_HOST','value':'127.0.0.1'},
            {'name':'KUBERNETES_SERVICE_PORT','value':'6443'}]
    spec['containers'][0]['imagePullPolicy'] = 'Never'
    # Explicitly disable unused streaming/iptables helpers in the NEW cluster.
    for item in items:
        if item['kind'] == 'ConfigMap' and item['metadata']['name'] == 'cloudcore':
            value = item['data']['cloudcore.yaml']
            value = value.replace('cloudStream:\n    enable: true', 'cloudStream:\n    enable: false')
            value = value.replace('iptablesManager:\n    enable: true', 'iptablesManager:\n    enable: false')
            item['data']['cloudcore.yaml'] = value
    items.append(deployment)
    for item in items:
        if item['kind'] == 'ClusterRole':
            for rule in item.get('rules', []):
                if 'devices.kubeedge.io' in rule.get('apiGroups', []) and 'devices' in rule.get('resources', []):
                    if 'devicestatuses' not in rule['resources']:
                        rule['resources'].append('devicestatuses')
    private = ROOT / 'dedicated-ke-private.json'
    private.write_text(json.dumps({'apiVersion':'v1','kind':'List','items':items}))
    os.chmod(private,0o600)
    image = 'docker.io/kubeedge/cloudcore:v1.23.1'
    if image not in run(N, 'exec', NEW, 'ctr', '-n', 'k8s.io', 'images', 'ls', '-q').splitlines():
        try:
            print(run(N, 'exec', OLD, 'ctr', '-n', 'k8s.io', 'images', 'export', '--platform', 'linux/amd64',
                      '/root/fig6-cloudcore.tar', image))
            print(run(N, 'cp', OLD + ':/root/fig6-cloudcore.tar', ROOT / 'cloudcore-image.tar'))
            print(run(N, 'exec', NEW, 'ctr', '-n', 'k8s.io', 'images', 'import', '--platform', 'linux/amd64', '/fig6-setup/cloudcore-image.tar'))
        except subprocess.CalledProcessError:
            # Old kind CRI discards unpacked compressed blobs; retrieve the exact
            # upstream digest instead of rebuilding or substituting CloudCore.
            ref = 'docker.io/kubeedge/cloudcore@sha256:17c68dd34177dc4d76743106e11be1c4446fd081338e29cf21c6f66b3e856fd1'
            print(run(N, 'exec', NEW, 'ctr', '-n', 'k8s.io', 'images', 'pull', '--platform', 'linux/amd64', ref))
            print(run(N, 'exec', NEW, 'ctr', '-n', 'k8s.io', 'images', 'tag', ref, image))
    print(kub(NEW, 'apply', '-f', '/fig6-setup/dedicated-ke-private.json'))
    print(kub(NEW, '-n', 'kubeedge', 'get', 'pods', '-o', 'wide'))
    ip = run(N, 'inspect', NEW, '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}').strip()
    (ROOT / 'cloud-target-ip').write_text(ip + '\n')
    print(ip)

if __name__ == '__main__':
    main()
