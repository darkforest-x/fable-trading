"""Draw a reproducible, stratified sample of symbol/month/side reversals from native H1."""
import random

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as dates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager, ticker
from matplotlib.collections import LineCollection, PolyCollection

from all_contract_touches import DEST
from census12 import read_raw
from scan import ROOT, bounds

OUTPUT = ROOT / "output/month-first-week-sample-20261111"
SEED = 20261111
HOUR = pd.Timedelta(hours=1)
KEY = ["币安合约", "纽约区间月", "方向"]
TZ = "Asia/Shanghai"


def draw(row):
    symbol, month, side = (row[k] for k in KEY)
    raw = read_raw(symbol)
    a = pd.DataFrame(raw[:, 1:5], columns=["o", "h", "l", "c"],
                     index=pd.to_datetime(raw[:, 0], unit="ms", utc=True))
    anchor, ready, end = bounds(month)
    touch = pd.Timestamp(row["首次触价小时（北京）"], tz=TZ).tz_convert("UTC")
    elapsed = int(row["第几小时达到20%"])
    hit = touch + elapsed * HOUR
    week = a[(a.index >= anchor) & (a.index < ready)]
    assert week.index.equals(pd.date_range(anchor, ready, inclusive="left", freq="h"))
    hi, lo = week.h.max(), week.l.min()
    upper, lower = hi + .618 * (hi-lo), lo - .618 * (hi-lo)
    up = side == "上轨"
    rail, sign = (upper, -1) if up else (lower, 1)
    assert np.isclose(rail, row["轨价"], rtol=1e-10, atol=0)
    assert a.loc[touch, "l"] <= rail <= a.loc[touch, "h"]
    target = rail * (1 + sign * .2)
    future = a[(a.index > touch) & (a.index <= hit)]
    assert future.index.equals(pd.date_range(touch+HOUR, hit, freq="h"))
    crossed = future.l <= target if up else future.h >= target
    assert future.index[crossed][0] == hit
    # Place the target marker at a traded price even if that hour gapped across it.
    mark_price = np.clip(target, a.loc[hit, "l"], a.loc[hit, "h"])
    begin, finish = anchor.floor("12h")-24*HOUR, hit.ceil("12h")+72*HOUR
    window = a[(a.index >= begin) & (a.index < finish)]
    groups = window.resample("12h")
    bars = groups.agg({"o":"first", "h":"max", "l":"min", "c":"last"})[groups.size().eq(12)]
    reclaim = (bars.loc[touch.floor("12h"),"c"]-rail)*sign > 0
    assert reclaim == row["当根收回"]
    colors = []
    for t in bars.index:
        close = t + 12*HOUR
        start, confirmed, _ = bounds(close.tz_convert("America/New_York").strftime("%Y-%m"))
        first = (t <= start < close) or (t >= start and close <= confirmed)
        colors.append("#2bc957" if first else "#8b909a")
    centers = bars.index + 6*HOUR
    x = dates.date2num(centers)
    fig, ax = plt.subplots(figsize=(13.3, 6.2), dpi=120)
    fig.subplots_adjust(left=.022,right=.932,top=.855,bottom=.136)
    ink, mute = "#343a40", "#6b717b"
    ax.grid(color="#dde0e3", linewidth=.7, linestyle=(0,(1.2,3.5)), zorder=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0, labelcolor=mute, labelsize=10, pad=7)
    ax.yaxis.tick_right()
    ax.set_xlim(begin, finish)
    low, high = min(bars.l.min(), lo), max(bars.h.max(), hi)
    pad=(high-low)*.09
    ax.set_ylim(max(0,low-pad), high+pad)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(7))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda p,_: f"{p:,.5g}"))
    marks = pd.date_range(begin, finish, freq=f"{max(2,round((finish-begin).days/8))}D")
    ax.set_xticks(marks, [t.tz_convert(TZ).strftime("%m/%d") for t in marks])
    # Only the selected month's levels are shown. They expire when that NY month ends.
    line_end = min(end, finish)
    ax.fill_between([ready,line_end],lo,hi,color="#f4f4f5",zorder=-1)
    for name, p in [("上 1.618",upper),("首周高",hi),("首周低",lo),("下 1.618",lower)]:
        if p <= ax.get_ylim()[0] or p >= ax.get_ylim()[1]:
            continue
        color = ink if "1.618" in name else "#82858a"
        ax.hlines(p,ready,line_end,color=color,lw=.9,zorder=1)
        ax.annotate(f"{name}  {p:.5g}",(ready,p),xytext=(3,4),textcoords="offset points",
                    color=color, fontsize=9, zorder=5, bbox=dict(fc="white",ec="none",alpha=.8,pad=.5))
    ax.add_collection(LineCollection([[(i,l),(i,h)] for i,l,h in zip(x,bars.l,bars.h)],
                                      colors=colors,linewidths=.85,zorder=2))
    bodies=[[(i-.164,o),(i+.164,o),(i+.164,c),(i-.164,c)] for i,o,c in zip(x,bars.o,bars.c)]
    ax.add_collection(PolyCollection(bodies,facecolors=colors,edgecolors=colors,linewidths=.65,zorder=3))
    ax.scatter([touch.floor("12h")+6*HOUR],[rail],s=117,marker="s",facecolors="none",
               edgecolors="#c54443",lw=1.7,zorder=6)
    ax.scatter([hit.floor("12h")+6*HOUR],[mark_price],s=47,marker="D",color="#41699a",
               edgecolors="white",lw=.9,zorder=6)
    status = "当根已收回" if reclaim else "当根未收回"
    label = touch.tz_convert(TZ).strftime("%Y-%m-%d %H:%M")
    fig.text(.023,.947,f"{symbol} 永续合约  ·  12小时  ·  Binance",fontsize=17,color=ink)
    fig.text(.023,.897,f"{month} 月首周区间    {label} 触{side}    {status}",fontsize=11,color=mute)
    fig.text(.931,.942,"USDT",ha="right",fontsize=11,color=mute)
    days, hours = divmod(elapsed,24)
    distance = f"{days} 天 {hours} 小时" if hours else f"{days} 天"
    hit_date = hit.tz_convert(TZ).strftime("%m-%d %H:%M")
    fig.text(.023,.038,f"红框  当日首次触轨     蓝菱形  {hit_date} 首次反向达到 20%（相隔 {distance}）",color=ink,fontsize=10)
    fig.text(.931,.038,"绿 K 为月首周  ·  时间为北京",ha="right",color=mute,fontsize=10)
    filename=f"{symbol}_{month}.png"
    fig.savefig(OUTPUT/filename, facecolor="white")
    plt.close(fig)
    return filename


def main():
    font_manager.fontManager.addfont("/System/Library/Fonts/Hiragino Sans GB.ttc")
    plt.rcParams.update({"font.family":"Hiragino Sans GB","axes.unicode_minus":False})
    daily = pd.read_csv(DEST/"catalogue/所有合约_触轨后已反转至少20%.csv")
    grouped = daily.sort_values("首次触价小时（北京）").drop_duplicates(KEY).sort_values(KEY)
    rng = random.Random(SEED)
    selected = []
    for category, side, n in [("COIN","上轨",2),("COIN","下轨",2),("EQUITY",None,1),("COMMODITY",None,1)]:
        pool = grouped[grouped["标的类别"] == category]
        if side is not None:
            pool = pool[pool["方向"] == side]
        selected.extend(rng.sample(list(pool.index),n))
    sample = grouped.loc[selected].copy()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    sample["图"] = [draw(row) for _,row in sample.iterrows()]
    sample.to_csv(OUTPUT/"抽样明细.csv", index=False, encoding="utf-8-sig")
    print(sample[KEY+["图"]].to_string(index=False))


if __name__ == "__main__":
    main()
