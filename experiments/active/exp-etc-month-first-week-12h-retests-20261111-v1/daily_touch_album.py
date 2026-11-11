"""Render a deterministic, outcome-blind year/side sample of twenty ETC daily first contacts."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, ticker
from matplotlib.collections import LineCollection, PolyCollection
import numpy as np
import pandas as pd

from touch_inventory import OUT, CACHE, MONTHS, bounds

DEST = OUT / "每日首触抽样20张"
SEED = 20261111
GRAY = "#898e99"
GREEN = "#30c95d"
INK = "#292d34"
BEIJING = "Asia/Shanghai"


def sample(daily):
    rng = np.random.default_rng(SEED)
    years = daily["开盘时间_北京"].str[:4]
    counts = years.value_counts().sort_index()
    quotas = counts * 20 / len(daily)
    sizes = quotas.astype(int)
    remainder = (quotas-sizes).sort_values(ascending=False, kind="stable")
    sizes.loc[remainder.index[:20-sizes.sum()]] += 1
    upper = daily["方向"].eq("上轨")
    upper_quotas = upper.groupby(years).sum() / counts * sizes
    upper_sizes = upper_quotas.astype(int)
    remainder = (upper_quotas-upper_sizes).sort_values(ascending=False, kind="stable")
    upper_target = round(upper.mean()*20)
    upper_sizes.loc[remainder.index[:upper_target-upper_sizes.sum()]] += 1
    chosen = []
    for year in sizes.index:
        for side, n in [(True,upper_sizes[year]),(False,sizes[year]-upper_sizes[year])]:
            candidates=daily.index[(years==year) & (upper==side)].to_numpy()
            chosen.extend(rng.choice(candidates,size=n,replace=False))
    return daily.loc[chosen].sort_values("开盘时间_北京")


def price(v):
    return f"{v:.6f}".rstrip("0").rstrip(".")


def panel(ax, candles, touch, month, levels, interval, title, show_near):
    clock = candles.index
    x = np.arange(len(clock))
    o,h,l,c = (candles[col].to_numpy() for col in ("open","high","low","close"))
    start,ready,end = bounds(month)
    colors=np.where((clock<ready)&(clock+interval>start), GREEN, GRAY)
    ax.set_title(title,loc="left",color=INK,fontsize=12,pad=9)
    ax.set_axisbelow(True)
    ax.set_xlim(-1,len(x)+.6)
    near=levels["首周高"] if levels["触价"]==levels["上 1.618"] else levels["首周低"]
    low,high=l.min(),h.max()
    if show_near and max(high,near)-min(low,near)<=1.8*(high-low):
        low,high=min(low,near),max(high,near)
    pad=max((high-low)*.11,levels["触价"]*.0004)
    ax.set_ylim(low-pad,high+pad)
    ax.grid(color="#d7d9de",linestyle=(0,(1,4)),linewidth=.65)
    ax.yaxis.tick_right(); ax.yaxis.set_major_locator(ticker.MaxNLocator(6))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v,_:price(v)))
    ax.tick_params(length=0,labelsize=9,pad=5,colors="#666b73")
    for spine in ax.spines.values(): spine.set_visible(False)
    left=max(-.5,(ready-clock[0])/interval-.5)
    right=min(len(x)-.5,(end-clock[0])/interval-.5)
    if left<right:
        ax.fill_between([left,right],levels["首周低"],levels["首周高"],color="#f4f4f5",zorder=0)
        for label in ("上 1.618","首周高","首周低","下 1.618"):
            v=levels[label]
            if ax.get_ylim()[0]<v<ax.get_ylim()[1]:
                ext="1.618" in label
                ax.hlines(v,left,right,colors=INK if ext else "#93969b",linewidth=1.05 if ext else .75,zorder=2)
                ax.annotate(f"{label}  {price(v)}",(right,v),xytext=(-3,3),textcoords="offset points",ha="right",va="bottom",fontsize=8,color=INK,backgroundcolor="white",zorder=5)
    ax.add_collection(LineCollection([[(i,a),(i,b)] for i,a,b in zip(x,l,h)],colors=colors,lw=.85,zorder=3))
    bodies=[[(i-.34,a),(i+.34,a),(i+.34,b),(i-.34,b)] for i,a,b in zip(x,o,c)]
    ax.add_collection(PolyCollection(bodies,facecolors=colors,edgecolors=colors,lw=.55,zorder=4))
    ix=clock.get_loc(touch.floor(interval))
    level=levels["触价"]
    assert l[ix]<=level<=h[ix]
    ax.plot(ix,level,marker="o",ms=8,mew=1.7,markerfacecolor="none",markeredgecolor="#bb4c28",zorder=6)
    bj=clock.tz_convert(BEIJING)
    ticks=np.flatnonzero((bj.minute==0)&((bj.hour%24==0) if show_near else (bj.hour%2==0)))
    ax.set_xticks(ticks,[d.strftime("%m/%d" if show_near else "%H:%M") for d in bj[ticks]])


def main():
    font_manager.fontManager.addfont("/System/Library/Fonts/Hiragino Sans GB.ttc")
    plt.rcParams.update({"font.family":"Hiragino Sans GB","axes.unicode_minus":False})
    DEST.mkdir(parents=True,exist_ok=True)
    daily=pd.read_csv(OUT/"每日首次触价.csv",dtype={"轨价":str})
    selected=sample(daily)
    weeks=pd.read_csv(OUT/"全部触价段.csv").drop_duplicates("月份").set_index("月份")
    a=np.concatenate([np.load(CACHE/(m+".npz"))["ohlc"] for m in MONTHS])
    bars=pd.DataFrame(a[:,1:5],index=pd.to_datetime(a[:,0],unit="ms",utc=True),columns=["open","high","low","close"])
    hourly=bars.resample("1h").agg(dict(open="first",high="max",low="min",close="last"))
    images=[]
    for i,(_,r) in enumerate(selected.iterrows(),1):
        month=r["月份"]; rail=float(r["轨价"]); day_id=r["日首触编号"];side=r["方向"]
        stamp=pd.Timestamp(r["开盘时间_北京"],tz=BEIJING).tz_convert("UTC")
        np.testing.assert_allclose(bars.loc[stamp].values,[r[k] for k in ["开","高","低","收"]],rtol=0,atol=1e-10)
        H,L=weeks.loc[month,["首周高","首周低"]]; w=H-L
        levels={"上 1.618":round(H+.618*w,6),"下 1.618":round(L-.618*w,6),"首周高":H,"首周低":L,"触价":rail}
        assert abs(levels["上 1.618" if side=="上轨" else "下 1.618"]-rail)<1e-9
        fig,axes=plt.subplots(2,1,figsize=(12,8.5),dpi=135,facecolor="white")
        fig.subplots_adjust(left=.03,right=.94,top=.85,bottom=.055,hspace=.37)
        fig.text(.03,.95,f"ETC / USDT 永续    ·    {day_id}",fontsize=18,color=INK)
        fig.text(.94,.953,f"{i:02d} / 20",fontsize=12,ha="right",color="#6b7078")
        fig.text(.03,.902,f"OKX    {r['开盘时间_北京']} 北京时间    ·    当日首触{side}  {r['轨价']}    ·    首周高 {price(H)}    首周低 {price(L)}",fontsize=11,color="#535962")
        center=stamp.floor("h")
        panel(axes[0],hourly.loc[center-pd.Timedelta(hours=72):center+pd.Timedelta(hours=72)],stamp,month,levels,pd.Timedelta(hours=1),"1 小时   ·   首触前后 3 天",True)
        panel(axes[1],bars.loc[stamp-pd.Timedelta(hours=5):stamp+pd.Timedelta(hours=5)],stamp,month,levels,pd.Timedelta(minutes=5),"5 分钟   ·   首触前后 5 小时",False)
        name=f"{i:02d}_{day_id}.png"
        fig.savefig(DEST/name,dpi=135);plt.close(fig)
        images.append(dict(number=i,id=day_id,source_id=r["编号"],time=r["开盘时间_北京"],side=side,rail=r["轨价"],file=name))
    assert len(images)==20 and len({k["time"][:10] for k in images})==20
    (DEST/"抽样清单.json").write_text(json.dumps(dict(seed=SEED,source_days=len(daily),images=images),ensure_ascii=False,indent=2)+"\n")
    print(len(images),selected["方向"].value_counts().to_dict(),images[0]["id"],images[-1]["id"])


if __name__=="__main__":
    main()
