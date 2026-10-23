"""Static5m candle review of cached top trades, never a trade simulator.

SMA/EMA20/60/120 use only closes at/before each plotted candle, computed on the
full original source prefix before cropping. They are descriptive overlays,
not newly imposed entry conditions. Complete5m close and high/low series show
long holding periods without changing the timeframe. Local panels use their
own price scales; levels outside those scales stay listed in the sidebar.
"""
from __future__ import annotations
import json
import textwrap
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter
from yoyo.datasets.ma_rope_filter import add_six_mas, SIX_MA_COLUMNS
from yoyo.evaluation import monday_5m_wick_engulf as old
from yoyo.evaluation import monday_15m_volume_rejection as volume
from yoyo.evaluation.monday_cases_report_charts import FONT, C
from yoyo.evaluation.monday_top_trades import ROOT, OUT, DELIVERY, EXP
from yoyo.evaluation.range_framework import dump, file_stamp

STEP = pd.Timedelta(minutes=5)
MA_COLOR = {20:C['blue'],60:C['amber'],120:C['purple']}


def present(x):
    return x is not None and pd.notna(x)


def price(x):
    return f'{float(x):,.9g}' if present(x) else '未设置'


def when(x):
    return pd.Timestamp(x).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M') if present(x) else '—'


def xpos(f, t):
    """Observed bars have width1; true time labels and explicit gap marks remain."""
    t=pd.Timestamp(t)
    i=int(f.index.searchsorted(t, side='right'))-1
    if i<0:return -.5
    if t>=f.index[-1]+STEP:return len(f)-.5
    return i-.5+float((t-f.index[i])/STEP)


def levels(r):
    result=[('周一高',r.get('monday_high'),'#98a4b4',':'),
            ('周一低',r.get('monday_low'),'#98a4b4',':'),
            ('触价1.618',r.get('level'),C['purple'],'-'),
            ('原止损',r.get('stop'),C['down'],'--'),
            ('止盈',r.get('target_price'),C['blue'],'--'),
            ('入场',r['entry'],C['line'],'-.')]
    if r['source_key']=='partial':
        result += [('TP1',r.get('tp1_price'),C['blue'],'--'),('TP2',r.get('tp2_price'),C['blue'],':')]
    return [(a,float(b),c,d) for a,b,c,d in result if present(b)]


def style(ax, f, show_x=True):
    for s in ('top','right'):ax.spines[s].set_visible(False)
    for s in ('bottom','left'):ax.spines[s].set_color('#d7dee7')
    ax.grid(axis='y',color=C['grid'],lw=.7)
    ax.tick_params(labelsize=9,colors='#526175')
    ticks=sorted(set([0,len(f)-1]+[round(k*(len(f)-1)/5) for k in range(1,5)]))
    ax.set_xticks(ticks)
    ax.set_xticklabels([f.index[i].tz_convert('Asia/Shanghai').strftime('%m/%d\n%H:%M') for i in ticks])
    if not show_x:plt.setp(ax.get_xticklabels(),visible=False)
    ax.set_xlim(-1,len(f))
    gaps=np.flatnonzero(np.diff(f.index.asi8)!=STEP.value)
    for i in gaps:ax.axvline(i+.5,color='#a23d53',lw=.8,ls=':')


