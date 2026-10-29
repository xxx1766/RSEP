#!/usr/bin/env python3
"""Move one owned physical EdgeCore node to the dedicated Fig.6 cloud.

Run only after removing its old-cluster application Pods/Node and switching
the TCP forwards. Clears only the new owned nested node's runtime/cache.
"""
import argparse
import re
import subprocess

def main(node):
    prefix = ['/root/tmc-fig6-cluster-20261014/ke/nerdctl', '-n', 'tmc-fig6-ke',
              'exec', 'tmc-fig6-ke-' + node]
    def run(*args):
        return subprocess.check_output(prefix + list(args), text=True)
    print(run('systemctl', 'stop', 'edgecore'))
    for pod in run('crictl', 'pods', '-q').splitlines():
        print(run('crictl', 'stopp', pod))
        print(run('crictl', 'rmp', '-f', pod))
    script = '''from pathlib import Path
import re
p=Path('/var/lib/kubeedge/edgecore.db')
if p.exists(): p.rename(p.with_name('edgecore-shared-cluster-before-migration.db'))
p=Path('/etc/kubeedge/config/edgecore.yaml')
s=p.read_text()
s=s.replace('eventBus:\\n    enable: true','eventBus:\\n    enable: false')
s=s.replace('deviceTwin:\\n    dmiSockPath: /etc/kubeedge/\\n    enable: true','deviceTwin:\\n    dmiSockPath: /etc/kubeedge/\\n    enable: false')
p.write_text(s)
'''
    print(run('python3', '-c', script))
    print(run('systemctl', 'start', 'edgecore'))

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--node',required=True,choices=['nj2','nj3'])
    main(p.parse_args().node)
