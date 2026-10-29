#!/usr/bin/env python3
"""Build a data-loading notebook from the completed revision evidence bundle.

This script never generates experimental outcomes. Legacy filename is retained
as a compatibility entry point; the old synthetic design is archived separately.
"""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path,default=Path(__file__).resolve().parents[2]/'results-revision-20260928')
    a=p.parse_args()
    files=['figA_ctrl_plane','figB_degradation','figC_sensitivity','figD_hetero','figE_utilization','figF_optgap']
    if any(not (a.results/'figures'/f'{name}.png').is_file() for name in files):
        raise SystemExit('All A–F evidence figures are required; no synthetic fallback is allowed.')
    cells=[]
    def md(s):cells.append(dict(cell_type='markdown',metadata={},source=s.splitlines(keepends=True)))
    def code(s):cells.append(dict(cell_type='code',metadata={},execution_count=None,outputs=[],source=s.splitlines(keepends=True)))
    md('''# TMC 修订实验：正式数据与受限模型结果

本 notebook 已替换旧的占位演示。只读取已保存的输入、原始记录、汇总与图表，不生成性能数值。

- A / D / E：单机真实 Tide、Bare 与 Kubernetes 执行；对照名称和测量口径见图注。
- B：两进程真实工作负载的 HTTP 故障适配器，**不是原生 Tide 跨节点恢复证明**。
- C：上半部分为理想估计下的权重模型；下半部分为真实 TTL 回收与复用。
- F：受限小实例模型的最优解对照，已经独立求解器核验。

不得把全部结果写成真机实验，也不得声称 Tide 在所有场景优于对照。旧合成设计保存在 `archive/synthetic-design-20260928/`，不是实验结果。
''')
    code('''from pathlib import Path
import csv, json, hashlib
import matplotlib.pyplot as plt

# Works from the repository, scripts/, or notebook directory.
candidates = [Path.cwd(), *Path.cwd().parents]
ROOT = next((p for p in candidates if (p / 'results-revision-20260928').is_dir()), None)
if ROOT is None:
    ROOT = next(p / 'code/Tide' for p in candidates if (p / 'code/Tide/results-revision-20260928').is_dir())
RESULTS = ROOT / 'results-revision-20260928'
FIG = RESULTS / 'figures'
def load_json(path): return json.loads(path.read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read_csv(path):
    with path.open() as f: return list(csv.DictReader(f))
audit = load_json(RESULTS / 'workload-analysis-final/audit.json')
assert audit['status'] == 'passed' and audit['total_runs'] == 135 and audit['total_requests'] == 23760
for name, expected in audit['outputs_sha256'].items():
    assert sha(RESULTS / 'workload-analysis-final' / name) == expected, name
print(f"Verified workload analysis: {audit['total_runs']} runs, {audit['total_requests']} offered requests")
bundle = load_json(RESULTS / 'figure_data_manifest.json')
for entry in bundle['figures'].values():
    for name, expected in entry['figure_sha256'].items():
        assert sha(RESULTS / name) == expected, name
print('Verified all six figure files against the bundle manifest')
''')
    md('''## 关键表格

Goodput 统计 `[start,end)` 内按时完成的请求；成功比例保留窗口后完成和失败。
CPU 使用量与 CPU 预留分开；Tide cgroup 的 `memory.current` 不能等同进程 RSS。
''')
    code('''runs = read_csv(RESULTS / 'workload-analysis-final/per_run.csv')
for system in ('tide', 'bare', 'kubernetes'):
    group = [r for r in runs if r['system'] == system and r['workload'] == 'cpu' and int(r['rps']) == 40]
    print(system, 'CPU @ 40 req/s, on-time goodput by run:', [round(float(r['on_time_goodput']), 3) for r in group])
print('TTL observations (real logical CPU reservation):')
for row in read_csv(FIG / 'figC_ttl.csv'):
    print({k: row[k] for k in ('ttl_ms', 'repeat', 'creations', 'idle_reserved_cpu_s')})
''')
    captions={
        'figA_ctrl_plane':'A：控制进程 CPU/RSS、创建与复用、候选排序、跨进程 PAIX，以及同一请求的服务/端到端耗时。',
        'figB_degradation':'B：作用于实验 HTTP 端点的断开和恢复。本地资格关闭是消融条件；不代表 Kubernetes 或原生 Tide 的断网行为。',
        'figC_sensitivity':'C：权重模型与真实 TTL 实验分开标注。TTL 归还逻辑 CPU 预留，执行器容器保留。',
        'figD_hetero':'D：CPU、内存访问、同步 I/O 和交替 100/500 ms QoS。Bare 与容器的 I/O 存储路径不同。',
        'figE_utilization':'E：真实 CPU 时间除以两逻辑核预算；预留量见 TTL 数据，不能用预留量替代 CPU 使用率。',
        'figF_optgap':'F：固定串行执行器、共同截止期的受限模型；120 个实例，不是一般问题的近似比证明。'}
    for name in files:
        md('## '+captions[name])
        code(f"image = plt.imread(FIG / '{name}.png')\nplt.figure(figsize=(15, 10))\nplt.imshow(image)\nplt.axis('off')\nplt.show()\n")
    md('''## 从原始记录重新生成

在 `code/Tide` 下执行 `scripts/revision/regenerate_revision.py`，它只校验、统计和绘图，不启动远程实验。
需要 Python 的 numpy、scipy、matplotlib；PDF 使用 `tectonic results-revision-20260928/revision_experiments.tex`。
完整实验命令、程序标识、原始请求、故障事件和数据对应关系保存在结果目录。
''')
    for index, cell in enumerate(cells): cell['id']=f'cell-{index:02d}'
    notebook=dict(cells=cells,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3'),language_info=dict(name='python',version='3')),nbformat=4,nbformat_minor=5)
    scripts=Path(__file__).resolve().parents[1]
    for name in ('revision_figures.ipynb','placeholder_new_figs.ipynb'):
        (scripts/name).write_text(json.dumps(notebook,ensure_ascii=False,indent=1)+'\n')
    print('Wrote revision_figures.ipynb and legacy-name data-only entry point')


if __name__=='__main__':main()
