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
        assert np.all(np.diff(q.index[lo:hi].as_unit("ns").asi8) == pd.Timedelta(minutes=15).value)
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
    if key.startswith("prior20:"):
        return "此前20根最低" if key.endswith("low") else "此前20根最高"
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


def execution_inset(fig, row):
    """Optional exact 5m bars for reviewing a native stop/target sequence.

    Candle centers are bar opens; fill markers sit at the execution bar's
    right edge. After-exit OHLC is faded review context, never an open trade.
    """
    from matplotlib.patches import Rectangle
    bars=row['execution_bars']
    ax=fig.add_axes([.565,.064,.39,.12])
    ids=[b['index'] for b in bars]
    entry=ids.index(row['entry_5m_i']);exitbar=ids.index(row['exit_5m_i'])
    lower=min(min(b['low'] for b in bars),row['target'],row['stop'])
    upper=max(max(b['high'] for b in bars),row['target'],row['stop'])
    span=upper-lower
    for i,b in enumerate(bars):
        color='#D0D3DA' if i<=exitbar else '#515764'
        ax.vlines(i,b['low'],b['high'],color=color,lw=.65)
        body=min(b['open'],b['close']);height=max(abs(b['close']-b['open']),span*.002)
        ax.add_patch(Rectangle((i-.28,body),.56,height,facecolor=color,edgecolor=color,lw=.3))
    for price,color,name in [(row['entry'],COLORS[row['side']],'E'),(row['stop'],'#F36C7A','SL'),(row['target'],'#36C9A3','1R')]:
        ax.hlines(price,entry+.5,len(bars)-.5,color=color,lw=.55,linestyles='dashed')
        ax.text(len(bars)-.2,price,name,color=color,fontsize=7,va='center')
    ax.axvline(entry+.5,color=COLORS[row['side']],lw=.65,linestyle=':')
    ax.scatter([entry+.5],[row['entry']],marker='^' if row['side']==1 else 'v',s=18,color=COLORS[row['side']],zorder=5)
    ax.scatter([exitbar+.5],[row['exit']['price']],marker='D',s=22,facecolors='none',edgecolors='white',lw=.8,zorder=5)
    ticks=sorted(set([entry,exitbar,len(bars)-1]))
    ax.set_xticks(ticks,[stamp(bars[i]['time'],'%H:%M') for i in ticks])
    ax.set(xlim=(-1,len(bars)+1),ylim=(lower-span*.1,upper+span*.1))
    ax.set_yticks([]);ax.tick_params(axis='x',length=0,labelsize=7,colors='#8993A1')
    for spine in ax.spines.values():spine.set_visible(False)
    ax.set_title('5m 执行放大 · E入场 / ◇止损 / 灰暗K为退出后走势',loc='left',fontsize=8.2,color='#ABB5C3',pad=5)


