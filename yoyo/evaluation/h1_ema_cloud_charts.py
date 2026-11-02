"""Render six white review figures from frozen chart inputs, without trading logic.

Layout and colors adapt Stitch screen 67f5325d32b945d9b3465d1efe97fc08.
Candles use bar-open timestamps. EMA(close, 20/50) uses its actual availability
timestamp, bar-close, and all returns are read unchanged from the study ledger.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
import zipfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
from matplotlib import font_manager, pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "experiments/active/exp-h1-ema-cloud-20261102-v1/charts_white_v1"
TZ = ZoneInfo("Asia/Shanghai")
C = dict(ink="#111827", muted="#64748b", grid="#f1f3f5", border="#e2e8f0",
         a="#2563eb", b="#7c3aed", fast="#0284c7", slow="#d97706",
         up="#299873", down="#d66970", green="#dcfce7", red="#fee2e2",
         target="#15803d", level="#a16207", neutral="#94a3b8")


def setup():
    fonts = font_manager.findSystemFonts()
    path = next((p for p in fonts if p.endswith("/PingFang.ttc")), None)
    path = path or "/System/Library/Fonts/STHeiti Medium.ttc"
    font_manager.fontManager.addfont(path)
    name = font_manager.FontProperties(fname=path).get_name()
    plt.rcParams.update({"font.family": name, "font.size": 11,
                         "axes.unicode_minus": False, "figure.facecolor": "white",
                         "savefig.facecolor": "white", "text.color": C["ink"]})
    return {"font_path": path, "font_name": name}


def date_num(value):
    return mdates.date2num(pd.Timestamp(value).to_pydatetime())


def price(value):
    if value < 10:
        return f"{value:.3f}"
    return f"{value:,.2f}" if value < 10000 else f"{value:,.1f}"


def time_label(value):
    return pd.Timestamp(value).tz_convert(TZ).strftime("%m-%d %H:%M")


def candles(ax, f, minutes):
    x = mdates.date2num(pd.to_datetime(f.timestamp, utc=True).dt.to_pydatetime())
    half = minutes / 1440 * .30
    colors = np.where(f.close >= f.open, C["up"], C["down"])
    wick = [[(t, lo), (t, hi)] for t, lo, hi in zip(x, f.low, f.high)]
    ax.add_collection(LineCollection(wick, colors=colors, linewidths=.7, zorder=3))
    bodies = [[(t-half, o), (t+half, o), (t+half, c), (t-half, c)]
              for t, o, c in zip(x, f.open, f.close)]
    ax.add_collection(PolyCollection(bodies, facecolors=colors, edgecolors=colors,
                                     linewidths=.65, zorder=3))
    return x


def axis_style(ax, f, minutes, extra=()):
    x = candles(ax, f, minutes)
    prices = np.r_[f.low, f.high, extra]
    lo, hi = np.nanmin(prices), np.nanmax(prices)
    span = max(hi-lo, hi*.002)
    ax.set_ylim(lo-span*.13, hi+span*.19)
    ax.set_xlim(x[0]-minutes/1440, x[-1]+minutes/1440*2)
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MaxNLocator(5))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda p, _: price(p)))
    loc = mdates.AutoDateLocator(tz=TZ, minticks=4, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M", tz=TZ))
    ax.tick_params(axis="both", length=0, pad=8, labelsize=9, colors=C["muted"])
    for s in ax.spines.values():
        s.set_color(C["border"])
        s.set_linewidth(.7)
    ax.grid(axis="y", color=C["grid"], linewidth=.7, zorder=0)
    ax.set_axisbelow(True)
    return x


def label(ax, x, y, text, color, dx=12, dy=18):
    return ax.annotate(text, xy=(x, y), xytext=(dx, dy), textcoords="offset points",
                       ha="left" if dx >= 0 else "right", va="bottom" if dy >= 0 else "top",
                       color=color, fontsize=9.5, fontweight="medium", zorder=12,
                       bbox=dict(facecolor="white", alpha=.92, edgecolor="none", pad=2),
                       arrowprops=dict(arrowstyle="-", color=color, lw=.6,
                                       shrinkA=3, shrinkB=6))


def within(ax, x):
    return ax.get_xlim()[0] <= x <= ax.get_xlim()[1]


def level(ax, y, text, color, start, end, dash=False):
    lo, hi = ax.get_ylim()
    if not lo < y < hi:
        return
    left = max(start, ax.get_xlim()[0])
    right = min(end, ax.get_xlim()[1])
    if right <= left:
        return
    ax.plot([left, right], [y, y], color=color, lw=.9,
            linestyle=(0, (4, 4)) if dash else "-", alpha=.8, zorder=2)
    ax.text(left+(right-left)*.015, y, text, color=color, fontsize=9,
            va="bottom", ha="left", zorder=6,
            bbox=dict(facecolor="white", edgecolor="none", alpha=.9, pad=1))


def trade_marks(ax, case, detail=False):
    old, new = case["old"], case["new"]
    shared_exit = (new is not None and
                   case["old_markers"]["exit_bar_open"] == case["new_markers"]["exit_bar_open"]
                   and np.isclose(old["raw_exit_price"], new["raw_exit_price"]))
    for key, letter, color, shape in [("old", "A", C["a"], "o"),
                                      ("new", "B", C["b"], "D")]:
        trade = case[key]
        if trade is None:
            continue
        m = case[key+"_markers"]
        xe, xx = date_num(m["entry_bar_open"]), date_num(m["exit_bar_open"])
        pe, px = m["entry_price_raw"], m["exit_price_raw"]
        if within(ax, xe):
            ax.scatter([xe], [pe], s=65, marker=shape, color=color,
                       edgecolors="white", linewidths=1.2, zorder=9)
            label(ax, xe, pe, f"{letter} 入场", color,
                  dx=-20 if letter == "A" else 17,
                  dy=-23 if letter == "A" else 23)
        # Only the overview carries protective stop levels; detail stays legible.
        if not detail:
            xs, xf = max(xe, ax.get_xlim()[0]), min(xx, ax.get_xlim()[1])
            if xf > xs:
                ax.plot([xs, xf], [trade["stop"]]*2, color=color, lw=.8,
                        linestyle=(0, (3, 4)), alpha=.55, zorder=2)
        if within(ax, xx):
            size = 100 if shared_exit and letter == "A" else 55
            ax.scatter([xx], [px], s=size, marker=shape, facecolors="white",
                       edgecolors=color, linewidths=1.4, zorder=10)
            if not(shared_exit and letter == "A"):
                name = "A / B" if shared_exit else letter
                reason = {"target": "止盈", "stop": "止损", "timeout": "到期"}.get(trade["reason"], trade["reason"])
                label(ax, xx, px, f"{name} {reason}", color, dx=15,
                      dy=-23 if reason == "止盈" else 24)


def cancellation(ax, case):
    point = case["cancellation_reference"]
    if point is None:
        return
    if case["status"] == "invalid_geometry":
        x, y = date_num(point["entry_bar_open"]), point["entry_price_open"]
        text = "翻转出现 · 未入场"
    else:
        x, y = date_num(point["bar_open"]), point["target_price"]
        text = "目标先到 · 取消等待"
    if within(ax, x):
        ax.scatter([x], [y], marker="x", s=65, color=C["muted"], lw=1.8, zorder=11)
        label(ax, x, y, text, C["muted"], dx=15, dy=30)


def render(case):
    if case["validation"]["issues"]:
        raise ValueError(case["validation"]["issues"])
    h1 = pd.read_csv(ROOT / case["inputs"]["h1_csv"])
    m5 = pd.read_csv(ROOT / case["inputs"]["m5_csv"])
    fig = plt.figure(figsize=(16, 12.2), dpi=150, facecolor="white")
    side = "做多" if case["side"] == 1 else "做空"
    fig.text(.055, .956, f'{case["number"]:02d}   {case["symbol"]} · {side}',
             fontsize=23, fontweight="bold")
    fig.text(.945, .957, case["title"], fontsize=18, ha="right")
    fig.text(.055, .919, f'1h 信号确认：{pd.Timestamp(case["signal_time"]).tz_convert(TZ):%Y-%m-%d %H:%M}  ·  Binance 永续  ·  北京时间',
             fontsize=11, color=C["muted"])
    fig.add_artist(Line2D([.055, .945], [.892, .892], color=C["border"], lw=.8))
    for x, key, letter, name, color in [(.055, "old", "A", "原勾叉入场", C["a"]),
                                       (.39, "new", "B", "等 EMA 云带翻转", C["b"])]:
        row = case[key]
        fig.text(x, .863, f"{letter}  {name}", color=color, fontsize=12, fontweight="bold")
        if row:
            outcome = {"target": "止盈", "stop": "止损"}.get(row["reason"], row["reason"])
            fig.text(x, .827, f'{price(row["entry"])}   {row["net_r"]:+.2f} R · {outcome}',
                     fontsize=17, fontweight="bold", color=color)
        else:
            fig.text(x, .827, "未成交", fontsize=17, color=C["muted"], fontweight="bold")
    fig.text(.735, .863, "等待 / 入场时预期盈亏比", fontsize=11, color=C["muted"])
    if case["new"]:
        wait = f'{int(case["new"]["wait_minutes"])} 分钟'
        rr = f'{case["old"]["planned_rr"]:.2f} → {case["new"]["planned_rr"]:.2f}'
        fig.text(.735, .829, f"{wait}   /   {rr}", fontsize=14)
    else:
        fig.text(.735, .829, "本轮取消 · 无 B 收益", fontsize=14)

    fig.text(.055, .775, "1h  ·  信号与交易全程", fontsize=12, fontweight="bold")
    fig.text(.945, .775, "黑标 = 1h 信号   实心 = 入场   空心 = 退出   蓝/紫虚线 = 2% 止损", fontsize=9.5,
             color=C["muted"], ha="right")
    ax = fig.add_axes([.055, .451, .866, .301])
    old, new = case["old"], case["new"]
    extra = [old["stop"], old["target_price"]] + ([new["stop"]] if new else [])
    axis_style(ax, h1, 60, extra)
    # The pale band links the top context with the exact lower display window.
    ax.axvspan(date_num(m5.timestamp.iloc[0]), date_num(m5.timestamp.iloc[-1]),
               color="#f1f5f9", alpha=.6, zorder=0)
    ctx = case["level1618"]
    level(ax, ctx["level1618"]["active_outside_level"], "1.618 区间外边界", C["level"],
          date_num(ctx["levels_available_from_utc"]), date_num(ctx["next_monday_start_utc"]), True)
    level(ax, old["target_price"], f'目标 · 周一近侧边界  {price(old["target_price"])}', C["target"],
          date_num(ctx["levels_available_from_utc"]), date_num(ctx["next_monday_start_utc"]))
    sig = h1.loc[h1.timestamp.map(date_num).eq(date_num(case["signal_candle_time"]))].iloc[0]
    signal_y = sig.high if case["side"] == -1 else sig.low
    ax.scatter([date_num(case["signal_candle_time"])], [signal_y], marker="x" if case["side"] == -1 else "v",
               s=80, color=C["ink"], linewidths=1.5, zorder=8)
    trade_marks(ax, case)
    cancellation(ax, case)

    fig.text(.055, .394, "5m  ·  入场细节", fontsize=12, fontweight="bold")
    fig.text(.43, .394, "━ EMA20", color=C["fast"], fontsize=10)
    fig.text(.53, .394, "━ EMA50", color=C["slow"], fontsize=10)
    fig.text(.945, .394, "绿云 = EMA20 > EMA50   红云 = EMA20 < EMA50", fontsize=10,
             color=C["muted"], ha="right")
    detail = fig.add_axes([.055, .103, .866, .269])
    axis_style(detail, m5, 5, np.r_[m5.ema_fast, m5.ema_slow])
    xe = mdates.date2num(pd.to_datetime(m5.close_at, utc=True).dt.to_pydatetime())
    fast, slow = m5.ema_fast.to_numpy(), m5.ema_slow.to_numpy()
    detail.fill_between(xe, fast, slow, where=fast >= slow, interpolate=True,
                        color=C["green"], alpha=.8, zorder=1)
    detail.fill_between(xe, fast, slow, where=fast < slow, interpolate=True,
                        color=C["red"], alpha=.8, zorder=1)
    detail.plot(xe, fast, color=C["fast"], lw=1.1, zorder=4)
    detail.plot(xe, slow, color=C["slow"], lw=1.1, zorder=4)
    level(detail, old["target_price"], "目标", C["target"],
          date_num(case["signal_time"]), detail.get_xlim()[1])
    detail.axvline(date_num(case["signal_time"]), color=C["neutral"], ls=(0, (2, 4)), lw=.8)
    trade_marks(detail, case, detail=True)
    cancellation(detail, case)
    if case["status"] == "invalid_geometry":
        point = case["cancellation_reference"]
        level(detail, point["wick_stop_candidate"], "原信号影线止损参考", C["muted"],
              date_num(case["signal_time"]), date_num(point["entry_bar_open"]), True)

    fig.text(.055, .042, case["caption"], fontsize=12, fontweight="medium")
    fig.text(.055, .018, "说明性样例。1R = 入场价 2% 风险；净 R 含手续费和滑点，未计资金费。价格为回测基准；退出点标触发 5m 柱，EMA 标收盘时刻。",
             fontsize=8.5, color=C["muted"])
    name = f'{case["number"]:02d}_{case["symbol"]}_{case["role"]}.png'
    out = DIR / name
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return {"number": case["number"], "pair_id": case["pair_id"], "path": str(out),
            "width": 2400, "height": 1830, "bytes": out.stat().st_size,
            "net_r_a": old["net_r"], "net_r_b": new["net_r"] if new else None}


def main():
    font = setup()
    manifest = json.loads((DIR / "manifest.json").read_text())
    files = [render(case) for case in manifest["cases"]]
    archive = DIR / "EMA云带_6张白底复盘图.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for item in files:
            z.write(item["path"], Path(item["path"]).name)
    receipt = dict(files=files, archive=str(archive), font=font,
                   builder_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   stitch_screen="67f5325d32b945d9b3465d1efe97fc08",
                   source_returns="unchanged ledger", candle_time="bar open",
                   ema_time="bar close", exit_time="containing 5m bar open, not exact intrabar timestamp")
    (DIR / "delivery.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
