"""Render the owner's annual BTC review as 60-bar landscape PNG archives.

Reuse the frozen portrait manifest and original five-minute OHLC, without
changing signal selection or trade outcomes. Each main panel contains exactly
60 real, contiguous 15m candles: normally 20 before the arrow and 39 after it;
the window shifts backward at the data boundary. Fixed reference lines use
the Monday range known at the selected touch. All visible Fib lines are named,
and both Monday boundaries are included in the price axis on every page.
These are review/reference drawings, not reconstructed historic Pine state.
No new market data, execution simulation or strategy parameters are introduced.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import zipfile

from yoyo.evaluation.btc_annual_signal_charts import ROOT, DEST as PORTRAIT, FONT, stamp

DEST = ROOT / "artifacts/btc_annual_signal_landscape60_20261021"
COLORS = {1: "#36C9A3", -1: "#F36C7A"}


def prepare():
    import pandas as pd
    import numpy as np
    from yoyo.evaluation.pepe_range_exits import aggregate
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals

    data = json.loads((PORTRAIT / "chart_data.json").read_text())
    five = pd.read_csv(ROOT / data["data"], float_precision="round_trip")
    five.index = pd.DatetimeIndex(pd.to_datetime(five.pop("ts"), unit="ms", utc=True)
                                 + pd.Timedelta(minutes=5))
    q = aggregate(five, 15)
    opened = q.index - pd.Timedelta(minutes=15)
    arrows = signals(q).to_numpy()
    grid, names, weeks, _ = monday_levels(q, [0, .5, .618, .705, 1.13, 1.27, 1.618],
                                         "America/New_York")
    assert len(data["charts"]) == 229
    for row in data["charts"]:
        i, j, e = (row[k] for k in ("signal_i", "touch_i", "entry_i"))
        lo = max(0, min(i - 20, len(q) - 60))
        hi = lo + 60
        assert lo <= min(i, j, e) <= max(i, j, e) < hi <= len(q)
        assert np.all(np.diff(q.index[lo:hi].asi8) == pd.Timedelta(minutes=15).value)
        assert opened[i] == pd.Timestamp(row["signal_time"])
        assert opened[j] == pd.Timestamp(row["touch_time"])
        assert q.index[e] == pd.Timestamp(row["entry_time"])
        assert int(arrows[i]) == row["side"]
        assert abs(q.close.iloc[e] - row["entry"]) < 1e-7
        assert abs(grid[j, names.index(row["key"])] - row["level"]) < 1e-7
        assert abs(grid[j, 0] - row["monday_low"]) < 1e-7
        assert abs(grid[j, 1] - row["monday_high"]) < 1e-7
        assert weeks[j] == row["monday_date"]
        row["candles"] = [dict(index=k,
            time=opened[k].tz_convert("Asia/Shanghai").isoformat(),
            ny_day=int(opened[k].tz_convert("America/New_York").dayofweek),
            arrow=int(arrows[k]), **{c: float(q.iloc[k][c]) for c in ("open", "high", "low", "close")})
            for k in range(lo, hi)]
        row["levels"] = [dict(key=name, price=float(grid[j, k])) for k, name in enumerate(names)]
        row.pop("grid", None)
        row["window_close"] = q.index[hi - 1].tz_convert("Asia/Shanghai").isoformat()
        row["bars_before"] = i - lo
        row["bars_after"] = hi - i - 1
        row["exit"]["outside_window"] = pd.Timestamp(row["exit"]["time"]) > q.index[hi - 1]
    data["chart_contract"] = "60 contiguous real 15m candles; landscape; labeled Monday/Fib touch-time reference levels."
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "chart_data.json").write_text(json.dumps(data, ensure_ascii=False, allow_nan=False))
    print("Prepared 229 unchanged signals; 60 contiguous 15m candles each", flush=True)


def label(key):
    ratio, direction = key.split(":")
    if float(ratio) == 0:
        return "周一低点" if direction == "up" else "周一高点"
    if float(ratio) == .5:
        return "Fib 0.5 · 中点"
    return f"Fib {ratio} {'↑' if direction == 'up' else '↓'}"


def spread_labels(levels, lower, upper):
    """Keep all named lines readable without changing their actual prices."""
    ordered = sorted(levels, key=lambda r: r["price"])
    sep = (upper - lower) * .031
    positions = []
    for row in ordered:
        positions.append(max(row["price"], positions[-1] + sep if positions else lower + sep))
    for k in range(len(positions) - 1, -1, -1):
        ceiling = upper - sep if k == len(positions) - 1 else positions[k + 1] - sep
        positions[k] = min(positions[k], ceiling)
    return list(zip(ordered, positions))


def render(limit=0, version=None, number=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, ticker
    from matplotlib.patches import Rectangle

    data = json.loads((DEST / "chart_data.json").read_text())
    font_manager.fontManager.addfont(FONT)
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=FONT).get_name(),
        "axes.unicode_minus": False, "figure.facecolor": "#000000", "axes.facecolor": "#000000",
        "savefig.facecolor": "#000000", "text.color": "#D9DADB", "font.size": 10})
    selected = [r for r in data["charts"] if (version is None or r["version"] == version)
                and (number is None or r["number"] == number)]
    if limit:
        selected = selected[:limit]
    manifest = []
    for r in selected:
        bars = r["candles"]
        assert len(bars) == 60
        ids = [b["index"] for b in bars]
        ai, ti, ei = (ids.index(r[k]) for k in ("signal_i", "touch_i", "entry_i"))
        side, col = r["side"], COLORS[r["side"]]
        low = min(r["monday_low"], r["stop"], min(b["low"] for b in bars))
        high = max(r["monday_high"], r["stop"], max(b["high"] for b in bars))
        span = high - low
        ymin, ymax = low - .08 * span, high + .08 * span
        fig = plt.figure(figsize=(16, 9), dpi=180)
        fig.text(.045, .952, f"BTCUSDT.P   {r['version']}   #{r['number']:03d} / {r['total']}   "
                 + ("多头" if side == 1 else "空头"), fontsize=19, color=col, va="top")
        fig.text(.955, .949, stamp(r["signal_time"], "%Y-%m-%d %H:%M")
                 + "  ·  15m × 60  ·  北京时间", fontsize=12, color="#C8CDD6", ha="right", va="top")
        fig.text(.045, .910, f"触及 {label(r['key'])}  {r['level']:,.1f}", fontsize=12, color="#FFED46")
        cohort = (f"对应 {r['other_version']} #{r['other_number']:03d}" if r["other_number"] else "仅此版本")
        fig.text(.955, .910, cohort + "  ·  黄色=触价K  /  E=入场K  /  ◇=实际退出", fontsize=9.6,
                 color="#929BA9", ha="right")
        ax = fig.add_axes([.055, .245, .90, .625])
        # Sixty candles occupy 0..59; the empty right margin is for line names.
        xmax = 75
        ax.set(xlim=(-1, xmax), ylim=(ymin, ymax))
        ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=8, steps=[1, 2, 2.5, 4, 5, 10]))
        ax.yaxis.set_major_formatter(ticker.StrMethodFormatter("{x:,.0f}"))
        ax.tick_params(axis="both", colors="#7F8793", length=0, labelsize=9, pad=8)
        ticks = list(range(0, 60, 6)) + [59]
        ax.set_xticks(ticks, [stamp(bars[x]["time"], "%m-%d\n%H:%M") for x in ticks])
        ax.grid(color="#15191E", linewidth=.5, linestyle=(0, (1, 5)))
        for spine in ax.spines.values():
            spine.set_visible(False)
        visible = [lv for lv in r["levels"] if ymin < lv["price"] < ymax]
        assert {"0:up", "0:down", r["key"]} <= {lv["key"] for lv in visible}
        named = []
        for lv, text_y in spread_labels(visible, ymin, ymax):
            key, price = lv["key"], lv["price"]
            lc = "#F23645" if key == "0:down" else "#3680FF" if key == "0:up" else "#737B88"
            if key == r["key"]:
                lc = "#FFED46"
            ax.hlines(price, -.5, 60.1, color=lc, lw=1.05 if key.startswith("0:") else .65,
                      alpha=1 if key.startswith("0:") or key == r["key"] else .6, zorder=1)
            ax.plot([60.1, 61.1], [price, text_y], color=lc, lw=.55, alpha=.85, zorder=2)
            text = ("● " if key == r["key"] else "") + label(key) + f"  {price:,.1f}"
            named.append(ax.text(61.4, text_y, text, color=lc, fontsize=9.5,
                                va="center", zorder=8, bbox=dict(facecolor="black", edgecolor="none", pad=1)))
        for x, b in enumerate(bars):
            cc = ("#FFED46" if x == ti else "#4CAF50" if b["ny_day"] == 0
                  else "#B2B5BE" if b["ny_day"] >= 5 else "#858995")
            ax.vlines(x, b["low"], b["high"], color=cc, lw=1 if x == ti else .8, zorder=3)
            b0, b1 = sorted((b["open"], b["close"]))
            if b1 - b0 < span * .00012:
                ax.hlines(b0, x - .33, x + .33, color=cc, lw=.8, zorder=3)
            else:
                ax.add_patch(Rectangle((x - .33, b0), .66, b1 - b0,
                             facecolor=cc, edgecolor=cc, lw=.6, zorder=3))
            if b["arrow"]:
                direction = b["arrow"]
                ax.annotate("✓" if direction == 1 else "×",
                    xy=(x, b["low"] if direction == 1 else b["high"]),
                    xytext=(0, -9 if direction == 1 else 9), textcoords="offset points",
                    color="#28D64A" if direction == 1 else "#F45362", fontsize=15 if x == ai else 11,
                    ha="center", va="center", alpha=1 if x == ai else .32, zorder=5)
        touch = bars[ti]
        extreme = touch["low"] if side == 1 else touch["high"]
        extreme_label = ax.annotate(f"{extreme:,.1f}", xy=(ti, extreme),
            xytext=(max(2, ti - 7), extreme - side * span * .07), textcoords="data",
            fontsize=10, color="#FFED46", va="center", ha="right",
            bbox=dict(facecolor="black", edgecolor="none", pad=2),
            arrowprops=dict(arrowstyle="->", color="#FFED46", lw=.7, shrinkA=6, shrinkB=11), zorder=9)
        ax.scatter([ti], [r["level"]], s=21, facecolors="#FFFFFF", edgecolors="#000000", lw=.7, zorder=9)
        entry_end = ei + 6.4
        if entry_end > 59.7:
            # Retain six-bar reference length at the study edge, going left.
            entry_start, entry_end = ei - 5.6, ei + .4
        else:
            entry_start = ei + .4
        ax.hlines(r["entry"], entry_start, entry_end, color=col, lw=.7, zorder=7)
        named.append(ax.text(entry_end + .5, r["entry"], f"{r['entry']:,.1f}", fontsize=8.3,
            va="center", color=col, zorder=9, bbox=dict(facecolor="black", edgecolor="none", pad=1)))
        ax.hlines(r["stop"], entry_start, entry_end, color="#F36C7A", lw=.55, linestyles=(0, (3, 3)), zorder=6)
        ax.text(ei, -.102, "E", transform=ax.get_xaxis_transform(), color=col, ha="center", fontsize=10)
        ex = r["exit"]
        exit_x = (datetime.fromisoformat(ex["time"]) - datetime.fromisoformat(bars[0]["time"])).total_seconds() / 900 - .5
        if -.5 <= exit_x <= 59.5:
            ax.scatter([exit_x], [ex["price"]], s=35, marker="D", facecolors="none",
                       edgecolors="#E5E8F0", lw=.9, zorder=10)
        fig.text(.045, .157, "勾叉K " + stamp(r["signal_time"]) + "    触价K " + stamp(r["touch_time"]), fontsize=10.3)
        fig.text(.045, .124, "入场K " + stamp(r["entry_bar_time"]) + " → " + stamp(r["entry_time"], "%H:%M")
                 + f" 收盘  {r['entry']:,.1f}    止损 {r['stop']:,.1f}", fontsize=10.3, color=col)
        fig.text(.045, .091, f"触价K  O {touch['open']:,.1f}   H {touch['high']:,.1f}   L {touch['low']:,.1f}   C {touch['close']:,.1f}",
                 fontsize=9.5, color="#9AA2AF")
        fig.text(.045, .059, f"影线 {r['wick']:.1f} 点    越过 {r['depth']:.2f} 点 / {r['penetration']:.1%}",
                 fontsize=9.5, color="#9AA2AF")
        reason = {1: "止损", 7: "分型确认后退出", 0: "期末结算"}[ex["reason"]]
        fig.text(.565, .157, reason + " " + stamp(ex["time"]) + f"  {ex['price']:,.1f}"
                 + ("（图外）" if ex["outside_window"] else ""), fontsize=10.2)
        fig.text(.565, .124, f"实际毛收益 {ex['gross_r']:+.2f}R   /   扣0.2%费用后 {ex['net_r']:+.2f}R", fontsize=10.2, color="#B3BAC5")
        pivot = ex["pivot"]
        fig.text(.565, .091, (f"局部极值 {pivot['price']:,.1f} / {pivot['reference_r']:.2f}R（回看参考，非成交价）"
                 if pivot else "真实退出按原账本保留；本次只改展示"), fontsize=9.5, color="#9AA2AF")
        fig.text(.565, .059, f"周一 {r['monday_date']} · ↑低点向上 / ↓高点向下 · 线旁名称为匹配周的关键位",
                 fontsize=9.1, color="#9AA2AF")
        fig.text(.045, .024, f"原始OHLC重绘 · 60根真实15mK · 信号前{r['bars_before']}根 / 后{r['bars_after']}根 · "
                 + "关键线按触价时已知周一区间作复盘参照 · 淡色勾叉仅为背景信号", fontsize=8.4, color="#6E7785")
        path = DEST / r["version"] / f"{r['number']:03d}_{stamp(r['signal_time'], '%Y%m%d_%H%M')}_{'long' if side == 1 else 'short'}_60bars.png"
        path.parent.mkdir(exist_ok=True)
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for text in [*fig.texts, *named]:
            box = text.get_window_extent(renderer)
            assert 0 <= box.x0 <= box.x1 <= fig.bbox.width, (r["version"], r["number"], text.get_text())
            assert 0 <= box.y0 <= box.y1 <= fig.bbox.height, (r["version"], r["number"], text.get_text())
        fig.savefig(path, dpi=180)
        plt.close(fig)
        manifest.append(dict(version=r["version"], number=r["number"], signal_time=r["signal_time"],
            key=r["key"], level=r["level"], other_number=r["other_number"], candle_count=60,
            labeled_levels=[lv["key"] for lv in visible], file=str(path.relative_to(DEST))))
        if len(manifest) % 25 == 0:
            print(f"Rendered {len(manifest)} / {len(selected)}", flush=True)
    if not any((limit, version, number)):
        assert len(manifest) == 229
        (DEST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Rendered {len(manifest)} landscape charts", flush=True)


def pack():
    from PIL import Image
    rows = json.loads((DEST / "manifest.json").read_text())
    archives = []
    for version, count in (("V2.30", 131), ("V2.21", 98)):
        selected = [r for r in rows if r["version"] == version]
        assert len(selected) == count
        path = DEST / f"BTC_{version}_{count}_landscape_60bars_PNG.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for r in selected:
                image = DEST / r["file"]
                assert r["candle_count"] == 60
                assert {r["key"], "0:up", "0:down"} <= set(r["labeled_levels"])
                with Image.open(image) as im:
                    assert im.size == (2880, 1620)
                    im.verify()
                z.write(image, image.name)
        with zipfile.ZipFile(path) as z:
            assert len(z.namelist()) == count and z.testzip() is None
        assert path.stat().st_size < 49_000_000, "Split the archive before Telegram upload"
        archives.append(dict(version=version, count=count, file=path.name, bytes=path.stat().st_size))
    receipt = dict(charts=229, bars_per_chart=60, dimensions=[2880, 1620],
        matched_key_and_both_monday_bounds_labeled=229, all_png_verified=True,
        entries_stops_and_exits_unchanged=True, archives=archives,
        training_eligible=False, production_eligible=False)
    (DEST / "validation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "render", "pack"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--version")
    parser.add_argument("--number", type=int)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
    elif args.mode == "render":
        render(args.limit, args.version, args.number)
    else:
        pack()
