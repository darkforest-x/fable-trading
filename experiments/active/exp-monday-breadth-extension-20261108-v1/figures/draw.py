"""Render the three explanatory figures from existing trades; no new orders are simulated.

From the repository root: PYTHONPATH=. vectorbt-python <path-to-this-file>
"""
from pathlib import Path

import matplotlib as mpl
mpl.use("Agg")
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_payoffs as prices
from yoyo.evaluation.monday_breadth_extension_partial_marks import accumulated_cost
from yoyo.evaluation.monday_breadth_extension_risk import btc_marks, sleeve, stats

OUT = Path(__file__).resolve().parent
EXP = OUT.parent
INK, MUTED, GRID = "#142a37", "#526673", "#d9e2e5"
BLUE, ORANGE, GRAY = "#126482", "#aa541a", "#657782"
BG = "#fbfcfa"


def page(title, subtitle, height):
    fig = plt.figure(figsize=(9.6, height), facecolor=BG)
    fig.text(.075, 1-.30/height, title, fontsize=24, color=INK, va="top", weight="bold")
    fig.text(.075, 1-.83/height, subtitle, fontsize=12.5, color=MUTED, va="top")
    return fig


def axes(fig, bounds):
    ax = fig.add_axes(bounds, facecolor=BG)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0, colors=MUTED, pad=6, labelsize=11)
    ax.grid(axis="y", color=GRID, linewidth=.75)
    ax.set_axisbelow(True)
    return ax


def save(fig, name, evidence):
    fig.savefig(OUT/name, dpi=180, facecolor=BG,
                metadata={"Description": evidence, "Title": name})
    plt.close(fig)


def selection_chart(book, selected):
    normal = book[book.source.eq("medium")]
    keep = normal.decision.isin(selected.decision)
    special = selected.source.eq("case").sum()
    alt = pd.read_csv(EXP/"risk/integrated_sensitivity.csv").set_index("subset").loc["all_2026", "alt"]
    total = len(normal)+special+alt
    fig = page(f"原来的 {total:.0f} 次入场，哪 {sum(~keep)} 次被跳过？",
               "2026 年普通 BTC 报警逐笔对照  ·  每个点代表一笔持有 6 天后的净收益", 4.6)
    ax = axes(fig, [.29, .33, .64, .38])
    ax.grid(False)
    ax.set(xlim=(-14, 11), ylim=(-.49, 1.52), yticks=[])
    ax.set_xticks([-12,-8,-4,0,4,8])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x,_: f"{x:+.0f}%" if x else "0%"))
    ax.axvline(0, color=INK, lw=1.3)
    for y, mask, color, name, detail in [(1,keep,BLUE,"保留","4 小时收盘价 ≤ EMA20"),
                                         (0,~keep,ORANGE,"跳过","4 小时收盘价 > EMA20")]:
        values = np.sort(normal.loc[mask, "funded_pct"].to_numpy())
        ax.hlines(y,-14,11,color=GRID,lw=.85)
        # Stack near-neighbours vertically without displacing the return on the x axis.
        occupied = []
        for x in values:
            for offset in (0, .13, -.13, .26, -.26):
                if all(abs(x-old_x)>.53 or offset!=old_h for old_x,old_h in occupied):
                    break
            occupied.append((x,offset))
        x, dy = np.asarray(occupied).T
        ax.scatter(x, y+dy, s=52, marker="o" if y else "X", facecolor=color, edgecolor=BG, linewidth=.65, zorder=3)
        fy = fig.transFigure.inverted().transform(ax.transData.transform((0,y)))[1]
        fig.text(.075, fy+.065, f"{name} {len(values)} 笔", fontsize=17, color=color, weight="bold", va="center")
        fig.text(.075, fy, detail, fontsize=10.5, color=MUTED, va="center")
        fig.text(.075, fy-.062, f"均收 {values.mean():+.2f}%", fontsize=13, color=INK, va="center")
    fig.text(.075,.16,f"新组合的 {len(selected)+alt:.0f} 次入场 = {sum(keep)} 次普通 BTC + {special} 次急跌 BTC + {alt:.0f} 次山寨篮子",
             fontsize=15, color=INK, weight="bold")
    fig.text(.075,.07,"跳过的点按原策略计算。EMA20 是最近 20 根 4 小时收盘价的指数均线。",fontsize=11,color=MUTED)
    save(fig, "01-btc-filter.png", "partial/events.parquet; risk/integrated_btc_events.parquet; risk/integrated_sensitivity.csv")


