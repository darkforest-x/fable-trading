"""Seeded native-trade review charts from real5m OHLC and completed15m MAs.

Charts contain future outcomes for human review only. They are never signals
or training inputs. An entire-position line/range panel supplements bounded
actual5m entry/exit candles, rather than pretending a downsampled candle is5m.
All rendered samples retain source OHLCV and MA CSVs alongside their PNGs.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib import font_manager
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS
from yoyo.evaluation.monday_ma6_signals import _aggregate_15m
from yoyo.evaluation.monday_ma6_retest import EXP,ART,source_jobs,source_frame,finite
from yoyo.evaluation.monday_ma6_analysis import read
from yoyo.evaluation.range_framework import dump

COLORS=['#3b82f6','#f59e0b','#a855f7','#0ea5e9','#ef6c00','#db2777']
for path in ['/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/STHeiti Light.ttc']:
    if Path(path).exists():
        font_manager.fontManager.addfont(path)
        plt.rcParams['font.family']=font_manager.FontProperties(fname=path).get_name();break
plt.rcParams['axes.unicode_minus']=False


def mas15(f,symbol):
    h=_aggregate_15m(f,symbol)
    return h[h.complete].copy()


def candles(ax,frame,title,lines,marks=(),ma=False):
    if frame.empty:
        ax.text(.5,.5,'该窗口无完整行情',ha='center',transform=ax.transAxes);return
    x=np.arange(len(frame));values=frame[['open','high','low','close']].to_numpy()
    for i,(o,h,l,c) in enumerate(values):
        color='#059669' if c>=o else '#e34b63'
        ax.vlines(i,l,h,color=color,lw=.65)
        ax.add_patch(Rectangle((i-.34,min(o,c)),.68,max(abs(c-o),(values[:,1].max()-values[:,2].min())*.001),
                               facecolor=color,edgecolor=color,lw=.3))
    if ma:
        for col,color in zip(SIX_MA_COLUMNS,COLORS):
            if col in frame:ax.plot(x,frame[col],color=color,lw=.8,label=col)
    lo,hi=values[:,2].min(),values[:,1].max();span=max(hi-lo,hi*.001)
    lower,upper=lo-.08*span,hi+.12*span
    outside=[]
    for price,label,color in lines:
        if np.isfinite(price):
            if lower<=price<=upper:
                ax.axhline(price,color=color,lw=.8,ls='--',alpha=.85)
                ax.text(1.002,price,f'{label}\n{price:.7g}',transform=ax.get_yaxis_transform(),color=color,fontsize=7,va='center')
            else:outside.append(f'{label} {price:.7g}')
    if outside:
        ax.text(.01,.97,'窗外参考：'+'；'.join(outside),transform=ax.transAxes,fontsize=7,
                color='#64748b',va='top',bbox=dict(facecolor='white',alpha=.85,edgecolor='none'))
    for when,label,color in marks:
        t=pd.Timestamp(when)
        if frame.index[0]<=t<=frame.index[-1]+pd.Timedelta(minutes=15 if ma else 5):
            j=int(frame.index.searchsorted(t,side='right'))-1
            ax.axvline(j,color=color,lw=1,ls=':');ax.text(j,.99,label,transform=ax.get_xaxis_transform(),va='top',fontsize=8,color=color)
    positions=np.unique(np.linspace(0,len(frame)-1,min(7,len(frame)),dtype=int))
    ax.set_xticks(positions,frame.index[positions].tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M'),fontsize=7)
    ax.set_ylim(lower,upper)
    ax.set_xlim(-1,len(frame));ax.set_title(title,loc='left',fontsize=11,pad=10)
    ax.grid(alpha=.1);ax.tick_params(axis='y',labelsize=8)
    for spine in ax.spines.values():spine.set_color('#d8e0e5')
    if ma:ax.legend(loc='lower left',ncol=6,fontsize=6,framealpha=.8)
    if 'volume' in frame and frame.volume.notna().any() and frame.volume.max()>0:
        axv=ax.inset_axes([0,.01,1,.16]);axv.bar(x,frame.volume,color='#9eabb7',alpha=.5,width=.7)
        axv.set_xlim(-1,len(frame));axv.set_axis_off()


def sample_rows():
    t=read(ART/'report-data/trades.csv.gz');e=read(ART/'report-data/orders.csv.gz');o=read(ART/'report-data/opportunities.csv.gz')
    t=t[t.population.eq('signal')];rng=np.random.default_rng(20261024);chosen=[]
    for keys,g in t[t.outcome.isin(['win','loss'])].groupby(['asset_group','exit_mode','outcome'],sort=True):
        for i in rng.choice(g.index,size=min(2,len(g)),replace=False):
            r=g.loc[i].to_dict();r.update(sample_kind='trade',sampling_pool=len(g),seed=20261024)
            chosen.append(r)
    for asset,g in e[e.population.eq('signal')&e.status.ne('filled')].groupby('asset_group',sort=True):
        for i in rng.choice(g.index,size=min(2,len(g)),replace=False):
            r=g.loc[i].to_dict();r.update(sample_kind='unfilled',outcome=r['status'],exit_mode='shared_entry',sampling_pool=len(g),seed=20261024)
            chosen.append(r)
    if 'status' in o:
        for asset,g in o[~o.pair_id.isin(e[e.population.eq('signal')].pair_id)].groupby('asset_group',sort=True):
            for i in rng.choice(g.index,size=min(2,len(g)),replace=False):
                r=g.loc[i].to_dict();r.update(sample_kind='invalidated_monitor',outcome=r['status'],exit_mode='shared_entry',sampling_pool=len(g),seed=20261024)
                chosen.append(r)
    native=t[t.policy.ne('half_each')]
    for r in chosen:
        if r.get('policy')=='half_each':
            siblings=native[native.parent_id.eq(r['parent_id'])]
            r['leg_exits']=siblings[['policy','exit_interval_start','exit_price','reason']].to_dict('records')
    return chosen


def main():
    cfg=json.loads((EXP/'config.json').read_text());jobs=dict(source_jobs(cfg));rows=sample_rows()
    folder=ART/'盈亏与未成交K线';folder.mkdir(exist_ok=True)
    records=[]
    for symbol in sorted({r['symbol'] for r in rows}):
        f,q=source_frame(symbol,jobs[symbol]);h=mas15(f,symbol)
        for r in [r for r in rows if r['symbol']==symbol]:
            rank=len(records)+1;sid=f'{rank:03d}-{symbol}-{r["sample_kind"]}-{r["exit_mode"]}'
            tcol=next((c for c in ['first_touch_at','touch_time','trigger_time','pin_time','entry_time'] if c in r and pd.notna(r[c])),None)
            if tcol is None:raise ValueError('Sample lacks touch timestamp')
            touch=pd.Timestamp(r[tcol])
            if r['sample_kind']=='trade':
                # Native timestamps are close bounds. Mark the observed candle,
                # never shift its visual marker to the following five-minute bar.
                entry=pd.Timestamp(r.get('entry_interval_start',pd.Timestamp(r['entry_time'])-pd.Timedelta(minutes=5)))
                exit_=pd.Timestamp(r['exit_interval_start'])
            else:
                decision=next((r[c] for c in ['submitted_at','confirmation_at','invalidation_at'] if c in r and pd.notna(r[c]) and r[c]),touch)
                entry=pd.Timestamp(decision);exit_=entry+pd.Timedelta(hours=12)
            line=[]
            for c,label,color in [('level','触发 1.618','#2563eb'),('cancel_boundary','近侧周一边界','#64748b'),
                                  ('target_monday','周一对侧 TP','#d97706'),('target_far','另一侧 1.618','#8b5cf6'),
                                  ('entry','实际入场/初始挂价','#111827'),('stop','冻结止损','#ef4444')]:
                if c in r and pd.notna(r[c]):line.append((float(r[c]),label,color))
            first=h[(h.index>=touch-pd.Timedelta(hours=12))&(h.index<=touch+pd.Timedelta(hours=8))]
            setup=h[(h.index>=entry-pd.Timedelta(hours=18))&(h.index<=entry+pd.Timedelta(hours=3))]
            zoom=f[(f.index>=entry-pd.Timedelta(hours=3))&(f.index<=entry+pd.Timedelta(hours=6))]
            end=f[(f.index>=exit_-pd.Timedelta(hours=4))&(f.index<=exit_+pd.Timedelta(hours=2))]
            path=f[(f.index>=touch-pd.Timedelta(hours=1))&(f.index<=exit_+pd.Timedelta(hours=1))]
            fig,axes=plt.subplots(5,1,figsize=(18,19),gridspec_kw={'height_ratios':[1,1.15,1,1,.85]})
            marks=[(touch,'首次触价','#2563eb'),(entry,'成交柱/挂单参考','#111827')]
            if pd.notna(r.get('submitted_at')):
                marks.append((pd.Timestamp(r['submitted_at'])-pd.Timedelta(nanoseconds=1),'四根确认后提交','#d97706'))
            candles(axes[0],first,'15m 首次触价窗口｜真实六均线；后续画面仅供复盘',line,marks,True)
            candles(axes[1],setup,'15m 入场前后窗口｜首次触价相距较远时，见上方独立窗口',line,marks,True)
            candles(axes[2],zoom,'5m 入场窗口｜限价成交的准确柱内时刻未知',line,marks)
            exit_marks=[(exit_,'退出柱','#9b1c31')] if r['sample_kind']=='trade' else []
            candles(axes[3],end,'5m 退出窗口' if r['sample_kind']=='trade' else '5m 后续窗口｜未成交/失效不得当成一笔交易',line,exit_marks)
            if len(path):
                axes[4].plot(path.index,path.close,lw=.6,color='#2563eb')
                axes[4].fill_between(path.index,path.low,path.high,color='#93c5fd',alpha=.22)
                for price,label,color in line:
                    if path.low.min()<=price<=path.high.max():
                        axes[4].axhline(price,color=color,lw=.65,ls='--',label=label)
                axes[4].axvline(entry,color='#111827',lw=.8,ls=':')
                if r['sample_kind']=='trade':
                    for leg_index,leg in enumerate(r.get('leg_exits',[dict(exit_interval_start=exit_,exit_price=r.get('exit_price'),policy=r['exit_mode'],reason=r.get('reason'))])):
                        when=pd.Timestamp(leg['exit_interval_start']);price=leg.get('exit_price')
                        if price is not None and np.isfinite(price):
                            text=('周一目标腿' if leg['policy']=='monday_full' else '远侧目标腿') if r.get('leg_exits') else '退出'
                            suffix={1:'止损',4:'止盈',0:'边界未决'}.get(int(leg['reason']),'')
                            axes[4].scatter([when],[price],s=18,color='#9b1c31',zorder=4)
                            axes[4].annotate(text+suffix,(when,price),xytext=(-3,8+12*leg_index),ha='right',textcoords='offset points',fontsize=7,color='#9b1c31')
                handles,labels=axes[4].get_legend_handles_labels()
                if handles:axes[4].legend(loc='upper right',fontsize=6,ncol=3,framealpha=.8)
            axes[4].set_title('完整观察路径｜原始5m收盘线及高低范围，非压缩K线；横轴UTC',loc='left',fontsize=11)
            axes[4].grid(alpha=.15);axes[4].tick_params(labelsize=8)
            basis='毛' if symbol=='XAUUSD' else '净';value=r.get('return_r',np.nan)
            detail=f'{basis}收益 {value:+.3f}R' if pd.notna(value) else str(r['outcome'])
            fig.suptitle(f'{sid}  {"多头" if r["side"]==1 else "空头"}  {detail}',x=.07,ha='left',fontsize=17,fontweight='bold')
            fig.text(.07,.953,f'触发周 {r.get("week", "见明细")}  |  首次触价 UTC {touch:%Y-%m-%d %H:%M}  |  '
                     f'入场/观察参考 北京 {entry.tz_convert("Asia/Shanghai"):%Y-%m-%d %H:%M}  |  '
                     f'退出/后续参考 北京 {exit_.tz_convert("Asia/Shanghai"):%Y-%m-%d %H:%M}',fontsize=9,color='#475569')
            fig.text(.07,.018,'北京时间用于K线面板；均线仅取已完成15m。成交顺序为框架 O-H-L-C 假设。样本为固定随机种子抽取，不能代替全量统计。',fontsize=9,color='#64748b')
            fig.subplots_adjust(left=.07,right=.89,top=.94,bottom=.055,hspace=.39)
            image=folder/(sid+'.png');fig.savefig(image,dpi=140,facecolor='#fff');plt.close(fig)
            raw=folder/(sid+'-5m.csv.gz');path.rename_axis('open_time_utc').to_csv(raw)
            ma=folder/(sid+'-15m-MA.csv.gz')
            pd.concat([first,setup]).loc[lambda x:~x.index.duplicated()].sort_index().rename_axis('open_time_utc').to_csv(ma)
            records.append(finite(dict(sample_id=sid,**r,image_file=str(image.relative_to(ART)),
                data_file=str(raw.relative_to(ART)),ma_data_file=str(ma.relative_to(ART)),
                scope_start=str(path.index[0]) if len(path) else None,scope_end=str(path.index[-1]) if len(path) else None,
                direction='多' if r['side']==1 else '空')))
        print(f'Charts {len(records)}/{len(rows)} {symbol}',flush=True)
    dump(ART/'samples.json',records)
    pd.DataFrame(records).to_csv(EXP/'samples.csv',index=False)


if __name__=='__main__':main()
