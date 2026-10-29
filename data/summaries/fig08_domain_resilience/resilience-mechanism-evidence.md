# 域内韧性机制：配置与单轮观测证据

本说明只使用已通过审计的 Tide/Ray 轮次与 q1 的四个新 KE 单元，资格回执见 [interim-primary](interim-primary)。以下数值均是**指定单轮的机制例证**，不是三重复汇总、显著性检验或尚未完成的 36 单元最终结论。旧 KE 复用状态数据不进入这些例证；沿用与替换规则见 [v3 方法说明](v3-methodology-note.md)。冻结协议及源码不因本说明改变。

除明确标注的原始日志时间外，t 均相对该轮 A 的冻结首个 offer；B 时间按该轮时钟证据换算。每轮 [spec](../runs/q1-ke-cold-drop/A/spec.json)与 `A/clock-*.json`、`C/fault.json`保留绝对时间和故障转换依据。标准 drop 窗口约为 t=20–110 s，实际规则安装/撤除有非零边界；outage 分母使用冻结分析器识别的 offered-arrival cohort，而非挑选完成请求。下列 outage 计数均为各自指定轮次的 570 个 offers。

## KE：warm 本地数据路径可继续；cold 扩容另需云端反馈与下发

**配置支持的机制。** [真实 EdgeMesh 配置](../source/ke_assets.py)启用 edgeProxy/edgeTunnel；relayNodes 只有 A=`11.0.4.48`、B=`11.0.4.49`，未把 C 配置为应用请求的逐跳代理。应用使用真实 Service VIP `10.96.50.90:34090`，客户端进入 A 的网络命名空间，worker 在 A/B，C 应用预算为零。见 [部署适配](../deploy.py)、[warm 实际 setup](../evidence/q1-ke-warm-drop/setup.json)。因此不能把本基线描绘成“每个请求必须先经云”的人为劣势。

**warm-drop 的直接观测。** `q1-ke-warm-drop` 的 t0=`1793122856379866909` ns；B 的物理日志在 t=−31.612 s 已记录 transport-ready。隔离期 570/570 offers 按时正确返回，实际 A/B 执行分别为 285/285。依据为 [A 请求/返回记录](../runs/q1-ke-warm-drop/A/requests.jsonl)、[A 物理工作日志](../runs/q1-ke-warm-drop/A/http-worker-journals/requests.jsonl)、[B 物理工作日志](../runs/q1-ke-warm-drop/B/http-worker-journals/requests.jsonl)和[实际故障时间](../runs/q1-ke-warm-drop/C/fault.json)。这证明**该轮已就绪 A/B 路径在所施加的 C 通信隔离中仍能提供服务**；不等于无任何控制依赖、可永久离线，或冷扩容也具有相同能力。

**cold 的配置依赖。** [应用清单生成器](../source/ke_assets.py)将初始 Deployment 设为 1 replica、优先 A，硬性 A/B 节点亲和与 Pod 反亲和保证每机最多一应用实例；真正的 `autoscaling/v2` HPA 位于专用 C 集群，CPU target=30%、min=1、max=2。原生 metrics-server 经 CloudStream/EdgeStream 路径取得边缘指标，HPA 再更新 Deployment，由控制与边缘同步链使新 Pod 落地。见 [CloudStream/metrics 配置与准备验证](../runtime/kubeedge_domain.py)、[实际 connected 原生指标证明](../evidence/q1-ke-cold-online/setup.json)。这是扩容控制依赖，不是 warm 请求转发必经 C 的证据。

| 指定单轮 | 实际事件与时间 | 可支持的解释 |
|---|---|---|
| `q1-ke-cold-online` | t0=`1793122196260160188` ns；HPA `lastScaleTime=2026-10-27T17:30:54Z`（约 t=57.740 s）；B transport-ready t=58.632 s；首个 B timely return t=60.545 s | 连通控制下，该配置确实通过真实扩容产生并使用 B；并非只声明 B 可用 |
| `q1-ke-cold-drop` | t0=`1793122479384566253` ns；HPA `ScalingActive=False / FailedGetResourceMetric`，条件时间 `2026-10-27T17:35:26Z`（约 t=46.615 s）；末次 inventory 为 current/desired=1；完整窗口无 B ready/执行，隔离 cohort timely=85/570 | 该轮 cloud-feedback 路径失效与未形成 B 服务能力相一致，且 HPA 明确报告未收到目标 Pod 指标 |

