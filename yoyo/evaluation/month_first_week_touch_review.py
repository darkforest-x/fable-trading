"""NY first-complete-week 1.618 census and retrospective chart collection.

Anchors use only confirmed OHLC during first Monday00 -> next Monday00 NY.
Events begin after that window and expire at the next calendar month. This is
an event census, not a fill engine or P&L backtest. Future OHLC is used ONLY
for expressly retrospective charts / outcome labels, never for anchors.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import zipfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'experiments/active/exp-month-first-week-touches-20261102-v1'
NY = ZoneInfo('America/New_York')
HOUR = pd.Timedelta(hours=1)
COLS = ['open','high','low','close','volume']
CACHE = Path('/Users/zhangzc/Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3')


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def bounds(month):
    """Calendar arithmetic preserves the DST-short/long week (167/169 hours)."""
    d = pd.Timestamp(str(month)+'-01')
    mon = d + pd.Timedelta(days=(-d.weekday()) % 7)
    return tuple(x.tz_localize(NY).tz_convert('UTC') for x in
                 (mon, mon+pd.Timedelta(days=7), d+pd.offsets.MonthBegin(1)))


def contiguous(frame, start, end):
    return frame.index.equals(pd.date_range(start, end, freq='h', inclusive='left'))


def hourly(frame, minutes):
    """Use complete UTC-aligned hours only, never fill missing OHLC."""
    assert frame.index.is_unique
    assert not frame[COLS].isna().any().any()
    assert (frame.high >= frame[['open','close','low']].max(axis=1)).all()
    assert (frame.low <= frame[['open','close','high']].min(axis=1)).all()
    assert (frame.index.asi8 % (minutes*60*10**9) == 0).all()
    if minutes == 60:
        return frame[COLS].sort_index()
    n = frame.resample('h').size()
    agg = frame.resample('h').agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
    return agg[n == 60//minutes]


def csv_source(path, minutes, asof):
    f = pd.read_csv(path)
    f.index = pd.to_datetime(f.ts, unit='ms', utc=True)
    f = f.sort_index()
    if f.index.has_duplicates:
        assert not f.loc[f.index.duplicated(False), COLS].groupby(level=0).nunique().gt(1).any().any()
        f = f[~f.index.duplicated(keep='last')]
    f = f[f.index + pd.Timedelta(minutes=minutes) <= asof]
    h = hourly(f, minutes)
    receipt = dict(path=str(path.relative_to(ROOT)), resolved_path=str(path.resolve()),
                   bytes=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns,
                   source_rows=len(f), hourly_rows=len(h), first=str(h.index.min()), last=str(h.index.max()))
    return h, receipt


def inputs(config):
    """Snapshot public candle cache read-only; reuse the saved snapshot thereafter."""
    asof = pd.Timestamp(config['asof_exclusive'])
    receipt_path = DEST/'source_receipt.json'
    if receipt_path.exists():
        return {s: pd.read_csv(DEST/f'inputs_{s}_1h.csv.gz',index_col='time',parse_dates=True)
                for s in config['symbols']}, json.loads(receipt_path.read_text())
    receipt = {'asof_exclusive': str(asof), 'sources': {}, 'overlap': {}, 'merged': {}}
    data = {}
    for s in config['symbols']:
        paths = [
            (ROOT/f'data/crypto/market/kline_deep/okx_{s}_USDT_SWAP_15m_158499.csv',15),
            (ROOT/f'data/crypto/market/exp-ma-profit3r-20260922-v1-inputs/high/okx_{s}-USDT-SWAP_60m.csv',60),
            (ROOT/f'experiments/active/exp-btceth-5m-alt-response-20261101-v1/inputs/okx_{s}_USDT_SWAP_5m_18652.csv',5)]
        frames, sources = [], []
        for p,m in paths:
            f,r = csv_source(p,m,asof); frames.append(f); sources.append(r)
        with sqlite3.connect(f'file:{CACHE}?mode=ro',uri=True) as con:
            key = f'https://www.okx.com|{s}-USDT-SWAP|1H'
            rows = [json.loads(r[0]) for r in con.execute('select raw from candles where key=? order by t',(key,))]
        rows = [r for r in rows if r[8]=='1' and pd.to_datetime(int(r[0]),unit='ms',utc=True)+HOUR <= asof]
        f = pd.DataFrame([[float(v) for v in r[1:6]] for r in rows], columns=COLS,
                         index=pd.to_datetime([int(r[0]) for r in rows], unit='ms', utc=True))
        frames.append(hourly(f,60))
        sources.append(dict(path=str(CACHE), key=key, hourly_rows=len(f), first=str(f.index.min()),last=str(f.index.max()), read_only=True))
        merged = frames[0]
        overlap = []
        for i,f in enumerate(frames[1:],1):
            shared = merged.index.intersection(f.index)
            diff = (merged.loc[shared,COLS[:4]] - f.loc[shared,COLS[:4]]).abs()
            overlap.append(dict(priority=i, shared_hours=len(shared), ohlc_mismatch_hours=int((diff>1e-6).any(axis=1).sum()),
                                high_low_mismatch_hours=int((diff[['high','low']]>1e-6).any(axis=1).sum()),
                                max_ohlc_difference=float(diff.max().max()) if len(diff) else None))
            merged = pd.concat([merged,f]); merged=merged[~merged.index.duplicated(keep='last')].sort_index()
        # Substantial price disagreement must be resolved before a census, not hidden by priority.
        assert all(r['high_low_mismatch_hours']==0 for r in overlap), overlap
        gaps = pd.date_range(merged.index.min(),merged.index.max(),freq='h').difference(merged.index)
        receipt['sources'][s]=sources; receipt['overlap'][s]=overlap
        receipt['merged'][s]=dict(rows=len(merged),first=str(merged.index.min()),last=str(merged.index.max()),
                                   missing_hours=len(gaps),missing_hour_times=[str(t) for t in gaps])
        merged.to_csv(DEST/f'inputs_{s}_1h.csv.gz',index_label='time')
        data[s]=merged
    save(receipt_path,receipt)
    return data,receipt


def reaction(frame, t, level, side, n):
    """Labels only: next n hours AFTER touch hour, omitting unordered touch OHLC."""
    start=t+HOUR; end=start+n*HOUR
    f=frame[(frame.index>=start)&(frame.index<end)]
    if not contiguous(f,start,end):
        return {'complete':False,'inward_pct':None,'outward_pct':None,'close_inward_pct':None}
    upper=side=='upper'
    inward=(level-f.low.min()) if upper else (f.high.max()-level)
    outward=(f.high.max()-level) if upper else (level-f.low.min())
    close=(level-f.close.iloc[-1]) if upper else (f.close.iloc[-1]-level)
    return dict(complete=True,inward_pct=max(0.,float(inward/level*100)),
                outward_pct=max(0.,float(outward/level*100)),close_inward_pct=float(close/level*100))


def census(data, config):
    months=[]; events=[]
    asof=pd.Timestamp(config['asof_exclusive'])
    for s,f in data.items():
        first=f.index.min().tz_convert(NY).strftime('%Y-%m')
        last=asof.tz_convert(NY).strftime('%Y-%m')
        for m in pd.period_range(first,last,freq='M').astype(str):
            st,ready,expiry=bounds(m)
            a=f[(f.index>=st)&(f.index<ready)]
            r=dict(symbol=s,month=m,start=str(st),ready=str(ready),expiry=str(expiry),
                   anchor_expected_hours=int((ready-st)/HOUR),anchor_hours=len(a),
                   anchor_complete=contiguous(a,st,ready),complete_month=False,upper_hit=False,lower_hit=False)
            if not r['anchor_complete']:
                r['status']='forming' if ready>asof else 'missing_anchor'
                months.append(r); continue
            hi=float(a.high.max());lo=float(a.low.min());span=hi-lo
            r.update(high=hi,low=lo,upper=hi+.618*span,lower=lo-.618*span)
            end=min(expiry,asof)
            future=f[(f.index>=ready)&(f.index<end)]
            if not contiguous(future,ready,end):
                r['status']='missing_observation'; months.append(r); continue
            r['status']='complete' if expiry<=asof else 'ongoing'
            r['complete_month']=expiry<=asof
            for side in ['upper','lower']:
                hit=future[future.high>=r[side]] if side=='upper' else future[future.low<=r[side]]
                if hit.empty: continue
                t=hit.index[0]; bar=hit.iloc[0];level=r[side]
                r[side+'_hit']=True
                e=dict(symbol=s,month=m,side=side,level=level,touch=str(t),touch_ny=t.tz_convert(NY).isoformat(),
                       start=str(st),ready=str(ready),expiry=str(expiry),high=hi,low=lo,upper=r['upper'],lower=r['lower'],
                       literal_touch=bool(bar.low<=level<=bar.high),touch_hour_close_inward_pct=float((level-bar.close)/level*100*(1 if side=='upper' else -1)))
                for n in [24,72,168]:
                    e.update({f'{key}_{n}h':val for key,val in reaction(f,t,level,side,n).items()})
                events.append(e)
            months.append(r)
    ev=pd.DataFrame(events)
    if not events:
        ev=pd.DataFrame(columns=['touch','symbol','side'])
    ev=ev.sort_values(['touch','symbol','side']).reset_index(drop=True)
    ev.insert(0,'event_id',[f'{i:03d}' for i in range(1,len(ev)+1)])
    return pd.DataFrame(months),ev


def summarise(months,events,receipt):
    summary={'asof_exclusive':receipt['asof_exclusive'],'symbols':{},'total_events':len(events),
             'literal_touches':int(events.literal_touch.sum()),'horizons':{},'latest':{}}
    for s in ['BTC','ETH']:
        m=months[(months.symbol==s)&months.complete_month];e=events[events.symbol==s]
        summary['symbols'][s]=dict(complete_months=len(m),first_month=m.month.min(),last_month=m.month.max(),
             hit_months=int((m.upper_hit|m.lower_hit).sum()),no_hit_months=int((~m.upper_hit&~m.lower_hit).sum()),
             both_sides_months=int((m.upper_hit&m.lower_hit).sum()),upper_events=int((e.side=='upper').sum()),lower_events=int((e.side=='lower').sum()),
             events=len(e),excluded_months=months[(months.symbol==s)&~months.complete_month][['month','status']].to_dict('records'))
        summary['latest'][s]=e.tail(3).replace({np.nan:None}).to_dict('records')
    for group,frame in [('ALL',events),('BTC',events[events.symbol=='BTC']),('ETH',events[events.symbol=='ETH'])]:
        summary['horizons'][group]={}
        for n in [24,72,168]:
            e=frame[frame[f'complete_{n}h']]
            summary['horizons'][group][str(n)]=dict(complete=len(e),inward_ge1=int((e[f'inward_pct_{n}h']>=1).sum()),
               inward_ge3=int((e[f'inward_pct_{n}h']>=3).sum()),close_inward_positive=int((e[f'close_inward_pct_{n}h']>0).sum()),
               outward_ge1=int((e[f'outward_pct_{n}h']>=1).sum()),inward_median=float(e[f'inward_pct_{n}h'].median()),
               outward_median=float(e[f'outward_pct_{n}h'].median()),close_inward_median=float(e[f'close_inward_pct_{n}h'].median()))
    return summary


def render(data,months,events,summary,config):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as md
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.collections import LineCollection,PolyCollection
    from matplotlib.font_manager import fontManager,FontProperties
    from matplotlib.ticker import FuncFormatter,MaxNLocator
    from PIL import Image,ImageOps,ImageDraw
    fontpath='/System/Library/Fonts/Supplemental/Arial Unicode.ttf'
    fontManager.addfont(fontpath)
    plt.rcParams.update({'font.family':FontProperties(fname=fontpath).get_name(),'axes.unicode_minus':False})
    bg='#0b1019'; panel='#101925';ink='#edf3ff'; muted='#a8b5c7';blue='#69c7ff';pink='#ff7ca8';mint='#49e3c2'
    render_root=DEST/config.get('render_subdir','.')
    render_root.mkdir(parents=True,exist_ok=True)
    settings=config['render']
    out=render_root/'charts';out.mkdir(exist_ok=True)
    pdf=ROOT/config['pdf'];pdf.parent.mkdir(parents=True,exist_ok=True)
    paths=[];candle_counts=[]
    def aggregate(f,hours):
        # Display OHLC only; event/anchor prices always come from the hourly input.
        return f.resample(f'{hours}h').agg(dict(open='first',high='max',low='min',close='last')).dropna()
    def candle(ax,f,hours):
        x=md.date2num(f.index.to_pydatetime())+hours/48
        a=f[['open','high','low','close']].to_numpy();colors=np.where(a[:,3]>=a[:,0],'#78c6af','#e28b95')
        ax.add_collection(LineCollection([[(i,l),(i,h)] for i,(_,h,l,_) in zip(x,a)],colors=colors,linewidths=settings['wick_width_points'],zorder=3))
        w=hours/24*settings['body_width_fraction']/2;eps=(f.high.max()-f.low.min())*.00035
        bodies=[[(i-w,min(o,c)),(i+w,min(o,c)),(i+w,min(o,c)+max(abs(c-o),eps)),(i-w,min(o,c)+max(abs(c-o),eps))] for i,(o,_,_,c) in zip(x,a)]
        ax.add_collection(PolyCollection(bodies,facecolors=colors,edgecolors='none',linewidths=0,zorder=4))
    def style(ax):
        ax.set_facecolor(panel);ax.grid(axis='y',color='#65778f',alpha=.16,lw=.5)
        ax.tick_params(colors=muted,labelsize=9,length=0,pad=5)
        ax.yaxis.tick_right();ax.yaxis.set_major_locator(MaxNLocator(6));ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v:,.0f}'))
        for spine in ax.spines.values():spine.set_color('#293447')
        ax.xaxis.set_major_locator(md.AutoDateLocator(minticks=4,maxticks=7,tz=NY))
        ax.xaxis.set_major_formatter(md.DateFormatter('%m/%d',tz=NY))
    def levels(ax,e,left,right):
        ready=pd.Timestamp(e.ready);expiry=pd.Timestamp(e.expiry)
        # Confirmed lines only, no back-painted final values in source week.
        for key,color,title in [('upper',pink,'上1.618'),('high',blue,'首周高'),('low',blue,'首周低'),('lower',mint,'下1.618')]:
            val=getattr(e,key);start=max(left,ready);end=min(right,expiry)
            if start<end:ax.hlines(val,md.date2num(start),md.date2num(end),color=color,lw=.85,zorder=2)
            if right>expiry and max(left,expiry)<right:ax.hlines(val,md.date2num(max(left,expiry)),md.date2num(right),color=color,lw=.85,linestyles='dashed',alpha=.55)
            if ax.get_ylim()[0] <= val <= ax.get_ylim()[1]:
                ax.text(.008,val,f'{title}  {val:,.1f}',transform=ax.get_yaxis_transform(),color=color,fontsize=8.3,va='bottom',zorder=8,clip_on=True,bbox=dict(facecolor=panel,alpha=.90,edgecolor='none',pad=1))
    def metric(e,n):
        if not getattr(e,f'complete_{n}h'):return f'{n}h  数据未满'
        return f'{n}h  向内 {getattr(e,f"inward_pct_{n}h"):.2f}%  /  向外 {getattr(e,f"outward_pct_{n}h"):.2f}%  /  期末 {getattr(e,f"close_inward_pct_{n}h"):+.2f}%'
    with PdfPages(pdf,metadata={'Title':'BTC ETH 月首周1.618 全部首次触及','Author':'Fable Trading Research'}) as book:
        # The first page is an index to the chart collection, not a separate report.
        fig=plt.figure(figsize=(13,9),facecolor=bg)
        fig.text(.06,.91,'月首周 1.618 · 全部触及图 · 细K版',fontsize=26,color=ink)
        fig.text(.06,.855,'BTC + ETH  /  OKX USDT 永续  /  2022–2026',fontsize=15,color=muted)
        y=.76
        for s,r in summary['symbols'].items():
            fig.text(.06,y,f'{s}   {r["hit_months"]} / {r["complete_months"]} 个月触及  ·  {r["events"]} 次首次触及',fontsize=20,color=ink)
            fig.text(.06,y-.045,f'上沿 {r["upper_events"]} 次    下沿 {r["lower_events"]} 次    双边触及 {r["both_sides_months"]} 个月',fontsize=13,color=muted);y-=.14
        lines=[
            '口径：纽约时区每月第一个完整周一至周日；首周结束后才计触及，月底到期。',
            '每月、每币、每侧只计首次触及；同侧反复碰线不重复计数。',
            '高低点 ± 0.618 × 首周振幅；所有价位来自完整已收盘 1 小时数据。',
            '触及时间精度为 1 小时。后续反应从下一小时起算，避开触及根内部先后不明。',
            '向内＝朝区间内的最大幅度；向外＝继续突破的最大幅度；期末＝到点收盘向内涨跌幅。',
            '反应和突破可同时发生，不能直接当成胜率。此处没有成交、盈亏或因果有效性判断。',
            '蓝色阴影是首周；实线是已确认且有效的价位；月底后的虚线只供走势复盘。',
            '全景为4小时K（首周前14天至触后14天），细节为1小时K（触前后各7天）。',
            'K线按UTC聚合，首末展示K可能未满；横轴为纽约日期，分图纵轴独立。',
            '圆点对应包含触及小时的展示K；细节图只显示视野内价位，其他价位见上方全景。',
            f'完整月份：2022-02 至 2026-10。行情截止 {pd.Timestamp(config["asof_exclusive"]).tz_convert(NY):%Y-%m-%d %H:%M} 纽约时间。',
            '2022-01 首周不完整，2026-11 首周尚未完成，不进入完整月分母。',
            '下页起按触及时间排序，每个事件一页；所有事件都保留，包括没有反转的情况。']
        for i,line in enumerate(lines):fig.text(.06,.43-i*.026,line,fontsize=10.2,color=muted)
        book.savefig(fig,facecolor=bg);plt.close(fig)
        for e in events.itertuples(index=False):
            f=data[e.symbol];t=pd.Timestamp(e.touch);st=pd.Timestamp(e.start);ready=pd.Timestamp(e.ready)
            observed_end=f.index.max()+HOUR
            detail_end=min(t+pd.Timedelta(days=settings['detail_follow_days'])+HOUR,observed_end)
            overview_end=min(t+pd.Timedelta(days=settings['overview_follow_days'])+HOUR,observed_end)
            left=max(st-pd.Timedelta(days=settings['overview_lead_days']),f.index.min())
            fig,axs=plt.subplots(2,1,figsize=(13,9),facecolor=bg,gridspec_kw={'height_ratios':[1,1.25]})
            fig.subplots_adjust(left=.055,right=.90,top=.84,bottom=.17,hspace=.28)
            side='上沿' if e.side=='upper' else '下沿'
            fig.text(.055,.945,f'{e.event_id}   {e.symbol} · {e.month} {side}首次触及',fontsize=22,color=ink)
            fig.text(.055,.902,f'纽约时间 {t.tz_convert(NY):%Y-%m-%d %H:%M}–{(t+HOUR).tz_convert(NY):%H:%M}  |  1.618 = {e.level:,.2f} USDT',fontsize=11.5,color=muted)
            panels=[(axs[0],settings['overview_bar_hours'],left,overview_end,'月度背景 · 4小时K',True),
                    (axs[1],settings['detail_bar_hours'],max(t-pd.Timedelta(days=settings['detail_lead_days']),f.index.min()),detail_end,'触及前后 · 1小时K',False)]
            panel_counts={}
            for ax,hours,l,right,title,overview in panels:
                fr=aggregate(f[(f.index>=l)&(f.index<right)],hours)
                panel_counts['overview' if overview else 'detail']=len(fr)
                style(ax);candle(ax,fr,hours)
                vals=[fr.low.min(),fr.high.max(),e.upper,e.lower] if overview else [fr.low.min(),fr.high.max(),e.level]
                span=max(vals)-min(vals)
                ax.set_ylim(min(vals)-span*.09,max(vals)+span*.13)
                levels(ax,e,l,right)
                ax.axvspan(md.date2num(st),md.date2num(ready),color=blue,alpha=.07)
                marker_at=t.floor(f'{hours}h')+pd.Timedelta(hours=hours/2)
                ax.axvline(md.date2num(marker_at),color=ink,lw=.7,ls=':',alpha=.7)
                ax.scatter([md.date2num(marker_at)],[e.level],s=25,color=pink if e.side=='upper' else mint,edgecolors=bg,zorder=6)
                if l<ready<right:ax.axvline(md.date2num(ready),color=blue,lw=.6,ls=':',alpha=.6)
                ax.set_xlim(md.date2num(l),md.date2num(right)+.3)
                ax.set_title(f'{title} · {len(fr)} 根',loc='left',color=muted,fontsize=10,pad=8)
            for i,n in enumerate([24,72,168]):fig.text(.055,.122-i*.027,metric(e,n),color=ink,fontsize=10.5)
            if not e.complete_168h:
                recent=f[(f.index>t)&(f.index<detail_end)]
                available=len(recent)
                result=reaction(f,t,e.level,e.side,available)
                fig.text(.055,.041,f'已观测 {available}h：最大向内 {result["inward_pct"]:.2f}% / 最大向外 {result["outward_pct"]:.2f}% / 最新收盘 {result["close_inward_pct"]:+.2f}%',fontsize=10.5,color=mint)
            fig.text(.055,.018,'数据：OKX公开行情（复用本地历史与已收盘1H缓存）  |  触及根不参与后续极值  |  幅度相对1.618价位  |  USDT / 线性坐标',fontsize=8,color=muted)
            path=out/f'{e.event_id}_{e.symbol}_{e.month}_{e.side}.png'
            fig.savefig(path,dpi=165,facecolor=bg);book.savefig(fig,facecolor=bg);plt.close(fig);paths.append(path)
            candle_counts.append(dict(event_id=e.event_id,**panel_counts))
    manifest=[]
    for p in paths:
        with Image.open(p) as im:
            im.verify()
        manifest.append(dict(file=p.name,bytes=p.stat().st_size))
    # Contact sheets for an easy visual completeness check, one per 12 events.
    contacts=[]
    for start in range(0,len(paths),12):
        subset=paths[start:start+12];sheet=Image.new('RGB',(1890,436*((len(subset)+2)//3)),bg)
        for j,p in enumerate(subset):
            with Image.open(p) as im:
                thumb=im.convert('RGB');thumb.thumbnail((630,436));sheet.paste(thumb,((j%3)*630,(j//3)*436))
        cp=render_root/f'contact_{start//12+1:02d}.jpg';sheet.save(cp,quality=88);contacts.append(str(cp.relative_to(ROOT)))
    zp=render_root/'BTC_ETH_month_first_week_1618_all_charts_dense.zip'
    with zipfile.ZipFile(zp,'w',zipfile.ZIP_DEFLATED) as z:
        for p in paths:z.write(p,'charts/'+p.name)
        for filename in ['events.csv','months.csv','summary.json','config.json']:
            z.write(DEST/filename,filename)
    save(render_root/'chart_manifest.json',dict(render_settings=settings,candle_counts=candle_counts,renderer_commit=subprocess.check_output(['git','log','-1','--format=%H','--',__file__],cwd=ROOT,text=True).strip(),pdf=str(pdf.relative_to(ROOT)),pdf_pages=len(events)+1,pdf_bytes=pdf.stat().st_size,
                                        zip=str(zp.relative_to(ROOT)),charts=manifest,contacts=contacts))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--render-only',action='store_true');args=parser.parse_args()
    config=json.loads((DEST/'config.json').read_text());data,receipt=inputs(config)
    if args.render_only:
        months=pd.read_csv(DEST/'months.csv');events=pd.read_csv(DEST/'events.csv',dtype={'event_id':str});summary=json.loads((DEST/'summary.json').read_text())
    else:
        months,events=census(data,config);summary=summarise(months,events,receipt)
        summary['builder_commit']=subprocess.check_output(['git','log','-1','--format=%H','--',__file__],cwd=ROOT,text=True).strip()
        months.to_csv(DEST/'months.csv',index=False);events.to_csv(DEST/'events.csv',index=False);save(DEST/'summary.json',summary)
        print(json.dumps({'symbols':summary['symbols'],'horizons':summary['horizons'],'sources':receipt['merged'],'overlap':receipt['overlap']},ensure_ascii=False,indent=2),flush=True)
    render(data,months,events,summary,config)
    print('Completed chart collection',flush=True)

if __name__=='__main__':main()
