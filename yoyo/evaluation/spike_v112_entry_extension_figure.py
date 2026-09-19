"""Render source-backed baseline/treatment means with matched controls.

Reads only committed experiment statistics and renders no reconstructed candles.
Each panel uses the same period and timeframe; controls use matched trades only.
The chart is descriptive, not account equity or a confidence-interval graphic.
"""
from pathlib import Path
import hashlib
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v112_entry_extension import EXP, ARMS
from yoyo.evaluation.spike_v8_six_filters import _committed


def main():
    assert _committed((Path(__file__),))
    path=EXP/"statistics/summary.csv"
    raw=path.read_bytes();table=pd.read_csv(path)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties, fontManager
    font="/System/Library/Fonts/STHeiti Light.ttc";fontManager.addfont(font)
    plt.rcParams.update({"font.family":FontProperties(fname=font).get_name(),"axes.unicode_minus":False,
        "axes.spines.top":False,"axes.spines.right":False,"font.size":11})
    fig,axes=plt.subplots(2,2,figsize=(13,8.4))
    x=np.arange(3)
    for row,period in enumerate(("full","later")):
        for col,metric in enumerate(("r","bp")):
            ax=axes[row,col]
            for j,arm in enumerate(ARMS):
                q=table[(table.period==period)&(table.arm==arm)].set_index("timeframe").reindex(["15m","1h","4h"])
                off=(-.19 if j==0 else .19)
                values=q["mean_"+metric]
                label="原规则" if j==0 else "推进≤1过滤"
                bars=ax.bar(x+off,values,width=.34,color=("#B5BEC8" if j==0 else "#376EA8"),label=label)
                ax.bar_label(bars,labels=[f"{v:+.3f}" if metric=="r" else f"{v:+.1f}" for v in values],padding=5,fontsize=10)
                ax.scatter(x+off,q["random_"+metric],marker="_",s=230,color="#30343B",zorder=4,label="匹配随机均值" if j==0 else None)
            labels=[]
            for tf in ("15m","1h","4h"):
                ns=[int(table[(table.period==period)&(table.arm==arm)&(table.timeframe==tf)].iloc[0]["n"]) for arm in ARMS]
                labels.append(f"{tf}\nn={ns[0]}→{ns[1]}")
            ax.set_xticks(x);ax.set_xticklabels(labels)
            ax.axhline(0,color="#555B63",linewidth=.7)
            ax.grid(axis="y",alpha=.16);ax.set_axisbelow(True);ax.margins(y=.27)
            ax.set_ylabel("每笔净R" if metric=="r" else "每笔净bp（100bp=1%）")
            ax.set_title(("全期" if row==0 else "后段：2025-09-10起")+" · "+("风险单位" if col==0 else "价格收益"),loc="left",fontsize=14)
    axes[0,0].legend(frameon=False,fontsize=10,loc="upper left")
    fig.suptitle("框内突破：仅限制相对父V9的价格推进，是否改善入场？",fontsize=18,y=.985)
    fig.text(.05,.055,"固定29币 · 2024-09-10至2026-05-01前 · 每个策略独立串行 · 原止损/追踪/20bp成本不变",fontsize=10)
    fig.text(.05,.027,"柱为全部已平仓交易；黑横线为各臂匹配随机均值（4h有缺配对）。样本数/收益不代表账户净值；区间见报告。",fontsize=10)
    fig.tight_layout(rect=[.015,.08,.995,.95],h_pad=2)
    output=EXP/"comparison.png";fig.savefig(output,dpi=160);plt.close(fig)
    receipt={"renderer_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        "renderer_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input":{str(path):hashlib.sha256(raw).hexdigest()},"output":{str(output):hashlib.sha256(output.read_bytes()).hexdigest()}}
    (EXP/"figure_receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")


if __name__=="__main__":main()