def annotate_events(ax, f, r, overview=False):
    lo,hi=ax.get_ylim()
    for name,v,color,ls in levels(r):
        if lo<=v<=hi:ax.axhline(v,color=color,ls=ls,lw=.9,alpha=.78,zorder=1)
    start,end=f.index[0],f.index[-1]+STEP
    et=pd.Timestamp(r['entry_time']);xt=pd.Timestamp(r['exit_interval_start'])
    xe=pd.Timestamp(r['exit_interval_end']);pt=pd.Timestamp(r['pin_time'])
    if start<=et<end:
        x=xpos(f,et);ax.axvline(x,color=C['line'],lw=.9,ls=':')
        ax.scatter([x],[r['entry']],marker='^' if r['side']==1 else 'v',s=65,color=C['line'],zorder=8)
        ax.annotate('E 入场',xy=(x,r['entry']),xytext=(5,15 if r['side']==1 else -20),textcoords='offset points',fontsize=10,color=C['line'],zorder=10)
        ei=pd.Timestamp(r['entry_interval_end'])
        exact_open=str(r.get('opening_fill')).lower() in ('true','1','1.0')
        if ei>et and not exact_open:ax.axvspan(x,xpos(f,ei),color='#7191ab',alpha=.16,zorder=0)
    if start<=xt<end:
        is_grid=r['source_key']=='timed'
        ex=xpos(f,xt if is_grid else xt+(xe-xt)/2)
        ep=r['runner_exit_price'] if r['source_key']=='partial' else r['exit_price']
        ax.scatter([ex],[ep],marker='X',s=64,color=C['amber'],zorder=8)
        ax.axvspan(xpos(f,xt),xpos(f,xe),color='#e5b858',alpha=.22,zorder=0)
        ax.annotate('X 网格参考平仓' if is_grid else 'X 退出时段',xy=(ex,ep),xytext=(6,-18),textcoords='offset points',fontsize=9,color=C['amber'],zorder=10)
    if start<=pt<end:
        px=xpos(f,pt);ax.axvline(px,color=C['purple'],ls=':',lw=.8)
        ax.text(px,hi,'P 触价',va='top',ha='left',fontsize=9,color=C['purple'])
    if present(r.get('tp1_time')) and bool(r.get('tp1_filled')):
        t=pd.Timestamp(r['tp1_time'])
        if start<=t<end:
            x=xpos(f,t)+.5
            ax.scatter([x],[r['tp1_exit_price']],marker='D',s=32,color=C['blue'],zorder=8)
            ax.annotate('TP1 半仓',xy=(x,r['tp1_exit_price']),xytext=(5,12),textcoords='offset points',fontsize=9,color=C['blue'])
    if xe<end:ax.axvspan(max(-.5,xpos(f,xe)),len(f),color='#c9cfd7',alpha=.20,zorder=0)
    if overview:
        ax.axvspan(xpos(f,et),xpos(f,xe),color='#c9dbed',alpha=.15,zorder=0)


def candle_panel(ax,vol,f,r,title):
    o,h,l,c=(f[k].to_numpy(float) for k in old.OHLC)
    x=np.arange(len(f));colors=np.where(c>=o,C['up'],C['down'])
    ax.add_collection(LineCollection([[(i,a),(i,b)] for i,a,b in zip(x,l,h)],colors=colors,linewidths=.8,zorder=3))
    poly=[[(i-.30,a),(i+.30,a),(i+.30,b),(i-.30,b)] for i,a,b in zip(x,np.minimum(o,c),np.maximum(o,c))]
    ax.add_collection(PolyCollection(poly,facecolors=colors,edgecolors=colors,linewidths=.55,zorder=4))
    zero=o==c
    if zero.any():ax.hlines(o[zero],x[zero]-.3,x[zero]+.3,colors=colors[zero],lw=.8,zorder=4)
    # Local scales deliberately omit remote SL/TP prices to preserve candle shape.
    v=f[list(old.OHLC)+list(SIX_MA_COLUMNS)].to_numpy(float)
    low=float(np.nanmin(v));high=float(np.nanmax(v));pad=max((high-low)*.10,high*1e-5)
    ax.set_ylim(low-pad,high+pad)
    for period in (20,60,120):
        for prefix,ls in [('sma','-'),('ema','--')]:
            ax.plot(x,f[f'{prefix}{period}'],c=MA_COLOR[period],ls=ls,lw=.9,alpha=.85)
    ax.text(.5,.55,r['symbol']+' · 5m',transform=ax.transAxes,ha='center',fontsize=35,color='#253e58',alpha=.065,weight='bold')
    ax.set_title(title,loc='left',fontsize=12,pad=9,color=C['line'])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y,_:f'{y:,.8g}'))
    vol.bar(x,f.volume.to_numpy(float),width=.65,color=colors,alpha=.65)
    vol.ticklabel_format(axis='y',style='sci',scilimits=(0,0));vol.yaxis.get_offset_text().set_fontsize(8)
    vol.set_ylabel('成交量',fontsize=9);ax.set_ylabel('价格',fontsize=10)
    style(ax,f,False);style(vol,f);annotate_events(ax,f,r)


