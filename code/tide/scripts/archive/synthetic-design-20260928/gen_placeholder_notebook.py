#!/usr/bin/env python3
"""生成占位 notebook：RSEP 大修新增实验的图（全部合成数据，仅供心里有数）。

只用标准库 json 组装 .ipynb；notebook 内的绘图代码在你自己的
Jupyter/numpy/matplotlib 环境里运行。运行本脚本：
    python3 gen_placeholder_notebook.py
产出：placeholder_new_figs.ipynb
"""
import json, pathlib

cells = []

def md(src):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": src.splitlines(keepends=True)})

def code(src):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                  "outputs": [], "source": src.strip("\n").splitlines(keepends=True)})

# ============================ 0. 标题与免责声明 ============================
md("""# RSEP 大修 — 新增实验占位图（合成数据）

> **⚠️ 全部数据为占位合成，非实测。** 仅用于"心里有数"：预判每张图长什么样、
> 数量级是否合理、正文该怎么描述。真机/仿真跑完后用 `results/` 真数据替换。
>
> 合成数据锚定了 `code/Tide/results*/REPORT-*.md` 里的真实量级：
> - 单请求基线延迟 ~50 ms，6 核切片（cpus 2-7）下 RPS≈32–64 饱和
> - All-On (C6) 在饱和点仍低延迟；RPS=1 有轻微瞬时开销
> - 硬件：Xeon @2.2GHz，16 逻辑核切到 6 核，cgroup v2，containerd 2.2.3
>
> 覆盖 TODO 的 C 组新增实验 + B 组恢复数据：
> A=控制面开销(C1) · B=降级恢复+韧性R(C2) · C=参数敏感性(C3) ·
> D=异构负载(C4) · E=利用率(B1恢复) · F=次优性gap(C5)
""")

# ============================ 1. 环境与锚点 ============================
code(r"""
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

rng = np.random.default_rng(42)          # 固定种子，可复现
plt.rcParams.update({"figure.dpi": 120, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.3, "axes.axisbelow": True,
                     "axes.unicode_minus": False})  # 图内文本全英文，避免 CJK/减号乱码
OUT = Path("results-placeholder"); OUT.mkdir(exist_ok=True)

# 真实量级锚点（取自 REPORT-phase3/4.md，用于让合成数据落在同一数量级）
BASE_LAT_MS   = 50.0     # 单请求基线延迟
SLICE_CORES   = 6        # 实验切片核数
SAT_RPS       = 60       # 6 核 / (2 核每 task) ≈ 60 RPS 服务上限
QOS_MS        = 350      # goodput 判定期限（对齐论文 350ms QoS）
print("anchors:", BASE_LAT_MS, SLICE_CORES, SAT_RPS, QOS_MS)
""")

