#!/usr/bin/env python3
"""Replot archived ablation summaries without reconstructing individual runs."""

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


RATES = (1, 2, 4, 8, 16)
CONFIGS = ((0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1), (1, 1, 0), (1, 1, 1))
# Workbook columns are physical isolation, environment isolation, concurrency control.
COMPARISONS = (
    ("Concurrency control", (
        ("PI off, EI off", (0, 0, 0), (0, 0, 1)),
        ("PI off, EI on", (0, 1, 0), (0, 1, 1)),
        ("PI on, EI on", (1, 1, 0), (1, 1, 1)),
    )),
    ("Physical isolation", (
        ("CC off, EI on", (0, 1, 0), (1, 1, 0)),
        ("CC on, EI on", (0, 1, 1), (1, 1, 1)),
    )),
    ("Environment isolation", (
        ("CC off, PI off", (0, 0, 0), (0, 1, 0)),
        ("CC on, PI off", (0, 0, 1), (0, 1, 1)),
    )),
)
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def worksheet_rows(source: Path):
    with zipfile.ZipFile(source) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in item.findall(".//m:t", NS))
                      for item in root.findall("m:si", NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        for row in sheet.findall("m:sheetData/m:row", NS):
            cells = {}
            for cell in row.findall("m:c", NS):
                value = cell.find("m:v", NS)
                if value is None:
                    continue
                raw = value.text or ""
                if cell.attrib.get("t") == "s":
                    raw = shared[int(raw)]
                column = re.match(r"[A-Z]+", cell.attrib["r"]).group()
                cells[column] = raw
            yield int(row.attrib["r"]), cells


def read_summary(source: Path):
    values = {}
    for row_number, row in worksheet_rows(source):
        if row_number < 4:
            continue
        rate = int(row["A"])
        config = tuple(int(row[col]) for col in "BCD")
        key = (rate, config)
        if key in values:
            raise ValueError(f"duplicate row {key}")
        values[key] = {
            "source_row": row_number,
            "count_summary": float(row["F"]),
            "mean_ms": float(row["G"]),
            "p95_ms": float(row["M"]),
            "throughput_rps": float(row["P"]),
        }
        if any(values[key][field] <= 0 for field in ("mean_ms", "p95_ms", "throughput_rps")):
            raise ValueError(f"nonpositive statistic in row {row_number}")
    expected = {(rate, config) for rate in RATES for config in CONFIGS}
    if set(values) != expected:
        raise ValueError(f"missing={sorted(expected - set(values))}; extra={sorted(set(values) - expected)}")
    return values


def comparisons(values):
    result = []
    for feature, pairs in COMPARISONS:
        lines = []
        for name, off, on in pairs:
            points = []
            for rate in RATES:
                a, b = values[(rate, off)], values[(rate, on)]
                points.append({
                    "rate": rate,
                    "off_source_row": a["source_row"],
                    "on_source_row": b["source_row"],
                    "mean_latency_ratio_on_off": b["mean_ms"] / a["mean_ms"],
                    "p95_latency_ratio_on_off": b["p95_ms"] / a["p95_ms"],
                    "throughput_ratio_on_off": b["throughput_rps"] / a["throughput_rps"],
                })
            lines.append({"condition": name, "off": off, "on": on, "points": points})
        result.append({"feature": feature, "lines": lines})
    return result


def plot(data, output: Path):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "pdf.fonttype": 42})
    fig, axes = plt.subplots(1, 3, figsize=(7.05, 2.65), sharex=True, sharey=True)
    colors = ("#24608a", "#b15943", "#55805e")
    markers = ("o", "s", "^")
    for axis, group in zip(axes, data):
        axis.axhline(1, color="#777", linestyle="--", linewidth=0.8)
        for index, line in enumerate(group["lines"]):
            axis.plot(RATES, [p["mean_latency_ratio_on_off"] for p in line["points"]],
                      color=colors[index], marker=markers[index], linewidth=1.5,
                      markersize=3.5, label=line["condition"])
        axis.set_title(group["feature"], fontsize=8.5)
        axis.set_xticks(RATES)
        axis.set_xticklabels(RATES)
        axis.set_xlabel("Offered requests/s")
        axis.set_yscale("log")
        axis.set_ylim(0.015, 3.5)
        axis.grid(axis="y", color="#ddd", linewidth=0.5)
        axis.spines[["top", "right"]].set_visible(False)
        legend_location = "lower right" if group["feature"] == "Physical isolation" else "upper right"
        axis.legend(loc=legend_location, frameon=False, fontsize=6.8,
                    handlelength=1.5, labelspacing=0.25)
    axes[0].set_ylabel("Mean latency ratio (on / off)\nLower is better")
    fig.tight_layout(w_pad=1.3)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    fig.savefig(output.with_suffix(".png"), dpi=220, bbox_inches="tight")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = comparisons(read_summary(args.input))
    plot(data, args.output)
    args.output.with_suffix(".json").write_text(json.dumps({
        "source": str(args.input),
        "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "scope": "archived case summaries; no request-level or run-level records, host manifest, or scheduler parameters",
        "definition": "ratio = statistic with one mechanism enabled / statistic with that mechanism disabled",
        "comparisons": data,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
