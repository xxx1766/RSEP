#!/usr/bin/env python3
"""Verified four-objective calibration plots and self-contained notebook."""
import argparse
import csv
import hashlib
import json
import math
import shutil
import statistics
from pathlib import Path

if __name__ == '__main__':
    import matplotlib
    matplotlib.use('Agg')

# BEGIN EMBEDDED PLOT PROGRAM
import numpy as np
import matplotlib.pyplot as plt


def draw(data, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'figure.dpi': 120, 'font.size': 10, 'axes.grid': True,
                         'grid.alpha': .3, 'axes.axisbelow': True, 'axes.unicode_minus': False,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    grid = np.array(data['grid'])
    x, y = grid[:, 1]+grid[:, 2]/2, grid[:, 2]*np.sqrt(3)/2
    features = np.array([[r['GoodputNorm'], r['Resilience'], r['Util'], -r['LatencyCost']]
                         for r in data['calibration']])
    fig, axes = plt.subplots(2, 3, figsize=(12, 7.3))
    for ax, choice in zip(axes.flat, data['selection']):
        lam = np.array(choice['Lambda']); lam /= lam.sum()
        z = features @ lam
        sc = ax.scatter(x, y, c=z, cmap='viridis', s=42, marker='^')
        ax.plot([0, 1, .5, 0], [0, 0, np.sqrt(3)/2, 0], 'k-', lw=.8)
        wi = choice['WeightID']
        ax.scatter([x[wi]], [y[wi]], marker='*', s=160, c='tab:red', edgecolors='k', linewidths=.5,
                   label='Calibration choice', zorder=5)
        ax.scatter([x[66]], [y[66]], marker='o', s=80, facecolors='none', edgecolors='tab:red',
                   label='Equal score weights', zorder=5)
        ax.text(-.04, -.08, '$w_u$'); ax.text(.99, -.08, '$w_l$'); ax.text(.47, .92, '$w_d$')
        label = choice['Name'].replace('_', ' ').title()
        ax.set_title(label+'\n'+r'$\lambda=$'+','.join(f'{v:.2f}' for v in lam), fontsize=10)
        ax.set_xlim(-.10, 1.10); ax.set_ylim(-.12, 1.02); ax.set_aspect('equal'); ax.axis('off')
        fig.colorbar(sc, ax=ax, shrink=.72, pad=.01, label='Calibration objective')
    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='lower center', ncol=2, fontsize=9, frameon=False)
    fig.suptitle('Four-objective offline calibration (restricted simulation)\n'
                 'Triangle coordinates: online score weights; colors: fixed-preference objective', fontsize=12)
    fig.tight_layout(rect=[0, .05, 1, .94])
    fig.subplots_adjust(hspace=.5)
    fig.savefig(out/'objective_weight_simplex.pdf', bbox_inches='tight')
    fig.savefig(out/'objective_weight_simplex.png', bbox_inches='tight')

    fig2, axes2 = plt.subplots(2, 2, figsize=(10.5, 6.8))
    ids = [66, 67, data['selection'][0]['WeightID']]
    labels = ['Equal\n(1/3,1/3,1/3)', 'Archived candidate\n(.45,.35,.20)', 'Calibrated\n'+str(tuple(data['grid'][ids[2]]))]
    fields = [('GoodputNorm', 100, 'Window deadline goodput / offered rate (%)'),
              ('Resilience', 100, 'Outage-arrival on-time yield (%)'),
              ('Util', 100, 'Modeled CPU/memory occupancy (%)'),
              ('MeanLatencyMS', 1, 'Conditional mean response latency (ms)')]
    for ax, (key, scale, title) in zip(axes2.flat, fields):
        means = [data['test'][wi][key]*scale for wi in ids]
        errors = [data['test_ci95'][wi][key]*scale for wi in ids]
        bars = ax.bar(range(3), means, yerr=errors, capsize=4, width=.6,
                      color=['#8da0cb', '#fc8d62', '#66c2a5'])
        ax.set_xticks(range(3), labels, fontsize=9)
        ax.set_title(title, fontsize=10)
        ax.set_ylim(0, max(m+e for m, e in zip(means, errors))*1.22)
        for bar, val, err in zip(bars, means, errors):
            ax.text(bar.get_x()+bar.get_width()/2, val+err+ax.get_ylim()[1]*.025,
                    f'{val:.2f}', ha='center', va='bottom', fontsize=9)
    fig2.suptitle('Independent held-out traces; equal objective preference\n'
                  'Balanced scenario means; 95% t intervals across 3 independent seed batches', fontsize=12)
    fig2.tight_layout(rect=[0, 0, 1, .92])
    fig2.savefig(out/'objective_heldout_metrics.pdf', bbox_inches='tight')
    fig2.savefig(out/'objective_heldout_metrics.png', bbox_inches='tight')
    return [fig, fig2]
# END EMBEDDED PLOT PROGRAM


def mean_metrics(rows):
    return {key: statistics.mean(r['Metrics'][key] for r in rows) for key in rows[0]['Metrics']}