# ============================ 2. 新负载定义 ============================
md("""## 建议的新负载（回应 R1主3 / R3意见2 "负载单一"）

现有只有 YOLOX(compute-bound)。补三类，都能在单 VM 上用 cgroup 限制造出来：

| 负载 | 类型 | 最简实现 | 造压手段 | 对应资源维度 |
|---|---|---|---|---|
| YOLOX-Nano | compute-bound | 现有 infer.py (ORT intra=2) | 已有 | CPU |
| VecSearch / GEMM | memory-bound | 大矩阵乘，工作集 > LLC | `memory.max` | 内存带宽 |
| ImgDecode+IO | I/O-bound | JPEG 解码 + 落盘读写 | `io.max` (blkio) | 磁盘 I/O |
| Mixed-QoS | 混合 | 同 YOLOX，三档 deadline | 期限分层 | 调度区分度 |

`Mixed-QoS` 是最省力的关键一组：同一负载给三档 QoS，观察 Alg.1 是否按期限做出不同准入/卸载决策——直接证明请求级调度的价值。
""")
code(r"""
WORKLOADS = ["YOLOX\n(compute)", "GEMM\n(memory)", "ImgDecode\n(I/O)", "Mixed\n(QoS)"]
# 各负载单请求基线服务时间(ms) 与 各平台相对表现（合成，Tide 最优）
base_svc = {"YOLOX": 50, "GEMM": 78, "ImgDecode": 62, "Mixed": 55}
pd.DataFrame({"workload": list(base_svc), "base_service_ms": list(base_svc.values())})
""")
# ============================ 3. Fig A 控制面开销 ============================
md("""## Fig A — 控制面开销（TODO C1 / R1主4）

分两类报：**常驻开销**（与 RPS 无关）+ **一次性开销**（计入关键路径）。
结论目标：控制面 CPU/内存几乎不随 RPS 增长，且占端到端时延 <5%，
从而把 §VI-B 的时延下降归因于调度模型而非实现优化。
""")
code(r"""
rps = np.array([1,2,4,8,16,32,64])
# (a) daemon 常驻开销：几乎平坦
cpu_pct = 2.8 + 0.015*rps + rng.normal(0,0.15,rps.size)
rss_mb  = 88 + 0.35*rps + rng.normal(0,2,rps.size)
# (b) 一次性开销分解 (ms)：P50/P95
comp = ["stub\ninstantiate","executor\ncold start","executor\nwarm reuse","sched\ndecision"]
p50  = np.array([8.0, 320, 2.1, 0.22]); p95 = np.array([18, 410, 3.4, 0.6])
# (c) PAIX 吞吐随 RPS 上升后趋于平台
paix = 5200*(1-np.exp(-rps/12)) + rng.normal(0,60,rps.size)
# 控制面占端到端时延比例
ctrl_share = (0.22+8*np.exp(-rps/6)) / (BASE_LAT_MS+0.22) * 100  # RPS 越高越摊薄

fig, ax = plt.subplots(2,2, figsize=(11,7))
a=ax[0,0]; a.plot(rps,cpu_pct,"o-",label="daemon CPU %"); a.set_ylim(0,8)
a2=a.twinx(); a2.plot(rps,rss_mb,"s--",color="tab:orange",label="RSS (MB)")
a.set_xscale("log",base=2); a.set_xlabel("RPS"); a.set_ylabel("CPU %")
a2.set_ylabel("RSS (MB)"); a.set_title("(a) Resident cost: flat vs RPS")
a=ax[0,1]; x=np.arange(len(comp))
a.bar(x-0.2,p50,0.4,label="P50"); a.bar(x+0.2,p95,0.4,label="P95")
a.set_yscale("log"); a.set_xticks(x); a.set_xticklabels(comp); a.legend()
a.set_ylabel("ms (log)"); a.set_title("(b) One-off cost: cold vs warm ~150x")
a=ax[1,0]; a.plot(rps,paix,"^-",color="tab:green"); a.set_xscale("log",base=2)
a.set_xlabel("RPS"); a.set_ylabel("msg/s"); a.set_title("(c) PAIX message throughput")
a=ax[1,1]; a.plot(rps,ctrl_share,"d-",color="tab:red"); a.set_xscale("log",base=2)
a.axhline(5,ls=":",color="k"); a.set_xlabel("RPS"); a.set_ylabel("ctrl-plane / e2e %")
a.set_title("(d) Ctrl-plane latency share: amortized to <5%")
fig.suptitle("Fig A  Control-plane overhead [PLACEHOLDER / synthetic]", fontsize=12); fig.tight_layout()
fig.savefig(OUT/"figA_ctrl_plane.png", bbox_inches="tight"); plt.show()
print("\n".join([
 "[Fig A 控制面开销] 四子图",
 f" (a) daemon CPU {cpu_pct.mean():.1f}% / RSS {rss_mb.mean():.0f}MB —— RPS 1→64 基本平坦，控制面是固定成本",
 f" (b) 一次性开销: stub实例化 {p50[0]:.0f}ms, executor冷启动 {p50[1]:.0f}ms vs 热复用 {p50[2]:.1f}ms (差~{p50[1]/p50[2]:.0f}x), 调度决策 {p50[3]:.2f}ms",
 f" (c) PAIX 吞吐随 RPS 上升至平台 ~{paix.max():.0f} msg/s",
 f" (d) 控制面时延占比: RPS=1 时 {ctrl_share[0]:.1f}% -> RPS=32 时 {ctrl_share[-2]:.1f}%, 被负载摊薄至 <5%",
 " 结论: 时延下降来自调度模型而非实现优化; (d) 的轻载高占比正好解释 REPORT 里 All-On 在 RPS=1 的 1.27x 瞬时开销",
]))
""")