def overview_panel(ax,f,r):
    x=np.arange(len(f))
    ax.fill_between(x,f.low.to_numpy(float),f.high.to_numpy(float),color='#8294ac',alpha=.25,lw=0)
    ax.plot(x,f.close.to_numpy(float),color=C['line'],lw=.9,zorder=3)
    low=float(f.low.min());high=float(f.high.max());pad=max((high-low)*.10,high*1e-5)
    ax.set_ylim(low-pad,high+pad)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y,_:f'{y:,.8g}'))
    ax.set_title(f'完整过程 · {len(f):,}根5m收盘线与最高/最低区间（未降采样；蓝底=持仓，灰底=退出后）',loc='left',fontsize=11,pad=10)
    ax.set_ylabel('价格',fontsize=10);style(ax,f);annotate_events(ax,f,r,True)


def one(frame,r):
    entry=pd.Timestamp(r['entry_time']);end=pd.Timestamp(r['exit_interval_end']);pin=pd.Timestamp(r['pin_time'])
    a=frame.loc[entry.floor('5min')-STEP*72:entry.floor('5min')+STEP*120]
    b=frame.loc[pd.Timestamp(r['exit_interval_start']).floor('5min')-STEP*72:end.floor('5min')+STEP*24]
    all_bars=frame.loc[min(pin,entry)-STEP*72:end+STEP*24]
    assert len(a)>1 and len(b)>1 and len(all_bars)>1
    fig=plt.figure(figsize=(20,15),dpi=160)
    gs=fig.add_gridspec(5,1,height_ratios=[4,1,4,1,3.7],hspace=.38)
    fig.subplots_adjust(left=.06,right=.775,top=.83,bottom=.11)
    ap=fig.add_subplot(gs[0]);av=fig.add_subplot(gs[1],sharex=ap)
    bp=fig.add_subplot(gs[2]);bv=fig.add_subplot(gs[3],sharex=bp)
    whole=fig.add_subplot(gs[4])
    candle_panel(ap,av,a,r,f'A 入场附近 · 5分钟K线 · {when(a.index[0])} — {when(a.index[-1])}')
    candle_panel(bp,bv,b,r,f'B 退出附近 · 5分钟K线 · {when(b.index[0])} — {when(b.index[-1])}')
    overview_panel(whole,all_bars,r)
    rank=int(r['rank']);direction='多' if r['side']==1 else '空'
    fig.text(.06,.97,f'#{rank:03d}  {r["symbol"]}  做{direction}    净收益 {r["net_pct"]:+.2f}%',fontsize=23,weight='bold',color=C['line'])
    fig.text(.06,.935,r['scheme'],fontsize=14,color=C['line'])
    fig.text(.06,.908,'按原始开仓名义额计算，不含杠杆｜北京时间 UTC+8｜事后盈利样本｜上下局部图分别自动缩放',fontsize=11,color='#526175')
    handles=[Line2D([0],[0],color=MA_COLOR[p],ls=ls,lw=1.2,label=f'{pre.upper()}{p}')
             for p in (20,60,120) for pre,ls in [('sma','-'),('ema','--')]]
    fig.legend(handles=handles,loc='upper left',bbox_to_anchor=(.055,.891),ncol=6,frameon=False,fontsize=10)
    x=.80;y=.835
    def block(title,lines,color=C['line']):
        nonlocal y
        fig.text(x,y,title,fontsize=11,weight='bold',color=color,va='top');y-=.021
        for line in lines:
            fig.text(x,y,line,fontsize=10,color='#526175',va='top');y-=.019
        y-=.012
    rr=f"{r['net_r']:+.3f}R" if present(r.get('net_r')) else '不适用（无原始止损）'
    block('收益与成本',[f"净 {r['net_pct']:+.4f}%",f"毛 {r['gross_pct']:+.4f}%",f"手续费 {r['fee_pct']:.4f}%",'净R：'+rr])
    pl=[]
    for name,value,color,ls in levels(r):pl.append(f'{name}  {price(value)}')
    if r['source_key']=='timed':pl+=['止损：未设置','止盈：到时市价参考退出']
    block('原方案价格',pl)
    block('关键时间（北京时间）',['触价柱 '+when(pin),'入场   '+when(entry),
        '退出起 '+when(r['exit_interval_start']),'退出止 '+when(end)])
    hours=(end-entry).total_seconds()/3600
    block('记录说明',[f'持仓时长上界 {hours:,.2f}小时',f"同入场盈利版本 {int(r['positive_tested_versions'])}",
        '本图保留最高收益版本',f"原信号周期 {int(r['signal_minutes'])}分钟"])
    if r['source_key']=='timed':
        timing=('开盘成交，持仓时钟精确。' if str(r.get('actual_duration_exact')).lower() in ('true','1','1.0')
                else '触价柱内具体成交秒数未知。')
        block('定时平仓说明',['该收益为网格参考成交；',timing,'真实时钟收益范围见CSV。'],C['amber'])
    elif present(r.get('ambiguous_bar')) and str(r.get('ambiguous_bar')).lower() in ('true','1','1.0'):
        block('同K双触',['原账本标记同K止盈止损双触；','收益沿用原框架路径假设。'],C['amber'])
    fig.text(.06,.066,'E/▲/▼ 入场 · X 退出 · 黄带=退出时间区间 · P=触价柱 · 红竖虚线=数据时间缺口；缺口压缩显示，没有补造K线。',fontsize=10,color='#526175')
    fig.text(.06,.044,'六均线仅用于形态复盘；原策略未要求均线时不视作入场条件。远离局部纵轴的周一线/止盈止损只在右栏列价。',fontsize=10,color='#526175')
    fig.text(.06,.022,f"原记录：{r['source_key']} / {r['config_id']} / {r['trade_id']}   （完整文件与记录序号见CSV）",fontsize=8.8,color='#526175')
    path=DELIVERY/r['image_file'];fig.savefig(path,dpi=160);plt.close(fig)
    datadir=DELIVERY/'逐笔5m原始行情';datadir.mkdir(exist_ok=True)
    data=all_bars[[*old.OHLC,'volume',*SIX_MA_COLUMNS]].copy()
    data.index.name='open_time_utc'
    data.insert(0,'open_time_beijing',data.index.tz_convert('Asia/Shanghai').astype(str))
    data.to_csv(datadir/(path.stem+'.csv.gz'))
    return dict(rank=rank,symbol=r['symbol'],image_file=r['image_file'],
        width=3200,height=2400,entry_panel_bars=len(a),exit_panel_bars=len(b),
        full_5m_bars=len(all_bars),gap_count=int((all_bars.index.to_series().diff().dropna()!=STEP).sum()),
        source_ledger=r['source_ledger'],source_csv_record=int(r['source_csv_record']),
        canonical_trade_id=r['trade_id'],config_id=r['config_id'])


