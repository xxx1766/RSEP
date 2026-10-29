# Tide 集群构建与请求调度

本文整理两个问题：
1. Tide 集群是怎么构建的？机器之间通过什么通信？
2. 用户请求到来后，由谁决定在哪台机器上执行？

所有结论都带 `file:line` 索引，方便直接跳到源码核对。

---

## 一、集群构建与节点间通信

### 1. 节点角色与启动拓扑

三种角色在 `pkg/clsstat/node_role.go` 里定义：`things` / `edge` / `cloud`。集群**没有 master** —— 每个节点都是对等的 gRPC 服务端 + 客户端，差别只体现在**注册的 handler 集合**上：

- `cmd/tide/app/serve/tide.go:173-178` 按角色分叉：
  - `things` / `edge` → 注册 `stubmgrhdlr + clsstathdlr`
  - `cloud` → 只注册 `cloudhdlr`
- `serve/tide.go:199-201` 只有 `edge` 调 `procM.StartHeartbeat()`，周期性广播资源占用。

拓扑是**静态自举 + 递推发现**：启动时用 `--addresses 198.51.100.11:10001,...` 给出一组种子地址，剩下靠事件扩散。

### 2. 传输层：gRPC（只有一条 RPC）

所有跨节点流量走同一个 service、同一个方法。

- Service 定义：`proto/eventpb/event_grpc.pb.go` 的 `EventService`，只暴露
  `SendEvent(EventList) returns (EventResult)`。
- 服务端：`pkg/server/event_server.go:24-33` —— 收到 `EventList`，起 goroutine
  遍历事件交给本地 `EventDispatcher`（**按 List 内顺序串行**，保因果序）。
- 客户端：`pkg/cmnct/event_communicator.go`
  - `addr2cliCh` 按地址缓存 gRPC client channel 池（`getClient` / `retClient`，行 165-199）
  - `insecure` 凭证 + 1s 超时（`SendToAddress`，行 141）
  - 本机目标走进程内 dispatcher 短路（行 121-127）

换言之：**节点间只发 `eventpb.Event` / `EventList` 一种消息**，`Type` 字段决定语义，
`Content` 是嵌套 protobuf。事件类型常量集中在 `pkg/interfaces/event_names.go`。

### 3. 集群构建流程（加入握手）

`serve/tide.go:241-264` 的 `broadcastJoin`：

1. 新节点构造 `NodeNewEvent{NodeId, Address, Role, TotalResource, IsReply=false}`，
   对 `--addresses` 里每个种子 `SendToAddress` 一遍。
2. 种子节点的 `clsstathdlr.NodeNewHdlr`（`pkg/server/handlers/clsstathdlr/clsstat.go:27-76`）收到后：
   - `cs.AddNode(...)` 写进本地 `ClusterState`；
   - 若 `IsReply=false`，**回发一条 `IsReply=true` 的 `NodeNewEvent`**，把自身信息告诉对方。
3. **不是 gossip**：C 只向 `--addresses` 里的节点握手。想让 B 发现 C，
   B 的启动参数里或 C 的种子列表里必须包含 B。

集群视图的权威存储是 `clsstat.Singleton()`（单例 `ClusterState`），每个节点独立维护，靠事件最终一致。

### 4. 集群里"持续说话"的事件

装配完成后，节点间往来主要是下表几类（全部复用 `EventService.SendEvent`）：

| 事件 | 发起方 | 用途 |
|---|---|---|
| `NodeNewEvt`  | 启动时 | 加入集群 / 回握 |
| `NodeSyncEvt` | edge 心跳 | 广播 remaining CPU/GPU，others 更新 `clsstat` |
| `StubNewEvt` / `StubSyncEvt` | things/edge | 新 stub 上线 / stub 预估等待时间同步 |
| `AppNewEvt`   | things | 新提交的 app 向集群广播 |
| `RoutineSubmitEvt` / `RoutineDoneEvt` 等 | things ↔ 持 executor 的 stub | 任务下发与回传 |

广播粒度由 `cmnct` 提供几档（`event_communicator.go:74-163`）：
`BroadcastToAllNode`、`BroadcastToEdgThg`（跳过 cloud）、`SendToNode(nid)`、
`SendToStub(sid)`（先查 `clsstat` 得到 stub 的 nodeId，再走 `SendToNode`）。