def equity_chart(book, selected, quotes, fills):
    stored = pd.read_parquet(EXP/"sleeves/equity_5m.parquet")
    history = funding.rates("BTCUSDT", history=True, local_only=True)
    settlements = history.realized_rate*history.mark_price.fillna(quotes.open.reindex(history.index))
    paths = btc_marks(book, quotes, fills)
    for r in book.itertuples():
        marks = pd.date_range(r.decision,r.exit_time,freq="5min")
        cost = accumulated_cost(settlements,r.decision,r.exit_time,marks)
        if r.exit_legs == 2:
            cost = (cost+accumulated_cost(settlements,r.decision,r.first_exit_time,marks))/2
        np.testing.assert_allclose(cost[-1],r.funding_points,atol=1e-6)
        paths[r.decision] = paths[r.decision]-cost/r.entry_open
    reference = pd.read_csv(EXP/"risk/accounts.csv").query(
        'scope=="2026_funded" and sleeve_deployment==1 and extra_roundtrip_pp==0').set_index("strategy")
    fig = page("相同资金，过滤前后的账户怎么走？",
               "初始 1,000 USDT，BTC 与山寨各分 500  ·  两边独立复利，计手续费和资金费", 6.1)
    top = axes(fig, [.085,.48,.705,.29])
    bottom = axes(fig, [.085,.20,.705,.18])
    for ax in (top,bottom):
        ax.spines["bottom"].set_visible(False)
    for trades, key, label, color, style in [(book,"half_then_6d","原组合",GRAY,"--"),
                                             (selected,"below_plus_case","新组合",BLUE,"-")]:
        nav = (sleeve(trades,paths,stored.index,1.,0.)+stored.ALT)/2
        s = stats(nav)
        np.testing.assert_allclose([s['growth_pct'],s['max_dd_pct']],reference.loc[key,['growth_pct','max_dd_pct']].astype(float),atol=1e-7)
        close = nav.resample("1D").last()
        dd = (100*(nav/nav.cummax().clip(lower=1)-1)).resample("1D").min()
        bounds = nav.index[[0,-1]].tz_localize(None)
        top.plot(close.index.tz_localize(None),1000*close,c=color,ls=style,lw=2.5)
        top.annotate(f"{label} {1000*close.iloc[-1]:,.0f}\n{s['growth_pct']:+.2f}%",(close.index[-1].tz_localize(None),1000*close.iloc[-1]),
                     xytext=(10,0),textcoords="offset points",va="center",fontsize=12.5,color=color,weight="bold")
        bottom.plot(dd.index.tz_localize(None),dd,c=color,ls=style,lw=1.7)
        low = dd.idxmin().tz_localize(None)
        bottom.scatter(low,dd.min(),s=32,color=color,zorder=3)
        bottom.annotate(f"{dd.min():.2f}%",(low,dd.min()),xytext=(7,-2),textcoords="offset points",fontsize=11.5,color=color,weight="bold")
    top.set_title("账户净值（USDT）",loc="left",fontsize=13,color=INK,pad=8)
    top.set(ylim=(880,1505),yticks=[900,1100,1300,1500],xticks=[])
    bottom.set_title("自此前最高净值回落多少",loc="left",fontsize=13,color=INK,pad=8)
    bottom.set(ylim=(-13.8,.9),yticks=[-12,-6,0])
    bottom.yaxis.set_major_formatter(FuncFormatter(lambda y,_:f"{y:.0f}%"))
    end = bounds[-1].normalize()
    ticks = [pd.Timestamp(2026,m,1) for m in [1,3,5,7,9]]+[end]
    bottom.set_xticks(ticks,[t.strftime("%m/%d") for t in ticks])
    for ax in (top,bottom):
        ax.set_xlim(bounds[0],end)
    fig.text(.085,.093,"信号统计至 10/27 UTC，最后一笔跟踪到 10/29。上图取每日末值，下图取日内最深的 5 分钟回撤。",
             fontsize=10.8,color=MUTED)
    fig.text(.085,.042,"2020–2026 全历史只扣手续费，原组合最大回撤 39.03%，新组合 26.75%。",
             fontsize=11.5,color=INK)
    save(fig, "02-account-equity.png", "risk/accounts.csv; exact 5m ALT sleeve and native BTC marks; partial/events.parquet")


def candles(ax, bars):
    x = mdates.date2num(bars.index)
    width = (x[1]-x[0])*.68
    lower, upper = bars[["open","close"]].min(axis=1),bars[["open","close"]].max(axis=1)
    up = bars.close.ge(bars.open)
    colors = np.where(up, "#337c73", "#955545")
    ax.add_collection(LineCollection([[(i,l),(i,h)] for i,l,h in zip(x,bars.low,bars.high)],
                                    colors=colors,linewidths=.85))
    polys = [[(i-width/2,l),(i-width/2,h),(i+width/2,h),(i+width/2,l)] for i,l,h in zip(x,lower,upper)]
    ax.add_collection(PolyCollection(polys,facecolors=colors,edgecolors=colors,linewidths=.55))
    ax.autoscale_view()