def verify_prices(f,r):
    """Check plotted original bars cover the original fills; no fills recalculated."""
    entry=pd.Timestamp(r['entry_time']).floor('5min');exit=pd.Timestamp(r['exit_interval_start']).floor('5min')
    assert entry in f.index and exit in f.index,(r['rank'],'missing fill candle')
    ep=float(r['entry']);xp=float(r['runner_exit_price'] if r['source_key']=='partial' else r['exit_price'])
    for t,p in [(entry,ep),(exit,xp)]:
        bar=f.loc[t];eps=max(p*1e-8,1e-10)
        assert bar.low-eps<=p<=bar.high+eps,(r['rank'],t,p,bar.low,bar.high)
    if r['source_key']!='timed':
        assert np.isclose(ep,f.loc[entry,'open'],rtol=1e-8,atol=1e-10),(r['rank'],'entry open differs')


def render(ranks=None):
    selected=pd.read_csv(OUT/'selected.csv',float_precision='round_trip')
    if ranks:selected=selected[selected['rank'].isin(ranks)]
    cfg=json.loads((old.EXP/'config.json').read_text())
    manifest=[];inputs=[]
    previous=OUT/'charts-manifest.json'
    if previous.exists():manifest=json.loads(previous.read_text())
    rendered_ranks=set(selected['rank'].astype(int));manifest=[r for r in manifest if r['rank'] not in rendered_ranks]
    for symbol,g in selected.groupby('symbol',sort=True):
        # Frozen per-symbol identities avoid hundreds of missing-tail stat calls
        # on the migrated drive, and preserve the exact backtest input set.
        identity=json.loads((old.EXP/'symbols'/symbol/'identity.json').read_text())
        paths=[]
        for stamp in identity['inputs']:
            name=Path(stamp['path']).name
            original=(ROOT/cfg['crypto_root']/'series'/name if name.startswith('binance_um_')
                      else ROOT/cfg['crypto_tail']/name)
            current=file_stamp(original)
            assert current['bytes']==stamp['bytes'] and current['mtime_ns']==stamp['mtime_ns'],(symbol,'changed source')
            paths.append(str(original));inputs.append(current)
        f=volume.load(paths)
        f['ts']=f.index.as_unit('ms').asi8
        f=add_six_mas(f)
        for record in g.to_dict('records'):
            verify_prices(f,record)
            start=pd.Timestamp(record['week']).tz_localize('America/New_York').tz_convert('UTC')
            mon=f.loc[start:start+pd.Timedelta(days=1)-STEP]
            assert len(mon)==288,(symbol,record['week'],'incomplete original Monday')
            hi,lo=float(mon.high.max()),float(mon.low.min())
            lev=lo-.618*(hi-lo) if record['side']==1 else hi+.618*(hi-lo)
            for key,v in [('monday_high',hi),('monday_low',lo),('level',lev)]:
                if present(record.get(key)):assert np.isclose(record[key],v,rtol=1e-9,atol=1e-10),(symbol,key)
                else:record[key]=v
            result=one(f,record);manifest.append(result)
            dump(previous,sorted(manifest,key=lambda r:r['rank']))
            print(f"Rendered {int(record['rank']):03d} {symbol} ({len(manifest)}/200)",flush=True)
    inputs_path=OUT/'chart-inputs.json'
    prior=json.loads(inputs_path.read_text()) if inputs_path.exists() else []
    dump(inputs_path,list({r['path']:r for r in prior+inputs}.values()))
    if not ranks:
        assert {r['rank'] for r in manifest}==set(range(1,201))
        from PIL import Image
        for r in manifest:
            with Image.open(DELIVERY/r['image_file']) as im:
                assert im.size==(3200,2400);im.verify()
        summary=json.loads((OUT/'selection.json').read_text())
        text=f'''周一1.618 · 收益率前200笔 · 逐张复盘

打开方式：PNG按001—200排名命名，用Finder/预览按名称排序逐张看。
每笔一张3200×2400合图：A入场附近5分钟K线；B退出附近5分钟K线；底部是全部原始5分钟收盘和高低价范围，不降采样。
00_前200笔清单与复盘笔记.csv含全部200条记录、方案、净/毛收益率、手续费、净R、时刻、原始账本和图片名。最后三列留给你的逐笔观察。
逐笔5m原始行情/保存图中完整时间范围的原始OHLCV及六均线，可继续放大或重绘。

排序：本轮周一1.618已完成回测的已平仓策略交易，按每笔净收益率降序。收益率分母为原开仓名义额，不是保证金、账户本金或R倍数。
同币、同方向、同一UTC入场时间、同一入场价格仅留已测收益最高的一版；不同确认时刻的入场保留。这个选择使用事后收益，不能作为可执行的择优策略。
候选覆盖{summary['unique_positive_entries']:,}个去重盈利入场，选出200个、{summary['symbols']}个币种；净收益率从{summary['rank1_net_pct']:.4f}%到{summary['rank200_net_pct']:.4f}%。
随机对照、未平仓/截尾、旧版被修正结果、定时平仓OHLC上/下界情景均不入主榜。黄金费用未知，单独CSV只列毛收益，不混进净收益榜。

已完成源：5m插针吞没；15m量峰值/1h影线守线/4h收盘守线的确认根数及量窗研究；1h实体守线；近/远侧止盈；50/50分批及保本；1.618挂单5/15/60/240分钟网格参考平仓。
定时平仓图保留原研究的网格参考值，不冒充精确持仓分钟的收益；具体秒数未知，原CSV下/上界另列。原始框架路径与同K双触假设沿用冻结账本。
成本沿用原实验当前OKX情景：maker0.02%、taker0.05%，按每次实际成交计费；无滑点、资金费、队列、保证金或强平模拟。
所有图含事后行情，灰底是退出后，不计入这笔收益；六均线是复盘辅助，原方案未用均线时不构成入场条件。
只挑盈利单适合归纳和提出假设，不能用这200笔计算策略胜率、证明未来盈利或省略亏损对照。

本次只读并重绘已完成Nautilus/VectorBT成交，没有新回测，没有更改止盈止损或实盘设置。
复现：.venv/bin/python -m yoyo.evaluation.monday_top_trades --select --render
'''
        (DELIVERY/'00_先读我.txt').write_text(text)
        dump(OUT/'completion.json',dict(status='rendered',charts=len(manifest),pixel_size=[3200,2400],
            price_and_monday_checks=200,png_decode_checks=200,all_5m_data_files=200,
            folder=str(DELIVERY),browser_created=False,simulation_replayed=False))

