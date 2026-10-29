#!/usr/bin/env python3
"""Render the audited single-host four-system addendum as Chinese LaTeX."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil


def rows(path):
    with Path(path).open(newline="") as stream:
        return list(csv.DictReader(stream))


def values(data, metric, **keys):
    matches = [row for row in data if row["metric"] == metric
               and all(str(row[key]) == str(value) for key, value in keys.items())]
    if len(matches) != 1 or int(matches[0]["n"]) != 3:
        raise ValueError(f"missing three-run cell: {metric} {keys}")
    return matches[0]


def fmt(row, scale=1):
    return f"{float(row['mean']) * scale:.2f}"


def table_homogeneous(data, kind):
    output = [r"\begin{center}\small\begin{tabular}{r r r r r}\toprule",
              r"输入速率 & Tide & Bare & Ray Serve & KubeEdge\\\midrule"]
    for rate in (5, 10, 20, 40):
        cells = [fmt(values(data, "on_time_goodput", system=system, workload=kind, rps=rate))
                 for system in ("tide", "bare", "ray", "kubeedge")]
        output.append(f"{rate} & " + " & ".join(cells) + r"\\")
    output.append(r"\bottomrule\end{tabular}\end{center}")
    return "\n".join(output)


def table_mixed(data, metric, scale=1):
    output = [r"\begin{center}\small\begin{tabular}{r r r r r r}\toprule",
              r"输入速率 & 时限(ms) & Tide & Bare & Ray Serve & KubeEdge\\\midrule"]
    for rate in (20, 40, 80):
        for qos in (100, 500):
            cells = [fmt(values(data, metric, system=system, rps=rate, qos_ms=qos), scale)
                     for system in ("tide", "bare", "ray", "kubeedge")]
            output.append(f"{rate} & {qos} & " + " & ".join(cells) + r"\\")
    output.append(r"\bottomrule\end{tabular}\end{center}")
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--homogeneous", type=Path, required=True)
    parser.add_argument("--mixed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    homog_audit = json.loads((args.homogeneous / "audit.json").read_text())
    mixed_audit = json.loads((args.mixed / "mixed_audit.json").read_text())
    if homog_audit["status"] != "passed" or homog_audit["runs"] != 144 or homog_audit["raw_rows"] != 21600:
        raise ValueError("homogeneous audit is not complete")
    if mixed_audit["status"] != "passed" or mixed_audit["runs"] != 36 or mixed_audit["raw_rows"] != 10080:
        raise ValueError("mixed audit is not complete")
    if args.out.resolve() != args.homogeneous.resolve():
        raise ValueError("write the report next to its audited homogeneous figure")
    homog, mixed = rows(args.homogeneous / "per_cell.csv"), rows(args.mixed / "mixed_per_cell.csv")
    if not (args.out / "fourway_goodput_yield.pdf").is_file():
        raise ValueError("four-system plot is missing")
    mixed_figure = args.mixed / "mixed_class_goodput.pdf"
    if not mixed_figure.is_file():
        raise ValueError("mixed-QoS plot is missing")
    shutil.copy2(mixed_figure, args.out / mixed_figure.name)
    for name in ("mixed_per_cell.csv", "mixed_per_run.csv", "mixed_audit.json"):
        shutil.copy2(args.mixed / name, args.out / name)
    body = r"""\documentclass[10pt,a4paper,UTF8,fontset=fandol]{ctexart}
\usepackage[margin=0.72in]{geometry}
\usepackage{booktabs,graphicx,hyperref}
\hypersetup{colorlinks=true,urlcolor=blue}
\setlength{\parindent}{0pt}\setlength{\parskip}{5pt}
\setlength{\emergencystretch}{3em}
\begin{document}
\begin{center}{\LARGE 四系统补充对比（中文版）}\par
{\large Tide、Bare、Ray Serve 与 KubeEdge EdgeCore 的单机实测}\par
{2026 年 9 月 28 日}\end{center}

\section*{实验边界和核验}
这是新的单物理机补充实验，不替代原论文中的多设备、跨域、故障恢复、Ray Task/Actor 或 HPA 扩缩容实验。Tide 走正常的 SubmitRoutine/PAIX 路径；Bare 为直接 Go HTTP worker；Ray 为一个串行 Ray Serve 2.58.0 replica，持有同一 Go worker，并增加一次本地 HTTP 转发；KubeEdge 为 Kubernetes 1.32.2 的隔离 kind 集群中的 KubeEdge 1.23.1 EdgeCore 管理的单个 Pod。KubeEdge 的 edge 节点在同一台物理机的 kind 容器内，不代表真实跨机器链路。

四系统运行同一校准 Go 内核与输入速率。worker 使用两个逻辑 CPU 的预算，但 Ray 控制进程、CloudCore 和 EdgeCore 的资源范围与 Tide、Bare 不完全一致，因此这里只对比逐请求性能，不把进程 CPU/RSS 拼成公平的总体资源图。I/O 内核相同，Bare 与 Ray 的 Go worker 使用宿主机文件系统，Tide 与 KubeEdge 的容器使用可写 overlay；I/O 差异不能单独归因于调度策略。每格独立运行三次，Pod 或服务启动在观察窗口外；各系统实验在同一物理机上串行运行，未交错随机化系统顺序。