### 5. 一句话总结

> Tide 的"集群"是一组对等的 gRPC `EventService` 节点，靠 `broadcastJoin` 的
> `NodeNewEvent` 握手组装视图；之后所有协同（资源心跳、stub/app 注册、routine
> 调度回传）都走同一条 `SendEvent(EventList)` RPC，由各节点本地的
> `EventDispatcher` 按 `Event.Type` 分派到 handler。角色差异只体现在"注册了哪些
> handler"上。

---

## 二、请求到来后由谁决定在哪台机器执行

结论先放最前：**去中心化 + 两阶段**。没有 master 统一分配；决定者是
**用户连上的那个 things 节点上、这个 app 的 entry stub 里的 `Scheduler`**。

### 0. 用户请求先落到 entry stub

`sudo bin/tide submit ... --server 127.0.0.1:10001` 这一步
（`cmd/tide/app/submit/submit.go:52-85`）只做一件事：把 `StubCreateEvent` 发给
`--server` 指定的 things 节点。该 things 节点的 `stubCreateHdlr`
（`pkg/server/handlers/stubmgrhdlr/stubmgr.go:25-117`）：

1. 本地 `CreateStub` 建一个 `Mode=ENTRY` 的 stub；
2. 向**全部节点**广播 `AppNewEvent`（cloud 收到后 `cloudhdlr.appNewHdlr` 会
   `buildApp` 建一个对应的 cloud stub，`cloudhdlr/cloud.go:28-62`）；
3. 向 edge/things 广播 `StubNewEvent`，让所有节点 `clsstat` 里都有该 stub 的影子记录。

这个 entry stub 就是该 app 的"调度入口"——后续这个 app 的每个 routine，
都进到这个 stub 的 `Scheduler` 里决定去哪跑。

### 1. 调度器的 3 档决策（核心）

`pkg/stub/sched/scheduler.go:61-144` 的 `scheduleOne` 是 Tide 的"大脑"：

#### ① 先看死线 → 赶不上就直接上云

```go
endBefore := rtn.SubmitTime + int64(dev.GetToleranceTime())
if endBefore < time.Now().UnixNano() {
    if toCloud, cloudId = skdr.trySubmitToCloud(...); toCloud { goto ret }
}
```

`dev.GetToleranceTime()` 即 RSEP 论文的 SLO（硬编码 300ms，见 `pkg/dev/est_time.go`）。
**已超时或算不完 → 直接发 `CldRtnSbmEvt(appId)` 给云节点**（`trySubmitToCloud`
行 146-191，当前固定取 `ListCloudNode()[0]`）。

#### ② 否则在**已有的 FN stub**里挑一个

```go
sortedStub = skdr.sortStubFn(rtn, appStat.Stubs)
for _, stStat := range sortedStub {
    if stStat.Mode == interfaces.StubEntry { continue }
    execTime := uint64(dev.GetEstExecTime(stStat.NodeId))
    if !clsstat.Singleton().TryUpdateEstWaitTime(stStat.Id, execTime, uint64(endBefore)) {
        continue
    }
    if err := skdr.submitToStub(rtn, stStat); err != nil { continue }
    goto ret
}
```

- 候选来自 `appStat.Stubs` —— **全集群**为该 app 建过的 FN stub 视图
  （靠 `StubNewEvt` / `StubSyncEvt` 同步），所以这里就是跨机决策。
- `defaultSortStub`（`sched/default.go:18-38`）按 `CreateTime` 排序，过滤掉
  `Mode==ENTRY` 的和 `EstEndTime + execTime > endBefore` 的。
- `TryUpdateEstWaitTime` 是**乐观并发控制**：多个 entry 同时决策时，CAS 原子地
  把排队时间加到某个 stub 上，失败就跳过——这保证了无中心协调也不会超卖。
- 选中后 `submitToStub`（行 193-233）向 **该 stub 所在的 nodeId** 发
  `RtnSbmEvt(stStat.Id)`。事件名带 stubId，目标节点 `stubhdlr` 认领。

#### ③ 没有合适的 stub → **挑一台 edge 扩容新建 stub**

