#!/usr/bin/env python3
"""Generate issue-8 manuscript text from audited measured CSVs (no pilot pooling)."""
import csv,json
from pathlib import Path
root=Path('code/Tide/results-issue8-20261005');summ=root/'formal/summary';plots=Path('revision-figs/evaluation/issue8-20261005')
def read(p):
 with p.open() as f:return list(csv.DictReader(f))
audit=json.loads((summ/'audit.json').read_text());assert audit['status']=='complete' and audit['requests']==1620
values=json.loads((plots/'lifecycle_plot_values.json').read_text())
life=read(summ/'summary.csv');ctrl=read(Path('code/Tide/results-control-20261004/confirmatory-02/summary/summary.csv'))
find=lambda mode,metric:next(r for r in life if r['mode']==mode and r['metric']==metric)
num=lambda mode,metric:f"{float(find(mode,metric)['mean']):.2f}"
val=lambda mode,metric:f"{values[mode][metric]['mean']:.2f}"
effect=audit['paired_cold_minus_warm_ms'];pert=audit['trace_on_minus_off_ms']
ci=f"{effect['mean']-effect['ci95_half']:.2f}--{effect['mean']+effect['ci95_half']:.2f}"
pci=f"{pert['mean']-pert['ci95_half']:.2f}--{pert['mean']+pert['ci95_half']:.2f}"
labels={'tide':'Tide','kubeedge':'KubeEdge','ray':'Ray Serve'}
tex=[r'\begin{tabular}{@{}llrrrr@{}}',r'\toprule',r'Condition & System & Control + support CPU & Control + support PSS & Framework-only CPU & Framework-only PSS \\',r'\midrule']
for rate in (0,20,80):
 for system in ('tide','kubeedge','ray'):
  r=next(r for r in ctrl if r['system']==system and int(r['rps'])==rate and int(r['ddl_ms'])==(200 if rate else 0))
  cells=['Idle' if rate==0 else f'{rate} req/s',labels[system]]+[f"{float(r[k]):.2f} $\\pm$ {float(r[k+'_sd']):.2f}" for k in ('stack_cpu_pct','stack_pss_mib','framework_cpu_pct','framework_pss_mib')]
  tex.append(' & '.join(cells)+r' \\')
