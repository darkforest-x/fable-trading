"""Aggregate committed morphology receipts and render past-only evidence.

Inputs are the frozen density scan, not future prices or trade outcomes.
Cases are explicit mechanism examples; charts are not precision estimates.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from yoyo.evaluation.spike_ma_density import MAS
from yoyo.evaluation.spike_ma_density_study import EXP, digest

CASES = (
    ("BTCUSDT", "15m", "same_pair_repeat", "同一对反复交叉，仍计为密集"),
    ("ETHUSDT", "15m", "not_all_groups_connected", "局部交叉，三组线没有连通"),
    ("SOLUSDT", "1h", "mean_hides_over3", "平均宽度掩盖窗口内的分离"),
    ("BTCUSDT", "4h", "atr_denominator_sensitive", "换用核心前 ATR 后不再通过"),
    ("BTCUSDT", "15m", "recent_but_not_now", "当前已分离，近期记忆仍通过"),
    ("SOLUSDT", "15m", "compact_interwoven", "紧凑且三组交织的候选"),
    ("ETHUSDT", "1h", "compact_without_cross", "线束较近，却因交叉不足被拒"),
    ("BTCUSDT", "4h", "compact_converging", "末端收拢的候选"),
)


def verified_tables(root):
    identity = json.loads((root / "identity.json").read_text())
    done = json.loads((root / "completion.json").read_text())
    assert done["identity_hash"] == identity["identity_hash"]
    assert set(done["symbols"]) == set(identity["inputs"]) and len(done["symbols"]) == 29
    frames = {}
    for symbol in done["symbols"]:
        folder = root / "streams" / symbol
        receipt = json.loads((folder / "completion.json").read_text())
        assert receipt["identity_hash"] == identity["identity_hash"]
        assert receipt["source_sha256"] == identity["inputs"][symbol]["sha256"]
        assert receipt["streams"] == 3 and receipt["legacy_feature_parity"]
        for name, expected in receipt["files"].items():
            assert digest(folder / name) == expected, (symbol, name)
            frames.setdefault(name.removesuffix(".csv.gz"), []).append(pd.read_csv(folder / name))
    return identity, {k: pd.concat(v, ignore_index=True) for k, v in frames.items()}


def panel(ax, window, row, title, zoom=False):
    z = window[window.relative_i >= (-24 if zoom else -80)].copy()
    ref = float(window.iloc[-1].close)
    transform = lambda x: (np.asarray(x) / ref - 1) * 100
    ax.axvspan(-12.5, -.5, color="#e9be57", alpha=.14)
    ax.axvline(-.5, color="#707c8d", ls=":", lw=.8)
    for _, r in z.iterrows():
        o, h, l, c = transform([r.open, r.high, r.low, r.close])
        color = "#9aafbf" if c >= o else "#586274"
        ax.vlines(r.relative_i, l, h, color=color, lw=.65, alpha=.65)
        ax.add_patch(Rectangle((r.relative_i-.28, min(o,c)), .56, max(abs(c-o),.001), color=color, alpha=.45))
    for i, name in enumerate(MAS):
        ax.plot(z.relative_i, transform(z[name]), color=("#008f83", "#347cdc", "#ad61ca")[i//2],
                lw=1.4, ls="-" if i % 2 == 0 else "--", label=name.upper())
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", pad=9)
    ax.set_xlim(z.relative_i.min()-.7, .7)
    ax.set_ylabel("相对右端收盘价 (%)", fontsize=9)
    ax.grid(alpha=.14)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=8)
    ax.set_xlabel("相对判定 K 线的位置（0=判定；金色=此前12根）", fontsize=9)


def main():
    source = Path(__file__).relative_to(Path.cwd()) if Path(__file__).is_absolute() else Path(__file__)
    assert not subprocess.check_output(["git", "status", "--porcelain", "--", str(source)], text=True).strip(), "Commit renderer first"
    root = EXP / "run_v1"
    out = root / "delivery_v1"
    out.mkdir(parents=True, exist_ok=False)
    identity, tables = verified_tables(root)
    outputs = {}
    for name in ("counts", "flags", "null"):
        dims = ["timeframe", "period", "flag" if name == "flags" else "arm"]
        result = tables[name].drop(columns="symbol").groupby(dims, as_index=False).sum(numeric_only=True)
        result.to_csv(out / (name+".csv"), index=False)
        outputs[name] = result
    tables["coverage"].to_csv(out / "coverage.csv", index=False)
    signals = tables["v9"]
    rows = []
    for (tf, period), group in signals.groupby(["timeframe", "period"]):
        rows.append(dict(timeframe=tf,period=period,candidates=len(group),known=int(group.known.sum()),
                         no_recent12=int((~group.legacy_recent12).sum()),no_current_core=int((~group.legacy_raw).sum()),
                         max_density_age=float(group.legacy_age.max()),median_width=float(group.mean_atr12.median())))
    pd.DataFrame(rows).to_csv(out / "v9_summary.csv", index=False)
    signals.to_csv(out / "v9_candidates.csv.gz", index=False, compression={"method":"gzip","mtime":0})
    plt.rcParams.update({"font.family":"Arial Unicode MS", "axes.unicode_minus":False, "savefig.facecolor":"white"})
    candidates = tables["case_candidates"].set_index("case_id")
    selected = []
    windows = tables["case_windows"]
    for symbol, tf, kind, title in CASES:
        cid = f"{symbol}_{tf}_{kind}"
        row = candidates.loc[cid]
        window = windows[windows.case_id == cid].sort_values("relative_i")
        assert len(window) == 81 and window.relative_i.max() == 0
        assert str(pd.Timestamp(window.iloc[-1].time)) == str(pd.Timestamp(row.time))
        selected.append(dict(case_id=cid, title=title, **row.to_dict()))
        fig, axes = plt.subplots(1, 2, figsize=(14, 5), gridspec_kw={"width_ratios":[1.45,1]})
        panel(axes[0],window,row,f"{symbol} · {tf} | {title}")
        panel(axes[1],window,row,"右端局部放大",True)
        axes[0].legend(ncol=6,loc="upper left",fontsize=8,handlelength=1.9,columnspacing=.8)
        foot = (f"判定K线开盘 {row.time} UTC | 旧核心规则 {'通过' if row.legacy_raw else '未通过'} | "
                f"均宽/最大宽 {row.mean_atr12:.2f}/{row.max_atr12:.2f} ATR | "
                f"交叉 {int(row.cross_count12)} 次 / {int(row.distinct_pairs12)} 对 / {int(row.group_edges12)} 组间边\n"
                f"核心前 ATR 归一均宽 {row.stable_mean12:.2f} | 末4根/首4根宽度 {row.contraction_ratio:.2f} | "
                "定向机制抽样；没有展示判定之后的行情，不能视为真实正负标签")
        fig.text(.05,.025,foot,fontsize=9,color="#3d4d61",linespacing=1.8)
        fig.tight_layout(rect=(0,.14,1,1))
        fig.savefig(out / (cid+".png"),dpi=140)
        plt.close(fig)
    pd.DataFrame(selected).to_csv(out / "selected_cases.csv", index=False)
    counts = outputs["counts"]
    full = counts[counts.period.eq("full")]
    tfs = ["15m","1h","4h"]
    arms = ["legacy","width_2","width_1.5","width_1","width_0.5","max_width_3","distinct_pairs_2","group_edges_2","coverage_10of12","relative_p20","contraction_20pct"]
    labels = ["旧规则","仅均宽≤2 ATR","仅均宽≤1.5 ATR","仅均宽≤1 ATR","仅均宽≤0.5 ATR","仅改最大宽≤3 ATR","仅改不同线对≥2","仅改三组交叉连通","仅加10/12根宽≤3","仅加自身历史P20","仅加末端收窄20%"]
    fig, axes = plt.subplots(1,3,figsize=(13,6),sharey=True)
    for ax, tf in zip(axes,tfs):
        data = full[full.timeframe.eq(tf)].set_index("arm")
        values = [100*data.loc[a,"bars"]/data.loc["legacy","bars"] for a in arms]
        ax.barh(np.arange(len(arms)),values,color=["#8d98a6"]+["#008f83"]*(len(arms)-1),height=.65)
        ax.set_yticks(range(len(arms)),labels if tf == "15m" else [])
        ax.set_title(tf,fontsize=14,fontweight="bold")
        ax.set_xlim(0,114)
        ax.set_xlabel("保留的旧密集判定 (%)")
        ax.grid(axis="x",alpha=.18)
        ax.spines[["top","right"]].set_visible(False)
        for i,v in enumerate(values):ax.text(v+1,i,f"{v:.1f}%",va="center",fontsize=9)
    axes[0].set_yticks(range(len(arms)),labels)
    axes[0].invert_yaxis()
    fig.suptitle("每次只改一个条件：保留率有多大变化？",fontsize=17,x=.04,ha="left")
    fig.text(.04,.015,"29个币 · 1,974,280根共同有效K线 · 重叠窗口相关；保留率不是准确率，也不代表收益。",fontsize=10)
    fig.tight_layout(rect=(0,.055,1,.96))
    fig.savefig(out / "component_comparison.png",dpi=140)
    plt.close(fig)
    receipt = {"source_identity":identity["identity_hash"],"renderer_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
               "renderer_sha256":digest(source),"symbols":29,"streams":87,"cases":len(selected),
               "files":{str(p.relative_to(out)):digest(p) for p in sorted(out.iterdir()) if p.is_file()}}
    (out / "completion.json").write_text(json.dumps(receipt,indent=2)+"\n")
    print(json.dumps({"output":str(out),"files":len(receipt["files"]),"cases":len(selected)}))


if __name__ == "__main__": main()
