#!/usr/bin/env python3
"""Build the repository's Chinese experiment report from audited final data."""
import argparse
import csv
import json
from pathlib import Path

p=argparse.ArgumentParser(); p.add_argument('--results',type=Path,required=True); a=p.parse_args()
root=a.results; summary=root/'confirmatory-02/summary'
def read(name):
    with (summary/name).open() as f:return list(csv.DictReader(f))
rows=read('summary.csv'); runs=read('per_run.csv'); effects=read('paired_effects.csv')
audit=json.loads((summary/'independent-audit.json').read_text()); cleanup=json.loads((root/'final-cleanup.json').read_text())
assert audit['status']=='complete' and audit['requests']==85050 and audit['warmup_requests']==90
assert cleanup['status']=='passed' and len(rows)==30 and all(int(r['n'])==3 for r in rows)
labels={'tide':'Tide','kubeedge':'KubeEdge','ray':'Ray Serve'}
lookup={(r['system'],int(r['rps']),int(r['ddl_ms'])):r for r in rows}
def fmt(r,key):return f"{float(r[key]):.2f} ± {float(r[key+'_sd']):.2f}"
lines=['# Tide KubeEdge 和 Ray Serve 控制面开销对比',
'', '本实验在消融矩阵完成并通过审计之后，于 NJ-k1 上串行运行。81 个负载单元、9 个空闲窗口全部完成，保留 85,050 个正式请求（包括失败）、90 个预热请求和 6,766 次逐进程采样。',
'', '**主要结果：Tide 的已测框架 PSS 最小；KubeEdge 的 CloudCore 与 EdgeCore 稳态 CPU 最低；Ray Serve 的 CPU 随请求压力显著增加。不能据此给出功能等价系统的统一优劣排名。** 本次将 DDL 从 50 改为 1000 ms 时，三系统在各请求速率下的框架 CPU 配对差值区间均覆盖零，未观察到稳定的 DDL CPU 开销效应。',
'', '## 实验设置与计量范围',
'', '- 单台 NJ-k1 KVM 虚拟机：16 vCPU，暴露的处理器型号为 Xeon Platinum 8163 @ 2.50 GHz，Debian Linux 6.12.95，cgroup v2。不是原论文 i7 平台或跨物理节点实验。',
'- 同一个 Go CPU 内核：65536 字节 × 100 次迭代；实际可执行文件 SHA256 在三系统一致。应用绑定 CPU 0–1，GOMAXPROCS=1；控制进程保持正常资源配置。',
'- 到达压力为 5、20、80 req/s；DDL 为 50、200、1000 ms。每格 30 s、三次测量块；每块另测 20 s 空闲，系统顺序和块内条件顺序固定种子随机化。启动及预热不计入开销窗口。外部请求等待上限名义值为 30 s（aiohttp 的取整可使实际等待接近 31 s）。',
'- Tide 经实际 SubmitRoutine／PAIX 路径，函数级 DDL 配置由运行日志验证。Ray Serve 2.58.0 为单副本、max_ongoing_requests=1，转发到相同 Go worker；KubeEdge 1.23.1 管理隔离 kind 集群内的 Pod，HTTP 请求直接到应用。Ray／KubeEdge 不使用这些 DDL 做原生逐请求准入；DDL 在它们的数据中是评价阈值。',
'- CPU 来自进程 CPU ticks 的时间差，100% 等于一个逻辑核；每 0.5 s 采样。RSS 与 PSS 均保留，主内存指标采用 PSS，避免直接相加 RSS 时重复计入共享页。主表为到达窗口内的时间加权均值；CPU 毫秒／提交请求及吞吐指标包含排空时间。',
'- “框架”包括 Tide 调度／通信进程及测试 HTTP 适配器、Ray 核心／launcher／agents／Serve proxy／replica 包装进程，或 KubeEdge CloudCore＋EdgeCore；相同 Go 应用内核单列并排除。',
'- “已采样依赖合计”另计 Tide 专属 shim 或 kind 容器内部的 Kubernetes／运行时等支撑进程。它不是完整主机开销：共同宿主机服务和 kind 外层 shim 未计入；共享宿主机 containerd 仅另列，不全额归给 Tide。详见组件表。',
'- KubeEdge 控制进程跨重复持续运行，仅应用 Pod 重建；Tide／Ray 每块重新启动。三次是测量块，不是三次独立重启的 KubeEdge 控制面。',
'', '## 代表性对比',
'', '以下为三块均值 ± 样本标准差，内存单位 MiB。负载行固定 DDL=200 ms；CPU 不含应用内核。',
'', '| 条件 | 系统 | 框架 CPU（% 单核） | 框架 PSS | 已采样依赖合计 CPU（% 单核） | 已采样依赖合计 PSS |',
'|---|---|---:|---:|---:|---:|']
for rate in (0,20,80):
    for system in ('tide','kubeedge','ray'):
        r=lookup[(system,rate,200 if rate else 0)]
        lines.append('| '+('空闲' if rate==0 else f'{rate} req/s')+f" | {labels[system]} | {fmt(r,'framework_cpu_pct')} | {fmt(r,'framework_pss_mib')} | {fmt(r,'stack_cpu_pct')} | {fmt(r,'stack_pss_mib')} |")
