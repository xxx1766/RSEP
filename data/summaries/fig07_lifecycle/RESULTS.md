# 第八条补测与论文回填

正式数据：30 组冷／预建配对（600 请求），10 组计时开关配对（1,020 请求，其中每会话首个请求排除于扰动均值），全部成功。试跑排除。严格核验阶段完整性、时间单调性、阶段相加、Executor 身份、启动状态、CPU 亲和性、worker 哈希与资源清理。

| 指标 | 实测均值 ms |
|---|---:|
| 按需首次请求 | 163.41 |
| 预建首次请求 | 61.21 |
| 后续热复用 | 31.32 |
| 按需首次容器创建／task setup | 91.96 |
| task start 调用 | 9.95 |
| 创建至 INIT 就绪 | 104.81 |
| 预建首次绑定／准备 | 38.94 |
| 后续绑定／准备 | 9.27 |

按需首次−预建首次配对均值 102.20 ms，95% t 区间 [95.27, 109.12] ms。探针开启−关闭为 0.016 ms，区间 [-0.25, 0.28]，关闭时均值 30.95 ms。后续复用在每块内先平均两会话共 18 请求，统计单位仍是 30 块。

这是相同镜像已缓存条件下的请求路径与生命周期干预，不含镜像拉取，不与首次 Stub 创建、单次绑定 API 或历史试测的时间混用。绑定阶段包括 BindProc/BindRtn 和 Execute 前准备。IPC／外部残差不能当作控制面延迟占比。冷−预建比较定位创建成本；已有暖机推理消融评估配置机制，均不支持“全部收益只来自算法”的归因。

三系统资源数据沿用 [完整控制面结果](../results-control-20261004/RESULTS.md)，未为了改变系统排名而重跑或剔除过载数据。主图／主表优先展示控制与部署支撑开销（框架＋已采样专属依赖）：Tide 加 runtime shim，KubeEdge 加本次 kind 内 Kubernetes／运行时，Ray 支撑进程已包含于框架范围。应用内核、共享宿主机服务和 kind 外层 shim 排除。该合计不是完整主机成本，也不是所有部署的固定成本；表中 Tide 的合计均值更低。框架单独数据保留，其中 KubeEdge 核心 CPU 最低。

交付：
- [阶段汇总 CSV](formal/summary/summary.csv)、[逐请求阶段](formal/summary/requests.csv)、[独立审计](formal/summary/audit.json)。
- [压力／DDL 图](../../../revision-figs/evaluation/issue8-20261005/control_paper.pdf)、[生命周期图](../../../revision-figs/evaluation/issue8-20261005/lifecycle_paper.pdf)。
- 正文 `revision_data/6_control_overhead.tex`（由 `6_evaluation.tex` 引入）、回复稿 Major 4、问题清单第八条均已同步。

复现：从仓库根目录运行 `python3 code/Tide/scripts/revision/analyze_issue8_latency.py --root code/Tide/results-issue8-20261005/formal`，随后运行 `plot_issue8_paper.py`（参数见脚本）和 `write_issue8_revision.py`。执行程序、脚本与完整插桩源码归档于 `provenance/`；原始 worker SHA256 为 `7a7a6f3af52810394d02737b23ecc0f62c7c715d474ec72c9b44b0d1e8bf3659`。宿主 NJ-k1 为 KVM 虚拟机，非新增物理机。原始测试源与已完成的控制面矩阵保持不变。