tex += [r'\bottomrule',r'\end{tabular}'];Path('revision_data/control_resource_table.tex').write_text('\n'.join(tex)+'\n')
section=r'''\begin{revision}
\subsection{Control-Plane Footprint and Request-Path Costs}
\label{sec:control-footprint}
We separate resident resource use from request-path wall time. The new
measurements use one NJ-k1 KVM virtual machine (16 vCPUs, exposed Xeon
Platinum 8163), Linux cgroup v2, and an identical Go CPU kernel processing
65536 bytes for 100 iterations. Application workers use CPUs 0--1 and
GOMAXPROCS=1. Three measurement blocks per framework cover idle and all
combinations of 5/20/80 requests/s and 50/200/1000-ms DDLs: 81 loaded
30-s cells, nine idle 20-s windows, and 85,050 measured requests.
System and cell order are randomized; startup and warmup are excluded.
Tide consumes the DDL in admission, whereas Ray Serve 2.58.0 and
KubeEdge 1.23.1 use it only as an evaluation threshold. Ray uses one
serial Serve replica; KubeEdge serves HTTP directly from the application Pod.

\begin{table*}[t]
\centering\small
\caption{\color{red}Measured control and deployment-support overhead, with framework-only values for reference: mean $\pm$ SD over three blocks. CPU is percent of one logical core; memory is PSS in MiB. Loaded rows use DDL=200 ms. Application kernels are excluded from both scopes.}
\label{tab:control-resources}
\resizebox{\textwidth}{!}{\input{revision_data/control_resource_table}}
\end{table*}
\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{revision-figs/evaluation/issue8-20261005/control_paper.pdf}
\caption{\color{red}Measured control and deployment-support CPU and PSS across offered pressure and DDL. Framework processes and sampled dedicated dependencies are included; application kernels and shared host services are excluded. Each panel has its own vertical scale. Error bars are exploratory 95\% $t$ intervals over three blocks, with plotted lower bounds clipped at zero. Baseline DDL labels do not imply native deadline-aware admission; loaded memory can retain effects of earlier cells.}
\label{fig:control-pressure}
\end{figure*}

Our primary resource metric is control and deployment-support overhead:
the framework plus its sampled dedicated dependencies (the measured stack).
It captures the support footprint of the deployed serving configuration.
Framework-only values remain in Table~\ref{tab:control-resources} to expose
the accounting boundary. The framework scope contains Tide's scheduler/communication process and
HTTP adapter; Ray's core, launcher, agents, Serve proxy and replica wrapper;
or KubeEdge's CloudCore and EdgeCore. The measured stack additionally
includes Tide's dedicated runtime shim or the two kind containers' internal
Kubernetes/runtime processes. Ray's launcher, agents and Serve wrappers
are already in its framework scope, so its two scopes coincide. The kind
cluster supports this specific KubeEdge test deployment; its complete
footprint should not be treated as mandatory for every KubeEdge deployment.
Shared host services and outer kind shims are
excluded; shared host containerd is sampled separately. Thus the stack is
an explicit subtotal, not total host cost. CPU and PSS are time-weighted
within the arrival window. KubeEdge controllers persist across blocks;
Tide/Ray restart, so these are not three independent KubeEdge controller boots.

Table~\ref{tab:control-resources} and Fig.~\ref{fig:control-pressure}
show the measured control and deployment-support scope. Tide has lower
mean CPU/PSS than either baseline for the displayed table conditions,
including idle (2.55\% CPU and 35.26 MiB PSS, versus 12.85\% and
1506.62 MiB for KubeEdge, and 8.92\% and 1542.54 MiB for Ray).
KubeEdge's core CPU is lower than Tide's; its controllers do not dispatch
each application request. At DDL=200 ms, increasing pressure from 5 to
80 requests/s increases control-plus-support CPU by 7.72 single-core
percentage points for Tide (95\% paired interval 7.35--8.08), versus
285.34 (278.43--292.26) for Ray and $-0.30$ ($-1.67$--1.07) for
KubeEdge. The nine within-block control-plus-support CPU contrasts
between DDL=1000 and 50 ms all include zero; this is insufficient evidence
of a stable DDL effect, not proof of equivalence. Runtime history can affect
PSS because all nine conditions share a deployment. All requests at 5 and
20 requests/s succeed; at 80 requests/s, completion rates are 42.20\%,
95.48\%, and 22.73\% for Tide, KubeEdge, and Ray, respectively. Failures
are retained. These unequal completed workloads preclude an equal-work
CPU-efficiency ranking. Tide has no cloud fallback in this local experiment.

\paragraph{Cold initialization and direct path decomposition.}
A separate instrumented experiment uses the same worker binary and CPU
allocation, DDL=1000 ms, and serial requests. Thirty randomized paired blocks
compare an initially empty executor pool with a pre-created executor;
each session includes its first request and nine later reuse requests
(600 requests, all successful). In-memory monotonic marks record admission,
resource waiting, executor acquisition, binding/preparation, execution-to-exit,
and HTTP response stages. Creation, task start and INIT readiness are
linked by executor identity and reported as nested lifecycle spans. The
nonoverlapping request stages sum to measured handler time; kernel service
time is timed by the unchanged worker. Images are cached:
this measures container/task initialization and readiness, not image pull.

\begin{figure}[t]
\centering
\includegraphics[width=\linewidth]{revision-figs/evaluation/issue8-20261005/lifecycle_paper.pdf}
\caption{\color{red}Direct Tide request-path partition, means over 30 paired blocks; error bars show 95\% $t$ intervals of total latency. Subsequent reuse averages the nine later requests in both sessions within each block. The residual includes IPC, serialization, return and external HTTP/client time, not pure control-plane execution.}
\label{fig:control-lifecycle}
\end{figure}

The on-demand first request averages COLD ms, versus WARM ms with a
pre-created executor; the paired difference is DIFF ms (95\% interval
CI ms). Subsequent warm reuse averages STEADY ms
(Fig.~\ref{fig:control-lifecycle}). For the on-demand path, cached-image
container creation/task setup averages CREATE ms, task-start invocation
START ms, and creation-through-INIT readiness READY ms. Executor
acquisition on the first request is ACQCOLD ms on demand and ACQWARM ms
when pre-created. Pre-creation does not remove first-use resource
binding/preparation: this stage averages BINDWARM ms on that first request
and BINDSTEADY ms during later reuse. Kernel execution remains separately
measured; neither a binding API call nor container readiness is mislabeled
as complete request latency.

Ten additional randomized trace-on/off warm pairs contain 50 measured
requests per session after one excluded first request (1,020 total requests).
The paired mean tracing difference is PERT ms (95\% interval PCI ms),
relative to OFF ms without tracing. These intervals describe measurement
variability rather than establishing zero instrumentation cost.
For complementary microbenchmarks, a 64-byte request padding yields
70.67~$\mu$s mean two-process PAIX/protobuf round-trip time and
13,031 round trips/s, including a minimal SHA256 worker; the candidate
filter/sort hook averages 1.43~$\mu$s for four candidates and
101.71~$\mu$s for 256. These are distinct operations. The lifecycle
intervention quantifies creation/reuse costs, while Fig.~\ref{fig:resp_valid}
evaluates configured mechanisms with actual inference warmup and
per-configuration retained runtimes. We do not infer a control-plane
latency percentage from CPU use or a residual, nor attribute all end-to-end
gains solely to the scheduling algorithm.
\end{revision}
'''
sub={'COLD':val('cold','latency_ms'),'WARM':val('warm','latency_ms'),'DIFF':f"{effect['mean']:.2f}",'CI':ci,'STEADY':val('steady','latency_ms'),'CREATE':num('cold','container_create_ms'),'START':num('cold','container_start_ms'),'READY':num('cold','container_ready_ms'),'ACQCOLD':num('cold','executor_acquisition'),'ACQWARM':f"{float(find('warm','executor_acquisition')['mean']):.3f}",'BINDWARM':num('warm','binding'),'BINDSTEADY':val('steady','binding'),'PERT':f"{pert['mean']:.3f}",'PCI':pci,'OFF':f"{pert['off_mean_ms']:.2f}"}
import re
section=re.sub(r'\b('+ '|'.join(sorted(sub,key=len,reverse=True))+r')\b',lambda m:sub[m[0]],section)
section=section.replace('\\begin{table*}[t]', '\\end{revision}\n\\begin{table*}[t]').replace('\\centering\\small', '\\centering\\small\\color{red}').replace('\\end{figure*}', '\\end{figure*}\n\\begin{revision}').replace('\\begin{figure}[t]', '\\end{revision}\n\\begin{figure}[t]').replace('\\end{figure}', '\\end{figure}\n\\begin{revision}')
Path('revision_data/6_control_overhead.tex').write_text(section)
p=Path('revision_data/6_evaluation.tex');s=p.read_text()
start=s.find('\\begin{revision}\n\\subsection{Scope of Additional Mechanism Measurements}')
if start>=0:
 end=s.index('A separate connected--disconnected--recovered run',start)
 s=s[:start]+'\\input{revision_data/6_control_overhead}\n\n\\begin{revision}\n\\subsection{Scope of Other Mechanism Measurements}\n'+s[end:];p.write_text(s)