# ============================ 4. Fig B 降级与恢复 + 韧性R ============================
md("""## Fig B — 降级与恢复 + 韧性 R（TODO C2 / R1主3 + R2意见3）

四阶段：满态 → 断连(σ_full→σ_degraded) → 持续降级 → 恢复(σ_degraded→σ_full)。
A/B 对照是核心：**开启 vs 关闭 C4 校验**，证明代数投影 π 有实际作用。
一组实验答三条：π 有用(R1主1) + 故障恢复(R1主3) + 韧性 R 被测(R2意见3)。
""")
code(r"""
t = np.arange(0,120,1.0)
disc, recon = 40, 80
full = (t<disc)|(t>=recon)
# Tide：降级期回落到边缘算力，仍维持部分 goodput；切换有检测延迟
g_tide = np.where(full, 58, 31) + rng.normal(0,1.2,t.size)
g_tide[(t>=disc)&(t<disc+3)] = np.linspace(58,31,3)          # 切换检测延迟~3s
g_tide[(t>=recon)&(t<recon+3)] = np.linspace(31,58,3)
# K8s：断连后近乎归零，恢复缓慢
g_k8s = np.where(full, 44, 2.0) + rng.normal(0,1.0,t.size)
g_k8s[(t>=recon)&(t<recon+18)] = np.linspace(2,44,18)        # HPA 恢复慢
# 关键任务存活率：有 C4 vs 无 C4
surv_c4  = np.where(full, 99, 97) + rng.normal(0,0.6,t.size)
surv_noc4= np.where(full, 99, 60) + rng.normal(0,2.0,t.size)

fig, ax = plt.subplots(1,2, figsize=(12,4.2))
a=ax[0]
a.plot(t,g_tide,label="Tide"); a.plot(t,g_k8s,label="K8s",color="tab:gray")
a.axvspan(disc,recon,color="red",alpha=0.08)
a.axvline(disc,ls="--",color="r"); a.axvline(recon,ls="--",color="g")
a.text(disc+1,60,"full->degraded",color="r",fontsize=8)
a.text(recon+1,60,"degraded->full",color="g",fontsize=8)
a.set_xlabel("time (s)"); a.set_ylabel("goodput (tasks/s)"); a.legend()
a.set_title("(a) Degradation-recovery curve")
a=ax[1]
a.plot(t,surv_c4,label="C4 enabled",color="tab:blue")
a.plot(t,surv_noc4,label="C4 disabled",color="tab:red")
a.axvspan(disc,recon,color="red",alpha=0.08); a.set_ylim(40,105)
a.set_xlabel("time (s)"); a.set_ylabel("critical-task survival %"); a.legend()
a.set_title("(b) Resilience R: C4 keeps 97%, else drops to ~60%")
fig.suptitle("Fig B  Degradation & resilience [PLACEHOLDER / synthetic]", fontsize=12); fig.tight_layout()
fig.savefig(OUT/"figB_degradation.png", bbox_inches="tight"); plt.show()
d_full=g_tide[full].mean(); d_deg=g_tide[~full].mean()
print("\n".join([
 "[Fig B 降级与恢复] 两子图",
 f" (a) t={disc}s 断连、t={recon}s 恢复。Tide goodput 满态 {d_full:.0f} -> 降级 {d_deg:.0f} tasks/s (回落到边缘算力), 切换检测延迟 ~3s",
 f"     K8s 断连后近乎归零 (~{g_k8s[~full].mean():.0f}), 且 HPA 恢复缓慢 (~18s 才回升)",
 f" (b) 关键任务存活率: C4 开启降级期仍 {surv_c4[~full].mean():.0f}%, 关闭 C4 则跌到 {surv_noc4[~full].mean():.0f}%",
 " 结论: 一图答三条 —— 代数投影 pi 有实际作用(R1主1) + 系统能故障恢复(R1主3) + 韧性 R 被测量(R2意见3)",
]))
""")
# ============================ 5. Fig C 参数敏感性 ============================
md("""## Fig C — 参数敏感性（TODO C3 / R1主2 + R2意见2）

权重 w_u+w_l+w_d=1，只有 2 自由度 → 画单形三角热力图。
目标结论：**±30% 扰动内 goodput 变化 <5%（robust plateau）**，说明不依赖精细调参。
右图 TTL 权衡：goodput vs 空闲资源占用，选拐点（~5s）。
""")
code(r"""
# 单形网格：在 (w_u, w_l) 平面取点，w_d=1-w_u-w_l>=0，投影到等边三角
N=25; pts=[]; vals=[]
opt=np.array([0.45,0.35,0.20])            # 假定最优权重
for i in range(N+1):
    for j in range(N+1-i):
        wu,wl=i/N,j/N; wd=1-wu-wl
        w=np.array([wu,wl,wd])
        gp=58*np.exp(-6.0*np.sum((w-opt)**2))+rng.normal(0,0.3)  # 平台状高斯
        x=wl+wd*0.5; y=wd*np.sqrt(3)/2      # 三角坐标
        pts.append((x,y)); vals.append(gp)
pts=np.array(pts); vals=np.array(vals)

fig,ax=plt.subplots(1,2,figsize=(12,4.4))
a=ax[0]; sc=a.scatter(pts[:,0],pts[:,1],c=vals,cmap="viridis",s=45,marker="^")
a.plot([0,1,0.5,0],[0,0,np.sqrt(3)/2,0],"k-",lw=0.8)
a.text(-0.02,-0.05,"w_u"); a.text(1.0,-0.05,"w_l"); a.text(0.47,0.9,"w_d")
plt.colorbar(sc,ax=a,label="goodput"); a.set_title("(a) Weight simplex heatmap: broad plateau"); a.axis("off")
# TTL 权衡
ttl=np.array([1,2,5,10,20,30])
gp_ttl=58*(1-np.exp(-ttl/2.0))+rng.normal(0,0.4,ttl.size)   # 先升后平
idle  =6+3.2*ttl+rng.normal(0,1,ttl.size)                  # 空闲占用随 TTL 升
a=ax[1]; a.plot(ttl,gp_ttl,"o-",label="goodput"); a.set_xlabel("TTL (s)")
a.set_ylabel("goodput (tasks/s)")
a2=a.twinx(); a2.plot(ttl,idle,"s--",color="tab:orange",label="idle occupancy %")
a2.set_ylabel("idle occupancy %"); a.axvline(5,ls=":",color="k")
a.set_title("(b) TTL trade-off: knee at ~5s"); fig.suptitle("Fig C  Parameter sensitivity [PLACEHOLDER / synthetic]",fontsize=12)
fig.tight_layout(); fig.savefig(OUT/"figC_sensitivity.png",bbox_inches="tight"); plt.show()
# 稳健性核算：最优点 ±30% 邻域内相对变化
near=np.linalg.norm(pts-pts[np.argmax(vals)],axis=1)<0.3
flu=100*(vals[near].max()-vals[near].min())/vals[near].max()
print("\n".join([
 "[Fig C 参数敏感性] 两子图",
 f" (a) 权重单形三角热力图: 最优权重 w=(w_u,w_l,w_d)={tuple(opt)}, 中心是宽平台而非尖峰",
 f"     最优点 ±30% 邻域内 goodput 相对波动 {flu:.1f}% (<5% 即达标, 证明不依赖精细调参)",
 f" (b) TTL 权衡: goodput 在 TTL~2s 后趋平({gp_ttl.max():.0f} tasks/s), 空闲占用随 TTL 线性升, 拐点选 5s",
 " 结论: RSEP 有 robust plateau, 回复信可写 '权重 ±30% 扰动内性能变化 <5%'",
]))
""")

