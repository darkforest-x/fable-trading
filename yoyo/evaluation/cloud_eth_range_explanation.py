"""Draw the source Monday range and hourly decision for the March 13 ETH short."""
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator, StrMethodFormatter
import numpy as np
import pandas as pd

from yoyo.evaluation.cloud_eth_simple_trade_charts import BG, GRID, AXIS, WHITE, ENTRY, STOP, draw_candles
from yoyo.evaluation.cloud_eth_week_charts import H, ROOT, SOURCE, STUDY, bjt
from yoyo.monitor.monday_fib import week_window


RANGE = "#83afd0"
OUT = ROOT / "output/eth-2026-03-13-1h-range"


def mark(ax, x, y, text, color, offset, align="left", marker="o"):
    ax.scatter(x, y, s=49, marker=marker, color=color, edgecolor=BG, linewidth=.9, zorder=7)
    ax.annotate(text, (x,y), xytext=offset, textcoords="offset points", fontsize=12,
                color=color, ha=align, va="center", linespacing=1.45, zorder=8,
                bbox=dict(boxstyle="round,pad=.30", fc=BG, ec="none", alpha=.95),
                arrowprops=dict(arrowstyle="-", color=color, lw=1.05, shrinkA=3, shrinkB=4))


def draw(hourly, trade, week, levels, detail):
    hi, lo, upper, lower = levels
    left = trade.key_ms - 5*H if detail else week["start"] - 4*H
    end = int(trade.exit_hour_ms)
    bars = hourly.loc[left:end-1]
    x = lambda ms: (ms-left)/H
    fig, ax = plt.subplots(figsize=(13,6.65), dpi=140, facecolor=BG)
    fig.subplots_adjust(left=.025, right=.835, bottom=.083, top=.95)
    ax.set_facecolor(BG)
    ax.spines[:].set_visible(False)
    ax.tick_params(length=0, pad=6, colors=AXIS, labelsize=9)
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.tick_params(axis="y", labelleft=False)
    ax.set_axisbelow(True)
    ax.grid(axis="y", lw=.55, color=GRID, ls=(0,(2,6)))
    draw_candles(ax,bars,left,H)
    n = len(bars)
    ax.set_xlim(-.18, n+.35)
    bottom = min(bars.low.min(), hi if detail else lower)
    top = max(bars.high.max(), upper if detail else hi)
    span = top-bottom
    ax.set_ylim(bottom-.07*span, top+(.26 if detail else .22)*span)
    times = list(range(int(trade.key_ms-5*H),end+1,H)) if detail else list(bars.index)
    if detail:
        times = [t for t in times if (t-trade.key_ms)//H in (-5,-2,0,3,6,9,12)]
    else:
        times = [t for t in times if bjt(t,"%H")=="00"]
        times = [week["start"],*times,end]
    ax.set_xticks([x(t) for t in times], [bjt(t,"%H:%M" if detail else "%m/%d %H时") for t in times])
    if detail:
        ax.get_xticklabels()[0].set_ha("left")
    else:
        # The first label is the start of New York Monday; the midnight tick is redundant.
        ax.get_xticklabels()[1].set_visible(False)
    ax.get_xticklabels()[-1].set_ha("right")
    fig.text(.037,.932, "ETH / USDT  ·  1小时", color=WHITE, size=21, weight="bold")
    fig.text(.038,.879, "3月13日  ·  上破、收回与开空" if detail else "3月9日至13日  ·  纽约周一区间", color=AXIS,size=13)
    fig.text(.972,.935,"北京时间", ha="right",color=AXIS,size=10)
    def line(price, name, color, start, style, end_x=None):
        ax.hlines(price,max(0,x(start)),n if end_x is None else end_x, colors=color,linewidth=1.3,
                  linestyles=style,zorder=2)
        ax.text(1.012,price,f"{name}   {price:,.2f}",transform=ax.get_yaxis_transform(),
                va="center",color=color,fontsize=12)
    if not detail:
        ax.add_patch(Rectangle((x(week["start"]),lo),24,hi-lo,
                               fc="#284357",ec=RANGE,lw=.8,alpha=.46,zorder=1))
        ax.text(x(week["start"])+12,hi+.025*span,"纽约周一 · 24小时",ha="center",color=RANGE,fontsize=11)
    line(upper,"上 1.618",ENTRY,week["end"],"-")
    line(hi,"周一高",RANGE,week["end"],(0,(5,3)))
    if detail:
        line(trade.stop,"开仓后止损",STOP,trade.entry_ms,(0,(2,3)))
        keybar=hourly.loc[trade.key_ms]
        prior=hourly.loc[trade.entry_ms-H]
        mark(ax,x(trade.key_ms)+.5,keybar.high,
             f'08–09时  上破 {keybar.high:,.2f}',ENTRY,(18,28))
        mark(ax,x(trade.entry_ms),trade.entry_px,
             f'10–11时  收回 {prior.close:,.2f}\n11:00  开空 {trade.entry_px:,.2f}',ENTRY,(-17,-48),"right")
        mark(ax,n,trade.exit_px,f'20:00  未确认，平空 {trade.exit_px:,.2f}',WHITE,(-10,42),"right","X")
    else:
        line(lo,"周一低",RANGE,week["end"],(0,(5,3)))
        line(lower,"下 1.618",ENTRY,week["end"],"-")
        mark(ax,x(trade.entry_ms),trade.entry_px,"3月13日 11:00  提前开空",ENTRY,(-28,48),"right")
    fig.savefig(OUT/("01-entry-1h.png" if detail else "02-week-range-1h.png"),dpi=155,facecolor=BG)
    plt.close(fig)


def main():
    font="/System/Library/Fonts/Hiragino Sans GB.ttc"
    font_manager.fontManager.addfont(font)
    mpl.rcParams.update({"font.family":font_manager.FontProperties(fname=font).get_name(),
                         "axes.unicode_minus":False})
    hourly=pd.read_csv(SOURCE/"bars1h.csv.gz",index_col="time",float_precision="round_trip")
    rows=pd.read_csv(STUDY/"trades.csv",float_precision="round_trip")
    group=rows[(rows.cohort=="early") & (rows.week=="2026-03-09")]
    assert len(group)==1
    t=next(group.itertuples())
    w=week_window(t.key_ms)
    m=hourly.loc[w["start"]:w["end"]-1]
    assert len(m)==24 and t.side==-1 and t.exit_reason=="scheduled_fail"
    hi,lo=m.high.max(),m.low.min()
    upper,lower=hi+.618*(hi-lo),lo-.618*(hi-lo)
    assert np.isclose(t.stop,upper*1.015) and hourly.at[t.entry_ms,"open"]==t.entry_px
    assert all(hourly.loc[t.key_ms:t.entry_ms-2*H,"close"]>upper)
    assert lower<hourly.at[t.entry_ms-H,"close"]<upper<hourly.at[t.key_ms,"high"]
    assert hourly.at[t.exit_hour_ms-H,"close"]>upper and hourly.at[t.exit_hour_ms,"open"]==t.exit_px
    OUT.mkdir(parents=True,exist_ok=True)
    for detail in (True,False):
        draw(hourly,t,w,(hi,lo,upper,lower),detail)


if __name__=="__main__":
    main()
