# Tide vs. K8s 系：定位与对比话术

本文整理在论文答辩 / 项目讲解时，如何回应 "Tide 相比 K8s 的优势是什么" 这类
问题。核心立场是 **Tide 不是另一个 K8s，而是填补 K8s 放弃的时间尺度与场景**。
所有论点都带源码索引，便于被追问时定位。

---

## 一句话核心立场（先抛出去）

> K8s 的调度单位是 **Pod**（秒级创建、长期运行的服务）；Tide 的调度单位是
> **routine**（毫秒级的单次请求）。Tide 解决的是 "当工作负载已经跑起来了、
> 现在要在 300ms SLO 下决定**这个请求**去哪个核上执行" 的问题。

---

## 四个可以展开的角度

### ① 调度粒度：Pod vs Routine（最关键的差异）

- K8s Scheduler 给 Pod 选 Node —— Pod 启动本身就是秒级（拉镜像、起容器、
  健康检查）。这个路径**不可能满足 300ms 级 SLO**。
- Tide 的容器是**预热的 executor pool**，routine 进来后通过 POSIX mqueue
  直接投递给已经跑在容器里的进程
  （`pkg/stub/execpool`, `pkg/executor/instance/ipc`）。调度决策 + 派发全在
  进程内完成，没有容器冷启。
- 类比：K8s 是"给新员工分配工位"，Tide 是"把工单塞进已经坐好的员工的 inbox"。

**这一点决定了 K8s 根本不在 Tide 的 problem space 里**。想用 K8s 做同样的事，
得堆 Knative / KEDA + pre-warmed pods + service mesh，复杂度不在一个量级。

### ② SLO-first 调度 vs 资源/亲和性调度

- K8s 默认 scheduler 的过滤 + 打分围绕 **资源 request/limit、taint/toleration、
  affinity**——**没有 deadline 概念**。
- Tide 的 `scheduleOne`（`pkg/stub/sched/scheduler.go:61-144`）第一行就算
  `endBefore = SubmitTime + ToleranceTime`，并用它筛选 stub：
  - 任何候选 stub 的 `EstEndTime + execTime > endBefore` 直接淘汰
    （`pkg/stub/sched/default.go:32`）
  - 赶不上当场转云兜底（`trySubmitToCloud`，scheduler.go:146-191）
- **deadline 是一等公民**，不是 annotation、不是 PriorityClass 的近似。

### ③ 控制面：中心化 vs 去中心化事件总线

| 维度 | K8s | Tide |
|---|---|---|
| 控制面 | etcd + apiserver + scheduler + controller-manager | 无。每节点自持 `clsstat` 视图 |
| 状态同步 | watch/list + reconcile loop | 事件 pub/sub（`eventpb.Event` + `EventDispatcher`） |
| 并发控制 | etcd 乐观 + resourceVersion | `TryUpdateEstWaitTime` CAS（`pkg/clsstat`） |
| 依赖 | etcd quorum、apiserver HA | 两个二进制（`bin/tide` + cgroup helper） |
| 调度→执行延迟 | apiserver bind → kubelet → runtime | 进程内发 mq，跨机一次 gRPC |

论点：**things/edge 这种资源受限、网络不可靠**的场景里，跑 etcd quorum
本身就奢侈。Tide 的事件模型让 edge 掉线不影响其他节点继续调度，cloud 可选可无。

### ④ 三层角色是**建模进代码**的，不是配置出来的

- K8s 里 "边缘" 通过 nodeSelector / taint 硬凑；KubeEdge 等本质是把
  apiserver 往边缘推，仍然 assume 一个逻辑中心。
- Tide 把 things(入口) / edge(执行) / cloud(兜底) 写进代码路径
  （`pkg/clsstat/node_role.go` + `cmd/tide/app/serve/tide.go:173-178` 按角色
  分叉注册 handler）：
  - entry stub 不执行 routine（`defaultSortStub` 跳过 `StubEntry`，default.go:32）
  - 扩容只考虑 edge（`sortNodeFn` 只看 `ListEdgeNode()`，scheduler.go:126）
  - 超时兜底永远是 cloud（`trySubmitToCloud` 取 `ListCloudNode()[0]`）
