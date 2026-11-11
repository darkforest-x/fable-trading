"""Plot actual OKX hourly candles aggregated at UTC 00/12, with ledger prices.

The annotated percentages are the archived 1h reclaim trades' fee-adjusted
returns with zero additional slippage. Rectangles identify first rail tests;
they do not supply entry prices. Horizontal rails start at the NY ready hour.
"""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager, ticker
from matplotlib.collections import LineCollection, PolyCollection
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'exp-alt-month-first-week-entries-20261110-v1'
OUT = HERE.parents[2] / 'output/etc-month-first-week-12h/ETC_2026_Jul_Sep.png'
EVENTS = [('2026-07','lower'),('2026-08','lower'),('2026-08','upper'),('2026-09','upper')]


def main():
    font_manager.fontManager.addfont('/System/Library/Fonts/Hiragino Sans GB.ttc')
    plt.rcParams.update({'font.family':'Hiragino Sans GB','axes.unicode_minus':False})
    h=pd.read_csv(HERE.parent/'exp-cloud-allmarket-screen-20261108-v1/inputs/ETC/bars1h.csv.gz')
    h.index=pd.to_datetime(h.time,unit='ms',utc=True)
    groups=h.resample('12h')
    a=groups.agg({'open':'first','high':'max','low':'min','close':'last'})
    a=a.loc['2026-07-01':'2026-09-30']
    assert groups.size().loc[a.index].eq(12).all()
    ev=pd.read_csv(SOURCE/'events.csv.gz').query("symbol=='ETC'").set_index(['month','side'])
    ledger=pd.read_csv(SOURCE/'position_ledger.csv.gz').query("symbol=='ETC' and is_control==False and entry_rule=='reclaim_1h' and status=='closed'").set_index(['month','side'])
    fig,ax=plt.subplots(figsize=(15,6.4),dpi=130)
    fig.subplots_adjust(left=.035,right=.951,top=.87,bottom=.12)
    ax.set_axisbelow(True)
    ax.grid(color='#d5d8dc',linewidth=.65,linestyle=(0,(1,4)))
    ax.yaxis.tick_right()
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis='both',length=0,color='#81858e',labelcolor='#626871',labelsize=9,pad=6)
    centers=a.index+pd.Timedelta(hours=6)
    x=mdates.date2num(centers)
    colors=np.full(len(a),'#858b96',dtype=object)
    for m in ('2026-07','2026-08','2026-09'):
        month=pd.Timestamp(m+'-01',tz='America/New_York')
        monday=month+pd.DateOffset(days=(7-month.weekday())%7)
        ready=monday+pd.DateOffset(days=7)
        colors[(a.index>=monday)&(a.index<ready)]='#24be57'
        end=month+pd.offsets.MonthBegin(1)
        row=ev.loc[(m,slice(None)),:].iloc[0]
        hi,lo=row.high,row.low
        ax.fill_between([ready,end],lo,hi,color='#f3f4f5',zorder=0)
        levels=[('上 1.618',hi+.618*(hi-lo)),('首周高',hi),('首周低',lo),('下 1.618',lo-.618*(hi-lo))]
        for name,y in levels:
            if y < 5.66 or y>10.65:
                continue
            ax.hlines(y,ready,end,linewidth=.8,color='#777d84' if '首周' in name else '#363b41')
            if (m,name) in [('2026-07','下 1.618'),('2026-08','下 1.618'),('2026-09','上 1.618')]:
                ax.text(ready+pd.Timedelta(hours=8),y+.046,f'{name}  {y:.3f}',color='#454a51',fontsize=8,va='bottom')
    lows,highs,opens,closes=(a[k].to_numpy() for k in ('low','high','open','close'))
    ax.add_collection(LineCollection([[(i,l),(i,h)] for i,l,h in zip(x,lows,highs)],colors=colors,linewidths=.8,zorder=3))
    ax.add_collection(PolyCollection([[(i-.16,o),(i+.16,o),(i+.16,c),(i-.16,c)] for i,o,c in zip(x,opens,closes)],facecolors=colors,edgecolors=colors,linewidths=.55,zorder=4))
    offsets={('2026-07','lower'):(-40,-37),('2026-08','lower'):(-30,-33),('2026-08','upper'):(-78,41),('2026-09','upper'):(-29,38)}
    for key in EVENTS:
        row,trade=ev.loc[key],ledger.loc[key]
        ts=pd.Timestamp(row.touch); tx=ts.floor('12h')+pd.Timedelta(hours=6)
        actual=a.loc[ts.floor('12h')]
        assert actual.low<=row.level<=actual.high
        ax.scatter([tx],[row.level],s=39,facecolors='none',edgecolors='#313b46',linewidths=1.1,zorder=6)
        offset=offsets[key]
        direction='下轨做多' if key[1]=='lower' else '上轨做空'
        local_entry=pd.Timestamp(trade.entry_time).tz_convert('Asia/Shanghai')
        label=f'{local_entry:%m/%d}  {direction}\n{trade.net_return_pct:+.2f}%'
        ax.annotate(label,(tx,row.level),xytext=offset,textcoords='offset points',ha='center',va='center',fontsize=9.5,color='#202934',
                    bbox=dict(facecolor='white',edgecolor='#d5d9de',pad=3,boxstyle='round,pad=.34'),
                    arrowprops=dict(arrowstyle='-',color='#656c74',linewidth=.8),zorder=7)
    start=pd.Timestamp('2026-07-01',tz='UTC');end=pd.Timestamp('2026-10-01',tz='UTC')
    ax.set_xlim(start,end);ax.set_ylim(5.55,10.95);ax.yaxis.set_major_locator(ticker.MultipleLocator(.5))
    marks=pd.date_range('2026-07-01','2026-10-01',freq='7D',tz='UTC')
    ax.set_xticks(marks,[t.tz_convert('Asia/Shanghai').strftime('%m/%d') for t in marks])
    fig.text(.035,.946,'ETCUSDT 永续合约 · 12小时 · OKX',fontsize=16,color='#363b42')
    fig.text(.035,.895,'月首周 1.618    2026 年 7–9 月',fontsize=11,color='#737984')
    fig.text(.951,.943,'USDT',fontsize=10,color='#60666f',ha='right')
    fig.text(.035,.032,'圆圈标出月内该侧首触。盈亏按 1 小时收回后的实际回测成交计算，已扣手续费，额外滑点为零。日期为北京时间。',color='#646a73',fontsize=9)
    OUT.parent.mkdir(exist_ok=True,parents=True)
    fig.savefig(OUT,facecolor='white');plt.close(fig)
    ledger.loc[EVENTS,['level','entry_time','entry_fill','target_price','stop_price','exit_time','exit_price','exit_reason','net_return_pct','stress_net_pct','mfe_pct','mae_pct']].to_csv(HERE/'chart_trades.csv')


if __name__=='__main__':
    main()
