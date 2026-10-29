# comparison-v3 冻结时的方法说明

本说明记录冻结时的数据沿用、隔离修正及解释边界；完成后的正式数值见[36 单元结果报告](domain-resilience-results.md)。主比较固定为 **3 系统 × 2 初始状态 × 2 连通条件 × 3 重复 = 36 单元**。v3 在 `1793122085718193000` ns 冻结；[协议](../comparison-v3/protocol.json)、[36 单元映射](../comparison-v3/order.json)、[22 次新执行顺序](../comparison-v3/execution-order.json)、[22 文件源码清单](../comparison-v3/source-hashes.json)与[三机实际部署哈希证明](../evidence/fresh-staged-sources-1793121676751816000.json)保留可追溯依据。

## 为什么整族替换旧 KE 数据

[跨轮隔离审计](cross-episode-isolation-audit.json)在物理主机采集的工作日志中，将每个 START/RESULT 的请求 ID 与当前轮冻结的 930 个 offered ID 比对，发现如下直接证据：

| 旧 KE 轮次 | 外来 START / RESULT | 请求来源 |
|---|---:|---|
| `f2-kubeedge-warm-online` | 92 / 92 | 前一轮 warm-drop |
| `f2-kubeedge-cold-online` | 241 / 241 | 前一轮 warm-online |
| `f2-kubeedge-cold-drop` | 361 / 361 | 前一轮 cold-online |

