"""Render ten reproducibly sampled losses from the frozen Monday v7 ledger.

No strategy replay or selection by appearance. Sample uniformly without
replacement from closed signal losses, using pandas random_state=20261022.
The existing minute-close tape supplies real OHLC; 15m bars are aggregation
only. Post-exit candles are explicitly shaded as retrospective context.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import dates as mdates, font_manager, ticker
from matplotlib.collections import LineCollection, PatchCollection
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_fib_confirmation import aggregate
from yoyo.evaluation.monday_fib_limits import aligned
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

EXP = ROOT/'experiments/active/exp-btc-monday-fib-body-20261022-v7'
SOURCE = ROOT/'experiments/active/exp-btc-monday-fib1618-okx-20261022-v1'
OUT = EXP/'loss_sample10_seed20261022'
SEED = 20261022
TZ = ZoneInfo('Asia/Shanghai')
BG, PANEL, TEXT, MUTED = '#10151e', '#151d29', '#edf1f7', '#9aaabd'
UP, DOWN, LEVEL, ENTRY, STOP, TP = '#3bc9a6', '#eb7083', '#b79afa', '#ffd166', '#ff697b', '#5dd1cc'


def stamp(t):
    return t.tz_convert(TZ).strftime('%Y-%m-%d %H:%M')


def xdate(t):
    return mdates.date2num(t.to_pydatetime())


def candles(ax, data, minutes):
    width = minutes/1440
    centers = mdates.date2num(data.index.to_pydatetime())-width/2
    colors = np.where(data.close >= data.open, UP, DOWN)
    ax.add_collection(LineCollection([[(x,l),(x,h)] for x,l,h in zip(centers,data.low,data.high)],
                                    colors=colors, linewidths=.9, zorder=2))
    patches = [Rectangle((x-width*.36,min(o,c)),width*.72,max(abs(c-o),.5))
               for x,o,c in zip(centers,data.open,data.close)]
    ax.add_collection(PatchCollection(patches,facecolors=colors,edgecolors=colors,linewidths=.4,zorder=3))


def chart(ax, data, minutes, row, entry_t, exit_t, touch_t, focus):
    candles(ax,data,minutes)
    left=xdate(data.index[0]-pd.Timedelta(minutes=minutes));right=xdate(data.index[-1])
    ax.set_xlim(left,right)
    lo=min(float(data.low.min()),row.entry,row.stop,row.level)
    hi=max(float(data.high.max()),row.entry,row.stop,row.level)
    pad=max((hi-lo)*.12,row.entry*.001)
    ax.set_ylim(lo-pad,hi+pad)
    ax.set_facecolor(PANEL)
    for spine in ax.spines.values():spine.set_color('#344052')
    ax.tick_params(colors=MUTED,labelsize=10)
    ax.grid(color='#708196',alpha=.13,linewidth=.6)
    ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:,.0f}'))
    locator=mdates.AutoDateLocator(minticks=4,maxticks=7,tz=TZ)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d\n%H:%M',tz=TZ))
    for price,color,style in [(row.level,LEVEL,'--'),(row.entry,ENTRY,'-'),(row.stop,STOP,'--')]:
        ax.axhline(price,color=color,ls=style,lw=1,alpha=.9,zorder=1)
    if focus=='overview':
        for j in range(1,5):
            price=getattr(row,f'tp{j}')
            if lo-pad<price<hi+pad:
                ax.axhline(price,color=TP,ls=':',lw=1,alpha=.7)
    if left<xdate(exit_t)<right:
        ax.axvspan(xdate(exit_t),right,color='#a7b1c2',alpha=.09,zorder=0)
        ax.text(.98,.97,'灰区：止损后走势',transform=ax.transAxes,color=MUTED,
                fontsize=10,ha='right',va='top')
    if left<=xdate(entry_t)<=right:
        if focus!='exit':
            ax.axvspan(xdate(entry_t-pd.Timedelta(minutes=15)),xdate(entry_t),
                       color='#7cafff',alpha=.10,zorder=0)
        ax.scatter([xdate(entry_t)],[row.entry],marker='^' if row.side==1 else 'v',
                   color=ENTRY,s=95,edgecolor=BG,zorder=6)
        if focus=='overview':
            ax.annotate('入场',xy=(xdate(entry_t),row.entry),xytext=(0,25 if row.side==1 else -30),
                        textcoords='offset points',color=ENTRY,fontsize=11,ha='center',
                        arrowprops=dict(arrowstyle='-',color=ENTRY,lw=.7))
    if left<=xdate(exit_t)<=right:
        ax.scatter([xdate(exit_t)],[row.stop],marker='x',color=STOP,s=90,lw=2,zorder=6)
        if focus=='overview':
            ax.annotate('止损',xy=(xdate(exit_t),row.stop),xytext=(0,-26 if row.side==1 else 24),
                        textcoords='offset points',color=STOP,fontsize=11,ha='center',
                        arrowprops=dict(arrowstyle='-',color=STOP,lw=.7))
    if focus=='overview' and left<=xdate(touch_t)<=right:
        ax.scatter([xdate(touch_t)],[row.level],marker='o',facecolor='none',edgecolor=LEVEL,
                   s=100,lw=1.8,zorder=5)


def ladder(ax,row):
    """Separate full-range price axis keeps distant targets from flattening candles."""
    ax.set_facecolor(PANEL);ax.set_xlim(0,1);ax.set_xticks([])
    levels=[('周一高',row.monday_high,MUTED),('周一低',row.monday_low,MUTED),
            ('外侧 1.618',row.level,LEVEL),('实际入场',row.entry,ENTRY),('初始止损',row.stop,STOP)]
    names=['TP1 · 0.5','TP2 · 0.618','TP3 · 0.705','TP4 · 周一高' if row.side==1 else 'TP4 · 周一低']
    for j,name in enumerate(names,1):levels.append((name,getattr(row,f'tp{j}'),TP))
    endpoint='周一高' if row.side==1 else '周一低'
    levels=[x for x in levels if x[0]!=endpoint]
    levels.sort(key=lambda x:x[1])
    prices=[v for _,v,_ in levels];span=max(prices)-min(prices)
    low=min(prices)-span*.10;high=max(prices)+span*.10
    ax.set_ylim(low,high);ax.set_yticks([])
    for spine in ax.spines.values():spine.set_color('#344052')
    loc=np.array(prices,float);gap=(high-low)*.078
    for i in range(1,len(loc)):loc[i]=max(loc[i],loc[i-1]+gap)
    if loc[-1]>high-gap*.5:loc-=loc[-1]-(high-gap*.5)
    for i in range(len(loc)-2,-1,-1):loc[i]=min(loc[i],loc[i+1]-gap)
    for (name,price,color),label_y in zip(levels,loc):
        ax.plot([.03,.26],[price,price],color=color,lw=1.5)
        ax.plot([.26,.38],[price,label_y],color=color,lw=.7,alpha=.8)
        ax.text(.41,label_y,f'{name}\n{price:,.1f}',fontsize=11,color=color,va='center',ha='left')
    ax.set_title('完整目标价位\n独立纵轴 · USDT',loc='left',fontsize=12,color=TEXT,pad=13)


def run():
    code='yoyo/evaluation/monday_fib_loss_charts.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',code],cwd=ROOT).returncode:
        raise RuntimeError('Commit chart builder before rendering')
    OUT.mkdir(exist_ok=True)
    font='/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
    font_manager.fontManager.addfont(font)
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=font).get_name(),
                         'axes.unicode_minus':False,'font.size':12,'savefig.facecolor':BG})
    ledger=pd.read_csv(EXP/'all_parents.csv',float_precision='round_trip')
    entries=pd.read_csv(EXP/'entries.csv',float_precision='round_trip')
    weekly=pd.read_csv(SOURCE/'weekly_orders.csv',float_precision='round_trip')
    cohort=ledger[ledger.population.eq('signal')&ledger.closed&ledger.outcome.eq('loss')].sort_values('parent_id')
    assert len(cohort)==62 and not cohort.tp1.any()
    chosen=cohort.sample(n=10,random_state=SEED).sort_values('entry_i')
    selected=entries.merge(chosen[['parent_id','exit_i','net_bp','net_r']],
                           left_on='trade_id',right_on='parent_id',validate='one_to_one')
    selected=selected.merge(weekly[['pair_id','monday_high','monday_low']],on='pair_id',validate='one_to_one').sort_values('entry_i')
    assert len(selected)==10 and selected.trade_id.is_unique
    frame=aligned(pd.read_csv(SOURCE/'candles.csv.gz',float_precision='round_trip'))
    quarter=aggregate(frame,15)
    native=pd.read_csv(EXP/'native_market_be_0/trades.csv',float_precision='round_trip')
    metadata=[]
    for number,row in enumerate(selected.itertuples(),1):
        ent=frame.index[row.entry_i];end=frame.index[row.exit_i];touch=frame.index[row.touch_i]
        actual=native[native.parent_id.eq(row.trade_id)]
        assert len(actual)==4 and actual.reason.eq(1).all()
        assert actual.exit_i.eq(row.exit_i).all() and (actual.exit_price-row.stop).abs().lt(1e-8).all()
        q=quarter.loc[ent]
        assert row.side*(q.open-row.level)>0 and row.side*(q.close-row.level)>0
        assert abs(frame.close.iloc[row.entry_i]-row.entry)<1e-8
        overview=quarter.loc[touch-pd.Timedelta(hours=3):end+pd.Timedelta(hours=6)]
        enter_zoom=frame.loc[ent-pd.Timedelta(minutes=30):ent+pd.Timedelta(minutes=35)]
        exit_zoom=frame.loc[end-pd.Timedelta(minutes=30):end+pd.Timedelta(minutes=45)]
        fig=plt.figure(figsize=(16,10),dpi=120,facecolor=BG)
        ax=fig.add_axes([.065,.44,.70,.38])
        ae=fig.add_axes([.065,.125,.32,.205]);az=fig.add_axes([.445,.125,.32,.205])
        al=fig.add_axes([.80,.125,.18,.695])
        chart(ax,overview,15,row,ent,end,touch,'overview')
        chart(ae,enter_zoom,1,row,ent,end,touch,'entry')
        chart(az,exit_zoom,1,row,ent,end,touch,'exit')
        ladder(al,row)
        direction='做多' if row.side==1 else '做空'
        held=row.exit_i-row.entry_i
        distance=row.side*(row.tp1-row.entry)/row.entry*100
        fig.text(.065,.955,f'{number:02d} / 10   BTCUSDT 永续 · {direction} · {stamp(ent)}',
                 color=TEXT,fontsize=21,weight='bold')
        fig.text(.065,.915,f'入场 {row.entry:,.1f}   →   止损 {row.stop:,.1f}    |    持仓 {held} 分钟    |    收益 {row.net_bp/100:.3f}%    |    TP1 距离 {distance:.2f}%',
                 color=MUTED,fontsize=12)
        ax.set_title('15分钟全程  ·  紫色圆圈：首次触及1.618  /  浅蓝背景：入场确认K线',
                     loc='left',color=TEXT,fontsize=12,pad=11)
        ae.set_title('1分钟细节 · 入场附近',loc='left',color=TEXT,fontsize=12,pad=12)
        az.set_title('1分钟细节 · 止损附近',loc='left',color=TEXT,fontsize=12,pad=12)
        fig.text(.065,.055,'黄线/三角：实际入场     紫虚线：外侧1.618     红虚线/叉：止损     青色：计划止盈（本单均未触及）',color=MUTED,fontsize=11)
        fig.text(.065,.026,f'OKX真实1m OHLC · 上海时间 · 周一区间按纽约日界 · 零手续费 · 62笔亏损中固定种子20261022随机抽10笔 · {row.trade_id}',color=MUTED,fontsize=9)
        path=OUT/f'{number:02d}_{ent.tz_convert(TZ):%Y%m%d_%H%M}_{"long" if row.side==1 else "short"}.png'
        fig.savefig(path);plt.close(fig)
        metadata.append(dict(number=number,trade_id=row.trade_id,side=direction,
            entry_time_shanghai=stamp(ent),stop_time_shanghai=stamp(end),holding_minutes=held,
            entry=row.entry,stop=row.stop,level=row.level,target1=row.tp1,
            tp1_distance_pct=distance,return_pct=row.net_bp/100,path=str(path),
            native_exit_legs_checked=len(actual),completed_body_checked=True))
    selected.to_csv(OUT/'sample.csv',index=False)
    dump(OUT/'manifest.json',dict(seed=SEED,population='closed signal losses only',
        population_count=62,sample_count=10,sampling='sort parent_id; pandas sample without replacement; random_state20261022; display chronologically',
        chart_timezone='Asia/Shanghai',range_timezone='America/New_York',
        source=[file_stamp(EXP/'all_parents.csv'),file_stamp(EXP/'entries.csv'),file_stamp(SOURCE/'candles.csv.gz')],
        generator_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip(),
        rendering={'matplotlib':matplotlib.__version__,'pandas':pd.__version__,'dpi':120,'pixels':[1920,1200]},
        checks={'loss_count':62,'sample_native_stop_legs':40,'completed_body_entries':10},
        notes='Historical illustrations, not new strategy evidence. Independent price axes per panel. Grey region is post-stop context, not held exposure.',images=metadata))
    print(json.dumps(metadata,ensure_ascii=False,indent=2))


if __name__=='__main__':
    run()