def render(limit=0, version=None, number=None, *, destination=None, source_data=None, trade_review=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, ticker
    from matplotlib.patches import Rectangle

    destination = DEST if destination is None else Path(destination)
    data = json.loads((destination / "chart_data.json").read_text()) if source_data is None else source_data
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
        precision=int(r.get('price_decimals',1))
        price=lambda value: f'{value:,.{precision}f}'
        low = min(r["monday_low"], r["stop"], min(b["low"] for b in bars))
        high = max(r["monday_high"], r["stop"], max(b["high"] for b in bars))
        if r.get('target') is not None:
            low=min(low,r['target']);high=max(high,r['target'])
        span = high - low
        ymin, ymax = low - .08 * span, high + .08 * span
        fig = plt.figure(figsize=(16, 9), dpi=180)
        fig.text(.045, .952, f"{r.get('symbol','BTC')}USDT.P   {r.get('display_version',r['version'])}   #{r['number']:03d} / {r['total']}   "
                 + ("多头" if side == 1 else "空头"), fontsize=19, color=col, va="top")
        fig.text(.955, .949, stamp(r["signal_time"], "%Y-%m-%d %H:%M")
                 + "  ·  15m × 60  ·  北京时间", fontsize=12, color="#C8CDD6", ha="right", va="top")
        fig.text(.045, .910, f"触及 {label(r['key'])}  {price(r['level'])}", fontsize=12, color="#FFED46")
        cohort = (f"对应 {r['other_version']} #{r['other_number']:03d}" if r["other_number"] else "仅此版本")
        legend = "黄色=触价K  /  入场、出场均为实际成交" if trade_review else cohort + "  ·  黄色=触价K  /  E=入场K  /  ◇=实际退出"
        if r.get('review_group'):
            legend = r['review_group'] + f"   ·   净 {r['exit']['net_r']:+.2f}R   ·   黄色=触价K"
        fig.text(.955, .910, legend, fontsize=9.6,
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
            key, line_price = lv["key"], lv["price"]
            lc = "#F23645" if key == "0:down" else "#3680FF" if key == "0:up" else "#737B88"
            if key.startswith('prior20:'):
                lc = '#AB9DDB'
            if key == r["key"] and not key.startswith("0:"):
                lc = "#FFED46"
            ax.hlines(line_price, -.5, 60.1, color=lc, lw=1.05 if key.startswith("0:") else .65,
                      linestyles=(0, (4, 3)) if key.startswith('prior20:') else 'solid',
                      alpha=1 if key.startswith("0:") or key == r["key"] else .6, zorder=1)
            ax.plot([60.1, 61.1], [line_price, text_y], color=lc, lw=.55, alpha=.85, zorder=2)
            text = ("● " if key == r["key"] else "") + label(key) + f"  {price(line_price)}"
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
        extreme_label = ax.annotate(price(extreme), xy=(ti, extreme),
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
        named.append(ax.text(entry_end + .5, r["entry"], price(r['entry']), fontsize=8.3,
            va="center", color=col, zorder=9, bbox=dict(facecolor="black", edgecolor="none", pad=1)))
        ax.hlines(r["stop"], entry_start, entry_end, color="#F36C7A", lw=.55, linestyles=(0, (3, 3)), zorder=6)
        ax.text(ei, -.102, "E", transform=ax.get_xaxis_transform(), color=col, ha="center", fontsize=10)
        ex = r["exit"]
        exit_x = (datetime.fromisoformat(ex["time"]) - datetime.fromisoformat(bars[0]["time"])).total_seconds() / 900 - .5
        if -.5 <= exit_x <= 59.5:
            ax.scatter([exit_x], [ex["price"]], s=35, marker="D", facecolors="none",
                       edgecolors="#E5E8F0", lw=.9, zorder=10)
        if r.get('target') is not None:
            ax.hlines(r['target'],ei+.5,entry_end,color='#36C9A3',lw=.6,linestyles='dashed',zorder=6)
            named.append(ax.text(entry_end+.5,r['target'],'1R '+price(r['target']),fontsize=8.3,
                color='#36C9A3',va='center',bbox=dict(facecolor='black',edgecolor='none',pad=1),zorder=9))
        if trade_review:
            # Both markers sit at the right edge of the candle whose close
            # generated the actual fill; axis timestamps label candle opens.
            entry_x = ei + .5
            ax.scatter([entry_x], [r['entry']], s=32, marker='v' if side == -1 else '^',
                       color=col, edgecolors='black', linewidths=.5, zorder=10)
            named.append(ax.annotate('入场', xy=(entry_x,r['entry']),
                xytext=(max(2,ei-(14 if r.get('separate_entry_callout') else 9)),r['entry']-side*span*.13), fontsize=12, color=col,
                ha='center', va='center', bbox=dict(facecolor='black',edgecolor='none',pad=3),
                arrowprops=dict(arrowstyle='->',color=col,lw=.85,shrinkA=7,shrinkB=8),zorder=11))
            named.append(ax.annotate(f"{ex.get('label','出场')}  {price(ex['price'])}",xy=(exit_x,ex['price']),
                xytext=(min(51,exit_x+9),ex['price']+side*span*.13),fontsize=12,color='#E5E8F0',
                ha='center',va='center',bbox=dict(facecolor='black',edgecolor='none',pad=3),
                arrowprops=dict(arrowstyle='->',color='#E5E8F0',lw=.85,shrinkA=7,shrinkB=8),zorder=11))
        fig.text(.045, .157, "勾叉K " + stamp(r["signal_time"]) + "    触价K " + stamp(r["touch_time"]), fontsize=10.3)
        fig.text(.045, .124, "入场K " + stamp(r["entry_bar_time"]) + " → " + stamp(r["entry_time"], "%H:%M")
                 + f" 收盘  {price(r['entry'])}    止损 {price(r['stop'])}", fontsize=10.3, color=col)
        fig.text(.045, .091, f"触价K  O {price(touch['open'])}   H {price(touch['high'])}   L {price(touch['low'])}   C {price(touch['close'])}",
                 fontsize=9.5, color="#9AA2AF")
        detail = f"影线 {r['wick']:.1f} 点    越过 {r['depth']:.2f} 点 / {r['penetration']:.1%}"
        if r.get('mtf_arrow_time'):
            detail += '    多周期信号收盘 ' + stamp(r['mtf_arrow_time'], '%H:%M')
        fig.text(.045, .059, detail,
                 fontsize=9.5, color="#9AA2AF")
        reason = {1: "止损", 7: "分型确认后退出", 0: "期末结算"}[ex["reason"]]
        if r.get('execution_bars'):
            execution_inset(fig,r)
            fig.text(.565,.218,'止损K收盘 '+stamp(ex['time'])+'  '+price(ex['price'])
                +f"  ·  毛 {ex['gross_r']:+.2f}R / 净 {ex['net_r']:+.2f}R",fontsize=10,color='#F36C7A')
            fig.text(.565,.031,'5m OHLC模拟；时间标记执行K收盘，非逐笔成交秒。'+
                ('本笔同5m双触，按O-H-L-C顺序。' if r.get('ambiguous') else ''),fontsize=7.7,color='#8993A1')
        else:
            fig.text(.565, .157, reason + " " + stamp(ex["time"]) + f"  {ex['price']:,.1f}"
                     + ("（图外）" if ex["outside_window"] else ""), fontsize=10.2)
        payoff = f"实际毛收益 {ex['gross_r']:+.2f}R   /   扣0.2%费用后 {ex['net_r']:+.2f}R"
        if r.get('review_group'):
            payoff = f"毛 {ex['gross_r']:+.2f}R  −  费用 {ex['gross_r']-ex['net_r']:.2f}R  =  净 {ex['net_r']:+.2f}R"
        if not r.get('execution_bars'):
            fig.text(.565, .124, payoff, fontsize=10.2, color="#B3BAC5")
        pivot = ex["pivot"]
        if not r.get('execution_bars'):
            fig.text(.565, .091, (f"局部极值 {pivot['price']:,.1f} / {pivot['reference_r']:.2f}R（回看参考，非成交价）"
                     if pivot else "真实退出按原账本保留；本次只改展示"), fontsize=9.5, color="#9AA2AF")
            fig.text(.565, .059, f"周一 {r['monday_date']} · ↑低点向上 / ↓高点向下 · 线旁名称为匹配周的关键位",
                     fontsize=9.1, color="#9AA2AF")
        footer=(f"周一 {r['monday_date']} · 关键线固定为触价时已知区间 · 淡色勾叉为背景信号" if r.get('execution_bars') else
            f"原始OHLC重绘 · 60根真实15mK · 信号前{r['bars_before']}根 / 后{r['bars_after']}根 · 关键线按触价时已知周一区间作复盘参照 · 淡色勾叉仅为背景信号")
        fig.text(.045, .024,footer,fontsize=8.4,color="#6E7785")
        path = destination / r["version"] / f"{r['number']:03d}_{stamp(r['signal_time'], '%Y%m%d_%H%M')}_{'long' if side == 1 else 'short'}_60bars.png"
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
            labeled_levels=[lv["key"] for lv in visible], file=str(path.relative_to(destination))))
        if len(manifest) % 25 == 0:
            print(f"Rendered {len(manifest)} / {len(selected)}", flush=True)
    if not any((limit, version, number)):
        assert len(manifest) == (229 if source_data is None else len(data['charts']))
        (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
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


def send():
    """Deliver only the owner-requested ZIPs to the verified existing TG target.

    Bot API sendDocument returns a Message. Persist pending before upload and
    never repeat an uncertain request. Tokens stay in the existing loader and
    never enter command arguments, logs or receipts. This is a manual upload,
    with no change to the disabled production notification worker.
    Reference: https://core.telegram.org/bots/api#senddocument
    """
    import requests
    from yoyo.notify import _load

    check = json.loads((DEST / "validation.json").read_text())
    assert check["charts"] == 229 and check["bars_per_chart"] == 60
    creds = _load()
    if creds is None:
        raise RuntimeError("Configured Telegram credentials missing")
    token, chat = creds
    url = "https://api.telegram.org/bot" + token + "/"
    try:
        response = requests.post(url + "getChat", json={"chat_id": chat}, timeout=(10, 20)).json()
    except Exception as exc:
        raise RuntimeError("getChat failed: " + type(exc).__name__) from None
    if not response.get("ok"):
        raise RuntimeError("getChat rejected")
    target = response["result"]
    if target.get("title") != "Yolo均线密集交易系统" or target.get("type") not in ("channel", "supergroup", "group"):
        raise RuntimeError("Configured Telegram target differs from verified owner destination")
    path = DEST / "telegram_receipt.json"
    receipt = json.loads(path.read_text()) if path.exists() else dict(
        target_id=target["id"], target_title=target["title"], actions=[], complete=False)
    assert receipt["target_id"] == target["id"]

    def save():
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        temporary.replace(path)

    for row in check["archives"]:
        archive = DEST / row["file"]
        assert archive.stat().st_size == row["bytes"] < 49_000_000
        previous = next((a for a in receipt["actions"] if a["file"] == row["file"]), None)
        if previous:
            if previous["status"] == "sent" and previous["bytes"] == row["bytes"]:
                continue
            raise RuntimeError("Previous upload is uncertain or rejected; no automatic resend")
        action = dict(file=row["file"], bytes=row["bytes"], status="pending")
        receipt["actions"].append(action)
        save()
        caption = (f"BTCUSDT.P {row['version']}｜{row['count']}个年度信号｜60根15m横图\n"
                   "2025-10-21 至 2026-10-21 00:00（北京时间）。\n"
                   "2880×1620原尺寸PNG。每图60根真实K线，关键线注明Fib比例、方向和价格；"
                   "周一高点红线、周一低点蓝线均完整显示。黄色为触价K，E为入场K。\n"
                   "信号编号及原入场、止损、退出保持不变；两版共有信号有对应编号。")
        try:
            with archive.open("rb") as handle:
                result = requests.post(url + "sendDocument", data={"chat_id": chat, "caption": caption},
                    files={"document": (archive.name, handle, "application/zip")}, timeout=(15, 120)).json()
        except Exception as exc:
            action.update(status="unknown", error_type=type(exc).__name__)
            save()
            raise RuntimeError("Upload response uncertain; no automatic resend") from None
        if not result.get("ok"):
            action.update(status="rejected", error_code=result.get("error_code"))
            save()
            raise RuntimeError("Telegram rejected upload, code " + str(result.get("error_code")))
        message = result["result"]
        doc = message.get("document", {})
        if (message["chat"]["id"] != target["id"] or doc.get("file_name") != archive.name
                or doc.get("file_size") != row["bytes"]):
            action["status"] = "unknown"
            save()
            raise RuntimeError("Telegram receipt does not match upload; no resend")
        username = target.get("username")
        link = (f"https://t.me/{username}/{message['message_id']}" if username else
                f"https://t.me/c/{str(target['id']).removeprefix('-100')}/{message['message_id']}")
        action.update(status="sent", message_id=message["message_id"], message_url=link,
                      telegram_file_size=doc["file_size"], sent_at=message["date"])
        save()
        print(json.dumps({k: action[k] for k in ("file", "status", "message_id", "message_url")}), flush=True)
    receipt["complete"] = True
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "render", "pack", "send"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--version")
    parser.add_argument("--number", type=int)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
    elif args.mode == "render":
        render(args.limit, args.version, args.number)
    elif args.mode == "pack":
        pack()
    else:
        send()