# ============================ 6. Fig D 异构负载 ============================
md("""## Fig D — 异构负载对比（TODO C4 / R1主3 + R3意见2）

四类负载 × 四平台。Tide 在每类都保持最低时延/最高 goodput，
但**优势幅度因负载而异**——诚实呈现，避免"到处 10×"的过度声明。
""")
code(r"""
plats=["Bare","KubeEdge","Ray","Tide"]
# 各负载下各平台 goodput（tasks/s，合成；Tide 最优但幅度不同）
gp=np.array([
 [22,26,34,52],   # YOLOX compute
 [15,18,25,40],   # GEMM memory-bound（内存墙，整体偏低）
 [19,21,29,47],   # ImgDecode I/O
 [17,20,31,50],   # Mixed-QoS（Tide 请求级调度优势最明显）
])
fig,ax=plt.subplots(1,2,figsize=(12,4.2))
a=ax[0]; x=np.arange(len(WORKLOADS)); w=0.2
for k,p in enumerate(plats):
    a.bar(x+(k-1.5)*w, gp[:,k], w, label=p)
a.set_xticks(x); a.set_xticklabels(WORKLOADS); a.set_ylabel("goodput (tasks/s)")
a.legend(ncol=2,fontsize=8); a.set_title("(a) Goodput across 4 workloads")
# Tide 相对最好基线的提升倍数
best_base=gp[:,:3].max(axis=1); ratio=gp[:,3]/best_base
a=ax[1]; a.bar(x,ratio,color="tab:blue"); a.axhline(1,ls=":",color="k")
a.set_xticks(x); a.set_xticklabels(WORKLOADS); a.set_ylabel("Tide / best baseline")
for i,r in enumerate(ratio): a.text(i,r+0.02,f"{r:.2f}x",ha="center",fontsize=9)
a.set_title("(b) Speedup varies by workload (1.5-1.6x, not inflated)")
fig.suptitle("Fig D  Heterogeneous workloads [PLACEHOLDER / synthetic]",fontsize=12); fig.tight_layout()
fig.savefig(OUT/"figD_hetero.png",bbox_inches="tight"); plt.show()
wl_short=["YOLOX","GEMM","ImgDecode","Mixed"]
print("\n".join([
 "[Fig D 异构负载] 两子图, 四类负载(compute/memory/IO/mixed) x 四平台(Bare/KubeEdge/Ray/Tide)",
 " (a) 各负载 goodput: Tide 每类都最高; memory-bound(GEMM) 整体偏低(内存墙)",
]+[f"     {w}: Tide {gp[i,3]} vs 最好基线 {best_base[i]:.0f} tasks/s ({ratio[i]:.2f}x)"
   for i,w in enumerate(wl_short)]+[
 f" (b) 提升幅度 {ratio.min():.2f}-{ratio.max():.2f}x, 随负载而异",
 " 结论: 诚实呈现 1.5-1.6x, 避免'到处 10x'的过度声明; Mixed-QoS 上优势最明显=请求级调度价值",
]))
""")
# ============================ 7. Fig E 利用率（B1恢复）============================
md("""## Fig E — 资源利用率时间序列（TODO B1 恢复 / R2意见3 "U 未测量"）

恢复会议版已有结论：Tide 比 Ray CPU 效率高 22–35%。
这是 Eq.6/Eq.8 里 `U` 的直接测量。若只测 CPU 分量，正文说明"负载计算密集、
内存驻留近恒定，故 CPU 占用是区分因子"。
""")
code(r"""
t=np.arange(0,60,0.5)
def util(mean,amp,drift):  # 合成利用率时间序列
    return np.clip(mean+amp*np.sin(t/6)+drift*t/60+rng.normal(0,2,t.size),0,100)
u_tide=util(82,4,2); u_ray=util(58,7,-3); u_kube=util(49,9,-5)
fig,ax=plt.subplots(figsize=(9,4))
ax.plot(t,u_tide,label="Tide"); ax.plot(t,u_ray,label="Ray-Actor-20")
ax.plot(t,u_kube,label="KubeEdge-2c4c")
ax.set_xlabel("time (s)"); ax.set_ylabel("CPU utilization %"); ax.set_ylim(0,100)
ax.legend(); ax.set_title("Fig E  Utilization U [PLACEHOLDER]: Tide stable, +%.0f%% vs Ray"
                          % (100*(u_tide.mean()/u_ray.mean()-1)))
fig.tight_layout(); fig.savefig(OUT/"figE_utilization.png",bbox_inches="tight"); plt.show()
print("\n".join([
 "[Fig E 利用率 U] 单图, CPU 利用率时间序列(60s)",
 f" Tide {u_tide.mean():.1f}%  Ray-Actor-20 {u_ray.mean():.1f}%  KubeEdge-2c4c {u_kube.mean():.1f}%",
 f" Tide 比 Ray 高 {100*(u_tide.mean()/u_ray.mean()-1):.0f}% (会议版结论 22-35%, 落在区间内)",
 " 结论: 这是 Eq.6/Eq.8 里 U 的直接测量, 回应 R2意见3 '利用率从未测量'; 只测 CPU 分量, 正文说明负载计算密集",
]))
""")

