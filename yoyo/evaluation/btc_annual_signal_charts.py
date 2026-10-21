"""Owner-requested annual BTC close-ups from frozen V2.21 / V2.30 ledgers.

Chart contract: reproduce the morning portrait/black OHLC charts, one image per
admitted signal, without return filtering. Read the frozen five-minute candles
and aggregate complete 15m bars with the existing builder. Admission, candidate
key, entry and stop are never recomputed or changed. Calendar colors and all
raw Stoch arrows provide context; yellow identifies the actual touch candle.
Post-entry candles and recorded exits are review context, never entry features.
Each price axis is local. PDF files only collect the same requested PNG images.

Preparation uses the existing Nautilus environment; rendering uses .venv with
Matplotlib, and PDF packing uses bundled ReportLab. Dependencies are unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "experiments/active/exp-range-v230-pivot-20261021-v1"
COMPARE = ROOT / "experiments/active/exp-range-v221-compare-20261021-v1"
DEST = ROOT / "artifacts/btc_annual_signal_charts_20261021"
FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def prepare():
    """Join frozen records to exact OHLC, grid and execution evidence by time."""
    import numpy as np
    import pandas as pd
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals
    from yoyo.evaluation.pepe_range_exits import aggregate

    config = json.loads((CURRENT / "config.json").read_text())
    data_path = CURRENT / "BTC/candles.csv.gz"
    five = pd.read_csv(data_path, float_precision="round_trip")
    five.index = pd.DatetimeIndex(pd.to_datetime(five.pop("ts"), unit="ms", utc=True)
                                 + pd.Timedelta(minutes=5))
    q = aggregate(five, 15)
    opened = q.index - pd.Timedelta(minutes=15)
    grid, names, weeks, _ = monday_levels(q, config["ratios"], config["timezone"])
    arrows = signals(q).to_numpy()
    sources = {"V2.30": CURRENT / "BTC",
               "V2.21": COMPARE / "v221_original/BTC"}
    matches = {v: pd.read_csv(p / "signal_matches.csv", float_precision="round_trip")
               for v, p in sources.items()}
    ids = {v: {(r.signal_time, int(r.side)): i + 1
               for i, r in m.iterrows()} for v, m in matches.items()}
    assert len(ids["V2.30"]) == 131 and len(ids["V2.21"]) == 98
    assert len(ids["V2.30"].keys() & ids["V2.21"].keys()) == 93
    charts = []
    for version, m in matches.items():
        trades = pd.read_csv(sources[version] / "native/pivot/trades.csv",
                             float_precision="round_trip")
        trades = trades.loc[trades.population.eq("signal")].set_index("pair_id")
        assert len(trades) == len(m) and trades.index.is_unique
        for index, match in m.iterrows():
            r = match.to_dict()
            i, j, e = (int(r[k]) for k in ("signal_i", "touch_i", "entry_i"))
            side = int(r["side"])
            trade = trades.loc[int(r.get("pair_id", index))]
            for k, pos in (("signal_time", i), ("touch_time", j)):
                assert pd.Timestamp(r[k]) == opened[pos], (version, index, k)
            assert pd.Timestamp(r["entry_time"]) == q.index[e]
            assert pd.Timestamp(trade.entry_time) == q.index[e]
            assert int(arrows[i]) == side
            assert abs(q.close.iloc[e] - r["entry"]) < 1e-7
            assert abs(trade.entry - r["entry"]) < 1e-7
            assert abs(trade.stop - r["stop"]) < 1e-7
            key_i = names.index(r["key"])
            assert abs(grid[j, key_i] - r["level"]) < 1e-7
            b = q.iloc[j]
            bodylo, bodyhi = min(b.open, b.close), max(b.open, b.close)
            wick = bodylo - b.low if side == 1 else b.high - bodyhi
            depth = r["level"] - b.low if side == 1 else b.high - r["level"]
            assert (b.low <= r["level"] < bodylo if side == 1
                    else bodyhi < r["level"] <= b.high)
            assert wick > 0 and depth / wick >= .1 - 1e-8
            # Same 9 bars before and 14 after as the morning renderer.
            lo, hi = max(0, min(i, j) - 9), min(len(q), max(i, j, e) + 15)
            candles = []
            for k in range(lo, hi):
                candles.append(dict(index=k,
                    time=opened[k].tz_convert("Asia/Shanghai").isoformat(),
                    ny_day=int(opened[k].tz_convert(config["timezone"]).dayofweek),
                    arrow=int(arrows[k]), **{c: float(q.iloc[k][c])
                        for c in ("open", "high", "low", "close")}))
            other = "V2.21" if version == "V2.30" else "V2.30"
            other_id = ids[other].get((r["signal_time"], side))
            reason = int(trade.reason)
            exit_time = pd.Timestamp(trade.exit_time).tz_convert("Asia/Shanghai")
            pivot = None
            if np.isfinite(trade.pivot_price):
                pt = pd.Timestamp(trade.pivot_open_time).tz_convert("Asia/Shanghai")
                pk = int(opened.get_indexer([pt])[0])
                assert pk >= 0
                assert abs(q.iloc[pk]["high" if side == 1 else "low"]
                           - trade.pivot_price) < 1e-7
                pivot = dict(time=pt.isoformat(), price=float(trade.pivot_price),
                             reference_r=float(trade.pivot_reference_r))
            charts.append(dict(
                version=version, number=index + 1, total=len(m),
                signal_i=i, touch_i=j, entry_i=e, side=side,
                signal_time=r["signal_time"], touch_time=r["touch_time"],
                entry_bar_time=opened[e].tz_convert("Asia/Shanghai").isoformat(),
                entry_time=r["entry_time"], entry=float(r["entry"]),
                stop=float(r["stop"]), key=r["key"], level=float(r["level"]),
                other_version=other, other_number=other_id,
                monday_date=str(weeks[j]), monday_high=float(grid[j, 1]),
                monday_low=float(grid[j, 0]), wick=float(wick), depth=float(depth),
                penetration=float(depth / wick), candles=candles,
                # Preserve historical weekly transitions instead of painting
                # the later completed range backwards over Monday candles.
                grid=[dict(key=name, values=[float(x) if np.isfinite(x) else None
                                            for x in grid[lo:hi, k]])
                      for k, name in enumerate(names)],
                exit=dict(time=exit_time.isoformat(), price=float(trade.exit_price),
                    reason=reason, gross_r=float(trade.gross_r), net_r=float(trade.net_r),
                    outside_window=bool(exit_time > q.index[hi - 1]), pivot=pivot),
                source=str((sources[version] / "signal_matches.csv").relative_to(ROOT))))
    DEST.mkdir(parents=True, exist_ok=True)
    payload = dict(start=config["start"], end=config["end"],
        data=str(data_path.relative_to(ROOT)), signal_counts={v: len(m) for v, m in matches.items()},
        shared=93, unique_events=136, charts=charts,
        omitted_context="Volume-profile lines omitted: frozen execution candles contain no volume.",
        chart_contract="Unchanged OHLC close-ups, all signals, exact matched key and entry; local scales.")
    (DEST / "chart_data.json").write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    print(json.dumps({k: v for k, v in payload.items() if k != "charts"}, ensure_ascii=False))


def stamp(s, fmt="%m-%d %H:%M"):
    from datetime import datetime
    return datetime.fromisoformat(s).strftime(fmt)


def key_name(key):
    ratio, direction = key.split(":")
    if float(ratio) == 0:
        return "周一低点" if direction == "up" else "周一高点"
    if float(ratio) == .5:
        return "Fib 0.5 · 区间中点"
    return "Fib " + ratio + " · " + ("低点向上" if direction == "up" else "高点向下")


def render(limit=0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, ticker
    from matplotlib.patches import Rectangle
    from datetime import datetime

    payload = json.loads((DEST / "chart_data.json").read_text())
    font_manager.fontManager.addfont(FONT)
    plt.rcParams.update({"font.family": font_manager.FontProperties(fname=FONT).get_name(),
        "axes.unicode_minus": False, "figure.facecolor": "#000000", "axes.facecolor": "#000000",
        "savefig.facecolor": "#000000", "text.color": "#D9DADB", "font.size": 10})
    output = []
    for r in payload["charts"][:limit or None]:
        folder = DEST / r["version"]
        folder.mkdir(exist_ok=True)
        bars = r["candles"]
        indices = [b["index"] for b in bars]
        ai, ti, ei = (indices.index(r[k]) for k in ("signal_i", "touch_i", "entry_i"))
        n, side, p = len(bars), r["side"], r["level"]
        col = "#36C9A3" if side == 1 else "#F36C7A"
        low = min(min(b["low"] for b in bars), r["stop"])
        high = max(max(b["high"] for b in bars), r["stop"])
        span = high - low
        fig = plt.figure(figsize=(6, 10), dpi=200)
        fig.text(.065, .965, f"{r['version']}  #{r['number']:03d} / {r['total']}   "
                 + ("多头" if side == 1 else "空头"), fontsize=16, color=col, va="top")
        fig.text(.065, .929, stamp(r["signal_time"], "%Y-%m-%d %H:%M")
                 + "   ·   OKX BTCUSDT.P · 15m · UTC+8", fontsize=9.3, color="#9DA1AC")
        cohort = (f"双版共有 · 对应 {r['other_version']} #{r['other_number']:03d}"
                  if r["other_number"] else "仅此版本")
        fig.text(.065, .902, cohort + "   /   黄色为触价K，E为入场K", fontsize=8.5, color="#9297A1")
        fig.text(.065, .872, key_name(r["key"]), fontsize=12, color="#EEEEEE")
        fig.text(.87, .872, f"{p:,.1f}", fontsize=13, color=col, ha="right")
        ax = fig.add_axes([.065, .305, .79, .535])
        ax.set(xlim=(-1, n - .1), ylim=(low - span * .16, high + span * .16))
        ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=7, steps=[1, 2, 2.5, 4, 5, 10]))
        ax.yaxis.set_major_formatter(ticker.StrMethodFormatter("{x:,.0f}"))
        ax.yaxis.tick_right()
        ax.tick_params(axis="both", colors="#7C7F88", length=0, labelsize=8, pad=7)
        ticks = list(range(1, n, 4))
        ax.set_xticks(ticks, [stamp(bars[x]["time"], "%H:%M") for x in ticks])
        ax.grid(axis="both", color="#151515", linewidth=.55, linestyle=(0, (1, 5)))
        for spine in ax.spines.values():
            spine.set_visible(False)
        for level in r["grid"]:
            k = level["key"]
            lc = "#2962FF" if k == "0:up" else "#F23645" if k == "0:down" else "#787B86"
            ax.plot(range(n), level["values"], color=lc, alpha=.85 if k.startswith("0:") else .5,
                    lw=.65, zorder=1)
        # The horizontal candidate price is explicitly the matched touch-time
        # value. This is distinct from the grid's historical time-varying lines.
        ax.hlines(p, max(-.5, ti - 3), n - .1, color=col, alpha=.85, lw=.65,
                  linestyles=(0, (3, 3)), zorder=2)
        for x, b in enumerate(bars):
            cc = ("#FFED46" if x == ti else "#4CAF50" if b["ny_day"] == 0
                  else "#B2B5BE" if b["ny_day"] >= 5 else "#787B86")
            ax.vlines(x, b["low"], b["high"], color=cc, lw=1.05 if x == ti else .9, zorder=3)
            b0, b1 = sorted((b["open"], b["close"]))
            if b1 - b0 < span * .0005:
                ax.hlines(b0, x - .34, x + .34, color=cc, lw=.9, zorder=3)
            else:
                ax.add_patch(Rectangle((x - .34, b0), .68, b1 - b0,
                             facecolor=cc, edgecolor=cc, lw=.65, zorder=3))
            if b["arrow"]:
                direction = b["arrow"]
                anchor = b["low"] if direction == 1 else b["high"]
                ax.annotate("✓" if direction == 1 else "×", xy=(x, anchor),
                    xytext=(0, -10 if direction == 1 else 9), textcoords="offset points",
                    color="#28D64A" if direction == 1 else "#F45362", fontsize=15,
                    ha="center", va="center", alpha=1 if x == ai else .4, zorder=6)
        b = bars[ti]
        extreme = b["low"] if side == 1 else b["high"]
        ax.annotate(f"{extreme:,.1f}", xy=(ti, extreme),
            xytext=(.02, .97 if side == -1 else .035), textcoords="axes fraction",
            fontsize=9, color="#FFED46", va="top" if side == -1 else "bottom",
            bbox=dict(facecolor="black", edgecolor="none", pad=2),
            arrowprops=dict(arrowstyle="->", color="#FFED46", lw=.7, shrinkA=5, shrinkB=12), zorder=8)
        ax.scatter([ti], [p], s=18, facecolors="#FFFFFF", edgecolors="#000000", lw=.65, zorder=9)
        ax.annotate(f"{p:,.1f}", xy=(n - .12, p), xytext=(5, 0), textcoords="offset points",
            color=col, fontsize=8.2, va="center", bbox=dict(facecolor="black", edgecolor="none", pad=2), zorder=9)
        ax.hlines(r["entry"], ei + .4, ei + 6.4, color=col, lw=.7, zorder=7)
        ax.text(ei + 6.65, r["entry"], f"{r['entry']:,.1f}", color=col, fontsize=7.4,
                va="center", bbox=dict(facecolor="black", edgecolor="none", pad=1), zorder=9)
        ax.hlines(r["stop"], ei + .4, ei + 6.4, color="#F36C7A", lw=.55,
                  linestyles=(0, (3, 3)), alpha=.8, zorder=6)
        ax.text(ei, -.075, "E", transform=ax.get_xaxis_transform(), color=col,
                fontsize=9, ha="center", weight="bold")
        ex = r["exit"]
        exit_x = ((datetime.fromisoformat(ex["time"]) - datetime.fromisoformat(bars[0]["time"]))
                  .total_seconds() / 900 - .5)
        if -.5 <= exit_x <= n - .5:
            ax.scatter([exit_x], [ex["price"]], s=27, marker="D", facecolors="none",
                       edgecolors="#E5E8F0", linewidths=.9, zorder=10)
        reason = {1: "止损", 7: "分型确认后退出"}.get(ex["reason"], "期末结算")
        fig.text(.065, .248, "勾叉K " + stamp(r["signal_time"]) + "   ·   触价K "
                 + stamp(r["touch_time"]), fontsize=9.3)
        fig.text(.065, .218, "入场K " + stamp(r["entry_bar_time"]) + " → "
                 + stamp(r["entry_time"], "%H:%M") + f" 收盘  {r['entry']:,.1f}", fontsize=9.3, color=col)
        fig.text(.065, .189, f"止损 {r['stop']:,.1f}   ·   影线 {r['wick']:.1f} 点"
                 + f"   ·   越过 {r['depth']:.2f} 点 / {r['penetration']:.1%}", fontsize=8.7, color="#B0B4BD")
        fig.text(.065, .160, f"开 {b['open']:,.1f}   高 {b['high']:,.1f}   低 {b['low']:,.1f}   收 {b['close']:,.1f}",
                 fontsize=8.2, color="#8D929B")
        fig.text(.065, .131, reason + " " + stamp(ex["time"]) + f"  {ex['price']:,.1f}"
                 + ("（图外）" if ex["outside_window"] else "  ◇"), fontsize=9, color="#C8CBD1")
        fig.text(.065, .103, f"实际毛收益 {ex['gross_r']:+.2f}R   /   扣0.2%费用后 {ex['net_r']:+.2f}R",
                 fontsize=9, color="#AEB3BD")
        if ex["pivot"]:
            pv = ex["pivot"]
            fig.text(.065, .077, f"局部极值 {pv['price']:,.1f} / {pv['reference_r']:.2f}R（回看参考，非成交价）",
                     fontsize=8.4, color="#848B95")
        else:
            fig.text(.065, .077, "止损按该版本原参数；图中后续K线只供复盘", fontsize=8.4, color="#848B95")
        fig.text(.065, .052, f"周一 {r['monday_date']}  高 {r['monday_high']:,.1f} / 低 {r['monday_low']:,.1f}",
                 fontsize=8.2, color="#737A85")
        fig.text(.065, .025, "原始OHLC重绘 · 非TradingView截图 · 局部纵轴 · 淡色勾叉仅为原始信号背景",
                 fontsize=7.3, color="#606772")
        name = f"{r['number']:03d}_{stamp(r['signal_time'], '%Y%m%d_%H%M')}_{'long' if side == 1 else 'short'}.png"
        path = folder / name
        fig.savefig(path, dpi=200)
        plt.close(fig)
        output.append(dict(version=r["version"], number=r["number"], signal_time=r["signal_time"],
            key=r["key"], level=r["level"], other_number=r["other_number"], file=str(path.relative_to(DEST))))
        if len(output) % 25 == 0:
            print(f"Rendered {len(output)} / {len(payload['charts'])}", flush=True)
    if not limit:
        assert len(output) == sum(payload["signal_counts"].values())
        (DEST / "manifest.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Completed {len(output)} images: {DEST}", flush=True)


def pack():
    """Collect the exact rendered PNGs, one unchanged image per PDF page."""
    from reportlab.pdfgen import canvas
    payload = json.loads((DEST / "chart_data.json").read_text())
    output = json.loads((DEST / "manifest.json").read_text())
    for version, count in payload["signal_counts"].items():
        selected = [r for r in output if r["version"] == version]
        assert len(selected) == count
        pdf = canvas.Canvas(str(DEST / f"BTC_{version}_all_{count}_signals.pdf"), pagesize=(432, 720))
        pdf.setTitle(f"BTCUSDT.P {version} - {count} annual signal close-ups")
        with zipfile.ZipFile(DEST / f"BTC_{version}_{count}_PNG.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for r in selected:
                image = DEST / r["file"]
                pdf.bookmarkPage(str(r["number"]))
                pdf.addOutlineEntry(f"{r['number']:03d}  {stamp(r['signal_time'], '%Y-%m-%d %H:%M')}", str(r["number"]))
                pdf.drawImage(str(image), 0, 0, width=432, height=720)
                pdf.showPage()
                archive.write(image, image.name)
        pdf.save()
        print(f"Packed {version}: {count} pages", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "render", "pack"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
    elif args.mode == "render":
        render(args.limit)
    else:
        pack()
