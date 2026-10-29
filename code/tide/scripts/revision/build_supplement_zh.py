#!/usr/bin/env python3
"""Translate the verified supplement tables without recomputing experiment results.

Run from Tide: python3 scripts/revision/build_supplement_zh.py
The Chinese document is maintained in revision_experiments_zh.tex and built with
tectonic from the result directory. This script only generates tables_zh/.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "results-revision-20260928"
SOURCE = ROOT / "tables"
OUTPUT = ROOT / "tables_zh"

HEADERS = {
    "Padding (B)": "填充量 (B)",
    "Mean request wire (B)": "请求线长均值 (B)",
    "Mean RTT (us)": "RTT 均值 (微秒)",
    "RTT p95 (us)": "RTT p95 (微秒)",
    "Round trips/s": "往返次数/秒",
    "Candidates": "候选数",
    "Repetitions": "重复次数",
    r"Hook cost (us/op), mean [95\% CI]": r"钩子耗时 (微秒/次)，均值 [95\% CI]",
    "Local eligible": "可使用本地节点",
    "Phase": "阶段",
    "Goodput (req/s)": "有效吞吐 (次/秒)",
    r"Yield (\%)": r"按时率 (\%)",
    "Failures": "失败数",
    "TTL (ms)": "TTL (毫秒)",
    "Created stubs": "创建的 stub 数",
    "Idle reservation (CPU s)": "空闲逻辑预留 (CPU 秒)",
    "Later-burst first latency (ms)": "后续突发首响应 (毫秒)",
    "Offered req/s": "输入速率 (次/秒)",
    "Network profile": "网络设定",
    r"Smallest grid mean yield (\%)": r"网格最小平均接纳率 (\%)",
    r"Largest grid mean yield (\%)": r"网格最大平均接纳率 (\%)",
    "Total req/s": "总输入速率 (次/秒)",
    "QoS (ms)": "QoS (毫秒)",
    "System": "系统",
    "Class goodput (req/s)": "类别有效吞吐 (次/秒)",
    r"On-time yield (\%)": r"按时率 (\%)",
    r"Success yield (\%)": r"成功率 (\%)",
    "Workload": "工作负载",
    "Req/s": "输入速率 (次/秒)",
    "Success p95 (ms)": "成功请求 p95 (毫秒)",
    r"Worker CPU (\% of 2 CPUs)": r"工作进程 CPU (2 核的 \%)",
    r"Controller CPU (\% of 1 CPU)": r"控制进程 CPU (1 核的 \%)",
    "Controller RSS (MiB)": "控制进程 RSS (MiB)",
    "Worker-memory scope": "工作端内存口径",
    "Scope-specific memory (MiB)": "对应口径内存 (MiB)",
    "Tasks": "任务数",
    "Nodes": "节点数",
    "Instances": "实例数",
    "Mean ratio": "平均比值",
    "Minimum ratio": "最小比值",
    "Mean gap": "平均数量差",
    "Maximum gap": "最大数量差",
    "Exact matches": "达到最优的实例数",
}

VALUES = {
    "On": "启用", "Off": "禁用",
    "Connected": "连通", "Disconnected": "断连", "Recovered": "恢复",
    "low": "低", "high": "高",
    "cpu": "CPU", "io": "I/O", "memory": "内存访问", "mixed-qos": "混合 QoS",
    "cgroup memory.current": r"cgroup memory.current",
    "worker process RSS": "工作进程 RSS",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def translate_line(line: str) -> str:
    if " & " not in line or not line.rstrip().endswith(r"\\") and r"\\\midrule" not in line:
        return line
    # Splitting at column separators prevents replacements inside measured values.
    fields = line.split(" & ")
    for i, field in enumerate(fields):
        suffix = ""
        for ending in (r"\\\midrule", r"\\"):
            if field.endswith(ending):
                field, suffix = field[:-len(ending)], ending
                break
        fields[i] = HEADERS.get(field.strip(), VALUES.get(field.strip(), field.strip())) + suffix
    return " & ".join(fields)


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    records = {}
    for src in sorted(SOURCE.glob("*.tex")):
        original = src.read_text()
        translated = "\n".join(translate_line(line) for line in original.splitlines()) + "\n"
        # Numeric result tokens, including intervals and row counts, must be byte-identical.
        old_numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", original)
        new_numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", translated)
        if old_numbers != new_numbers:
            raise ValueError(f"numeric tokens changed: {src.name}")
        if src.stem == "D_workload_results":
            translated = translated.replace(r"\renewcommand{\arraystretch}{1.18}",
                                            r"\renewcommand{\arraystretch}{1.05}")
        target = OUTPUT / src.name
        target.write_text(translated)
        records[src.name] = {"source_sha256": digest(src), "translation_sha256": digest(target)}
    (ROOT / "translation_tables_manifest.json").write_text(
        json.dumps({"method": "text-only translation of verified English TeX tables",
                    "files": records}, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Translated {len(records)} tables into {OUTPUT}")


if __name__ == "__main__":
    main()