- 这是**把 "request 应该流向哪" 的领域知识固化进调度器**，不是让运维用 YAML 凑。

---

## 被追问时的常见反驳与应答

**Q: "K8s + Knative + Istio 不也能做？"**
A: 能，代价是堆栈复杂度 + 冷启动。Tide 的 routine 级 SLO（~ms）在 Knative 的
cold start（~s）面前不是同一个问题。Knative 的 "scale to zero" 跟 Tide 的
"executor pool 常驻" 是反向设计。

**Q: "你这套不就是个 FaaS 么？"**
A: 更准确说是 **hierarchical FaaS with hard latency guarantees**。和
OpenFaaS / Fission 的差别在 things/edge/cloud 的分层调度 + deadline-driven
dispatch。一般 FaaS 框架跑在单一集群里。

**Q: "去中心化会不会产生决策冲突？"**
A: 会的概率存在，靠 `TryUpdateEstWaitTime` CAS（`pkg/clsstat`）抢占式更新
预估等待时间解决——失败就换下一个 stub。经典乐观并发，比 quorum 便宜，
粒度到 stub 而非对象，比 K8s 的 resourceVersion 更精细。

**Q: "那高可用怎么保证？"**
A: Tide 的高可用建立在 "**cloud 永远是兜底**" 这个前提上——只要 cloud 可达，
things / edge 都可以失败。边缘节点间无状态耦合，掉线只影响 `clsstat` 里那一行。
不追求 5 个 9 的中心控制面可用性，换来的是 edge 独立运行能力。

---

## 要**诚实承认**的 tradeoff（主动提出来显得你懂边界）

1. **没有多租户、没有 RBAC、没有 namespace 隔离** —— Tide 是研究原型，不是
   生产 PaaS。
2. **没有滚动升级、自愈、声明式运维**，K8s 在 day-2 operations 上碾压。
3. **广播式状态同步**扩展性有限（`BroadcastToAllNode` 全量扇出），集群到
   几十个节点可能撑不住；K8s watch 机制是为上万节点设计的。
4. **SLO 参数硬编码**（`dev.GetToleranceTime()=300ms`、`GetEstExecTime()=250ms`
   在 `pkg/dev/est_time.go`），产品化需要负载 profiling 自动校准。
5. **cgroup v1 硬耦合**，v2 兼容是当前升级工作（并行包 `pkg/containerxv2/`）。

---

## 答辩 / 讲解用开场白（可直接念）

> Tide 解决的是 K8s 调度器**不去**解决的问题：request 级别、毫秒 SLO、
> things/edge/cloud 三层异构。K8s 的调度单位是 Pod，启动就是秒级，而我们的
> 负载是 300ms 内必须完成的单次推理请求。我们用去中心化的事件总线代替中心化
> 控制面，用预热容器池 + POSIX mqueue 替代容器冷启，用 deadline-first 的调度
> 策略替代资源打分。代价是放弃了 K8s 的多租户、声明式、自愈等生产能力——
> 这是合理的 tradeoff，因为 Tide 不是要取代 K8s，而是填补它放弃的时间尺度。

---

## 关键文件索引（被追问细节时查）

| 论点 | 文件 |
|---|---|
| 调度决策核心 | `pkg/stub/sched/scheduler.go` |
| 候选排序与筛选 | `pkg/stub/sched/default.go` |
| executor pool / 预热容器 | `pkg/stub/execpool/`, `pkg/executor/instance/` |
| POSIX mqueue IPC | `pkg/executor/instance/ipc/` |
| 事件总线（去中心化核心） | `pkg/event/`, `pkg/cmnct/event_communicator.go` |
| 节点角色建模 | `pkg/clsstat/node_role.go`, `cmd/tide/app/serve/tide.go:173-178` |
| SLO 参数 | `pkg/dev/est_time.go` |
| 乐观并发 CAS | `pkg/clsstat` 的 `TryUpdateEstWaitTime` |