else:assert '\\input{revision_data/6_control_overhead}' in s
response=r'''\subsection{Major 4 --- Control-plane overhead / 控制面开销}
\label{sec:overhead}
\cmt{Report CPU/memory of the Tide daemon, stub instantiation, cold-start, PAIX throughput, so algorithmic gains are separable from implementation.}
\cn{\textbf{意见}：报告 Tide daemon 的 CPU/内存、Stub 实例化、冷启动、PAIX 吞吐，区分算法与实现成本。}
\resp{We have replaced the earlier pilot estimates with audited measurements on NJ-k1, a 16-vCPU x86 KVM virtual machine. The revised paper adds a three-framework resource table and pressure/DDL figure, and a directly instrumented cold/reused request-path figure (Section ``Control-Plane Footprint and Request-Path Costs''). We distinguish resource footprint, container readiness, first-use binding, steady reuse, and kernel execution.}
\cn{\textbf{回应}：已用 NJ-k1 的正式实测替换旧试测估计，新增三系统资源表、压力／DDL 图及冷／热请求路径分解图。资源占用、容器就绪、首次绑定、稳定复用和内核执行分别报告。}

\begin{paperbox}
\textbf{Resident resources and load sensitivity.} Three measurement blocks per framework cover 5/20/80 requests/s and DDL=50/200/1000 ms, plus idle: 81 loaded cells, nine idle windows and 85,050 formal requests. The identical Go kernel uses CPU 0--1 and GOMAXPROCS=1. CPU is percentage of one logical core; memory is PSS in MiB, and table values are mean $\pm$ SD. Loaded rows use DDL=200 ms.
\begin{center}
\resizebox{\linewidth}{!}{\input{revision_data/control_resource_table}}
\end{center}
The primary metric is control and deployment-support overhead: framework plus sampled dedicated dependencies. Framework-only columns remain for reference. The framework scope is the Tide scheduler and communication process plus HTTP adapter; Ray core, launcher and agents plus Serve proxy and replica wrapper; or KubeEdge CloudCore plus EdgeCore. Application kernels are excluded. The measured stack adds dedicated Tide shim or kind-internal Kubernetes/runtime support; common host services and outer kind shims are excluded. Ray's launchers, agents and Serve wrappers are already included in its framework scope, so its two scopes coincide. This kind cluster is part of the tested deployment, not a required fixed footprint of every KubeEdge installation. The measured subtotal is not total host cost. KubeEdge controllers persist across blocks, whereas Tide/Ray restart.

The main table and figure prioritize the control-plus-support scope. In the displayed table conditions, Tide has lower mean CPU and PSS than both baselines, including idle: 2.55\% and 35.26 MiB for Tide, 12.85\% and 1506.62 MiB for KubeEdge, and 8.92\% and 1542.54 MiB for Ray. In the framework-only scope, KubeEdge has the lowest CPU because its controllers do not dispatch individual application requests. At DDL=200 ms, the paired 80-minus-5 requests/s control-plus-support CPU increase is 7.72 points for Tide (95\% interval 7.35--8.08), $-0.30$ for KubeEdge ($-1.67$--1.07), and 285.34 for Ray (278.43--292.26). All nine 1000-minus-50 ms DDL control-plus-support CPU intervals include zero. Baseline DDL is an evaluation threshold, not native admission; Tide uses it in admission. Loaded memory also depends on preceding cells. All 5/20 requests/s requests succeed; at 80 requests/s success is 42.20\%/95.48\%/22.73\% for Tide/KubeEdge/Ray. This prevents an equal-completed-work efficiency ranking, and the local Tide test has no cloud fallback.

\textbf{Cold versus retained execution.} Thirty randomized pairs compare first requests with an empty versus pre-created executor pool, followed by nine reuse requests per session (600 successful requests). The mean on-demand first latency is COLD ms; pre-created first latency is WARM ms; the paired difference is DIFF ms (95\% interval CI ms). Later reuse averages STEADY ms. Direct marks place cached-image creation/task setup at CREATE ms, task-start invocation at START ms, and creation-through-INIT readiness at READY ms. First-use binding/preparation is BINDWARM ms even with pre-creation, versus BINDSTEADY ms for later reuse. Thus ``pre-created'', ``first request'', and ``steady warm reuse'' are not interchangeable. Images are cached and no image-pull cost is claimed.

The timing partition is audited against handler and client time. Ten trace-on/off pairs, each with 50 measured warm requests after an excluded first request, give a PERT-ms paired mean difference (95\% interval PCI ms), versus OFF ms without tracing. PAIX/protobuf separately measures 70.67~$\mu$s mean round trip and 13,031 round trips/s with 64-byte padding and a minimal worker, not one-way raw queue throughput. Candidate filtering/sorting takes 1.43~$\mu$s at four candidates and 101.71~$\mu$s at 256.

We remove the unsupported ``control plane below 5\% of end-to-end latency'' and algorithm-only attribution. The new lifecycle intervention measures creation/reuse effects; the warmed inference ablation tests the configured mechanisms. IPC/serialization/transport residual is explicitly labeled, and is not treated as a pure control-plane share.
\end{paperbox}
\cn{主图和主表优先展示“控制与部署支撑开销”：框架进程加已采样专属依赖，应用计算、共享宿主机服务和 kind 外层 shim 排除。Tide 加专属 runtime shim；KubeEdge 加本次专用 kind 内部 Kubernetes／运行时；Ray 的支撑进程已在框架范围内。本次 kind 部署的开销不能外推为所有 KubeEdge 部署的必需成本。空载及表中负载条件下 Tide 的合计 CPU／PSS 更低；同时保留框架单独数据，其中 KubeEdge 核心 CPU 更低。这些范围有明确边界，不能宣称所有指标、配置均优。DDL 的 CPU 配对差值区间均覆盖零，不能据此证明完全等价。高压成功率不同，保留全部失败，不能据此作等完成量效率排名。}
\cn{补充 30 组冷／预建配对、共 600 个请求：按需首次请求 COLD ms、预建首次请求 WARM ms、后续热复用 STEADY ms。预建首次绑定／准备仍需 BINDWARM ms，后续 BINDSTEADY ms；缓存镜像下容器创建到 INIT 就绪 READY ms，不能替代端到端延迟或镜像拉取时间。另用 10 组计时开关配对核验探针扰动。删除旧 17 MB、98 万消息/s、325/580 ms、2 ms、150 倍和“控制面低于 5\% 因而收益来自算法”等未经同口径实测支持的结论。保留实测 PAIX 往返和排序钩子结果，并明确机制消融与创建／复用干预的不同作用。}

'''
response=re.sub(r'\b('+ '|'.join(sorted(sub,key=len,reverse=True))+r')\b',lambda m:sub[m[0]],response)
p=Path('response_to_reviewers.tex');s=p.read_text();start=s.index('\\subsection{Major 4 --- Control-plane overhead');end=s.index('\\subsection{Major 5 --- Reproducibility',start);s=s[:start]+response+s[end:]
s=s.replace('%  NOTE: control-plane numbers are from an aarch64 pilot; re-run on the\n%  x86 testbed and replace where marked % X86-FINAL.','%  Control-plane resource and lifecycle numbers use audited NJ-k1 x86 measurements.\n%  Sources: results-control-20261004 and results-issue8-20261005.')
if '\\usepackage{graphicx}' not in s:s=s.replace('\\usepackage{array}','\\usepackage{array}\n\\usepackage{graphicx}')
p.write_text(s)
p=Path('revision_issue_log.md');s=p.read_text();start=s.index('### 8. 控制面开销');end=s.index('### 9.',start)
issue=f'''### 8. 控制面开销（R1 主 4）

- [x] 三系统正式实测已覆盖空闲、5/20/80 req/s 和 50/200/1000 ms DDL，三块重复，共 81 个负载单元、9 个空闲窗口、85,050 个请求；CPU/RSS/PSS、依赖计量范围和失败均保留（`code/Tide/results-control-20261004/RESULTS.md`）。
- [x] 补齐同条件端到端阶段分解：30 组随机冷／预建配对、600 个请求，直接记录创建、task start、INIT、资源等待、获取 Executor、绑定／准备、执行及返回；另以 10 组探针开关配对核验计时扰动（`code/Tide/results-issue8-20261005/formal/summary/`）。
- [x] 按需首次 {sub['COLD']} ms、预建首次 {sub['WARM']} ms、后续复用 {sub['STEADY']} ms；缓存镜像创建到 INIT 就绪 {sub['READY']} ms。明确排除镜像拉取，区分首次绑定和稳定复用。残差不称为控制面占比，也不由此推导全部收益来自调度算法。
- [x] 正文更新为实测资源表、压力／DDL 图和生命周期阶段图；资源主图及主表优先展示控制与部署支撑合计，框架单独数据保留作参考（`revision_data/6_control_overhead.tex`、`revision-figs/evaluation/issue8-20261005/`）；保留 PAIX 往返 70.67 μs、13,031 次/s 和排序钩子口径。
- [x] 回复稿 Major 4 中英文已替换旧 17 MB、98 万 msg/s、325/580 ms、2 ms、150 倍和控制面 <5% 归因说法；正文不宣称 Tide 核心 CPU 低于 KubeEdge，基线 DDL 仅为评价阈值，过载失败与计量限制明确。

'''
p.write_text(s[:start]+issue+s[end:])
report=f'''# 第八条补测与论文回填

正式数据：30 组冷／预建配对（600 请求），10 组计时开关配对（1,020 请求，其中每会话首个请求排除于扰动均值），全部成功。试跑排除。严格核验阶段完整性、时间单调性、阶段相加、Executor 身份、启动状态、CPU 亲和性、worker 哈希与资源清理。

| 指标 | 实测均值 ms |
|---|---:|
| 按需首次请求 | {sub['COLD']} |
| 预建首次请求 | {sub['WARM']} |
| 后续热复用 | {sub['STEADY']} |
| 按需首次容器创建／task setup | {sub['CREATE']} |
| task start 调用 | {sub['START']} |
| 创建至 INIT 就绪 | {sub['READY']} |
| 预建首次绑定／准备 | {sub['BINDWARM']} |
| 后续绑定／准备 | {sub['BINDSTEADY']} |

按需首次−预建首次配对均值 {sub['DIFF']} ms，95% t 区间 [{ci.replace('--',', ')}] ms。探针开启−关闭为 {sub['PERT']} ms，区间 [{pci.replace('--',', ')}]，关闭时均值 {sub['OFF']} ms。后续复用在每块内先平均两会话共 18 请求，统计单位仍是 30 块。

这是相同镜像已缓存条件下的请求路径与生命周期干预，不含镜像拉取，不与首次 Stub 创建、单次绑定 API 或历史试测的时间混用。绑定阶段包括 BindProc/BindRtn 和 Execute 前准备。IPC／外部残差不能当作控制面延迟占比。冷−预建比较定位创建成本；已有暖机推理消融评估配置机制，均不支持“全部收益只来自算法”的归因。

三系统资源数据沿用 [完整控制面结果](../results-control-20261004/RESULTS.md)，未为了改变系统排名而重跑或剔除过载数据。主图／主表优先展示控制与部署支撑开销（框架＋已采样专属依赖）：Tide 加 runtime shim，KubeEdge 加本次 kind 内 Kubernetes／运行时，Ray 支撑进程已包含于框架范围。应用内核、共享宿主机服务和 kind 外层 shim 排除。该合计不是完整主机成本，也不是所有部署的固定成本；表中 Tide 的合计均值更低。框架单独数据保留，其中 KubeEdge 核心 CPU 最低。

交付：
- [阶段汇总 CSV](formal/summary/summary.csv)、[逐请求阶段](formal/summary/requests.csv)、[独立审计](formal/summary/audit.json)。
- [压力／DDL 图](../../../revision-figs/evaluation/issue8-20261005/control_paper.pdf)、[生命周期图](../../../revision-figs/evaluation/issue8-20261005/lifecycle_paper.pdf)。
- 正文 `revision_data/6_control_overhead.tex`（由 `6_evaluation.tex` 引入）、回复稿 Major 4、问题清单第八条均已同步。

复现：从仓库根目录运行 `python3 code/Tide/scripts/revision/analyze_issue8_latency.py --root code/Tide/results-issue8-20261005/formal`，随后运行 `plot_issue8_paper.py`（参数见脚本）和 `write_issue8_revision.py`。执行程序、脚本与完整插桩源码归档于 `provenance/`；原始 worker SHA256 为 `{audit['worker_sha256']}`。宿主 NJ-k1 为 KVM 虚拟机，非新增物理机。原始测试源与已完成的控制面矩阵保持不变。
'''
(root/'RESULTS.md').write_text(report)
print('Generated paper section, shared table, bilingual response, issue log, report')
