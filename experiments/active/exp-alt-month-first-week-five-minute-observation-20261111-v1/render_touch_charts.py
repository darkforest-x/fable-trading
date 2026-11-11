"""Render the nineteen fully verified touch-bar reclaims from the frozen event ledger."""
from pathlib import Path
from decimal import Decimal
from zoneinfo import ZoneInfo
import zipfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, ticker, transforms
from matplotlib.collections import LineCollection, PolyCollection
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "output" / "month-first-week-5m-19"
STEP = 300_000
BEIJING = ZoneInfo("Asia/Shanghai")
INK, GRAY, GRID = "#25272b", "#898e99", "#d6d8dc"
font_manager.fontManager.addfont("/System/Library/Fonts/Hiragino Sans GB.ttc")
plt.rcParams.update({"font.family": "Hiragino Sans GB", "axes.unicode_minus": False})

def price(v):
    return format(Decimal(str(round(float(v), 10))), "f").rstrip("0").rstrip(".") if "." in str(Decimal(str(round(float(v), 10)))) else format(Decimal(str(round(float(v), 10))), "f")

def render(e, bars, number):
    stamp = pd.Timestamp(e.touch_5m_open_utc)
    ms = stamp.value // 1_000_000
    index = ms + np.arange(-124, 105) * STEP
    win = bars.loc[index]
    root = win.loc[ms]
    side_up = e.side == "upper"
    H, L, rail = e.first_week_high, e.first_week_low, e.outer_rail
    assert np.allclose(root[["open","high","low","close"]], [e.touch_5m_open,e.touch_5m_high,e.touch_5m_low,e.touch_5m_close], rtol=0, atol=1e-12)
    assert (root.high >= rail > root.close) if side_up else (root.low <= rail < root.close)
    x = np.arange(-124,105)
    barlo, barhi = win.low.min(), win.high.max()
    near, far = (H,L) if side_up else (L,H)
    lo, hi = min(barlo,near), max(barhi,near)
    span = hi-lo
    lo, hi = lo-.09*span, hi+.09*span
    if max(hi,far)-min(lo,far) <= 2.5*(barhi-barlo):
        lo, hi = min(lo,far-.04*span), max(hi,far+.04*span)
    fig = plt.figure(figsize=(17.6,9.1),dpi=125,facecolor="white")
    ax=fig.add_axes([.007,.066,.928,.85])
    ax.set(xlim=(-125,165),ylim=(lo,hi),axisbelow=True)
    ax.yaxis.tick_right()
    ax.tick_params(axis="both",colors="#777b81",labelsize=9,length=0,pad=8)
    for spine in ax.spines.values(): spine.set_visible(False)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=15,steps=[1,2,2.5,5,10]))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v,_:price(v)))
    clock = pd.to_datetime(index,unit="ms",utc=True).tz_convert(BEIJING)
    xt=np.flatnonzero((clock.minute==0)&(clock.hour%3==0))
    ax.set_xticks(x[xt], [t.strftime("%m-%d\n%H:%M") if i==0 or t.hour==0 else t.strftime("%H:%M") for i,t in enumerate(clock[xt])])
    ax.grid(color=GRID,linestyle=(0,(1,4)),linewidth=.65)
    ready=(e.first_week_ready_ms_derived-ms)/STEP
    start=max(-125,ready)
    midnight=stamp.tz_convert("America/New_York").normalize()
    expiry=(midnight.replace(day=1)+pd.offsets.MonthBegin(1)).value//1_000_000
    end=min(165,(expiry-ms)/STEP)
    if start<end:
        ax.fill_between([start,end],[L,L],[H,H],color="#f6f6f6",linewidth=0,zorder=0)
        for name,level,col,w in [("首周高",H,"#717171",.85),("首周低",L,"#717171",.85),("上 1.618",H+.618*(H-L),INK,1),("下 1.618",L-.618*(H-L),INK,1)]:
            if lo<=level<=hi:
                ax.hlines(level,start,end,color=col,linewidth=w,zorder=2)
                ax.annotate(f"{name}  {price(level)}",(min(134,end-2),level),xytext=(0,4),textcoords="offset points",ha="right",va="bottom",fontsize=9,color=col)
    o,h,l,c=[win[col].to_numpy() for col in ("open","high","low","close")]
    colors=np.where(index<e.first_week_ready_ms_derived,"#2ac85b",GRAY)
    ax.add_collection(LineCollection([[(i,a),(i,b)] for i,a,b in zip(x,l,h)],colors=colors,linewidths=.8,zorder=3))
    ax.add_collection(PolyCollection([[(i-.36,a),(i+.36,a),(i+.36,b),(i-.36,b)] for i,a,b in zip(x,o,c)],facecolors=colors,edgecolors=colors,linewidths=.55,zorder=4))
    ax.plot(0,rail,marker="o",markersize=5,markerfacecolor="none",markeredgecolor=INK,markeredgewidth=1,zorder=5)
    ax.axhline(c[-1],color=GRAY,linewidth=.55,linestyle=(0,(1,4)),zorder=1)
    ax.text(1.002,c[-1],price(c[-1]),transform=transforms.blended_transform_factory(ax.transAxes,ax.transData),va="center",ha="left",fontsize=8,color="white",bbox=dict(facecolor=GRAY,edgecolor="none",pad=2))
    local=stamp.tz_convert(BEIJING)
    fig.text(.013,.967,f"{e.symbol}USDT Perpetual Swap Contract · 5 · OKX",fontsize=15,color="#61656b")
    fig.text(.013,.933,f"月首周 1.618 V1.5    {e.month}      首周高  {price(H)}      首周低  {price(L)}",fontsize=9,color="#84888d")
    fig.text(.933,.966,"USDT",fontsize=10,ha="right",color=INK)
    ax.annotate(f"首触  {local:%H:%M}",(0,0),xycoords=ax.get_xaxis_transform(),xytext=(0,-3),textcoords="offset points",va="bottom",ha="center",fontsize=8,color=INK,bbox=dict(facecolor="white",edgecolor="none",pad=1.5))
    fig.text(.5,.009,f"{local:%Y-%m-%d} 北京时间    ·    {('上轨' if side_up else '下轨')}首触 K 线  {local:%H:%M}–{local+pd.Timedelta(minutes=5):%H:%M}    ·    收盘  {price(root.close)}",fontsize=9,ha="center",color="#61656b")
    dest=OUT/f"{number:02d}_{e.symbol}_{local:%Y-%m-%d_%H%M}_{'upper' if side_up else 'lower'}.png"
    fig.savefig(dest,dpi=125,pil_kwargs={"compress_level":3});plt.close(fig)
    return dest

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    ledger=pd.read_csv(HERE/"event_paths.csv")
    selected=ledger[(ledger.category=="A") & (ledger.touch_basis=="pre_h1_5m_history_verified_no_touch")].sort_values(["touch_5m_open_utc","symbol"])
    assert len(selected)==19 and selected.event_id.nunique()==19
    cache={}; files=[]
    for i,e in enumerate(selected.itertuples(),1):
        if e.symbol not in cache: cache[e.symbol]=pd.read_csv(ROOT/e.local_5m_file,usecols=["ts","open","high","low","close"]).set_index("ts",verify_integrity=True)
        files.append(render(e,cache[e.symbol],i))
    with zipfile.ZipFile(OUT/"19张月首周首触收回_5分钟K线.zip","w",compression=zipfile.ZIP_STORED) as archive:
        for f in files: archive.write(f,f.name)

if __name__=="__main__": main()