对应原始材料：[online HPA/Pod inventory](../evidence/q1-ke-cold-online/ke-end-inventory.json)、[online B ready/执行日志](../runs/q1-ke-cold-online/B/http-worker-journals/requests.jsonl)、[online 客户端记录](../runs/q1-ke-cold-online/A/requests.jsonl)；[drop HPA/Pod inventory](../evidence/q1-ke-cold-drop/ke-end-inventory.json)、[drop B 连续物理观测](../runs/q1-ke-cold-drop/B/observations.jsonl)、[drop A 工作日志](../runs/q1-ke-cold-drop/A/http-worker-journals/requests.jsonl)、[drop 客户端记录](../runs/q1-ke-cold-drop/A/requests.jsonl)。inventory 是测量后快照；条件内嵌转换时间可定位事件，但快照本身不是完整 HPA/Pod 事件流。

额外的被动 [online A SQLite 观测](../sensitivity/resets/q1-ke-cold-online/A/db-observations.jsonl)在 seq=97、t≈61.106 s 首次采到 A+B ready Endpoints；[drop A SQLite 观测](../sensitivity/resets/q1-ke-cold-drop/A/db-observations.jsonl)只采到 A。**采样时刻不是路由安装时刻**：online 的首次 B timely return 早于该 1 Hz 首次采样，恰说明不能把离散 DB 记录直接等同于代理内部生效时间。这些观测不经新增 MetaServer HTTP 查询，但也不属于零开销观测。

边界：以上直接命中了指标获取失败，并显示新 B 未成为实际服务者；它没有逐段证明 CloudStream、CloudHub、调度器、Pod 下发与 EdgeMesh 各自贡献多少失败。末次 inventory 不能证明整段时间从未有任何短暂控制尝试；日志也不支持将全部超时单独归咎于某个组件。warm/cold 的对照支持“保留可用数据路径”与“获得新的服务能力”是不同问题。

## Ray：已有边缘数据路径与云端控制状态、恢复并非同一件事

**配置。** [冻结适配器](../runtime/ray_domain.py)将真实 Ray head/GCS 和 Serve controller 放在 C，A/B 各提供 2 Ray CPU，C 为 0；EveryNode proxy 使 A 的 HTTP 入口可到边缘 replica，Go 内核由边缘 replica 经 loopback 调用。warm 固定 2 replicas；cold 使用真实 Serve autoscaling（initial/min=1、max=2）。请求转发中没有故障计时器或模拟失败分支。该轮 [实际 deployment](../runs/f1-ray-warm-drop/C/ray/deployment.json)记录 Ray **2.58.0**、默认 heartbeat/reconnect 设置、无 adapter override。显式停止由测量后的 [campaign 清理路径](../campaign.py)触发。

`f1-ray-warm-drop` 的 t0=`1793115619020335174` ns（主机日志显示 `2026-10-27 23:40:19.020 +08:00`）；以下为同一轮的真实日志序列，日志时间到毫秒，t 仅为近似定位：

| 原始事件 | 时间 / 相对 t | 证据层次 |
|---|---|---|
| C Serve controller 收不到边缘 replica 的 health response，标记 unhealthy | `23:41:15.146`、`23:41:16.582`，约 t=56.126/57.562 s | 真实 Serve 控制循环判定；不等于边缘进程当时已物理死亡 |
| C GCS 将 B、A 标记 dead，理由为 missed heartbeats；出现 actor reschedule/health-state 更新 | `23:41:41.775`、`23:41:42.036`，约 t=82.755/83.016 s | 真实 GCS 日志，发生在 C 隔离期间 |
| A proxy 此前仍记录成功请求，随后删除被判死 replica，request `...-0821` 报 ActorDiedError/HTTP 500，原因明确指向 missed-heartbeat node death | `23:42:35.525–527`，约 t=136.505–136.507 s | 恢复连通后的测量窗口内，数据路径实际受到原生控制状态影响 |
| A/B raylet 自报因 GCS 已将其判死而退出 | `23:43:00.167/.232`，约 t=161.147/161.212 s | **已超出 150 s 测量窗口**，不能用这两次退出直接解释窗口内的损失 |

原始日志：[C controller](../runs/f1-ray-warm-drop/C/ray/tmp/session_2026-10-27_23-39-58_155614_1936834/logs/serve/controller_1937395.log)、[C GCS](../runs/f1-ray-warm-drop/C/ray/tmp/session_2026-10-27_23-39-58_155614_1936834/logs/gcs_server.out)、[A proxy 日志目录](../runs/f1-ray-warm-drop/A/ray/tmp/session_2026-10-27_23-39-58_155614_1936834/logs/serve)、[A raylet](../runs/f1-ray-warm-drop/A/ray/tmp/session_2026-10-27_23-39-58_155614_1936834/logs/raylet.out)、[B raylet](../runs/f1-ray-warm-drop/B/ray/tmp/session_2026-10-27_23-39-58_155614_1936834/logs/raylet.out)。客户端[结束回执](../runs/f1-ray-warm-drop/A/driver.json)与[清理回执](../evidence/f1-ray-warm-drop/cleanup.json)用于区分测量、恢复及行政清理；不要把清理 SIGTERM 当成注入的应用故障。

