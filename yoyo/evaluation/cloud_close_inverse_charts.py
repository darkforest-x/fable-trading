"""Plot the causal entry snapshot and the actual native fills of four frozen OKX positions."""
from datetime import timezone

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_inverse_examples as book
from yoyo.monitor.cloud_signals import ema_points
from yoyo.monitor.cloud_signal_plans import stop_pct_for
from yoyo.monitor.monday_fib import week_window

HOUR = 3600000
STEP = HOUR * 12
INK, GRAY, GRID = "#182a36", "#4c626d", "#dce5e8"
BLUE, ORANGE, VIOLET, TEAL, RED = "#2164a8", "#9e601d", "#7056a1", "#047d71", "#ad4050"


def bjt(ms, fmt):
    return pd.Timestamp(int(ms), unit="ms", tz=timezone.utc).tz_convert("Asia/Shanghai").strftime(fmt)


def px(value):
    return f"{value:.6g}"


def axes_style(ax):
    ax.spines[["left", "top", "right"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=GRAY, length=0, pad=6, labelsize=9)
    ax.grid(axis="y", color=GRID, lw=.7)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda n, _: f"{n:.5g}"))


def draw(position, path, fills, source, eth_btc_ret):
    entry, end = int(position.entry_ms), int(position.exit_bar_ms)
    key = entry - STEP
    hourly, bars = book.original.parent.repair.market.frames(source)
    wk = week_window(key)
    mon = hourly.loc[wk["start"]:wk["end"]-1]
    assert len(mon) == 24 and wk["end"] <= key
    lo, hi = mon.low.min(), mon.high.max()
    lower, upper = lo - .618*(hi-lo), hi + .618*(hi-lo)
    is_long = position.side == 1
    boundary = lower if is_long else upper
    assert np.isclose(boundary*(1-position.side*stop_pct_for(position.symbol)), path.stop_price)
    ema = pd.DataFrame(ema_points(bars.loc[:key].reset_index().to_dict("records"))).set_index("time")
    hist = bars.loc[:key].tail(17).join(ema[["ema20","ema50"]])
    current = hist.iloc[-1]
    assert current.low < lower if is_long else current.high > upper
    assert lower < current.close < upper and (current.ema20-current.ema50)*position.side > 0
    assert eth_btc_ret*position.side < 0 and np.isclose(hourly.at[entry, "open"], path.entry_price)
    grouped = (fills.assign(price=fills.fill_price.round(10))
               .groupby(["hour_ms", "action", "price"], as_index=False).net_units.sum())
    entries = grouped[grouped.action.eq("entry")]
    assert len(entries) == 1 and np.isclose(entries.price.iat[0], path.entry_price)
    final = grouped[grouped.hour_ms.eq(end) & grouped.action.str.startswith(("ema_flip", "stop"))].iloc[-1]
    ending = "硬止损" if final.action.startswith("stop") else "云带反转"
    level_name = "下轨" if is_long else "上轨"
    side_name = "多单" if is_long else "空单"
    pnl = position.cash_pnl
    status = "盈利" if pnl > 0 else "亏损"
    fig = plt.figure(figsize=(12.8, 6.2), facecolor="white")
    left, right = fig.subplots(1, 2, gridspec_kw={"width_ratios":[1, 1.13]})
    fig.subplots_adjust(left=.074, right=.975, bottom=.224, top=.689, wspace=.19)
    fig.text(.055, .924, f"{position.symbol}   /   {status}{side_name}", fontsize=24, color=INK, weight="bold")
    fig.text(.055, .859, f"OKX 永续   ·   {bjt(entry, '%Y-%m-%d %H:%M')} 北京时间进场   ·   ETH/BTC 近 30 天 {eth_btc_ret:+.2%}", fontsize=11, color=GRAY)
    fig.text(.972, .923, f"{pnl:+,.2f} U", fontsize=23, color=TEAL if pnl>0 else RED, ha="right", weight="bold")
    fig.text(.972, .859, "本笔净盈亏（含交易手续费）", fontsize=10, color=GRAY, ha="right")
    fig.add_artist(Line2D([.055,.975],[.815,.815],transform=fig.transFigure,color=GRID,lw=1))
    for ax,title in [(left, "进场时可见的 12H K 线"), (right, "进场后的价格与实际成交")]:
        axes_style(ax)
        ax.set_title(title, loc="left", fontsize=13, color=INK, weight="bold", pad=28)
    n=len(hist)
    x=np.arange(n)
    for i,r in enumerate(hist.itertuples()):
        color=TEAL if r.close>=r.open else RED
        left.vlines(i, r.low, r.high, color=color, lw=1.05)
        left.add_patch(Rectangle((i-.27,min(r.open,r.close)),.54,abs(r.close-r.open),fc=color,ec=color,lw=.7))
    left.plot(x,hist.ema20,c=BLUE,lw=1.2,label="EMA20")
    left.plot(x,hist.ema50,c=ORANGE,lw=1.2,label="EMA50")
    left.fill_between(x,hist.ema20,hist.ema50,color="#73899a",alpha=.12)
    left.axvspan(n-1-.47,n-1+.47,color="#e5daae",alpha=.5,zorder=0)
    left.scatter(n-1,current.close,marker="o",s=32,fc="white",ec=INK,zorder=6,label="信号收盘")
    # Candle centres represent the close; draw weekly levels only after their inputs closed.
    anchor_x = n-1-(entry-wk["end"])/STEP
    for lev in [lower,upper]:
        left.hlines(lev,max(-.5,anchor_x),n-1+.4,color=VIOLET,linestyles="--",lw=1.1)
    left.set_xlim(-.8,n-1+.9)
    left.autoscale_view(scalex=False)
    left.set_xticks([0,4,8,12,16], [bjt(hist.index[i]+STEP,"%m/%d") for i in [0,4,8,12,16]])
    left.set_xlabel("按每根收盘日标记（北京时间）",color=GRAY,fontsize=9,labelpad=7)
    left.set_ylabel("价格（USDT）",color=GRAY,fontsize=9)
    handles,labels=left.get_legend_handles_labels()
    left.legend(handles+[Line2D([],[],c=VIOLET,ls="--")],labels+["周一双轨"],loc="lower left",bbox_to_anchor=(0,1.001),ncol=4,fontsize=8,frameon=False,handlelength=1.25,columnspacing=1,borderaxespad=0)
    # The exit hour's post-exit OHLC is excluded; plot the actually filled price as the endpoint.
    observed=hourly[(hourly.index>=entry)&(hourly.index<end)]
    long_view=(end-entry)/HOUR>120
    unit=24*HOUR if long_view else HOUR
    xc=(observed.index+HOUR-entry)/unit
    last_x=(end-entry)/unit
    right.fill_between(xc,observed.low,observed.high,color="#c2d4db",alpha=.48,lw=0)
    right.plot(np.r_[0,xc,last_x],np.r_[path.entry_price,observed.close,final.price],c="#64808d",lw=1.05,zorder=2)
    right.axhline(path.stop_price,color=RED,ls=":",lw=1.1,alpha=.9)
    trim=grouped[grouped.action.eq("trim")]
    target=grouped[grouped.action.str.startswith("take_profit")]
    if len(trim):
        right.scatter((trim.hour_ms-entry)/unit,trim.price,s=14,marker="o",fc="white",ec=VIOLET,lw=.7,zorder=3,label="仓位超限减仓")
    right.scatter([0],[path.entry_price],marker="^",s=65,color=BLUE,edgecolors="white",lw=.7,zorder=5,label="开仓")
    if len(target):
        right.scatter((target.hour_ms-entry)/unit,target.price,marker="D",s=42,color=ORANGE,edgecolors="white",lw=.7,zorder=5,label="翻倍止盈" if is_long else "2R 止盈")
    right.scatter([last_x],[final.price],marker="X",s=67,color=INK,edgecolors="white",lw=.7,zorder=6,label=ending)
    if observed.high.max()/observed.low.min()>8:
        right.set_yscale("log")
        right.yaxis.set_major_formatter(FuncFormatter(lambda n,_:f"{n:g}"))
        right.set_yticks([.5,1,2,5,10])
        right.set_ylabel("价格（USDT，对数轴）",color=GRAY,fontsize=9)
    else:
        right.yaxis.set_major_locator(MaxNLocator(nbins=6))
    right.set_xlim(-.045*max(last_x,1),last_x+max(last_x,1)*.04)
    right.xaxis.set_major_locator(MaxNLocator(nbins=6, integer=True))
    right.set_xlabel("进场后的天数" if long_view else "进场后的小时数",fontsize=9,color=GRAY,labelpad=7)
    right.legend(loc="lower left",bbox_to_anchor=(0,1.001),ncol=4,fontsize=8,frameon=False,handlelength=1.1,columnspacing=.8,borderaxespad=0)
    fig.text(.055,.115,f"信号{level_name} {px(boundary)}    ·    信号收盘 {px(current.close)}    ·    实际开仓 {px(path.entry_price)}    ·    初始止损 {px(path.stop_price)}",color=INK,fontsize=11)
    text=f"{ending}退出余仓 {px(final.price)}" if len(target) or len(trim) else f"{ending}退出 {px(final.price)}"
    if len(trim):
        text=f"{trim.hour_ms.nunique()} 个小时发生超限减仓    ·    "+text
    if len(target):
        text=("翻倍" if is_long else "2R")+f" 止盈 {px(target.price.iat[0])}    ·    "+text
    fig.text(.055,.052,text+"    ·    出场标在成交所属小时",color=GRAY,fontsize=10)
    fig.savefig(book.OUT/f"{position.symbol}.png",dpi=155,facecolor="white")
    plt.close(fig)


def main():
    for file in ["/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"]:
        font_manager.fontManager.addfont(file)
    mpl.rcParams.update({"font.family":font_manager.FontProperties(fname="/System/Library/Fonts/STHeiti Medium.ttc").get_name(),
                         "axes.unicode_minus":False,"savefig.bbox":None})
    funded=pd.read_csv(book.BASE/"funded_inverse_1.5x.csv.gz",index_col="event_id")
    tape=pd.read_csv(book.BASE/"selected_paths.csv.gz")
    fills=pd.read_csv(book.OUT/"sample_fills.csv")
    _,_,src=book.original.source.inputs()
    closes=[book.original.parent.repair.market.frames(src[s])[1].close for s in ["ETH","BTC"]]
    ratio=closes[0]/closes[1]
    original=book.original.season.direction(src)
    for event in book.samples(funded,tape):
        position=funded.loc[event]
        key=position.entry_ms-STEP
        change=ratio.at[key]/ratio.at[key-60*STEP]-1
        assert np.sign(change)==original.at[position.entry_ms]
        path=tape[tape.event_id.eq(event)&tape.tp_r.eq(0)].iloc[0]
        draw(position,path,fills[fills.event_id.eq(event)],src[position.symbol],change)


if __name__=="__main__":
    main()