def case_chart(trade, quotes, fill):
    t0,t1,t2 = [pd.Timestamp(v).tz_convert("Asia/Shanghai").tz_localize(None)
                for v in [trade.decision,trade.first_exit_time,trade.exit_time]]
    path = quotes.loc[trade.decision-pd.Timedelta(hours=12):trade.exit_time].copy()
    path.index = path.index.tz_convert("Asia/Shanghai").tz_localize(None)
    # The deadline exit is at the open; omit the still-unobserved remainder of that 5m bar.
    path.loc[t2,["high","low","close"]] = path.at[t2,"open"]
    bars = path.resample("1h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    sell1 = fill.first_exit_price
    sell2 = 2*fill.exit_vwap-sell1
    np.testing.assert_allclose(sell2,path.at[t2,"open"])
    fig = page("9 月 16 日的 BTC，多头是怎样退出的？",
               "Binance BTCUSDT 永续合约  ·  北京时间  ·  左图为 1 小时 K 线，右图放大最后一晚", 5.9)
    main = axes(fig,[.09,.34,.49,.43])
    zoom = axes(fig,[.69,.34,.255,.43])
    candles(main,bars)
    main.set_ylim(73500,87500)
    main.set_yticks([75000,80000,85000],["75,000","80,000","85,000"])
    main.set_xlim(path.index[0]-pd.Timedelta(hours=3),t2+pd.Timedelta(hours=5))
    main.set_xticks([pd.Timestamp(f"2026-09-{d}") for d in [16,18,20,22]],["9/16","9/18","9/20","9/22"])
    main.set_title("价格（USDT / BTC）",loc="left",fontsize=12.5,color=INK)
    main.scatter(t0,trade.entry_open,s=78,marker="^",color=BLUE,edgecolor=BG,zorder=5)
    main.annotate(f"买入  {trade.entry_open:,.1f}",(t0,trade.entry_open),
                  xytext=(.19,.34),textcoords="axes fraction",fontsize=12,color=BLUE,weight="bold",
                  arrowprops=dict(arrowstyle="-",color=BLUE,lw=1.1))
    start = t1-pd.Timedelta(hours=2)
    main.axvspan(start,t2,color=ORANGE,alpha=.12)
    close = path.loc[start:t2,"close"].copy()
    # Plot 5m closes at candle completion. At the deadline only the market open is used.
    close = close[close.index < t2]
    close.index = close.index+pd.Timedelta(minutes=5)
    zoom.plot(close.index,close,color=GRAY,lw=1.35)
    for stamp,px,mark in [(t1,sell1,"v"),(t2,sell2,"s")]:
        zoom.scatter(stamp,px,s=64,marker=mark,color=ORANGE,edgecolor=BG,zorder=5)
    zoom.set_xlim(start,t2+pd.Timedelta(minutes=45))
    zoom.margins(y=.17)
    zoom.set_title("5 分钟收盘与卖出价",loc="left",fontsize=12.5,color=INK)
    zoom.yaxis.set_major_locator(mpl.ticker.MaxNLocator(4))
    zoom.yaxis.set_major_formatter(FuncFormatter(lambda y,_:f"{y:,.0f}"))
    ticks=[pd.Timestamp("2026-09-21 22:00"),pd.Timestamp("2026-09-22 00:00"),pd.Timestamp("2026-09-22 03:00")]
    zoom.set_xticks(ticks,[t.strftime("%H:%M") for t in ticks])
    fig.text(.085,.22,f"▼  9/21 23:05 卖出一半  {sell1:,.0f}        ■  9/22 03:20 六天到期，卖出余仓  {sell2:,.0f}",
             fontsize=13,color=ORANGE,weight="bold")
    stress=pd.read_csv(EXP/"partial/sep16_native_cost_sensitivity.csv").query('rule=="half_then_6d"').iloc[0]
    fig.text(.085,.126,f"按最初买入 1 枚 BTC 计，扣费后赚 {trade.funded_points:,.2f} 点。",fontsize=16,color=INK,weight="bold")
    fig.text(.085,.054,f"市价成交每次不利 0.10% 时，第一档止盈未触发，六天到期净赚 {stress.funded_points:,.2f} 点。",fontsize=11.8,color=MUTED)
    save(fig, "03-btc-september.png", "Native funded case fills, BTCUSDT local 5m OHLC, partial/sep16_native_cost_sensitivity.csv")


def run():
    font = "/System/Library/Fonts/Hiragino Sans GB.ttc"
    fm.fontManager.addfont(font)
    mpl.rcParams.update({"font.family":[fm.FontProperties(fname=font).get_name(),"DejaVu Sans"],
                         "axes.unicode_minus":False,"axes.formatter.useoffset":False})
    book=pd.read_parquet(EXP/"partial/events.parquet").query('exit_policy=="half_then_6d" and epoch==3').copy()
    selected=pd.read_parquet(EXP/"risk/integrated_btc_events.parquet").query("epoch==3")
    fill=pd.read_parquet(funding.FUNDED_CACHE,filters=[("policy_id","=",4)]).set_index("entry_time")
    q,_=prices.load_full_ohlc("BTCUSDT",book.decision.min(),book.exit_time.max(),repair_missing=False)
    selection_chart(book,selected)
    equity_chart(book,selected,q,fill)
    trade=selected.loc[selected.decision.eq(pd.Timestamp("2026-09-15 19:20",tz="UTC"))].iloc[0]
    case_chart(trade,q,fill.loc[trade.decision])


if __name__=="__main__":
    run()
