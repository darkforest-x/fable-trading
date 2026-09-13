"""Render causal MA-cycle timelines and deterministic outcome examples.

Each state is available only at its own bar close. Future shaded bars explain
the recorded trade path and never relabel the original confirmation. Selected
examples explain definitions; neither their selection nor rendering tunes rules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import load_verified_stream
from yoyo.evaluation.spike_v8_ma_cycle_study import cycle_features

EXP = Path("experiments/active/exp-spike-v8-ma-cycle-20260913-v1")
COLORS = {"armed_consolidation": ("压缩就绪", "#d8ad53"),
          "launch": ("启动中", "#1b9e87"), "expansion": ("排列扩散", "#5484cd"),
          "reconsolidation": ("重新收拢", "#ad80bd"),
          "awaiting_compression": ("等待新整理", "#b9c1c9"), "unknown": ("未知", "#dfe3e6")}
MAS = (("s20", "#157f75"), ("e20", "#75b2a6"), ("s60", "#3565a4"),
       ("e60", "#91aac9"), ("s120", "#444b57"), ("e120", "#8d94a0"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plot(context, start: int, end: int, output: Path, *, title: str,
         confirmation: pd.Timestamp | None = None, trade: pd.Series | None = None,
         reference_marks: list[tuple[str, str]] | None = None) -> None:
    bars = context.cache["bars"]
    gap = context.cache["data_gap"].reindex(bars.index).fillna(True).astype(bool)
    fields = cycle_features(bars, gap, context.minutes)
    view, state = bars.iloc[start:end], fields.iloc[start:end]
    local = view.index.tz_convert("Asia/Shanghai")
    x = mdates.date2num(local.to_pydatetime())
    step = context.minutes / 1440
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(3, 1, figsize=(17, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3.5, 1, 1]}, constrained_layout=True)
    for ax in axes:
        for i, (_, row) in enumerate(state.iterrows()):
            color = COLORS.get(str(row.state), COLORS["unknown"])[1]
            ax.axvspan(x[i] - step / 2, x[i] + step / 2, color=color, alpha=.13, lw=0)
        ax.grid(alpha=.12)
    ax = axes[0]
    for xi, row in zip(x, view.itertuples()):
        color = "#d45055" if row.close >= row.open else "#158879"
        ax.vlines(xi, row.low, row.high, color=color, lw=.7)
        height = max(abs(row.close-row.open), abs(row.close)*1e-7)
        ax.add_patch(Rectangle((xi-step*.32, min(row.open,row.close)), step*.64,
                               height, color=color, lw=.4))
    for name, color in MAS:
        ax.plot(local, view[name], color=color, lw=.9, label=name)
    if confirmation is not None:
        closed_at = confirmation + pd.Timedelta(minutes=context.minutes)
        for axis in axes:
            axis.axvline(closed_at.tz_convert("Asia/Shanghai"), color="#9360aa", ls="--", lw=1)
        ax.axvspan(closed_at.tz_convert("Asia/Shanghai"), local[-1], color="#64748b", alpha=.07)
    if trade is not None:
        entry, exit_time = pd.Timestamp(trade.entry_time), pd.Timestamp(trade.exit_time)
        ax.scatter(entry.tz_convert("Asia/Shanghai"), trade.entry_price, color="#111827", marker="^", zorder=8)
        ax.scatter(exit_time.tz_convert("Asia/Shanghai"), trade.exit_price, color="#111827", marker="x", zorder=8)
        ax.hlines(trade.initial_stop, entry.tz_convert("Asia/Shanghai"), exit_time.tz_convert("Asia/Shanghai"),
                  color="#c35a63", ls="--", lw=.8)
    for stamp, label in reference_marks or []:
        instant = pd.Timestamp(stamp)
        value = float(bars.loc[instant, 'close'])
        ax.scatter(instant.tz_convert('Asia/Shanghai'), value, s=32, color='#725294', zorder=9)
        ax.annotate(label, xy=(instant.tz_convert('Asia/Shanghai'), value),
                    xytext=(0, 46), textcoords='offset points', ha='center', fontsize=9,
                    bbox=dict(boxstyle='round,pad=.4', facecolor='white', edgecolor='#ddd5e3', alpha=.95),
                    arrowprops=dict(arrowstyle='->',color='#725294',lw=.8))
    ax.set_title(title, fontsize=13)
    ax.set_ylabel("价格")
    ax.legend(ncol=6, loc="upper left", fontsize=8)
    axes[1].plot(local, state.order_long, color="#168776", label="多头排列 / 12")
    axes[1].plot(local, state.order_short, color="#b95165", label="空头排列 / 12")
    axes[1].set_yticks([0,6,12]); axes[1].set_ylabel("跨周期排列")
    axes[1].legend(loc="upper left", ncol=2, fontsize=8)
    axes[2].plot(local, state.width_close*100, color="#315685", label="六线宽度 / 价格")
    axes[2].plot(local, state.compression_threshold*100, color="#b88b36", ls="--", label="此前256根20%分位")
    axes[2].set_ylabel("带宽 %"); axes[2].legend(loc="upper left", ncol=2, fontsize=8)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=local.tz))
    caption = "北京时间K线开盘标时；状态在各自收盘后才可知。"
    caption += ("紫线为V8确认时点，灰色叠加区为后续走势；三角为原入场，叉为原退出。" if confirmation is not None
                else "A/B/C为原图三笔观察；背景为每根收盘后的固定状态，不是事后回填。")
    axes[2].set_xlabel(caption)
    fig.legend(handles=[Patch(facecolor=c, alpha=.4, label=n) for n,c in COLORS.values()],
               loc="outside upper center", ncol=6, fontsize=9)
    fig.savefig(output, dpi=150)
    plt.close(fig)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    for source in (Path(__file__), Path("yoyo/evaluation/spike_v8_ma_cycle_study.py")):
        rel=source.resolve().relative_to(Path.cwd().resolve())
        if (subprocess.run(["git","cat-file","-e",f"HEAD:{rel}"],capture_output=True).returncode
            or subprocess.run(["git","diff","--quiet","HEAD","--",str(rel)],check=False).returncode):
            raise ValueError("Commit chart and state builders before rendering")
    if args.output.exists():
        raise ValueError("Output directory exists; preserve prior charts")
    args.output.mkdir(parents=True)
    config=json.loads((EXP/"config.json").read_text())
    raw=Path(config["raw"]); replay=Path(config["v8_replay"])
    events=pd.read_csv(args.events)
    eligible=events.loc[events.venue.eq("okx") & events.scoring_closed.astype(bool)]
    selected=[]
    for stage in ("launch","expansion"):
        for side in (1,-1):
            for outcome in ("profit","loss"):
                mask=eligible.net_r.gt(0) if outcome=="profit" else eligible.net_r.lt(0)
                sub=eligible.loc[mask & eligible.side.eq(side) & eligible[f"same_direction_{stage}"].astype(bool)].copy()
                if sub.empty:
                    selected.append({"chart_id":f"{stage}_{side}_{outcome}","status":"no_eligible_case"}); continue
                median=float(sub.net_r.median())
                case=sub.assign(distance=(sub.net_r-median).abs()).sort_values(["distance","trade_id"]).iloc[0]
                context=load_verified_stream(raw/"streams"/str(case.stream_key))
                trades=pd.read_csv(replay/"streams"/f"{case.stream_key}.trades.csv.gz")
                trade=trades.loc[trades.arm.eq("v8") & trades.trade_id.eq(case.trade_id)]
                if len(trade)!=1: raise ValueError("Ambiguous exact V8 trade")
                trade=trade.iloc[0]; bars=context.cache["bars"]
                confirm=pd.Timestamp(case.signal_bar_open); i=int(bars.index.get_loc(confirm))
                exit_i=int(bars.index.get_loc(pd.Timestamp(trade.exit_time)))
                chart_id=f"{stage}_{side}_{outcome}"
                plot(context,max(0,i-64),min(len(bars),max(i+73,exit_i+13)),args.output/f"{chart_id}.png",
                     title=f"{case.symbol} · {context.minutes}分钟 · {'多' if side==1 else '空'} · 原确认时{COLORS[stage][0]} · 实现 {case.net_r:.2f}R",
                     confirmation=confirm,trade=trade)
                selected.append({"chart_id":chart_id,"status":"rendered","trade_id":case.trade_id,
                                 "stream_key":case.stream_key,"confirmation":str(confirm),"net_r":case.net_r,
                                 "subgroup_median_net_r":median,"eligible_cases":len(sub)})
    # This owner-selected chart is a definition audit, not a chosen winner.
    comp=events.loc[events.venue.eq("okx") & events.symbol.eq("COMP-USDT-SWAP") & events.timeframe_min.eq(60)]
    if not comp.empty:
        context=load_verified_stream(raw/"streams"/str(comp.stream_key.iloc[0])); bars=context.cache["bars"]
        start=int(bars.index.searchsorted(pd.Timestamp("2026-07-22T00:00:00Z")))
        end=int(bars.index.searchsorted(pd.Timestamp("2026-08-04T00:00:00Z")))
        plot(context,start,end,args.output/"comp_1h_cycle.png",title="COMP 1H · 原案例区间的固定状态划分（非新信号，不以此调参）",
             reference_marks=[('2026-07-23T16:00:00Z','A · 多 17.61'),
                              ('2026-07-26T04:00:00Z','B · 多 17.43'),
                              ('2026-07-27T08:00:00Z','C · 空 17.20')])
    pd.DataFrame(selected).to_csv(args.output/"selection.csv",index=False)
    (args.output/"manifest.json").write_text(json.dumps({"builder_sha256":sha(Path(__file__)),
        "state_builder_sha256":sha(Path("yoyo/evaluation/spike_v8_ma_cycle_study.py")),
        "event_input_sha256":sha(args.events),"selection_sha256":sha(args.output/"selection.csv"),
        "method":"OKX, original confirmation state, side and outcome sign; closest netR to subgroup median; trade_id tie-break",
        "charts":{p.name:sha(p) for p in args.output.glob("*.png")}},indent=2))


if __name__=="__main__":
    main()