```go
sortedNode := skdr.sortNodeFn(rtn, clsStat.ListEdgeNode())
for _, ndStat := range sortedNode {
    if err = skdr.requestNewStub(rtn, ndStat); err == nil { break }
}
```

- 候选集是 `ListEdgeNode()` —— things 和 cloud 都不会被选来横向扩容。
- `defaultSortNode` 实际走 `cpuBalanceSortNode`（`sched/default.go:54-79`）：
  按 **CPU 剩余率 `ResRemain/ResTotal` 降序**，即"最闲的 edge 优先"。剩余资源来自
  edge `procM.StartHeartbeat()` 周期广播的 `NodeSyncEvt`。
- 命中的 edge 收到 `StubCreateEvt`，本地 `stubmgrhdlr` 开新 stub 并广播
  `StubNewEvent` 回来。
- 外层有 `atomic.CompareAndSwapInt32(&skdr.creating, 0, 1)` 闸门 —— 一次只允许一个
  扩容请求在飞，1s 超时或新 stub 上线就复位。
- 扩容期间，这条 routine 本身**兜底走云**（行 140 再调一次 `trySubmitToCloud`）：
  "新 stub 还没建好，请求先别卡住"。

### 2. 整体决策流向

```
用户 submit ──► things 节点 entry stub ──► Scheduler (things 上执行)
                                           │
                   ┌───────────────────────┼───────────────────────┐
                   ▼ ①超时                 ▼ ②有合适 FN stub       ▼ ③都不行
              CldRtnSbmEvt          RtnSbmEvt(stubId)          StubCreateEvt
                  │                       │                         │
                  ▼                       ▼                         ▼
               cloud 节点             stub 所在 node           某 edge 新建 stub
            (ListCloudNode()[0])    (可能是 things/edge)     (剩余 CPU 率最高者)
```

### 3. 做消融 / 读代码要注意的点

1. **调度位置 = 入口 things**。决策完全在接到 submit 的那个 things 上，基于它本地
   `clsstat` 缓存的集群视图；决策质量取决于 `NodeSyncEvt` / `StubSyncEvt` 的新鲜度。
2. **入口节点不跑 routine**。`defaultSortStub` 跳过 `StubEntry`（`default.go:32`），
   `sortNodeFn` 只拉 `ListEdgeNode()`（scheduler.go:126）——纯 things 节点永远是
   调度器、不是执行器。
3. **SLO 硬编码**。`GetToleranceTime=300ms`、`GetEstExecTime=250ms` 写死在
   `pkg/dev/est_time.go`，RSEP 论文提醒要按工作负载校准。
4. **云是兜底**。只要有 cloud 节点，超时或扩容失败都会丢过去；云侧有独立的 stub
   （`cloudhdlr.buildApp`），直接接收 routine 不用经 entry。
5. **排队时间靠 CAS 而非锁**。`TryUpdateEstWaitTime` 是无中心协调下防超卖的核心
   技巧；做 cgroup v2 消融对比时，这段逻辑不变，变化的只是 `execTime` 实测值。

---

## 关键文件索引

| 主题 | 文件 |
|---|---|
| 组合根（进程装配） | `cmd/tide/app/serve/tide.go` |
| 客户端提交 | `cmd/tide/app/submit/submit.go` |
| gRPC 客户端池 / 广播 | `pkg/cmnct/event_communicator.go` |
| gRPC 服务端 | `pkg/server/event_server.go` |
| 事件定义 | `proto/eventpb/event.pb.go`, `event_grpc.pb.go` |
| 集群视图 handler | `pkg/server/handlers/clsstathdlr/clsstat.go` |
| stub 生命周期 handler | `pkg/server/handlers/stubmgrhdlr/stubmgr.go` |
| cloud handler | `pkg/server/handlers/cloudhdlr/cloud.go` |
| 调度器 | `pkg/stub/sched/scheduler.go`, `pkg/stub/sched/default.go` |
| 节点角色 | `pkg/clsstat/node_role.go` |
| 消融开关 / SLO | `pkg/dev/rsep_switches.go`, `pkg/dev/est_time.go` |
| 事件名常量 | `pkg/interfaces/event_names.go` |