def cell(kind, source):
    result = dict(id=hashlib.sha256(source.encode()).hexdigest()[:12], cell_type=kind,
                  metadata={}, source=source.splitlines(keepends=True))
    if kind == 'code':
        result.update(execution_count=None, outputs=[])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--paper-figs', type=Path, required=True)
    args = parser.parse_args()
    root = args.root
    audit = json.loads((root/'verification.json').read_text())
    assert audit['verified_runs'] == 4896 and audit['verified_decisions'] == 4882536
    manifest = json.loads((root/'manifest.json').read_text())
    for name, h in manifest['artifact_sha256'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest() == h, name
    inputs = json.loads((root/'inputs.json').read_text())
    traces = {tr['ID']: tr for tr in inputs['Traces']}
    rows = [json.loads(s) for s in (root/'summary.jsonl').read_text().splitlines()]
    data = dict(grid=inputs['Grid'], selection=json.loads((root/'selection.json').read_text()),
                lambda_sensitivity=json.loads((root/'lambda_sensitivity.json').read_text()),
                calibration=[], test=[], test_ci95=[], provenance=manifest['artifact_sha256'])
    for wi in range(68):
        for split in ['calibration', 'test']:
            group = [r for r in rows if r['WeightID'] == wi and r['Split'] == split]
            assert len(group) == 36
            data[split].append(mean_metrics(group))
            if split == 'test':
                batches = [mean_metrics([r for r in group if traces[r['TraceID']]['Seed'] % 100 == rep]) for rep in range(3)]
                data['test_ci95'].append({key: 4.3026527299*statistics.stdev(b[key] for b in batches)/math.sqrt(3)
                                         for key in group[0]['Metrics']})
    (root/'plot_data.json').write_text(json.dumps(data, indent=2)+'\n')
    figures = draw(data, root/'figures')
    for fig in figures:
        plt.close(fig)
    args.paper_figs.mkdir(parents=True, exist_ok=True)
    for p in (root/'figures').iterdir():
        shutil.copy2(p, args.paper_figs/p.name)
    fields = ['GoodputNorm', 'Resilience', 'Util', 'MeanLatencyMS', 'P95LatencyMS']
    with (root/'heldout_summary.csv').open('w') as fp:
        w = csv.writer(fp); w.writerow(['weight_id', 'w_u', 'w_l', 'w_d', *fields])
        for wi, weights in enumerate(data['grid']):
            w.writerow([wi, *weights, *(data['test'][wi][key] for key in fields)])
    with (root/'per_scenario.csv').open('w') as fp:
        w = csv.writer(fp); w.writerow(['weight_id', 'split', 'rate', 'network', 'pattern', *fields])
        for wi in [66, 67, data['selection'][0]['WeightID']]:
            for split in ['calibration', 'test']:
                for rate in [10, 30, 60]:
                    for net in ['low', 'high']:
                        for pattern in ['steady', 'burst']:
                            group = [r for r in rows if r['WeightID']==wi and r['Split']==split and r['Network']==net
                                     and traces[r['TraceID']]['Rate']==rate and traces[r['TraceID']]['Pattern']==pattern]
                            assert len(group)==3
                            m=mean_metrics(group);w.writerow([wi,split,rate,net,pattern,*(m[key] for key in fields)])
    program = Path(__file__).read_text().split('# BEGIN EMBEDDED PLOT PROGRAM\n', 1)[1].split('# END EMBEDDED PLOT PROGRAM', 1)[0]
    program = 'from pathlib import Path\nget_ipython().run_line_magic("matplotlib", "inline")\n'+program
    heading = ('# 四个 λ 离线选参：受限模拟结果\n\n'
               '四个目标权重选择三项在线评分参数；不修改 Tide 在线评分函数。'
               '本 notebook 内嵌全部绘图数据和 Python 程序，不引用外部图片。\n\n'
               '4896 次模拟、36 条保存的输入轨迹、4,882,536 个决策。校准/测试种子分离；'
               '两种网络共享相同请求输入。服务尺度来自历史内核校准，容量、网络、内存令牌和误差为模型设定。'
               'R_srv 为断网窗口提交请求的按时完成率；U_model 为执行期间 CPU/内存令牌占用，均不冒充原生 Tide 实测。\n\n'
               '图中误差棒来自 3 个独立种子批次；每批先平均 12 个场景，不能解释为硬件测量置信区间。'
               '六组预设 λ 均选出 (0,0.9,0.1)，48 次单坐标 λ 扰动中有 2 次选择不同参数。'
               '这不是普遍最优权重或性能保证。\n')
    nb = dict(nbformat=4, nbformat_minor=5,
              metadata=dict(kernelspec=dict(display_name='Python 3', language='python', name='python3'), language_info=dict(name='python')),
              cells=[cell('markdown', heading), cell('code', 'data = '+repr(data)+'\n'), cell('code', program),
                     cell('code', "figures = draw(data, Path('objective-calibration-exports'))\nplt.show()\n")])
    notebook = Path(__file__).resolve().parents[1]/'objective_calibration.ipynb'
    notebook.write_text(json.dumps(nb, ensure_ascii=False, indent=1)+'\n')
    print(notebook)
    print(json.dumps({s['Name']:s['Weights'] for s in data['selection']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
