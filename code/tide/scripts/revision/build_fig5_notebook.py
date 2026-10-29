#!/usr/bin/env python3
"""Embed measured Fig. 5 data and the plotting Python in a standalone notebook."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def cell(kind, source):
    value={"id":hashlib.sha256(source.encode()).hexdigest()[:12],
           "cell_type":kind,"metadata":{},"source":source.splitlines(keepends=True)}
    if kind=='code': value.update(execution_count=None,outputs=[])
    return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    plan=json.loads((args.root/'plan.json').read_text())
    audit=json.loads((args.root/'summary/audit.json').read_text())
    rows=list(csv.DictReader((args.root/'summary/per_cell.csv').open()))
    assert len(rows)==36 and {int(row['runs']) for row in rows}=={plan['repeats']}
    source=Path(__file__).with_name('plot_fig5_2x3.py').read_text().split('\ndef main() -> None:')[0]
    heading=("# Fig. 5：NJ-k1 实测消融\n\n"
             f"6 种 CC/PI/EI 配置 × 6 档速率 × {plan['repeats']} 次独立重复；每次发送 {plan['duration_sec']} 秒，"
             f"共 {audit['requests']:,} 个正式请求（另有预热）。真实 YOLOX-Nano，每请求十次推理。\n\n"
             "误差棒为独立运行均值的 95% t 置信区间；吞吐分母包括完整发送窗口及响应排空。"
             "数据和绘图 Python 均嵌入本文件，无需引入外部图片。运行代码会输出 PDF 和 PNG。\n")
    data="# 审计 CSV 的完整数值，未平滑或调整排序。\nrows = "+repr(rows)+"\n\nvalues = {(int(row['rps']), row['cell']): row for row in rows}\nruns = "+str(plan['repeats'])+"\n"
    # draw() closes the figure after saving; capture that same live Figure for
    # notebook display while preserving the standalone CLI plotting program.
    source=source.replace('    plt.close(fig)','    return fig')
    source=source.replace('matplotlib.use("Agg")',
                          'get_ipython().run_line_magic("matplotlib", "inline")')
    draw="fig = draw(values, runs, Path('fig5_njk1_corrected_2x3.pdf'))\nfrom IPython.display import display\ndisplay(fig)\nplt.close(fig)\n"
    notebook={"nbformat":4,"nbformat_minor":5,
              "metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python"}},
              "cells":[cell('markdown',heading),cell('code',data),cell('code',source),cell('code',draw)]}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(notebook,ensure_ascii=False,indent=1)+'\n')
    print(args.out)


if __name__=='__main__': main()
