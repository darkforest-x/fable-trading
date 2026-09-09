"""Standalone human-review figures for the authorized USELESS launch case.

Source features are emitted by useless_multiscale_case.py. Price candles are
placed at OPEN timestamps; indicators and event markers are placed at CLOSE
timestamps, their earliest completed-bar availability. Blue shading marks
data unavailable at the original 2026-08-31 08:00 Beijing decision. These
figures deliberately show subsequent history for review and never enter
YOLO model_inputs. No market data is fetched, inferred or altered here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
from matplotlib import font_manager
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

TZ = ZoneInfo("Asia/Shanghai")
DECISION = pd.Timestamp("2026-08-31T08:00:00+08:00")
SIGNAL_OPEN = pd.Timestamp("2026-08-31T07:00:00+08:00")
COLORS = {"sma20": "#158b80", "ema20": "#66b7ae", "sma60": "#4979b8",
          "ema60": "#8fabd0", "sma120": "#626b76", "ema120": "#a1a7af"}
UP, DOWN, INK, MUTED = "#0b9b86", "#d25465", "#1c3442", "#657987"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configure_font() -> str:
    """Use installed PingFang, with explicit Chinese system-font fallback."""
    choices = [Path("/System/Library/Fonts/PingFang.ttc")]
    choices += sorted(Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font8").glob("*/AssetData/PingFang.ttc"))
    choices += [Path("/System/Library/Fonts/STHeiti Light.ttc")]
    font = next((path for path in choices if path.is_file()), None)
    if font is None:
        raise RuntimeError("No verified Chinese font installed")
    font_manager.fontManager.addfont(str(font))
    name = font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({"font.family": name, "axes.unicode_minus": False,
        "font.size": 11, "axes.labelcolor": MUTED, "xtick.color": MUTED,
        "ytick.color": MUTED, "text.color": INK, "axes.edgecolor": "#dce4e9",
        "savefig.facecolor": "white", "figure.facecolor": "white"})
    return str(font)


def read_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame.index = pd.to_datetime(frame.open_time, utc=True)
    frame["closed_at"] = pd.to_datetime(frame.close_time, utc=True)
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("Features are not chronological")
    return frame


def num(value):
    if isinstance(value, str):
        value = pd.Timestamp(value)
    return mdates.date2num(value)


def axis_style(ax):
    ax.grid(axis="y", color="#e6edf1", linewidth=.6, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(length=0, pad=6)
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.set_axisbelow(True)


def candles(ax, frame, minutes):
    """Candles at their exchange opening timestamp, with close-source MAs."""
    width = minutes / 1440 * .66
    minimum = max((frame.high.max() - frame.low.min()) * .0008, 1e-8)
    for when, row in frame.iterrows():
        x, color = num(when), UP if row.close >= row.open else DOWN
        ax.plot([x, x], [row.low, row.high], color=color, linewidth=.75, zorder=2)
        body = max(abs(row.close - row.open), minimum)
        ax.add_patch(Rectangle((x-width/2, min(row.open, row.close)), width, body,
                     facecolor=color, edgecolor=color, linewidth=.4, zorder=3))
    # An MA value belongs to the completed candle, hence use its close clock.
    for name, color in COLORS.items():
        ax.plot(num(frame.closed_at), frame[name], color=color, linewidth=1.0, label=name.upper(), alpha=.95)
    axis_style(ax)
    ax.set_ylabel("USDT")


def phase(ax, right, annotate=False):
    ax.axvspan(num(DECISION), num(right), color="#ebf2ff", alpha=.53, linewidth=0, zorder=-1)
    ax.axvline(num(DECISION), color="#8b5ec5", linewidth=1.15, linestyle="--", zorder=4)
    if annotate:
        ax.text(.99, .97, "浅蓝区：08:00 当时看不到的后续", transform=ax.transAxes,
                ha="right", va="top", color="#6c73a5", fontsize=10,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": .85, "pad": 4})


def line_panel(ax, frame):
    x = num(frame.closed_at)
    ax.plot(x, frame.md, color="#447ed0", linewidth=1.75, label="IMACD 主线")
    ax.plot(x, frame.sb, color="#cf922e", linewidth=1.5, label="信号线")
    ax.axhline(0, color="#8595a0", linewidth=.8)
    axis_style(ax)
    ax.set_ylabel("IMACD")


def legend(ax, cols=6):
    ax.legend(loc="upper left", ncol=cols, frameon=False, fontsize=9,
              handlelength=1.8, columnspacing=1.2, borderaxespad=.6)


def global_chart(frame, summary, out):
    """Full later history plus explicitly hypothetical entry/exit references."""
    selected = frame.loc[(frame.index >= pd.Timestamp("2026-08-25T00:00Z")) &
                         (frame.index < pd.Timestamp("2026-09-09T00:00:00+08:00"))]
    key = SIGNAL_OPEN.tz_convert("UTC").isoformat()
    path = summary["paths"][key]
    entry, stop = path["entry_reference"], path["pine_initial_stop_v27"]["stop"]
    target = entry + 3 * (entry-stop)
    right = selected.closed_at.iloc[-1]
    fig, axes = plt.subplots(3, 1, figsize=(16, 10), sharex=True,
                             gridspec_kw={"height_ratios": [4.0, 1.2, 1.0], "hspace": .10})
    ax = axes[0]
    candles(ax, selected, 60)
    legend(ax)
    ax.hlines([entry, stop, target], num(DECISION), num(right),
              colors=["#5b7383", DOWN, "#50a48f"], linestyles=[":", "--", ":"], linewidths=.9)
    ax.annotate(f"08/31 08:00 启动确认\n收盘 {entry:.5f}", xy=(num(DECISION), entry),
                xytext=(-42, 65), textcoords="offset points", ha="right", color="#7855ac",
                arrowprops={"arrowstyle": "->", "color": "#7855ac"}, fontsize=10)
    sma20 = path["first_close_below_sma20"]
    breach = pd.Timestamp(sma20["signal_close_time"])
    ax.scatter([num(breach)], [sma20["close"]], s=44, color=DOWN, marker="x", linewidth=1.6, zorder=6)
    ax.annotate(f"首次收盘低于 SMA20\n{breach.tz_convert(TZ):%m/%d %H:%M} · {sma20['close']:.5f}\n价格变化 +{sma20['close_return_pct']:.1f}%",
                xy=(num(breach), sma20["close"]), xytext=(10, 72), textcoords="offset points",
                ha="left", color=DOWN, fontsize=10, arrowprops={"arrowstyle": "->", "color": DOWN})
    peak = pd.Timestamp(path["highest_time"]) + pd.Timedelta(hours=1)
    ax.annotate(f"后续最高 {path['highest_high']:.5f}\n最大有利幅度 +{path['maximum_favorable_pct']:.1f}%\n不等于可兑现收益",
                xy=(num(peak), path["highest_high"]), xytext=(-145, -30), textcoords="offset points",
                color=INK, fontsize=10, arrowprops={"arrowstyle": "->", "color": MUTED})
    ax.text(.012, .035, f"V2.7 追溯参考  初始止损 {stop:.5f}（{path['pine_initial_stop_v27']['risk_pct']:.2f}%）  ·  3R {target:.5f}（非固定止盈）",
            transform=ax.transAxes, fontsize=10, color=MUTED,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": .9, "pad": 4})
    line_panel(axes[1], selected)
    legend(axes[1], 2)
    axes[2].bar(num(selected.closed_at), selected.relative_volume_mean20,
                width=60/1440*.75, color="#7fada7", alpha=.85)
    axes[2].axhline(1, color=MUTED, linestyle=":", linewidth=.8)
    axis_style(axes[2])
    axes[2].set_ylabel("量 / 前20均量")
    for a in axes:
        phase(a, right, a is ax)
    axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=2, tz=TZ))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=TZ))
    axes[-1].set_xlim(num(selected.index[0])-1/24, num(right)+2/24)
    fig.suptitle("USELESS · 1H 全局复盘：抓到启动，与按什么规则拿住，是两个问题", x=.06, ha="left", fontsize=18, fontweight="bold")
    fig.text(.06, .923, "OKX 永续 · 2026/08/25—09/08 · 北京时间 · 蜡烛按开盘位置，指标与事件按收盘时间", color=MUTED, fontsize=11)
    fig.text(.06, .025, "仅作已知案例的价格路径复盘；未计手续费、滑点、资金费、杠杆。初始止损按现有 V2.7 追溯，不冒充当时旧版实际成交。", color=MUTED, fontsize=10)
    fig.subplots_adjust(top=.89, bottom=.075, left=.06, right=.94)
    path_out = out / "01_useless_1h_global.png"
    fig.savefig(path_out, dpi=160)
    plt.close(fig)
    return path_out


def detail_chart(frame, name, minutes, out, order):
    """Four panels separate price, IMACD, relative volume and Momentum 1.0."""
    left = pd.Timestamp("2026-08-30T12:00:00+08:00")
    right = pd.Timestamp("2026-09-01T00:00:00+08:00")
    selected = frame.loc[(frame.index >= left) & (frame.index < right)]
    if selected.empty:
        raise ValueError(f"No data for {name} detail")
    available = frame.loc[frame.closed_at <= DECISION].iloc[-1]
    fig, axes = plt.subplots(4, 1, figsize=(16, 11.5), sharex=True,
                        gridspec_kw={"height_ratios": [3.3, 1.2, 1.1, 1.1], "hspace": .11})
    candles(axes[0], selected, minutes)
    legend(axes[0])
    releases = selected.loc[selected.release_side.eq(1)]
    axes[0].scatter(num(releases.closed_at), releases.close, color="#8052b7", marker="^", s=70, zorder=6)
    for _, row in releases.iterrows():
        axes[0].annotate(f"启动确认 {row.closed_at.tz_convert(TZ):%d日 %H:%M}",
                        xy=(num(row.closed_at), row.close), xytext=(5, 22), textcoords="offset points",
                        fontsize=9, color="#8052b7", arrowprops={"arrowstyle": "-", "color": "#8052b7"})
    line_panel(axes[1], selected)
    legend(axes[1], 2)
    crosses = selected.loc[selected.golden_cross.astype(str).str.lower().eq("true")]
    axes[1].scatter(num(crosses.closed_at), crosses.md, color=UP, marker="o", s=30, zorder=5)
    for _, row in crosses.iterrows():
        axes[1].annotate(f"金叉确认 {row.closed_at.tz_convert(TZ):%H:%M}", xy=(num(row.closed_at), row.md),
                        xytext=(7, 11), textcoords="offset points", color=UP, fontsize=9)
    x = num(selected.closed_at)
    axes[2].bar(x, selected.relative_volume_mean20, width=minutes/1440*.7,
                color=np.where(selected.close >= selected.open, "#60aaa0", "#d99ba5"))
    axes[2].axhline(1, color=MUTED, linestyle=":", linewidth=.8)
    axis_style(axes[2]); axes[2].set_ylabel("量 / 前20均量")
    axes[3].plot(x, selected.momentum10, color="#9568bd", linewidth=1.45)
    axes[3].axhline(90, color="#b78a4e", linestyle="--", linewidth=.8)
    axes[3].axhline(0, color="#bcc7cd", linewidth=.6)
    strong = selected.loc[selected.momentum10.ge(90)]
    axes[3].scatter(num(strong.closed_at), strong.momentum10, s=15, color="#b78a4e", zorder=4)
    axis_style(axes[3]); axes[3].set_ylabel("动能 1.0")
    axes[3].set_ylim(-115, 125)
    for ax in axes:
        phase(ax, right, ax is axes[0])
    for ax in axes[1:]:
        ax.axvline(num(DECISION), color="#8b5ec5", linewidth=1.15, linestyle="--")
    axes[-1].set_xlim(num(left)-minutes/1440*.7, num(right)+minutes/1440*.3)
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(interval=4, tz=TZ))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H:%M", tz=TZ))
    fig.suptitle(f"USELESS · {name} 启动前后：只把已收盘证据放在 08:00 决策线上", x=.06, ha="left", fontsize=18, fontweight="bold")
    text = (f"08:00 可用的最后收盘：{available.closed_at.tz_convert(TZ):%m/%d %H:%M}  ·  "
            f"量比 {available.relative_volume_mean20:.2f}×  ·  动能 {available.momentum10:.1f}  ·  "
            f"价格高于 {int(available.above6_count)}/6 根均线")
    fig.text(.06, .929, text, color=MUTED, fontsize=11)
    fig.text(.06, .025, "动能默认 SMA50：相对其最近50根最大绝对偏差归一化；90 为源码强区阈值。量基准不含当前K。不同周期嵌套，不能视作独立证据票。", color=MUTED, fontsize=10)
    fig.subplots_adjust(top=.895, bottom=.085, left=.06, right=.94)
    path_out = out / f"{order:02d}_useless_{name.lower()}_detail.png"
    fig.savefig(path_out, dpi=160)
    plt.close(fig)
    return path_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("analysis/output/useless_multiscale_20260909"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out = args.out or args.source / "human_review_charts"
    if out.exists() and any(out.iterdir()):
        raise ValueError("Refusing to replace already generated charts")
    font = configure_font()
    frames = {name: read_frame(args.source / f"features_{name}.csv") for name in ("1H", "15m", "4H")}
    summary = json.loads((args.source / "case_summary.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    charts = [global_chart(frames["1H"], summary, out)]
    for order, name, minutes in ((2, "15m", 15), (3, "1H", 60), (4, "4H", 240)):
        charts.append(detail_chart(frames[name], name, minutes, out, order))
    receipt = {"kind": "human_review_only_not_model_input", "decision_beijing": DECISION.isoformat(),
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "builder_sha256": sha(Path(__file__)),
        "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "font": font, "sources": {f"features_{name}.csv": sha(args.source / f"features_{name}.csv") for name in frames},
        "summary_sha256": sha(args.source / "case_summary.json"),
        "artifacts": [{"path": str(path), "sha256": sha(path)} for path in charts]}
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