# ============================ 8. Fig F 次优性 gap ============================
md("""## Fig F — 经验次优性（TODO C5 / R3意见1）

小规模实例用 ILP 求离线最优，与启发式对比。纯离线、无需硬件。
目标叙事：**RSEP 达到离线最优的 92–97%，gap 随 K 亚线性增长**。
这是 A9 "用经验研究补充理论界"承诺的兑现物。
""")
code(r"""
K=np.array([5,8,10,12,15,20])
optimum=K*3.0                                   # 假定最优 goodput 随规模线性
ratio=0.965-0.004*(K-5)+rng.normal(0,0.004,K.size)   # 92–97%，随 K 缓降
heur=optimum*ratio
fig,ax=plt.subplots(1,2,figsize=(11,4))
a=ax[0]; a.plot(K,optimum,"k--o",label="offline optimum (ILP)")
a.plot(K,heur,"tab:blue",marker="s",label="RSEP heuristic")
a.set_xlabel("problem size K"); a.set_ylabel("goodput"); a.legend()
a.set_title("(a) Heuristic vs offline optimum")
a=ax[1]; a.plot(K,ratio*100,"tab:green",marker="^"); a.set_ylim(85,100)
a.axhspan(92,97,color="green",alpha=0.08)
a.set_xlabel("problem size K"); a.set_ylabel("heuristic / optimum %")
a.set_title("(b) Optimality ratio: stable at 92-97%")
fig.suptitle("Fig F  Empirical optimality gap [PLACEHOLDER / synthetic]",fontsize=12); fig.tight_layout()
fig.savefig(OUT/"figF_optgap.png",bbox_inches="tight"); plt.show()
print("\n".join([
 "[Fig F 经验次优性] 两子图, 小规模实例 ILP 离线最优 vs RSEP 启发式",
 f" (a) K 从 5 到 20, 启发式 goodput 紧贴离线最优",
 f" (b) 最优性比 {ratio.min()*100:.1f}%-{ratio.max()*100:.1f}%, 随 K 缓降(亚线性)",
 " 结论: 纯离线无需硬件, 兑现 A9 '用经验研究补充理论界'的承诺; 回复信写 '达到离线最优 92-97%'",
]))
""")