最后一轮中，新建 A worker 在当前首个 offer 前约 39.5 秒已开始执行旧轮请求，且 204 个外来 RESULT 出现在当前 t=0 之后。B 的早期快照虽为零 worker，其真实 transport-ready 事件却发生在 t=−10.369 秒，冷启动状态未保持到首个 offer。这证明输入与初始状态发生跨轮污染；**尚未证明是哪一个组件保存或重放了请求**。见[详细评估及物理日志链接](kubeedge-carryover-assessment.md#direct-evidence-addendum-cross-round-requests-and-comparison-v3)、[A 工作日志](../runs/f2-kubeedge-cold-drop/A/http-worker-journals/requests.jsonl)、[B 工作日志](../runs/f2-kubeedge-cold-drop/B/http-worker-journals/requests.jsonl)。本地完成不等同于客户端按时收到结果。

因此，已采集的 **8 个旧 KE 轮次全部保留为复用状态诊断证据，全部退出主比较**；按预先冻结的新 ID 重新执行完整的 **12 个 KE 单元**，不只替换低分或已明确污染的行。其余旧 KE 行未观察到外来 ID，不等于已证明其控制与路由状态干净；也不能据此声称全部 8 行均被直接证明污染。该修正针对隔离失效，不以收益、超时率或 B 是否被使用作为取舍条件。

Tide/Ray 已完成的 **14 单元**通过同一严格请求 ID 隔离检查及原协议审计，按原 ID、原结果沿用；其余 **10 单元**继续原冻结方法，不重选已完成结果。因此主比较为 **14 沿用 + 12 新 KE + 10 原计划 Tide/Ray = 36**。新采集共 22 次。请求失败、低收益、未扩容或未使用 B 均不能成为丢弃或重跑理由；后续资格问题须保留原始证据并明确披露，不能靠替换差结果修正。

## 诊断与正式样本分开

`diag-ke-fresh-cold`、`...cold2`、`...cold3`、`...cold4` 均止于基础设施准备，未发送应用 offers；其[准备失败与恢复回执](../sensitivity/resets)保留，相关源码版本另行归档。这四次不计正式重复。NodeReady 条件时间与 NodeLease 新鲜度、真实 Node 重注册、CloudCore 替换后 Pending Pod 的等待以及启动顺序，均在正式 v3 冻结前修正。

成功的 `diag-ke-fresh-cold5` 也只属于诊断（`pilot=true`、`repeat=0`）。[冻结引用的诊断审计](fresh-diagnostic5-audit-correct-proof.json)为 PASS，记录 930 个 START、930 个 RESULT、零外来 ID；物理 B 日志归属 373 次执行。这仅说明该次准备、观测与隔离流程得到验证，**B 使用量不是通过阈值，也不是正式性能结果**。该单次 connected 诊断不能证明未来所有轮次均成功，不能估计故障效应或替代 36 单元最终审计。

## 每个新 KE 单元的固定准备顺序

按[协调器](../comparison-v3/coordinator.py)、[fresh helper](../sensitivity/runtime/fresh_kubeedge.py)与[CloudCore helper](../sensitivity/runtime/cloudcore_reset.py)，在应用启动前固定执行：

1. 确认无应用 Pod/运行容器；记录外层容器、DaemonSet、C Node 与旧 A/B Node、Mesh Pod/容器身份。
2. 停止 A/B 自有 EdgeCore，验证停止；按旧 Pod UID 精确停止并移除 Mesh CRI 容器和 sandbox。C 端以 UID 前置条件删除旧 Mesh Pod 及自有 A/B Node 对象。
3. 在 EdgeCore 停止时，将私有 SQLite 主文件及存在的 WAL/SHM/回滚日志按同文件系统重命名归档；保留镜像缓存、证书、配置和应用原始日志，公开哈希与身份元数据，不导出私有数据库内容。
4. **先启动 A/B EdgeCore**并记录活动 MainPID；等待 A/B 以新 Node UID、正确 IP、Ready 状态及属于新 Node 的新鲜 Lease 完成真实注册。此屏障不要求 Mesh 已运行；C Node UID/IP 与 DaemonSet UID保持一致。
5. **注册完成后**，UID 精确删除并替换自有 CloudCore Pod；要求新 Pod Ready，Deployment/ReplicaSet UID、镜像及 imageID 保持一致。只允许未分配节点且未 Ready 的 Pending 状态作为等待中间态。
6. 在同一活动 EdgeCore MainPID 下等待真实新 Mesh Pod/CRI/进程身份；再验证新 Node/Lease 和原生 A/B Metrics。将控制线程固定到 CPU 8–15，验证新数据库身份。专用云集群、配置、证书、缓存镜像与 DaemonSet 保留；这不是重建整个集群。
7. 启动原应用 Deployment/HPA：warm 为 A/B 各一实例；cold 为 A 一实例、B 无应用实例且镜像已缓存。按原协议发送 150 秒、930 offers，deadline 500 ms、客户端 timeout 2 s；drop 条件使用 20–110 秒的既定 C 路径故障。测量中不重启、不修缓存、不手动扩容、不替换真实 EdgeMesh 路由。测量后按冻结清理流程停止 sampler，并验证应用 worker 消失。

这是一个**联合新鲜状态处理**：同时刷新边缘持久元数据、Mesh 进程、A/B Node 注册及 CloudCore Pod。即使其后隔离正常，也不能把修正效果单独归因于某一 Mesh 缓存或 CloudCore 缺陷；准备等待时间也不是应用冷扩容时间。

## 公平性与结论边界

新增 KE 轮次在 A/B 以 1 Hz 执行[只读 SQLite sampler](../sensitivity/runtime/sqlite_sampler.py)，精确读取当前 namespace 的 Service/Endpoints，使用 `mode=ro`、`query_only`，可见已提交 WAL；不通过新增的 MetaServer HTTP 查询刷新缓存。它运行于控制 CPU 8–15，但仍共享磁盘、页缓存及 SQLite/WAL 协调资源。**只读不等于零开销或零扰动**；该额外观测只用于新 KE，不能声称三系统新增观测开销完全相同。采样耗时、错误、停止与退出回执应随证据保留，不能把数据库视图直接当作 Mesh 生效路由状态。

14 次沿用与 22 次新增来自不同采集时段。预先冻结替换整族、保留全部合格 Tide/Ray 单元能避免按结果择优，但不能消除时间漂移，也不能把主比较描述为同一时段完整随机化的 36 次实验。应按轮次展示结果，以重复轮次而非单轮的 930 个请求作为比较不确定性的单位。

A/B 固定为不同物理主机；每机应用上限同为 CPU 0–1、2 CPU 配额、512 MiB、禁 swap，C 的应用预算为零。**相同预算不代表相同处理速度**；原[冻结协议](../protocol/protocol.json)已记录 A/B 校准差异且未人工均速。共享 Go 内核为 CPU-only、65536 字节 × 1000 次，单线程执行；2 CPU 是声明与分配预算，不代表每请求持续占满两核。结果反映该异构 A/B 拓扑、该负载及真实路由/扩容实现，不能独立归因于调度算法，也不能外推到同构集群、GPU、感知/执行器、I/O 密集任务或完整云主机断电。当前故障是冻结作用域内的 C 通信路径丢包。

能力存在、实例 Ready、B 本地执行与 A 按 deadline 收到正确结果应分别报告。主服务指标必须保留所有 offered 请求作为分母，包含拒绝、超时和未收到有效结果；重复执行不增加有用 offers。固定域能力 `R_cap` 不能直接表示调度质量，当前 C4 也没有实现新的域泛化门控；详见[公式与实现评估](formula-assessment.md)。正式矩阵完成、隔离/资源/故障/清理审计通过之前，本说明不作系统排序或韧性收益的数值结论。

可供论文复用的英文方法表述：

> Cross-episode request contamination invalidated the isolated-workload interpretation of the reused KubeEdge path. We retained all eight collected episodes as diagnostics and prospectively replaced the complete twelve-cell KubeEdge family under a fixed reset protocol. Fourteen previously collected Tide/Ray episodes passed strict isolation checks; ten remaining episodes retained the original method. Setup diagnostics were excluded from the primary comparison, and neither application success nor use of B was a qualification threshold. Interpretation remains conditional on heterogeneous hardware, acquisition epochs, and additional read-only KubeEdge instrumentation.
