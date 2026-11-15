"""Show the confirmed bodies of the three candles containing the same ETH first breach."""
from pathlib import Path
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import MultipleLocator, StrMethodFormatter
import pandas as pd

from yoyo.evaluation.cloud_eth_simple_trade_charts import BG, GRID, AXIS, WHITE, ENTRY, UP
from yoyo.evaluation.cloud_eth_week_charts import H, ROOT, SOURCE
from yoyo.monitor.monday_fib import week_window


OUT = ROOT/'output/eth-2026-03-13-1h-range/03-first-breach-1h-4h-12h.png'


def main():
    font='/System/Library/Fonts/Hiragino Sans GB.ttc'
    font_manager.fontManager.addfont(font)
    mpl.rcParams.update({'font.family':font_manager.FontProperties(fname=font).get_name(),
                         'axes.unicode_minus':False})
    h,b=(pd.read_csv(SOURCE/f'bars{n}h.csv.gz',index_col='time',float_precision='round_trip') for n in (1,12))
    key=int(pd.Timestamp('2026-03-13 08:00',tz='Asia/Shanghai').timestamp()*1000)
    week=week_window(key)
    monday=h.loc[week['start']:week['end']-1]
    upper=monday.high.max()+.618*(monday.high.max()-monday.low.min())
    assert len(monday)==24 and h.loc[week['end']:key-1].high.max()<=upper<h.at[key,'high']
    fig,ax=plt.subplots(figsize=(11.5,5.7),facecolor=BG)
    fig.subplots_adjust(left=.045,right=.935,bottom=.145,top=.83)
    ax.set_facecolor(BG)
    ax.set_xlim(-.48,2.57)
    prices=[]
    for j,period in enumerate((1,4,12)):
        frame=h.loc[key:key+period*H-1]
        assert len(frame)==period
        o,hi,lo,c=frame.open.iloc[0],frame.high.max(),frame.low.min(),frame.close.iloc[-1]
        if period==12:
            assert [o,hi,lo,c]==list(b.loc[key,['open','high','low','close']])
        assert lo<o<upper<c<hi
        prices.extend((hi,lo))
        ax.vlines(j,lo,hi,color=UP,lw=2.0,zorder=2)
        ax.add_patch(Rectangle((j-.115,o),.23,c-o,fc=UP,ec=UP,zorder=3))
        ax.add_patch(Rectangle((j-.115,upper),.23,c-upper,fc='#ffac57',ec='none',zorder=4))
        ax.annotate(f'收 {c:,.2f}',(j+.115,c),xytext=(9,8),textcoords='offset points',
                    color=WHITE,fontsize=12,va='bottom',zorder=6)
    ax.axhline(upper,color=ENTRY,lw=1.25,ls=(0,(6,3)),zorder=5)
    ax.text(-.47,upper+1.1,f'上 1.618\n{upper:,.2f}',color=ENTRY,fontsize=11,ha='left',va='bottom')
    ax.set_ylim(min(prices)-3,max(prices)+8)
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MultipleLocator(20))
    ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    ax.set_xticks([0,1,2],['1H   08:00–09:00','4H   08:00–12:00','12H   08:00–20:00'])
    ax.tick_params(axis='x',length=0,pad=9,colors=WHITE,labelsize=13)
    ax.tick_params(axis='y',length=0,pad=4,colors=AXIS,labelsize=10)
    ax.grid(axis='y',lw=.5,color=GRID)
    ax.set_axisbelow(True)
    ax.spines[:].set_visible(False)
    fig.text(.048,.923,'ETH / USDT   ·   3月13日首次上破',fontsize=19,color=WHITE,weight='bold')
    fig.text(.048,.859,'同一次上冲，三种周期。橙色是收盘时仍在上轨外的实体。',fontsize=12,color=AXIS)
    fig.text(.93,.927,'2026  /  北京时间',fontsize=11,color=AXIS,ha='right')
    OUT.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(OUT,dpi=150,facecolor=BG)
    plt.close(fig)


if __name__=='__main__':
    main()