观察窗口为半开区间 $[t_0,t_1)$；常规负载 8 秒，500 毫秒时限，输入速率每秒 5/10/20/40 次。混合 QoS 负载为同一 CPU 内核的 6 秒运行，按请求 ID 交替设定 100/500 毫秒时限，输入速率每秒 20/40/80 次。按时有效吞吐只计入在窗口内完成且成功并满足该请求时限的结果；按时率的分母保留所有窗口内提交的请求，包括失败、超时和窗口后完成的请求。成功率也使用相同分母。成功请求的延迟统计包含排空阶段，故必须结合失败数和按时率阅读。

已逐格核验外层运行清单及其文件 SHA-256、原始请求条数与 ID、参数、真实内核配置、窗口边界、成功数以及 Ray replica/EdgeCore 节点身份。常规负载为 144 次运行、21,600 条请求；混合负载为 36 次运行、10,080 条请求。图中点是三次运行指标的算术均值，误差线是运行级双侧 95\% Student-$t$ 区间；没有把请求混合为一个伪重复样本。三次重复的区间可能很宽，端点可能超出指标的物理范围，绘图未暗中截断。

\section*{常规负载}
下表为按时有效吞吐（请求/秒）的三次运行均值。置信区间、成功率、按时率、平均延迟和 p95 延迟详见机器可读的 \texttt{per\_cell.csv}；全部运行记录见 \texttt{per\_run.csv} 与 \texttt{audit.json}。

\subsection*{CPU 内核}
__CPU_TABLE__
\subsection*{内存访问内核}
__MEMORY_TABLE__
\subsection*{I/O 内核}
__IO_TABLE__

\begin{figure}[!ht]\centering
\includegraphics[width=\linewidth,height=0.43\textheight,keepaspectratio]{fourway_goodput_yield.pdf}
\caption{同构负载的按时有效吞吐和按时率；每点三次运行，须结合置信区间和原始请求记录解释。KubeEdge 的 kind 网络、Ray 的本地 HTTP 转发和不同文件系统路径是保留的实现差异。}
\end{figure}
\clearpage
\section*{混合 100/500 毫秒 QoS}
每个系统每次运行都接收相同数量的两类请求；没有引入优先级队列。下表分别给出每类请求的按时有效吞吐（请求/秒）与按时率（\%）的三次运行均值。类内分母包含该类所有提交请求。运行级置信区间和成功率在 \nolinkurl{mixed_per_cell.csv}，原始核验在 \nolinkurl{mixed_audit.json}。

\subsection*{分类按时有效吞吐}
__MIXED_GOODPUT_TABLE__
\subsection*{分类按时率（\%）}
__MIXED_YIELD_TABLE__

__KEY_FINDING__

\begin{figure}[!ht]\centering
\includegraphics[width=\linewidth,height=0.35\textheight,keepaspectratio]{mixed_class_goodput.pdf}
\caption{按截止时限拆分的按时有效吞吐；误差线为三次运行的运行级 95\% Student-$t$ 区间。100 毫秒类和 500 毫秒类必须分别解释，不能把一个类别的收益写成全体请求的收益。}
\end{figure}

\end{document}
"""
    for key, replacement in {
        "__CPU_TABLE__": table_homogeneous(homog, "cpu"),
        "__MEMORY_TABLE__": table_homogeneous(homog, "memory"),
        "__IO_TABLE__": table_homogeneous(homog, "io"),
        "__MIXED_GOODPUT_TABLE__": table_mixed(mixed, "on_time_goodput"),
        "__MIXED_YIELD_TABLE__": table_mixed(mixed, "on_time_yield", 100),
        "__KEY_FINDING__": (
            "在每秒 80 次输入时，500 毫秒类的按时有效吞吐为 Tide "
            + fmt(values(mixed, "on_time_goodput", system="tide", rps=80, qos_ms=500))
            + "、Bare " + fmt(values(mixed, "on_time_goodput", system="bare", rps=80, qos_ms=500))
            + "、Ray Serve " + fmt(values(mixed, "on_time_goodput", system="ray", rps=80, qos_ms=500))
            + "、KubeEdge " + fmt(values(mixed, "on_time_goodput", system="kubeedge", rps=80, qos_ms=500))
            + " 请求/秒；100 毫秒类中，Tide 为 "
            + fmt(values(mixed, "on_time_goodput", system="tide", rps=80, qos_ms=100))
            + "，Bare 为 " + fmt(values(mixed, "on_time_goodput", system="bare", rps=80, qos_ms=100))
            + "。这一组观察显示宽时限类受益与紧时限类受损同时存在，不能称为全体请求的普遍提升。"
            + "在常规 CPU 每秒 40 次输入时，Tide 的按时有效吞吐为 "
            + fmt(values(homog, "on_time_goodput", system="tide", workload="cpu", rps=40))
            + " 请求/秒，Bare 和 KubeEdge 均为 40.00 请求/秒，也不支持 Tide 在全部负载上领先的结论。"
        ),
    }.items():
        body = body.replace(key, replacement)
    out = args.out / "fourway_supplement_zh.tex"
    out.write_text(body)
    print(out)


if __name__ == "__main__":
    main()