该轮隔离 cohort 仍有 569/570 timely，说明 Ray warm 数据路径不是“C 一断即全停”；恢复 cohort 为 207/316 timely，且上述 proxy ActorDiedError 确实发生在恢复段。这支持原生云端健康判断与恢复期状态一致性值得单独分析，**不是**仅凭恢复均值推断唯一原因，也不是人为 adapter 强制失败。原始[A 请求记录](../runs/f1-ray-warm-drop/A/requests.jsonl)及物理[A](../runs/f1-ray-warm-drop/A/http-worker-journals/requests.jsonl)/[B](../runs/f1-ray-warm-drop/B/http-worker-journals/requests.jsonl)工作日志可逐请求复核。

`f1-ray-cold-drop` 的完整 [B 观测](../runs/f1-ray-cold-drop/B/observations.jsonl)无实际 B worker ready/执行；这与 C 控制器负责 autoscaling/actor 调度的配置相符，但不能单凭缺失 B 判定故障发生在哪一步。已有 warm 服务维持、cold 新能力获取和恢复期行为必须分别报告。

## Tide：隔离期间的真实 B 创建与返回；不能归因为域泛化 C4

`f1-tide-cold-drop` 的 t0=`1793114812842885573` ns，B 初始无应用执行者。[B 原生 runtime 日志](../runs/f1-tide-cold-drop/B/native/B/runtime.jsonl)在 `trace_unix_ns=1793114859301798974`（约 t=46.459 s）记录来自 A 的 `/stubmgr/create_stub`，继而分配 2 CPU、创建真实 executor。物理 B 的[第一个](../runs/f1-tide-cold-drop/B/native-journals/110753ea-fd40-484f-a148-36d5dada7494.jsonl)与[第二个](../runs/f1-tide-cold-drop/B/native-journals/9a855295-57c2-4b04-8c0c-f192155c56a7.jsonl)executor 在 t=46.561/46.562 s 记录 transport-ready；同一 stub 的多个 prefork executor 不等于多个并行应用预算。首个 B timely return 为 t=47.619 s，依据[客户端记录](../runs/f1-tide-cold-drop/A/requests.jsonl)与上述物理工作日志的 ID/instance join。这些事件均处于[实际 C 隔离时间](../runs/f1-tide-cold-drop/C/fault.json)内，并有 [A↔B 可达探测](../runs/f1-tide-cold-drop/A/peer-probes.jsonl)辅助验证。

这提供了“该轮从 A 到 B 的原生创建、执行和正确及时返回路径不需当时可用的 C 通信”的观测证据。不能只使用 worker 的 `node=A` 标签来否认 B 执行：原生转发保留该标签，实际归属依据物理采集位置、进程/namespace 与 journal join。

实现关系可由 [原生入口](../../cmd/revision-domain-native/main_linux.go)、[Scheduler.requestNewStub](../../pkg/stub/sched/scheduler.go)、[C4 实现](../../pkg/c4/check.go)及[冻结构建来源](../protocol/artifact-provenance.json)核对。此实验的 C4 检查不可变 originating `LocalNodeID` 的 CPU 总能力；A 的 2 CPU 与请求的 2 CPU 相容，在 warm/cold 都可通过。它不是 A/B 域通用的能力联合检查，不保证 B 自动创建、路由成功或 deadline，也没有通过消融证明它造成了此处扩容成功。不能把观察到的 native 调度/创建路径归因成新域公式已实现。

## 可复用的结论尺度

这些单轮证据支持区分三件事：**预部署数据路径继续工作、故障中形成新的执行能力、控制连通恢复后的状态处理**。可用配置加直接日志解释某些观测链条；不能由此推出系统的普遍优劣、长期离线保证或单一组件的因果份额。`R_cap`、实际 C4 与所有 offered 请求上的 `R_srv` 应保持分离，见[公式与实现评估](formula-assessment.md)。异构 A/B、CPU-only 内核、采集时段和 KE 额外只读观测等限制仍见[方法说明](v3-methodology-note.md)。最终三重复结果必须等待全部 36 单元资格与完整性审计通过。
