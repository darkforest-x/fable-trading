"""One full-bleed candlestick chart per early ETH trade, using frozen OKX fills.

Red and green areas run from the real entry to its native exit hour, towards
the original fixed stop and the opposite Monday 1.618 rail respectively.
An intrahour exit is placed at the middle of its known hour for display only.
The blue confirmation annotation refers only to the same 12H key and side.
"""
from pathlib import Path
from zipfile import ZipFile

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import PolyCollection
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator, StrMethodFormatter
from matplotlib.transforms import Bbox
import numpy as np
import pandas as pd

from yoyo.evaluation.cloud_eth_week_charts import H, SOURCE, STUDY, ROOT, bjt, exit_time
from yoyo.monitor.monday_fib import week_window


OUT = ROOT / "output/eth-v19-simple-10"
BG, GRID, AXIS = "#101419", "#242b32", "#a3abb5"
UP, DOWN = "#3ac6a0", "#ed6472"
ENTRY, CONFIRM, STOP, WHITE = "#ffce79", "#89bcff", "#ff9ca6", "#edf1f5"


def window(row, data, check):
    begin, end = row.entry_ms, max(exit_time(row), check)
    hours = (end-begin)/H
    period = 12 if hours > 290 else 4 if hours > 90 else 1
    step = H * period
    span = int(np.ceil(hours/period))
    before = max(7 if span < 7 else 15, int(span*.26))
    after = max(7, int(span*.10))
    left = min(row.key_ms - 3*step, begin - before*step) // step * step
    right = min(data.index.max() + H, (int(end)//step + 1 + after)*step)
    bars = data.loc[left:right-1].groupby(lambda ms: ms//step*step).agg(
        dict(open="first", high="max", low="min", close="last"))
    return bars, period, left, right


def draw_candles(ax, bars, origin, step):
    x = (bars.index.to_numpy()-origin)/step + .5
    colors = np.where(bars.close >= bars.open, UP, DOWN)
    ax.vlines(x, bars.low, bars.high, colors=colors, linewidth=.92, zorder=3)
    width = .31
    bodies = np.stack([np.c_[x-width,bars.open],np.c_[x+width,bars.open],
                       np.c_[x+width,bars.close],np.c_[x-width,bars.close]],axis=1)
    ax.add_collection(PolyCollection(bodies, facecolors=colors, edgecolors=colors,
                                     linewidths=.75, zorder=4))
    return x


def add_label(ax, xy, text, color, preferred, obstacles, reserved, marker):
    """Choose a nearby on-chart label position clear of the other labels and wicks."""
    fig = ax.figure
    renderer = fig.canvas.get_renderer()
    offsets = [(sx * dx, sy * dy) for dx,dy in ((13,19),(14,38),(30,54),(40,73),(65,29))
               for sx,sy in (preferred,(-preferred[0],preferred[1]),(preferred[0],-preferred[1]),
                             (-preferred[0],-preferred[1]))]
    options = []
    safe = ax.bbox.padded(-3)
    for i,(dx,dy) in enumerate(offsets):
        ha = "left" if dx>0 else "right"
        a = ax.annotate(text, xy, xytext=(dx,dy), textcoords="offset points",
                        ha=ha, va="center", fontsize=12.4, color=color)
        box = a.get_window_extent(renderer).expanded(1.07,1.45)
        a.remove()
        overlap = sum(box.overlaps(b) for b in reserved)
        clipped = not (safe.contains(box.x0,box.y0) and safe.contains(box.x1,box.y1))
        candles = sum(box.overlaps(b) for b in obstacles)
        options.append((10000*clipped + 800*overlap + 4*candles + i*.75, dx,dy,ha,box))
    _,dx,dy,ha,box = min(options,key=lambda o:o[0])
    reserved.append(box)
    ax.annotate(text,xy,xytext=(dx,dy),textcoords="offset points",ha=ha,va="center",
                fontsize=12.4, color=color, zorder=9,
                bbox=dict(boxstyle="round,pad=.22",fc=BG,ec="none",alpha=.95),
                arrowprops=dict(arrowstyle="-",color=color,lw=.95,shrinkA=2,shrinkB=3))
    if marker:
        ax.scatter(*xy,s=42,marker=marker,edgecolors=BG,facecolors=color,
                   linewidths=.8,zorder=10)


def draw(row, formal, hourly):
    side = row.side
    key = week_window(int(row.key_ms))
    monday = hourly.loc[key["start"]:key["end"]-1]
    assert len(monday)==24 and key["week"]==row.week
    hi, lo = monday.high.max(), monday.low.min()
    lower, upper = lo-.618*(hi-lo), hi+.618*(hi-lo)
    rail, target = (lower,upper) if side==1 else (upper,lower)
    assert np.isclose(rail*(1-.015*side),row.stop)
    same = formal is not None and formal.key_ms==row.key_ms and formal.side==side
    assert bool(row.matched)==same
    bars,period,left,right = window(row,hourly,formal.entry_ms if same else row.entry_ms)
    step = period*H
    x = lambda ms:(ms-left)/step
    fig = plt.figure(figsize=(13.6,7.6),dpi=130,facecolor=BG)
    ax = fig.add_axes([.012,.067,.922,.914],facecolor=BG)
    low,high = min(bars.low.min(),row.stop), max(bars.high.max(),row.stop)
    pad = max(high-low,row.entry_px*.004)
    ax.set_xlim(-.42, x(right)+.5)
    ax.set_ylim(low-.065*pad, high+.16*pad)
    ax.spines[:].set_visible(False)
    ax.set_axisbelow(True)
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    fmt = "%m/%d %H:%M" if right-left < 96*H else "%m/%d"
    ticks = np.unique(np.linspace(0,len(bars)-1,6).round().astype(int))
    ax.set_xticks(ticks+.5,[bjt(bars.index[i],fmt) for i in ticks])
    ax.get_xticklabels()[0].set_ha("left")
    ax.get_xticklabels()[-1].set_ha("right")
    ax.tick_params(length=0,pad=5,colors=AXIS,labelsize=9.8)
    ax.grid(axis="y",color=GRID,lw=.6,ls=(0,(2,5)))
    a,b = x(row.entry_ms),x(exit_time(row))
    assert b>a
    for price,fill,edge in ((row.stop,"#552931","#b95565"),(target,"#163f37","#308571")):
        ax.add_patch(Rectangle((a,min(row.entry_px,price)),b-a,abs(row.entry_px-price),
                               lw=.75,facecolor=fill,edgecolor=edge,zorder=1,alpha=.86))
    ax.hlines(row.entry_px,a,b,colors="#b8bec6",lw=.9,zorder=2)
    centers = draw_candles(ax,bars,left,step)
    direct = "多" if side==1 else "空"
    heading = ax.text(.014,.965,f"ETH / USDT   ·   做{direct}",transform=ax.transAxes,
                      va="top",color=WHITE,fontsize=21,weight="bold",zorder=9)
    subtitle = ax.text(.015,.907,f"{bjt(row.entry_ms,'%Y.%m.%d %H:%M')}   北京时间   ·   {period}小时K",
                       transform=ax.transAxes,va="top",color=AXIS,fontsize=11.3,zorder=9)
    fig.canvas.draw()
    reserved=[heading.get_window_extent().padded(6),subtitle.get_window_extent().padded(5)]
    lows = ax.transData.transform(np.c_[centers-.34,bars.low])
    highs = ax.transData.transform(np.c_[centers+.34,bars.high])
    obstacles=[Bbox.from_extents(*p,*q) for p,q in zip(lows,highs)]
    add_label(ax,(a,row.entry_px),f"提前开{direct}  {row.entry_px:,.2f}",ENTRY,
              (-1,side),obstacles,reserved,"o")
    reason = {"fixed_stop":"止损出场","data_end":"期末结算","scheduled_fail":"未确认出场",
              "cloud_flip":"云带反向出场"}[row.exit_reason]
    add_label(ax,(b,row.exit_px),f"{reason}  {row.exit_px:,.2f}",WHITE,
              (1,-side),obstacles,reserved,"X")
    if same:
        add_label(ax,(x(formal.entry_ms),formal.entry_px),f"12H确认  {formal.entry_px:,.2f}",CONFIRM,
                  (1,side),obstacles,reserved,"D")
    if row.exit_reason!="fixed_stop":
        add_label(ax,(a+(b-a)*.38,row.stop),f"止损  {row.stop:,.2f}",STOP,
                  (1,-side),obstacles,reserved,None)
    return fig


def main():
    font="/System/Library/Fonts/Hiragino Sans GB.ttc"
    font_manager.fontManager.addfont(font)
    mpl.rcParams.update({"font.family":font_manager.FontProperties(fname=font).get_name(),
                         "axes.unicode_minus":False})
    hourly=pd.read_csv(SOURCE/"bars1h.csv.gz",index_col="time",float_precision="round_trip")
    trades=pd.read_csv(STUDY/"trades.csv",float_precision="round_trip")
    early=trades[trades.cohort=="early"].sort_values("entry_ms",ascending=False).head(10)
    assert early.week.is_unique and len(early)==10
    confirmed={r.week:r for r in trades[trades.cohort=="confirmed"].itertuples()}
    OUT.mkdir(parents=True,exist_ok=True)
    with ZipFile(OUT/"ETH-10-PNG.zip","w") as archive:
        for i,r in enumerate(early.itertuples(),1):
            assert hourly.at[r.entry_ms,"open"]==r.entry_px
            bar=hourly.loc[r.exit_hour_ms]
            assert bar.low-1e-7<=r.exit_px<=bar.high+1e-7
            fig=draw(r,confirmed.get(r.week),hourly)
            path=OUT/f"{i:02d}_ETH_{bjt(r.entry_ms,'%Y-%m-%d')}.png"
            fig.savefig(path,dpi=140,facecolor=BG)
            plt.close(fig)
            archive.write(path,path.name)


if __name__=="__main__":
    main()