lines += ['', '[完整 30 行表格](../../../revision-figs/evaluation/control-20261004/control_table.pdf) · [可编辑 CSV](confirmatory-02/summary/summary.csv) · [进程组件表](../../../revision-figs/evaluation/control-20261004/control_components.pdf)',
'', '## 请求压力是否改变开销差异',
'', '固定 DDL=200 ms，按同一重复块配对计算 80−5 req/s 的 CPU 差值：',
'', '| 系统 | CPU 增量（单核百分点） | 探索性 95% t 区间 |', '|---|---:|---:|']
for system in ('tide','kubeedge','ray'):
    e=next(e for e in effects if (e['system'],e['factor'],e['condition'],e['metric'])==(system,'pressure','ddl200','framework_cpu_pct'))
    lines.append(f"| {labels[system]} | {float(e['high_minus_low']):.2f} | [{float(e['ci95_low']):.2f}, {float(e['ci95_high']):.2f}] |")
gap5=float(lookup[('ray',5,200)]['framework_cpu_pct'])-float(lookup[('tide',5,200)]['framework_cpu_pct'])
gap80=float(lookup[('ray',80,200)]['framework_cpu_pct'])-float(lookup[('tide',80,200)]['framework_cpu_pct'])
lines += ['', f'因此，开销差距确实随压力变化：同一 DDL 下，Ray Serve−Tide 的平均 CPU 差距由 5 req/s 时的 {gap5:.2f} 个百分点扩大到 80 req/s 时的 {gap80:.2f} 个百分点。KubeEdge 核心进程变化较小、区间覆盖零；这与其控制面不处理应用逐请求调度的路径相一致，不能解释为提供相同逐请求策略却成本更低。',
'', '[各系统内部压力与 DDL 曲线](../../../revision-figs/evaluation/control-20261004/control_sensitivity.pdf)使用独立纵轴，便于观察小幅变化；[统一口径对比图](../../../revision-figs/evaluation/control-20261004/control_pressure_deadline.pdf)便于比较绝对量级。误差线为三块均值的 95% t 区间，图中下界截于零；CSV 保留原始区间半宽。',
'', '## DDL 是否改变开销',
'', '以下是同块配对的 1000−50 ms DDL 框架 CPU 差值，而不是请求延迟差值。',
'', '| req/s | Tide：差值 [95% 区间] | KubeEdge：差值 [95% 区间] | Ray Serve：差值 [95% 区间] |', '|---:|---:|---:|---:|']
for rate in (5,20,80):
    cells=[]
    for system in ('tide','kubeedge','ray'):
        e=next(e for e in effects if (e['system'],e['factor'],e['condition'],e['metric'])==(system,'deadline',f'rps{rate}','framework_cpu_pct'))
        assert float(e['ci95_low'])<=0<=float(e['ci95_high'])
        cells.append(f"{float(e['high_minus_low']):.2f} [{float(e['ci95_low']):.2f}, {float(e['ci95_high']):.2f}]")
    lines.append('| '+str(rate)+' | '+' | '.join(cells)+' |')