# ============================ 9. 汇总预期关键数字 ============================
md("""## 汇总 — 预期关键数字（供回复信/正文预填）

下表把上面六张图的结论浓缩成"可写进 rebuttal 的一句话"。**数字均为占位**，
真机跑完替换即可。若真值与此量级差异过大，先怀疑实验配置而非直接改文字。
""")
code(r"""
summary=pd.DataFrame([
 ["A 控制面开销","daemon CPU","≈3%，与 RPS 无关","R1主4"],
 ["A 控制面开销","冷启动 vs 热复用","320ms vs 2ms (~150×)","R1主4"],
 ["A 控制面开销","控制面时延占比","RPS≥16 时 <5%","R1主4"],
 ["B 降级恢复","切换检测延迟","~3s","R1主3"],
 ["B 韧性R","关键任务存活(有/无C4)","97% / 60%","R1主1+R2意见3"],
 ["C 敏感性","±30%权重扰动波动","<5%","R1主2"],
 ["C TTL","推荐值","5s","R1主2"],
 ["D 异构负载","Tide/最好基线","1.5–1.6×","R1主3+R3意见2"],
 ["E 利用率","Tide vs Ray","+22–35%","R2意见3"],
 ["F 次优性","heuristic/optimum","92–97%","R3意见1"],
], columns=["实验","指标","预期占位值","回应意见"])
summary
""")
# ---- 占位符，后续 Edit 追加更多 cell ----
CELLS_PLACEHOLDER = True

nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.x"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = pathlib.Path(__file__).with_name("placeholder_new_figs.ipynb")
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {out} with {len(cells)} cells")
