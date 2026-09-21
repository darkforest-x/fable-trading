"""Owner-requested retrospective Telegram gallery from the frozen 448 winners.

Select realized net R descending, not future peak R. Verify the original blind
pack and each raw stream. Native OHLC and SMA/EMA20/60/120 are unchanged; future
bars appear only in this review gallery, physically outside the blind inputs.
The supplied TradingView reference determines styling, not prices or signals.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_10r_search as source
from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation.spike_10r_visual_audit import EXP, MAS
from yoyo.evaluation.spike_v8_six_filters import _committed

PACK = EXP / 'pack_v1'
OUT = EXP / 'tg_top50_v1'


def selection():
    receipt = json.loads((PACK / 'receipt.json').read_text())
    if source.digest(PACK / 'private.csv') != receipt['files']['private.csv']:
        raise ValueError('private selection identity changed')
    rows = pd.read_csv(PACK / 'private.csv')
    winners = rows.loc[rows.cohort.eq('winner_gt10') & ~rows['repeat']].copy()
    if len(winners) != 448 or winners.event_key.duplicated().any() or not winners.net_r.gt(10).all():
        raise ValueError('448 unique original winners required')
    chosen = winners.sort_values(['net_r', 'event_key'], ascending=[False, True]).head(50).copy()
    chosen['rank'] = np.arange(1, 51)
    return chosen, receipt


def plot(frame, row, destination):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    from matplotlib.collections import LineCollection, PolyCollection
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    font = FontProperties(fname='/System/Library/Fonts/STHeiti Light.ttc')
    signal = int(frame.index.get_loc(pd.Timestamp(row.signal_bar_open)))
    entry_i = int(frame.index.get_loc(pd.Timestamp(row.entry_time)))
    exit_i = int(frame.index.get_loc(pd.Timestamp(row.exit_time)))
    if signal != int(row.local_i) or entry_i != signal + 1:
        raise ValueError('signal/entry clock mismatch')
    entry, stop, exit_price = float(row.entry_price), float(row.initial_stop), float(row.exit_price)
    risk = entry - stop
    if not np.isclose(float(frame.open.iloc[entry_i]), entry) or risk <= 0:
        raise ValueError('entry price mismatch')
    if not np.isclose((exit_price-entry-.002*entry)/risk, float(row.net_r)):
        raise ValueError('net R mismatch')
    start, end = max(0, signal-48), min(len(frame), exit_i+13)
    part = frame.iloc[start:end]
    if not np.isfinite(part[['open','high','low','close',*MAS]].to_numpy()).all():
        raise ValueError('unknown plotted price/MA')
    x = (part.index-part.index[0]).total_seconds().to_numpy()/60/int(row.timeframe_min)
    xe, xx, xs = x[entry_i-start], x[exit_i-start], x[signal-start]
    o,h,l,c = (part[k].to_numpy(float) for k in ('open','high','low','close'))
    fig = plt.figure(figsize=(9, 13.5), dpi=120, facecolor='white')
    ax = fig.add_axes([.07,.115,.785,.745])
    colors = np.where(c>=o, '#4278df', '#8763ba')
    ax.add_collection(LineCollection([[(v,a),(v,b)] for v,a,b in zip(x,l,h)], colors=colors, linewidths=.7, zorder=4))
    eps = max(float(np.ptp(h))*.0003, float(np.median(c))*1e-6)
    verts = [[(v-.32,a),(v+.32,a),(v+.32,b if abs(b-a)>eps else a+eps),(v-.32,b if abs(b-a)>eps else a+eps)] for v,a,b in zip(x,o,c)]
    ax.add_collection(PolyCollection(verts, facecolors=colors, edgecolors=colors, linewidths=.35, zorder=5))
    for period, color in [(20,'#35a398'), (60,'#70a4cf'), (120,'#aaa997')]:
        ax.plot(x,part[f's{period}'],color=color,lw=1.15,zorder=3)
        ax.plot(x,part[f'e{period}'],color=color,lw=.95,ls='--',alpha=.85,zorder=3)
    lower = min(float(l.min()),stop,float(part[list(MAS)].min().min()))
    upper = max(float(h.max()),float(part[list(MAS)].max().max()))
    span = upper-lower
    pad = max(span*.055, entry*.001)
    ax.set_ylim(lower-pad,upper+pad)
    ax.set_xlim(float(x[0])-1,float(x[-1])+max(4,len(part)*.06))
    ax.add_patch(Rectangle((xe,entry),max(xx-xe,.6),exit_price-entry,facecolor='#65b9a2',edgecolor='#67ae9c',alpha=.12,lw=1,zorder=0))
    ax.add_patch(Rectangle((xe,stop),max(xx-xe,.6),risk,facecolor='#d7a0af',edgecolor='#c891a2',alpha=.19,lw=1,zorder=0))
    for value, color, style in [(entry,'#419790','-'),(stop,'#c07890','--'),(exit_price,'#479b8f','--')]:
        ax.hlines(value,xe,xx,color=color,lw=.9,linestyles=style,zorder=2)
    last_level = entry
    for r in [3,5,10]:
        value = entry+r*risk
        if value < exit_price and value-last_level > span*.035 and exit_price-value > span*.035:
            ax.hlines(value,xe,xx,color='#7eaea5',ls='--',lw=.65,alpha=.8)
            ax.text(xe+max(1,len(part)*.01),value,f'{r}R',color='#63958c',fontsize=9,va='bottom')
            last_level = value
    ax.scatter([xe],[entry],marker='^',s=45,color='#258f88',zorder=8)
    ax.annotate('入场',xy=(xe,entry),xytext=(-18,-27),textcoords='offset points',fontproperties=font,fontsize=10,color='#258f88',arrowprops={'arrowstyle':'-','color':'#258f88','lw':.6})
    ax.scatter([xx],[exit_price],marker='o',s=26,facecolor='white',edgecolor='#258f88',zorder=8)
    ax.annotate('实际退出',xy=(xx,exit_price),xytext=(-58,16),textcoords='offset points',fontproperties=font,fontsize=10,color='#258f88',arrowprops={'arrowstyle':'-','color':'#258f88','lw':.6})
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MaxNLocator(nbins=13))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v:.6g}'))
    ax.tick_params(axis='both',labelsize=10,colors='#7e8790',length=0,pad=9)
    tick_values=np.linspace(float(x[0]),float(x[-1]),5)
    ax.set_xticks(tick_values)
    ax.set_xticklabels([(part.index[0]+pd.Timedelta(minutes=float(t)*int(row.timeframe_min))).tz_convert('Asia/Shanghai').strftime('%m-%d\n%H:%M') for t in tick_values])
    ax.grid(color='#cfd6dd',alpha=.65,lw=.55,ls=(0,(2,4)))
    for spine in ax.spines.values():spine.set_visible(False)
    tf = f'{int(row.timeframe_min)//60}h' if int(row.timeframe_min)%60==0 else f'{int(row.timeframe_min)}m'
    fig.text(.07,.956,f'{int(row["rank"]):02d} / 50    {row.asset}USDT',fontsize=20,color='#243443',weight='bold')
    fig.text(.07,.921,f'{row.venue.upper()}  ·  {tf}  ·  {row.visual_id}',fontsize=12,color='#7d8791')
    fig.text(.855,.924,f'{float(row.net_r):.2f}R',fontsize=18,color='#278c83',ha='right')
    fig.text(.07,.888,'SMA — / EMA --',fontsize=10,color='#8b959e')
    for left,label,color in [(.36,'20','#35a398'),(.49,'60','#70a4cf'),(.62,'120','#aaa997')]:
        fig.text(left,.888,label,color=color,fontsize=11)
    local=pd.Timestamp(row.entry_time).tz_convert('Asia/Shanghai')
    fig.text(.07,.065,f'入场  {entry:.7g}    ·    初始 SL  {stop:.7g}    ·    退出  {exit_price:.7g}',fontproperties=font,fontsize=11,color='#4e5d6a')
    fig.text(.07,.042,f'{local:%Y-%m-%d %H:%M} 北京时间   |   历史交易复盘 · 净R扣除20bp成本',fontproperties=font,fontsize=10,color='#7d8791')
    fig.text(.07,.021,'原始周期K线；绿色区间为入场至实际退出，非预设止盈；信号前后均显示。',fontproperties=font,fontsize=9,color='#939aa2')
    fig.savefig(destination,dpi=120,facecolor='white')
    plt.close(fig)
    return {'rank':int(row['rank']),'event_key':row.event_key,'visual_id':row.visual_id,'asset':row.asset,
            'venue':row.venue,'timeframe_min':int(row.timeframe_min),'entry_time':row.entry_time,'exit_time':row.exit_time,
            'entry_price':entry,'initial_stop':stop,'exit_price':exit_price,'net_r':float(row.net_r),
            'visible_start':str(part.index[0]),'visible_end':str(part.index[-1]),'bars':len(part),
            'path':str(destination),'sha256':source.digest(destination)}


def build(limit=50):
    deps=[Path(__file__),EXP/'TG_TOP50_PLAN.md']
    if not _committed(deps):raise ValueError('commit renderer and chart plan before build')
    selected, receipt=selection()
    OUT.mkdir(exist_ok=True)
    selected.to_csv(OUT/'selection.csv',index=False)
    sources={r['stream_key']:r for r in receipt['sources']}
    rows=[]
    for _,row in selected.head(limit).iterrows():
        raw=base.SOURCE_STREAMS/row.stream_key
        if source.digest(raw/'control_cache.pkl.gz')!=sources[row.stream_key]['raw_cache_sha256']:
            raise ValueError('raw source changed')
        context=base.load_verified_stream(raw)
        dst=OUT/f'{int(row["rank"]):02d}_{row.asset}_{int(row.timeframe_min)}m.png'
        rows.append(plot(context.cache['bars'],row,dst))
        print(f'rendered {int(row["rank"]):02d}/50 {row.asset}',flush=True)
    manifest={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'source_receipt_sha256':source.digest(PACK/'receipt.json'),'selection':'net_r descending; event_key ascending',
              'expected_images':50,'images':rows,'production_eligible':False,'training_eligible':False}
    source.dump(OUT/('manifest.json' if limit==50 else 'preview_manifest.json'),manifest)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=50)
    args=parser.parse_args();build(args.limit)