lines += ['', '本次没有足够证据认定 DDL 带来稳定的 CPU 开销变化，也不能用三次重复证明完全等价。区间是探索性的，未经多重比较校正。对于 Ray／KubeEdge，更不能把评价阈值的分组差异解释成原生 DDL 策略开销。',
'', '内存还受运行历史影响：Tide／Ray 在过载后可能保留较高的驻留内存，因此低速率单元的 PSS 有时高于先执行的高速率单元。每块九个条件共享进程，虽然顺序随机化，三块仍不足以排除历史效应。汇总保留 order_in_block、prior_high_pressure_cells 和起始时间；不将这些内存均值差异直接归因于 DDL 或“高压力降低内存”。',
'', '## 过载结果必须一起阅读',
'', '5 和 20 req/s 的正式请求全部成功。80 req/s 下三系统实际完成量不同，因此高压 CPU 数值不能直接解释为完成相同工作量时的效率。',
'', '| 系统 | 80 req/s 请求数 | 成功数 | 成功率 |', '|---|---:|---:|---:|']
for system in ('tide','kubeedge','ray'):
    subset=[r for r in runs if r['system']==system and int(r['rps'])==80]
    n=sum(int(r['requests']) for r in subset); success=sum(int(r['successes']) for r in subset)
    lines.append(f'| {labels[system]} | {n:,} | {success:,} | {100*success/n:.2f}% |')
assert all(int(r['successes'])==int(r['requests']) for r in runs if int(r['rps']) in (5,20))
lag=max(float(r['arrival_lag_p99_ms']) for r in runs)
lines += ['', f'所有失败原样保留：KubeEdge 976 个、Ray Serve 16,691 个、Tide 12,484 个。Tide 中 12,464 个为适配器返回的 504，20 个为客户端 599；其他两系统的失败均为客户端记录的 599，不能把 599 当作框架原生 HTTP 状态。599 错误字符串为空，时间约 30–31 s，与外部超时一致，但不推断未记录的异常类型。各单元发送延迟 p99 的最大值为 {lag:.2f} ms。',
'', 'Tide 本次只有本地节点：过载路径日志含“there is no cloud node”，因此这些超时不代表配置了云端后备节点的完整 Things–Edge–Cloud 系统。Ray 结果只覆盖这里的单副本 Serve 配置，不外推为原生 Ray Tasks、全部 Actor 配置或自动扩缩容的普遍结论。',
'', '## 数据与复现',
'', '- [逐次测量汇总](confirmatory-02/summary/per_run.csv)、[全部条件统计](confirmatory-02/summary/summary.csv)、[组件统计](confirmatory-02/summary/component_summary.csv)。',
'- [配对压力与 DDL 差值](confirmatory-02/summary/paired_effects.csv)、[系统间差值](confirmatory-02/summary/system_gaps.csv)、[进程身份与计入范围](confirmatory-02/summary/process_inventory.csv)。',
'- 原始数据位于 `confirmatory-02/{system}-r{1,2,3}/`：每格保留 requests.csv、samples.jsonl、measurement.json；每块保留 setup.json 和相应运行日志。',
'- [完整独立审计](confirmatory-02/summary/independent-audit.json)、[原始文件 SHA256](confirmatory-02/summary/raw-sha256.json)、[清理核验](final-cleanup.json)。',
'- [图表目录](../../../revision-figs/evaluation/control-20261004/)包含 PDF／PNG 全表、组件表、空闲依赖图、跨系统对比及系统内敏感性图；两张表同时提供 LaTeX。',
'- `provenance-02` 保留首个 KubeEdge 块使用的脚本／二进制；`provenance-03` 为恢复后版本。文件描述符上限及可选 cgroup 元数据采集问题的失败尝试单独保留，未纳入正式统计。恢复时随机计划不变，首块原始文件哈希验证未变化。首次 KubeEdge 高压单元前 8.17 s 的外部驱动 nice 为 5，随后改为 0；控制进程一直为 0，时间记录和发送延迟审计保留。',
'', '从仓库根目录重算：', '', '```bash',
'python3 code/Tide/scripts/revision/audit_control_comparison.py \\',
'  --root code/Tide/results-control-20261004/confirmatory-02 \\',
'  --provenance code/Tide/results-control-20261004/provenance-03 \\',
'  --history-provenance code/Tide/results-control-20261004/provenance-02',
'python3 code/Tide/scripts/revision/analyze_control_comparison.py \\',
'  --root code/Tide/results-control-20261004/confirmatory-02',
'python3 code/Tide/scripts/revision/plot_control_comparison.py \\',
'  --summary code/Tide/results-control-20261004/confirmatory-02/summary/summary.csv \\',
'  --out revision-figs/evaluation/control-20261004',
'```', '', '绘图需要 matplotlib；本次环境为 Python 3.14.5、matplotlib 3.11.2、numpy 2.5.3（`/private/tmp/tmc-fig5-plot-20261004/bin/python`）。最终核验确认本次进程、Pod、Tide 容器、子 cgroup 和消息队列均无遗留；原有集群服务保留。']
(root/'RESULTS.md').write_text('\n'.join(lines)+'\n')
print(root/'RESULTS.md')
