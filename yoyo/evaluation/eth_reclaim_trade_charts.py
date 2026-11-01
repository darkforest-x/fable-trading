"""Owner review plots of recent ETH reclaim samples and saved native fills.

Inputs are frozen census OHLC, the previous VectorBT aggregate trade ledger,
and its native legs. No entry/exit search, new PnL or parameter change occurs.
Each overview shows a source event, including excluded and unfilled events;
each execution image shows one original candidate and its selected path.
Partial exits read both half-position records rather than the aggregate row.
All chart times are Asia/Shanghai. Intrabar fills are labelled 15m intervals,
never presented as observed ticks. Future prices are review context only.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
from zipfile import ZipFile,ZIP_DEFLATED
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_1618_early_marks_charts import setup,BG,FG,MUTED,GOLD,FONT

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-eth-reclaim-trade-charts-20261101-v1'
OUT=ROOT/'artifacts/eth-reclaim-trade-review-20261101'
CENSUS=ROOT/'experiments/active/exp-top20-12h-1618-reclaims-20261101-v1'
STUDY=ROOT/'experiments/active/exp-top20-12h-strategies-20261101-v1'
OHLC=['open','high','low','close'];TZ=ZoneInfo('Asia/Shanghai')
ENTRY='#65b8ff';STOP='#fa7585';TARGET='#5dd5b1';PURPLE='#b4a1f2'
MODES={'A_limit':'A 回挂1.618','B_618':'B 0.618回调确认','B_705':'B 0.705回调确认','C_sweep':'C 二次扫针'}
TARGETS={'5R':'5R止盈','10R':'10R止盈','near':'近侧区间止盈','opposite':'对侧1.618止盈','partial':'5R平一半＋余仓对侧'}
REASONS={'target':'止盈成交','stop':'止损成交','time':'到期平仓','censored':'数据截止，未退出'}
STATUS={'no_confirmation':'没有出现规定的15m确认','no_post_needle_impulse':'针尖后无完整15m反弹段',
        'no_next_open_before_expiry':'确认后已超过等待期限','opening_quote_invalidates_stop':'下一根开盘已越过止损',
        'missing_confirmation_quote':'确认后报价缺失','expired':'挂单有效期已结束'}


def dump(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False,default=str)+'\n')


def stamp(t,full=False):
    return pd.Timestamp(t).tz_convert(TZ).strftime('%Y-%m-%d %H:%M' if full else '%m-%d %H:%M')


def dn(t):
    import matplotlib.dates as md
    return md.date2num(pd.Timestamp(t).to_pydatetime())


def interval(t,exact=False):
    return stamp(t) if exact else stamp(t)+'–'+(pd.Timestamp(t)+pd.Timedelta(minutes=15)).tz_convert(TZ).strftime('%H:%M')


def prepare():
    cfg=json.loads((EXP/'config.json').read_text())
    paths=[CENSUS/'events.csv',CENSUS/'ETH/bars12.csv.gz',STUDY/'trades.csv.gz',
           STUDY/'symbols/ETH/legs.csv.gz',STUDY/'symbols/ETH/entries.csv.gz',STUDY/'symbols/ETH/coverage.csv',STUDY/'config.json',
           ROOT/'experiments/active/exp-top20-1618-short-marks-20261031-v1/ETH/bars.csv.gz']
    receipt=[dict(path=str(p),bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in paths]
    f=pd.read_csv(paths[-1],float_precision='round_trip');f.index=pd.DatetimeIndex(pd.to_datetime(f.pop('open_time'),utc=True)).as_unit('ns')
    b=pd.read_csv(paths[1],index_col=0,parse_dates=True,float_precision='round_trip');b.index=pd.to_datetime(b.index,utc=True)
    e=pd.read_csv(paths[0],float_precision='round_trip');e=e[e.symbol.eq('ETH') & pd.to_datetime(e.signal_open,utc=True).ge(pd.Timestamp(cfg['start']))].sort_values(['signal_open','side'])
    assert len(e)==14 and e.kind.value_counts().to_dict()=={'pure_wick':11,'body_reclaim':3}
    trades=pd.read_csv(paths[2],float_precision='round_trip');trades=trades[~trades.control & trades.symbol.eq('ETH') & trades.event_id.isin(e.event_id)]
    legs=pd.read_csv(paths[3],float_precision='round_trip');legs=legs[~legs.control & legs.event_id.isin(e.event_id)]
    entries=pd.read_csv(paths[4],float_precision='round_trip');entries=entries[entries.event_id.isin(e.event_id)]
    coverage=pd.read_csv(paths[5]);recipes={v['config']:v for v in json.loads(paths[6].read_text())['candidates']}
    events=[];panels=[]
    for number,r in enumerate(e.to_dict('records'),1):
        t=pd.Timestamp(r['signal_open']).tz_convert('UTC');bar=b.loc[t]
        np.testing.assert_allclose([r[k] for k in OHLC],bar[OHLC].to_numpy(float),atol=1e-9,rtol=0)
        r['number']=number;r['weekday']='一二三四五六日'[t.tz_convert(TZ).dayofweek]
        r['folder']=f'{number:02d}_{t.tz_convert(TZ):%Y%m%d_%H%M}_{"long" if r["side"]==1 else "short"}'
        admitted=r['kind']=='pure_wick' and t.tz_convert(TZ).dayofweek in [1,3]
        r['admitted']=admitted
        r['exclusion']=('实体收回，不属于本轮纯插针策略' if r['kind']!='pure_wick' else
                        f'周{r["weekday"]}，不在周二／周四范围' if not admitted else '')
        mode_states={}
        for mode,label in MODES.items():
            if not admitted:mode_states[mode]='未纳入回测';continue
            cv=coverage[coverage.event_id.eq(r['event_id']) & coverage['mode'].eq(mode)]
            assert len(cv)==1
            status=cv.status.iloc[0]
            en=entries[entries.event_id.eq(r['event_id']) & entries['mode'].eq(mode)]
            if len(en) and en.filled.any():
                z=en[en.filled].iloc[0]
                mode_states[mode]=f'已入场 {stamp(z.entry_time)} · {z.entry:,.2f}'
            elif status=='pending':mode_states[mode]='确认后挂单，24小时内未成交'
            else:mode_states[mode]=STATUS.get(status,status)
        r['mode_states']=mode_states;r['overview_path']=f'overview/{r["folder"]}.png'
        event_trades=trades[trades.event_id.eq(r['event_id'])]
        for tr in event_trades.to_dict('records'):
            recipe=recipes[tr['config']]
            names=['partial_5R','partial_opposite'] if recipe['target']=='partial' else [recipe['target']]
            lg=legs[legs.event_id.eq(r['event_id']) & legs['mode'].eq(recipe['mode']) & legs.path.eq(tr['path']) & legs.days.eq(recipe['days']) & legs.target_name.isin(names)]
            assert len(lg)==len(names)
            np.testing.assert_allclose(lg.net_r.sum(),tr['net_r'],atol=1e-9,rtol=0)
            np.testing.assert_allclose(lg.entry,tr['entry'],atol=1e-9,rtol=0)
            np.testing.assert_allclose(lg.stop,tr['stop'],atol=1e-9,rtol=0)
            assert pd.Timestamp(tr['entry_time'])>=pd.Timestamp(r['signal_close'])
            for lr in lg.itertuples():
                assert f.index[int(lr.entry_i)]==pd.Timestamp(lr.entry_time)
                assert f.index[int(lr.exit_i)]==pd.Timestamp(lr.exit_time)
            tr['legs']=lg.sort_values('exit_time').to_dict('records')
            tr['recipe']=recipe;tr['sample']=r
            tr['relative_path']=f'samples/{r["folder"]}/{tr["config"]}.png'
            panels.append(tr)
        r['variants']=len(event_trades);events.append(r)
    assert len(panels)==60
    return f,b,events,panels,receipt


def candles(ax,frame,step,signal=None):
    from matplotlib.collections import PatchCollection
    from matplotlib.patches import Rectangle
    xs=[];lo=[];hi=[];colors=[];patches=[]
    width=step.total_seconds()/86400*.69
    for t,r in frame.iterrows():
        if not np.isfinite(r[OHLC].to_numpy(float)).all():continue
        x=dn(t+step/2);col='#b1becd' if r.close>=r.open else '#586675'
        if signal is not None and t==signal:col=GOLD
        xs.append(x);lo.append(r.low);hi.append(r.high);colors.append(col)
        patches.append(Rectangle((x-width/2,min(r.open,r.close)),width,max(abs(r.close-r.open),(r.high-r.low)*.012,.015)))
    ax.vlines(xs,lo,hi,color=colors,lw=1)
    ax.add_collection(PatchCollection(patches,facecolors=colors,edgecolors=colors,lw=.5))


def decorate(ax,start,end,zoom=False):
    import matplotlib.dates as md
    from matplotlib.ticker import MaxNLocator,FuncFormatter
    ax.set_xlim(dn(start),dn(end));ax.grid(axis='y',color=MUTED,alpha=.15)
    locator=md.AutoDateLocator(minticks=4,maxticks=7,tz=TZ)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(md.DateFormatter('%m-%d\n%H:%M' if zoom else '%m-%d',tz=TZ))
    ax.yaxis.set_major_locator(MaxNLocator(5 if zoom else 6))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda p,_:f'{p:,.0f}'))
    ax.tick_params(colors=MUTED,labelsize=11)
    for spine in ax.spines.values():spine.set_color('#344355')


def bounds(ax,frame,extras=()):
    prices=np.r_[frame.low.dropna().to_numpy(),frame.high.dropna().to_numpy(),list(extras)]
    low=float(prices.min());high=float(prices.max());pad=max((high-low)*.12,3.)
    ax.set_ylim(low-pad,high+pad)


def price_labels(ax,levels):
    """Stable sorted vertical spacing, leader lines, exact price text."""
    low,high=ax.get_ylim();ordered=sorted([v for v in levels if low<=v[1]<=high],key=lambda v:v[1])
    if not ordered:return
    slots=[]
    for _,value,_ in ordered:
        y=(value-low)/(high-low);slots.append(max(y,.06 if not slots else slots[-1]+.075))
    if slots[-1]>.94:
        slots[-1]=.94
        for i in range(len(slots)-2,-1,-1):slots[i]=min(slots[i],slots[i+1]-.075)
    for (label,value,color),y in zip(ordered,slots):
        ax.annotate(f'{label} {value:,.2f}',xy=(1,(value-low)/(high-low)),xycoords='axes fraction',
                    xytext=(1.018,y),textcoords='axes fraction',color=color,fontsize=11,
                    va='center',annotation_clip=False,
                    arrowprops=dict(arrowstyle='-',color=color,lw=.6))


def context(ax,b,event,trade=None):
    signal=pd.Timestamp(event['signal_open']).tz_convert('UTC');confirm=pd.Timestamp(event['signal_close']).tz_convert('UTC')
    start=signal-pd.Timedelta(days=6);valid=b[b.complete];end=valid.index[-1]+pd.Timedelta(hours=12)
    frame=b[(b.index>=start)&(b.index<end)]
    candles(ax,frame,pd.Timedelta(hours=12),signal)
    levels=[('1.618',event['level'],GOLD)]
    if trade:
        levels += [('入场',trade['entry'],ENTRY),('初始止损',trade['stop'],STOP)]
        for i,lr in enumerate(trade['legs'],1):levels.append((f'目标{i}' if len(trade['legs'])>1 else '目标',lr['target'],TARGET))
    bounds(ax,frame,[v[1] for v in levels]);decorate(ax,start,end)
    ax.axvspan(dn(confirm),dn(end),color='#9daec9',alpha=.04)
    ax.axvline(dn(confirm),color=FG,ls=':',lw=.9)
    ax.text(dn(confirm),.98,' 12h确认',transform=ax.get_xaxis_transform(),ha='left',va='top',color=MUTED,fontsize=11)
    ready=max(pd.Timestamp(event['ready']),start);expiry=pd.Timestamp(event['expiry'])
    ax.hlines(event['level'],dn(ready),dn(min(expiry,end)),color=GOLD,lw=1.5)
    if expiry<end:ax.hlines(event['level'],dn(expiry),dn(end),color=GOLD,ls='--',lw=1,alpha=.55)
    for key in ['monday_low','monday_high']:
        ax.hlines(event[key],dn(ready),dn(min(expiry,end)),color=MUTED,ls=':',alpha=.5,lw=.8)
    pin=event['low'] if event['side']==1 else event['high']
    ax.scatter(dn(signal+pd.Timedelta(hours=6)),pin,s=120,facecolors='none',edgecolors=GOLD,lw=2,zorder=8)
    ax.scatter(dn(signal+pd.Timedelta(hours=6)),event['close'],s=30,color=FG,marker='D',zorder=8)
    if trade:position(ax,trade,start,end,labels=False)
    price_labels(ax,levels)


def position(ax,trade,start,end,labels=True):
    """Active protection is solid; post-exit references are explicitly dashed."""
    def exit_marker(lr):
        t=pd.Timestamp(lr['exit_time'])
        return t if lr['reason']=='time' or int(lr['exit_segment'])==0 else t+pd.Timedelta(minutes=7.5)
    entry=pd.Timestamp(trade['entry_time']);last=max(exit_marker(lr) for lr in trade['legs'])
    entry_exact=int(trade['entry_segment'])%4==0
    mx=entry if entry_exact else entry+pd.Timedelta(minutes=7.5)
    low,high=ax.get_ylim()
    levels=[('入场',trade['entry'],ENTRY),('初始止损',trade['stop'],STOP)]
    for i,lr in enumerate(trade['legs'],1):levels.append((f'目标{i}' if len(trade['legs'])>1 else '目标',lr['target'],TARGET))
    ends=[last,last]+[exit_marker(lr) for lr in trade['legs']]
    for (label,p,color),active_end in zip(levels,ends):
        if low<=p<=high:
            left=max(mx,start);right=min(active_end,end)
            if right>left:ax.hlines(p,dn(left),dn(right),color=color,lw=1.2)
            if active_end<end:ax.hlines(p,dn(max(active_end,start)),dn(end),color=color,ls=':',lw=.8,alpha=.45)
    if start<=mx<=end and low<=trade['entry']<=high:
        ax.scatter(dn(mx),trade['entry'],marker='^' if trade['side']==1 else 'v',s=105,color=ENTRY,edgecolors=BG,lw=.6,zorder=10)
        ax.annotate('入场',xy=(dn(mx),trade['entry']),xytext=(5,14),textcoords='offset points',color=ENTRY,fontsize=11)
    for i,lr in enumerate(trade['legs'],1):
        x=exit_marker(lr)
        if start<=x<=end and low<=lr['exit_price']<=high:
            color=STOP if lr['reason']=='stop' else TARGET if lr['reason']=='target' else FG
            ax.scatter(dn(x),lr['exit_price'],marker='X',s=90,color=color,edgecolors=BG,lw=.6,zorder=10)
            title=(f'退出{i}' if len(trade['legs'])>1 else REASONS[lr['reason']])
            ax.annotate(title,xy=(dn(x),lr['exit_price']),xytext=(6,-22 if lr['reason']=='stop' else 14),textcoords='offset points',color=color,fontsize=11)
    if labels:
        price_labels(ax,levels)
        if any(not low<=p<=high for _,p,_ in levels):
            ax.text(.01,.02,'窗外价位见上方12h总览',transform=ax.transAxes,color=MUTED,fontsize=10)


def zoom(ax,f,event,trade,exit_view=False):
    t=pd.Timestamp(trade['exit_time'] if exit_view else trade['entry_time'])
    start=max(t-pd.Timedelta(hours=6),f.index[0]);end=min(t+pd.Timedelta(hours=6),f.index[-1]+pd.Timedelta(minutes=15))
    frame=f[(f.index>=start)&(f.index<end)]
    candles(ax,frame,pd.Timedelta(minutes=15))
    extras=[trade['legs'][-1]['exit_price']] if exit_view else [trade['entry'],trade['stop']]
    bounds(ax,frame,extras);decorate(ax,start,end,True)
    level=event['level']
    if ax.get_ylim()[0]<=level<=ax.get_ylim()[1]:ax.axhline(level,color=GOLD,lw=.9,ls='--',alpha=.7)
    if not exit_view and np.isfinite(trade.get('retrace',np.nan)):
        p=trade['retrace']
        if ax.get_ylim()[0]<=p<=ax.get_ylim()[1]:
            ax.axhline(p,color=PURPLE,lw=.8,ls=':')
            ax.text(.01,.97,f'回调线 {p:,.2f}',transform=ax.transAxes,color=PURPLE,fontsize=11,va='top')
    position(ax,trade,start,end)


def title(recipe):
    return f'{MODES[recipe["mode"]]} ｜ {TARGETS[recipe["target"]]} ｜ 最长{recipe["days"]}天'+(' ｜近侧空间≥5R' if recipe['space_filter'] else '')


def render_trade(f,b,trade):
    import matplotlib.pyplot as plt
    event=trade['sample'];out=OUT/trade['relative_path'];out.parent.mkdir(parents=True,exist_ok=True)
    fig=plt.figure(figsize=(20,12.8),dpi=150)
    fig.text(.055,.965,f'ETHUSDT.P · 样本 {event["number"]:02d} / 14 · 回测逐笔核对',fontsize=25,weight='bold')
    fig.text(.055,.928,title(trade['recipe']),fontsize=18)
    fig.text(.86,.965,f'{trade["net_r"]:+.2f} R',ha='right',fontsize=25,color=TARGET if trade['net_r']>0 else STOP,weight='bold')
    fig.text(.055,.898,f'信号K开盘 {stamp(event["signal_open"],True)}（周{event["weekday"]}）  ·  {"做多" if event["side"]==1 else "做空"}  ·  全部时间为北京时间',fontsize=14,color=MUTED)
    top=fig.add_axes([.055,.615,.80,.245]);context(top,b,event,trade)
    fig.text(.055,.869,'12h全景：保留退出以后的完整已知走势',fontsize=15)
    left=fig.add_axes([.055,.265,.345,.26]);zoom(left,f,event,trade)
    right=fig.add_axes([.565,.265,.29,.26]);zoom(right,f,event,trade,True)
    fig.text(.055,.545,'15m入场放大 · 独立纵轴',fontsize=16)
    fig.text(.565,.545,'15m最终退出放大 · 独立纵轴',fontsize=16)
    entry_when=interval(trade['entry_time'],int(trade['entry_segment'])%4==0)
    fig.text(.055,.198,f'确认 {stamp(event["signal_close"])}  →  入场 {entry_when}  @ {trade["entry"]:,.2f}    初始止损 {trade["stop"]:,.2f}',fontsize=15,color=ENTRY)
    for i,lr in enumerate(trade['legs']):
        exact=lr['reason']=='time' or int(lr['exit_segment'])==0
        line=f'{"半仓" if lr["weight"]==.5 else "整仓"} · 计划目标 {lr["target"]:,.2f}    →    {REASONS[lr["reason"]]} {interval(lr["exit_time"],exact)}  @ {lr["exit_price"]:,.2f}    净 {lr["net_r"]:+.2f}R'
        fig.text(.055,.160-i*.035,line,fontsize=14,color=FG)
    fig.text(.055,.078,'蓝三角=入场；红线=初始止损；绿线=计划止盈；X=回测退出。退出后的虚线仅作价位参考，不代表继续持仓。',fontsize=12,color=MUTED)
    fig.text(.055,.048,'止盈/止损的区间时间表示只能定位到该15m K线；使用原账本较保守路径。12h只画已完成K，未来仅供人工复盘。',fontsize=11,color=MUTED)
    fig.text(.055,.023,f'原回测成交记录，非实盘成交 · 已扣交易手续费，未计资金费/滑点 · 原始15m截至 {stamp(f.index[-1]+pd.Timedelta(minutes=15),True)} · {trade["config"]}',fontsize=10,color=MUTED)
    fig.savefig(out,dpi=150,pil_kwargs={'compress_level':1});plt.close(fig)
    return str(out)


def render_overview(f,b,event,panels):
    import matplotlib.pyplot as plt
    main=next((r for r in panels if r['event_id']==event['event_id'] and r['config']=='B_618_10R_14d'),None)
    fig=plt.figure(figsize=(20,11.6),dpi=150)
    fig.text(.055,.955,f'ETHUSDT.P · 样本 {event["number"]:02d} / 14 · 12h样本总览',fontsize=26,weight='bold')
    kind='纯插针' if event['kind']=='pure_wick' else '实体收回'
    fig.text(.055,.915,f'{stamp(event["signal_open"],True)} 开盘（周{event["weekday"]}） · {"下刺收回" if event["side"]==1 else "上刺收回"} · {kind}',fontsize=19)
    sub=event['exclusion'] or ('本图叠加B618 / 10R；其他成交版本在同编号子目录' if main else '符合初始筛选，但本例没有策略成交')
    fig.text(.055,.878,sub,fontsize=15,color=MUTED if event['admitted'] else GOLD)
    ax=fig.add_axes([.055,.34,.80,.50]);context(ax,b,event,main)
    for i,(mode,name) in enumerate(MODES.items()):
        fig.text(.055,.252-i*.04,f'{name}：{event["mode_states"][mode]}',fontsize=15,color=FG)
    if not event['admitted']:
        fig.text(.57,.252,'未生成入场、止损、止盈订单；不是回测亏损。',fontsize=14,color=GOLD)
    else:
        fig.text(.57,.252,f'此样本共有 {event["variants"]} 个有成交的参数版本。',fontsize=14,color=FG)
        near=next((r for r in panels if r['event_id']==event['event_id'] and r['config']=='B_618_near_14d'),None)
        if main and near:fig.text(.57,.205,f'B618同一入场：近侧目标 {near["net_r"]:+.2f}R / 10R目标 {main["net_r"]:+.2f}R',fontsize=14,color=FG)
    fig.text(.055,.065,'圆圈=原12h针尖；菱形=当根收盘；竖虚线=信号确认。价格继续运行，不等于回测仓位一直持有。',fontsize=12,color=MUTED)
    fig.text(.055,.032,f'只读原始OHLC与保存的成交账本 · 时间均为北京时间 · 15m数据截至 {stamp(f.index[-1]+pd.Timedelta(minutes=15),True)}',fontsize=11,color=MUTED)
    out=OUT/event['overview_path'];out.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(out,dpi=150,pil_kwargs={'compress_level':1});plt.close(fig);return str(out)


def contacts(events):
    from PIL import Image,ImageDraw,ImageFont
    font=ImageFont.truetype(FONT,30);out=[];folder=OUT/'overview';folder.mkdir(exist_ok=True)
    for page in range(math.ceil(len(events)/4)):
        subset=events[page*4:page*4+4];canvas=Image.new('RGB',(2000,1240),BG);draw=ImageDraw.Draw(canvas)
        draw.text((24,16),f'ETH近期样本 · {page+1}/{math.ceil(len(events)/4)} · 打开单图查看完整标注',font=font,fill=FG)
        for k,r in enumerate(subset):
            with Image.open(OUT/r['overview_path']) as im:
                canvas.paste(im.convert('RGB').resize((1000,580)),((k%2)*1000,65+(k//2)*580))
        p=folder/f'contact_{page+1:02d}.jpg';canvas.save(p,quality=92);out.append(str(p))
    return out


def run(preview=False):
    cfg_path=EXP/'config.json';setup()
    f,b,events,panels,receipt=prepare()
    if not preview:assert not (OUT/'delivery.json').exists(),'Reuse completed delivery'
    selected_events=events if not preview else [e for e in events if e['number'] in [1,3,6,7,14]]
    selected_panels=panels if not preview else [r for r in panels if (r['sample']['number'],r['config']) in [(1,'B_618_partial_14d'),(6,'B_618_10R_14d'),(6,'B_618_near_14d'),(7,'C_sweep_10R_14d')]]
    for r in selected_events:render_overview(f,b,r,panels)
    for i,r in enumerate(selected_panels,1):
        render_trade(f,b,r)
        if i%10==0:print('Rendered',i,'/',len(selected_panels),flush=True)
    if preview:
        print('Preview complete',flush=True);return
    contact=contacts(events);rows=[]
    for e in events:
        rows.append(dict(样本编号=e['number'],信号K开盘北京时间=stamp(e['signal_open'],True),类型=e['kind'],方向='多' if e['side']==1 else '空',策略='样本总览',状态=e['exclusion'] or '见各策略成交状态',图片=e['overview_path']))
    for r in panels:
        rows.append(dict(样本编号=r['sample']['number'],信号K开盘北京时间=stamp(r['signal_open'],True),类型=r['kind'],方向='多' if r['side']==1 else '空',策略=r['config'],状态=REASONS.get(r['reason'],'分批退出'),
            入场北京时间=interval(r['entry_time'],int(r['entry_segment'])%4==0),入场价=r['entry'],初始止损=r['stop'],计划目标=' / '.join(f'{lr["target"]:.2f}' for lr in r['legs']),
            实际退出=' / '.join(f'{interval(lr["exit_time"],lr["reason"]=="time" or int(lr["exit_segment"])==0)} @ {lr["exit_price"]:.2f} ({REASONS[lr["reason"]]})' for lr in r['legs']),净R=r['net_r'],图片=r['relative_path']))
    pd.DataFrame(rows).to_csv(OUT/'逐图索引.csv',index=False,encoding='utf-8-sig')
    payload=[]
    for r in panels:
        payload.append(dict(event_id=r['event_id'],config=r['config'],path=r['path'],entry=r['entry'],stop=r['stop'],entry_time=r['entry_time'],net_r=r['net_r'],image=r['relative_path'],
                            legs=[{k:lr[k] for k in ['leg_id','target','weight','exit_time','exit_price','reason','net_r']} for lr in r['legs']]))
    dump(OUT/'chart_values.json',payload);dump(OUT/'source_receipt.json',receipt)
    from PIL import Image
    images=[OUT/r['overview_path'] for r in events]+[OUT/r['relative_path'] for r in panels]
    for p in images:
        with Image.open(p) as im:assert im.width==3000;im.verify()
    for r in receipt:
        p=Path(r['path']);assert p.stat().st_size==r['bytes'] and p.stat().st_mtime_ns==r['mtime_ns']
    archive=OUT/'ETH近期14样本_60策略核对图.zip'
    with ZipFile(archive,'w',ZIP_DEFLATED,compresslevel=1) as z:
        for p in images+[Path(p) for p in contact]+[OUT/'逐图索引.csv',OUT/'chart_values.json',OUT/'source_receipt.json']:
            z.write(p,p.relative_to(OUT))
        z.write(cfg_path,'config.json')
    with ZipFile(archive) as z:assert z.testzip() is None;assert sum(n.endswith('.png') for n in z.namelist())==74
    delivery=dict(samples=14,pure_wick=11,body_reclaim=3,overviews=14,strategy_charts=60,contacts=contact,archive=str(archive),archive_bytes=archive.stat().st_size,
        index=str(OUT/'逐图索引.csv'),all_images_decoded=True,all_chart_price_values_from_saved_legs=True,inputs_unchanged=True,no_backtest_replay=True,
        first=stamp(events[0]['signal_open'],True),last=stamp(events[-1]['signal_open'],True),source_end=stamp(f.index[-1]+pd.Timedelta(minutes=15),True),
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),training_eligible=False,production_eligible=False)
    dump(OUT/'delivery.json',delivery);dump(EXP/'delivery.json',delivery);print(json.dumps(delivery,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--preview',action='store_true');args=parser.parse_args()
    code=['yoyo/evaluation/eth_reclaim_trade_charts.py','experiments/active/exp-eth-reclaim-trade-charts-20261101-v1/config.json']
    for p in code:subprocess.run(['git','ls-files','--error-unmatch',p],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    assert subprocess.run(['git','diff','--quiet','HEAD','--',*code],cwd=ROOT).returncode==0,'Commit renderer before generating'
    run(args.preview)
