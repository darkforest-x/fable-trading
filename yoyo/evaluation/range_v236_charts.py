"""Count and render V2.36 retrospective5m pairs from real OKX candles.

Sources: delivered monday_range_v2_36.pine and frozen annual OKX5m cache.
Stoch5/3/3 reads only completed local OHLC; pair labels may use a later15m
close, explicitly recorded as publication_time. Positions compare candle
OPEN clocks within +/-5 five-minute bars. Each5m arrow appears once, matched
to the earliest confirmed same-side15m arrow. No key, weekday or sweep gate.
Frame outcomes reuse the installed Nautilus adapter; they are retrospective
reference paths, not historically executable entries or profitability evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.pepe_range_exits import aggregate, signals
from yoyo.evaluation.monday_range_v2_2_reference import round_stop

EXP = ROOT / 'experiments/active/exp-range-v236-recent2m-20261022-v1'
DEST = ROOT / 'artifacts/range_v236_recent2m_20261022'
CONFIG = EXP / 'config.json'


def matched_events(five, start, end, tick, window=5):
    """Offline review labels; prefix stability holds after publication_time.

    Only confirmed bars <=end are supplied. Stop uses latest5 completed5m
    OHLC including the signal bar; entry is its close, buffer entry*0.05%.
    Future pairing never changes the hypothetical entry/stop clocks.
    """
    five = five.loc[five.index <= pd.Timestamp(end)]
    quarter = aggregate(five, 15)
    fs, qs = signals(five), signals(quarter)
    qopens = quarter.index - pd.Timedelta(minutes=15)
    fopens = five.index - pd.Timedelta(minutes=5)
    qevents = [(qopens[j], quarter.index[j], int(qs.iloc[j])) for j in np.flatnonzero(qs)]
    low = five.low.rolling(5).min().to_numpy()
    high = five.high.rolling(5).max().to_numpy()
    rows = []
    for i in np.flatnonzero(fs):
        if i < 4 or not pd.Timestamp(start) <= fopens[i] < pd.Timestamp(end):
            continue
        side = int(fs.iloc[i])
        matches = [(o, c) for o, c, s in qevents if s == side and
                   abs((fopens[i]-o).total_seconds()) <= window*300]
        if not matches:
            continue
        opened, closed = matches[0]
        entry = float(five.close.iloc[i])
        stop = round_stop(side, low[i] if side == 1 else high[i], entry*.0005, tick)
        if stop <= 0 or side*(entry-stop) < tick:
            continue
        publication = max(five.index[i], closed)
        rows.append(dict(pair_id=len(rows), entry_i=int(i), side=side, entry=entry, stop=stop,
            risk=side*(entry-stop), signal_time=fopens[i].isoformat(), entry_time=five.index[i].isoformat(),
            quarter_time=opened.isoformat(), quarter_close=closed.isoformat(),
            publication_time=publication.isoformat(), retrospective=bool(publication > five.index[i]),
            offset_bars=int((fopens[i]-opened).total_seconds()/300),
            segment=0, week=fopens[i].strftime('%Y-%W'), bucket=0, end_i=len(five)-1,
            population='signal'))
    return pd.DataFrame(rows)


def fetch_tail(symbol, begin, end, dest):
    """Reuse the public OKX request transport, fetching only the missing tail."""
    from src.data.fetch_okx import _request, API
    from yoyo.evaluation.pepe_range_data import validate
    path = dest/'tail.csv'
    if path.exists():
        return pd.read_csv(path, float_precision='round_trip')
    rows, urls, cursor = [], [], end
    while cursor > begin:
        url = API+'?'+urlencode(dict(instId=symbol+'-USDT-SWAP', bar='5m', limit=100, after=cursor))
        result = _request(url)
        if result['code'] != '0' or not result['data']:
            raise ValueError('OKX returned no usable tail: '+str(result))
        page = result['data']; urls.append(url)
        rows.extend([int(r[0]), *map(float, r[1:5])] for r in page
                    if int(r[8]) == 1 and begin <= int(r[0]) < end)
        nxt = min(int(r[0]) for r in page)
        if nxt >= cursor:
            raise ValueError('OKX pagination stalled')
        cursor = nxt
    frame = validate(pd.DataFrame(rows, columns=['ts','open','high','low','close']))
    frame.to_csv(path, index=False)
    dump(dest/'tail_receipt.json', dict(instrument=symbol+'-USDT-SWAP', urls=urls,
        rows=len(frame), begin=begin, end=end, confirmed_only=True))
    return frame


def load(symbol):
    raw = pd.read_csv(EXP/symbol/'candles.csv.gz', float_precision='round_trip')
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop('ts'), unit='ms', utc=True)
                                 +pd.Timedelta(minutes=5)).as_unit('ns')
    return raw


def prepare():
    from yoyo.evaluation.pepe_range_data import validate
    cfg = json.loads(CONFIG.read_text())
    summary = dict(config=cfg, symbols=[], source_commit=subprocess.check_output(
        ['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip())
    for spec in cfg['symbols']:
        symbol, tick = spec['symbol'], spec['tick']
        dest = EXP/symbol; dest.mkdir(exist_ok=True)
        source = ROOT/cfg['source']/symbol/'candles.csv.gz'
        raw = pd.read_csv(source, float_precision='round_trip')
        warm = int(pd.Timestamp(cfg['warmup']).timestamp()*1000)
        end = int(pd.Timestamp(cfg['end']).timestamp()*1000)
        # Include one cached overlap bar; require exact OHLC agreement.
        tail = fetch_tail(symbol, int(raw.ts.iloc[-1]), end, dest)
        raw = validate(pd.concat([raw[raw.ts >= warm], tail]))
        raw = raw[raw.ts < end].reset_index(drop=True)
        assert raw.ts.iloc[0] == warm and raw.ts.iloc[-1]+300000 == end
        assert (raw.ts.diff().dropna() == 300000).all()
        raw.to_csv(dest/'candles.csv.gz', index=False)
        five = load(symbol)
        rows = matched_events(five, cfg['start'], cfg['end'], tick)
        assert rows.entry_i.is_unique and (pd.to_datetime(rows.publication_time) <= pd.Timestamp(cfg['end'])).all()
        rows.to_csv(dest/'signals.csv', index=False)
        # The native adapter settles at the study boundary. Open frames remain
        # labeled open in the drawings; no forced close is implied by this mark.
        executable = rows[rows.entry_i < len(five)-1]
        executable.to_csv(dest/'candidates.csv', index=False)
        native = dict(schema=1, experiment_id=cfg['experiment_id'], engine='nautilus',
            symbol=symbol, tick=tick, market_tick=tick, entry_mode='provided',
            data=str((dest/'candles.csv.gz').relative_to(ROOT)),
            entry_candidates=str((dest/'candidates.csv').relative_to(ROOT)),
            stop_window_5m=5, buffer_percent=.05, maximum_risk_percent=0,
            round_trip_cost=.002, policies=[dict(name='reference', trail_arm_r=5,
                trail_atr_multiple=4, opposite_hour_exit=True)],
            output=str((dest/'native').relative_to(ROOT)))
        dump(dest/'native.json', native)
        # Recompute at selected prefixes; never publish a later15m pair early.
        for cut in [len(five)//2, len(five)-30, len(five)-3]:
            before = matched_events(five.iloc[:cut], cfg['start'], five.index[cut-1], tick)
            expected = rows[pd.to_datetime(rows.publication_time) <= five.index[cut-1]]
            cols = ['entry_i','side','entry','stop','quarter_time','publication_time']
            pd.testing.assert_frame_equal(before[cols].reset_index(drop=True), expected[cols].reset_index(drop=True))
        count = dict(symbol=symbol, count=len(rows), longs=int(rows.side.eq(1).sum()),
            shorts=int(rows.side.eq(-1).sum()), retrospective=int(rows.retrospective.sum()),
            immediate=int((~rows.retrospective).sum()), distinct15m=int(rows.quarter_time.nunique()),
            source=file_stamp(source), bars=len(raw), gaps=0, prefix_checks=3)
        if symbol == 'BTC':
            owner = rows[pd.to_datetime(rows.signal_time) == pd.Timestamp('2026-10-22T05:40:00+08:00')]
            assert len(owner) == 1
            o = owner.iloc[0]
            assert o.entry == 84751.9 and abs(o.stop-84842.3) < 1e-8 and o.offset_bars == -4
            assert pd.Timestamp(o.publication_time) == pd.Timestamp('2026-10-22T06:15:00+08:00')
            # Previously verified native chart began on Sep14 08:00 and had
            # completed its last confirmed5m bar at10:05 on Oct22.
            native_rows = rows[(pd.to_datetime(rows.signal_time) >= pd.Timestamp('2026-09-14T08:00:00+08:00')) &
                (pd.to_datetime(rows.publication_time) <= pd.Timestamp('2026-10-22T10:05:00+08:00'))]
            count['native_overlap_count'] = len(native_rows)
            count['native_overlap_retrospective'] = int(native_rows.retrospective.sum())
            assert len(native_rows) == 77 and native_rows.retrospective.sum() == 69
        summary['symbols'].append(count)
        print(json.dumps(count, ensure_ascii=False), flush=True)
    dump(EXP/'summary.json', summary)


def chart_data():
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    cfg = json.loads(CONFIG.read_text()); charts=[]
    for spec in cfg['symbols']:
        symbol=spec['symbol']; five=load(symbol); folder=EXP/symbol
        from yoyo.evaluation.monday_range_v2_2_reference import atr
        q=aggregate(five,15); a=atr(q)
        rows=pd.read_csv(folder/'signals.csv', float_precision='round_trip')
        trades=pd.read_csv(folder/'native/reference/trades.csv', float_precision='round_trip').set_index('pair_id')
        grid,names,weeks,developing=monday_levels(five,[0,.5,.618,.705,1.13,1.27,1.618],
                                                'America/New_York',bar_minutes=5)
        opened=five.index-pd.Timedelta(minutes=5)
        for n,r in enumerate(rows.to_dict('records'),1):
            i=int(r['entry_i']); lo=max(0,min(i-20,len(five)-60)); hi=lo+60
            t=trades.loc[r['pair_id']].to_dict() if r['pair_id'] in trades.index else dict(
                reason=0,exit_i=i,exit_price=r['entry'],peak_r=0,arm_i=-1,protection=r['stop'])
            assert abs(t.get('entry',r['entry'])-r['entry']) < 1e-8
            assert abs(t.get('stop',r['stop'])-r['stop']) < 1e-8
            exi=int(t['exit_i']); exclose=five.index[exi]
            # Background arrows include only published matches of this module.
            accepted={int(x.entry_i):int(x.side) for x in rows.itertuples()}
            candles=[dict(index=k,time=opened[k].isoformat(),ny_day=opened[k].tz_convert('America/New_York').dayofweek,
                arrow=accepted.get(k,0), **{c:float(five.iloc[k][c]) for c in ['open','high','low','close']}) for k in range(lo,hi)]
            levels=[dict(key=name,price=float(grid[i,j])) for j,name in enumerate(names) if np.isfinite(grid[i,j])]
            # Same completed15m close ratchet as the native/Pine engines, using
            # the framework's recorded first arm; prices are display-only.
            trail=[]; protection=r['stop']
            if int(t['arm_i']) >= 0:
                for at in q.index[(q.index>=five.index[int(t['arm_i'])])&(q.index<=exclose)]:
                    if at==exclose and int(t['reason']) in [1,2]:continue
                    candidate=round_stop(r['side'],float(q.loc[at,'close']),4*float(a.loc[at]),spec['tick'])
                    protection=max(protection,candidate) if r['side']==1 else min(protection,candidate)
                    trail.append(dict(time=at.isoformat(),price=protection))
            r.update(symbol=symbol,number=n,total=len(rows),candles=candles,levels=levels,
                monday_date=weeks[i],monday_developing=bool(developing[i]),
                exit=dict(time=exclose.isoformat(),price=float(t['exit_price']),reason=int(t['reason']),
                    outside=exi>=hi,peak_r=float(t['peak_r']),
                    result_r=float(r['side']*(t['exit_price']-r['entry'])/r['risk']),trail=trail),
                precision=1 if symbol=='BTC' else 2)
            charts.append(r)
    DEST.mkdir(parents=True,exist_ok=True)
    dump(DEST/'chart_data.json',dict(config=cfg,charts=charts))
    print('Prepared charts:',len(charts),flush=True)


def render(limit=0):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, ticker
    from matplotlib.patches import Rectangle
    from yoyo.evaluation.btc_annual_signal_charts import FONT
    from yoyo.evaluation.btc_annual_signal_landscape import label, spread_labels, COLORS
    font_manager.fontManager.addfont(FONT)
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=FONT).get_name(),
        'axes.unicode_minus':False,'figure.facecolor':'black','axes.facecolor':'black',
        'savefig.facecolor':'black','text.color':'#D9DADB','font.size':10})
    data=json.loads((DEST/'chart_data.json').read_text()); manifest=[]
    def stamp(s,fmt='%m-%d %H:%M'):
        return pd.Timestamp(s).tz_convert('Asia/Shanghai').strftime(fmt)
    for r in data['charts'][:limit or None]:
        bars=r['candles']; side=r['side']; col=COLORS[side]; ex=r['exit']; n=r['number']
        i=[b['index'] for b in bars].index(r['entry_i']); start=pd.Timestamp(bars[0]['time'])
        x=lambda s:(pd.Timestamp(s)-start).total_seconds()/300-.5
        qx=x(r['quarter_time'])+.5; px=x(r['publication_time']); endx=min(59.5,x(ex['time']))
        entryx=i+.5; price=lambda v:f'{v:,.{r["precision"]}f}'
        bottom=min(min(b['low'] for b in bars),r['stop']); top=max(max(b['high'] for b in bars),r['stop'])
        span=top-bottom; ymin=bottom-span*.17; ymax=top+span*.17
        fig=plt.figure(figsize=(16,9),dpi=180)
        fig.text(.045,.952,f'{r["symbol"]}USDT.P   V2.36   #{n:03d} / {r["total"]}   '+('多头' if side==1 else '空头'),fontsize=19,color=col,va='top')
        fig.text(.955,.949,stamp(r['signal_time'],'%Y-%m-%d %H:%M')+'  ·  5m × 60  ·  北京时间',fontsize=12,ha='right',va='top')
        fig.text(.045,.909,f'5m 勾叉 {stamp(r["signal_time"],"%H:%M")}  ↔  15m 勾叉 {stamp(r["quarter_time"],"%H:%M")}  ·  相距 {abs(r["offset_bars"])} 根',fontsize=12,color='#FFED46')
        fig.text(.955,.909,'黄色=本笔5m信号K   ·   蓝色=配对15m范围   ·   紫线=确认时刻',fontsize=10,color='#9AA2AF',ha='right')
        ax=fig.add_axes([.055,.245,.90,.625]); ax.set(xlim=(-1,75),ylim=(ymin,ymax))
        ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=8)); ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:,.1f}'))
        ax.tick_params(axis='both',colors='#7F8793',length=0,labelsize=9,pad=8)
        ticks=list(range(0,60,6))+[59];ax.set_xticks(ticks,[stamp(bars[j]['time'],'%m-%d\n%H:%M') for j in ticks])
        ax.grid(color='#15191E',lw=.5,linestyle=(0,(1,5)))
        for sp in ax.spines.values():sp.set_visible(False)
        ax.axvspan(qx-.5,qx+2.5,color='#458CDB',alpha=.12,zorder=0)
        ax.axvline(px,color='#B099F0',lw=.8,linestyle=(0,(4,4)),alpha=.9)
        ax.text(px,.985,'确认 '+stamp(r['publication_time'],'%H:%M'),transform=ax.get_xaxis_transform(),
                color='#B099F0',fontsize=9,ha='center',va='top',bbox=dict(facecolor='black',edgecolor='none',pad=2))
        # Clip reference boxes to the60-bar viewport.5R is the native minimum
        # visual reward height, not a claim that price reached5R.
        reward=r['entry']+side*max(5.,ex['peak_r'])*r['risk']
        for a,b,c in [(r['entry'],r['stop'],'#F36C7A'),(r['entry'],reward,'#36C9A3')]:
            ax.add_patch(Rectangle((entryx,min(a,b)),max(.1,endx-entryx),abs(b-a),facecolor=c,
                edgecolor=c,lw=.65,alpha=.13,zorder=1,clip_on=True))
        visible=[lv for lv in r['levels'] if ymin<lv['price']<ymax]
        named=[]
        for lv,y in spread_labels(visible,ymin,ymax):
            key=lv['key'];lc='#F23645' if key=='0:down' else '#3680FF' if key=='0:up' else '#737B88'
            ax.hlines(lv['price'],-.5,60,color=lc,lw=.8 if key.startswith('0:') else .5,alpha=.8,zorder=1)
            ax.plot([60,61],[lv['price'],y],color=lc,lw=.5)
            named.append(ax.text(61.3,y,label(key)+'  '+price(lv['price']),fontsize=8.8,color=lc,va='center'))
        for j,b in enumerate(bars):
            cc='#FFED46' if j==i else '#4CAF50' if b['ny_day']==0 else '#B2B5BE' if b['ny_day']>=5 else '#858995'
            ax.vlines(j,b['low'],b['high'],color=cc,lw=1 if j==i else .8,zorder=3)
            bodylo,bodyhi=sorted([b['open'],b['close']])
            ax.add_patch(Rectangle((j-.33,bodylo),.66,max(bodyhi-bodylo,span*.00012),facecolor=cc,edgecolor=cc,lw=.6,zorder=3))
            if b['arrow']:
                s=b['arrow'];ax.annotate('✓' if s==1 else '×',xy=(j,b['low'] if s==1 else b['high']),
                    xytext=(0,-11 if s==1 else 11),textcoords='offset points',color=COLORS[s],fontsize=16 if j==i else 10,
                    ha='center',va='center',alpha=1 if j==i else .4,zorder=5)
        ax.hlines(r['entry'],entryx,min(entryx+6,59.5),color=col,lw=.7,zorder=5)
        named.append(ax.text(min(entryx+6,59.5)+.5,r['entry'],price(r['entry']),fontsize=9,color=col,va='center',
            bbox=dict(facecolor='black',edgecolor='none',pad=1),zorder=8))
        ax.scatter([entryx],[r['entry']],marker='^' if side==1 else 'v',s=35,color=col,zorder=8)
        for rr in [-1,1,3,5]:
            p=r['entry']+side*rr*r['risk']
            if ymin<p<ymax:
                ax.hlines(p,entryx,endx,color='#F36C7A' if rr<0 else '#36C9A3',lw=.5,linestyles=(0,(3,4)),alpha=.7)
        visible_trail=[t for t in ex['trail'] if x(t['time'])<=endx]
        if visible_trail:
            xx=[x(t['time']) for t in visible_trail];yy=[t['price'] for t in visible_trail]
            ax.step(xx+[endx],yy+[yy[-1]],where='post',color='#00BEC8',lw=.8,zorder=4)
        if ex['reason'] and not ex['outside']:
            ax.scatter([endx],[ex['price']],marker='D',s=34,facecolor='none',edgecolor='white',lw=.8,zorder=9)
            named.append(ax.annotate(f'{ex["result_r"]:+.2f}R',xy=(endx,ex['price']),xytext=(8,-15 if side==1 else 15),
                textcoords='offset points',color='white',fontsize=10,zorder=9))
        monday={lv['key']:lv['price'] for lv in r['levels'] if lv['key'].startswith('0:')}
        bounds='  /  '.join(label(k)+' '+price(v)+('（图外）' if not ymin<v<ymax else '') for k,v in monday.items())
        # Miniature range overview keeps both Monday lines visible without
        # flattening the main5m candles when the weekly range is much wider.
        if len(monday)==2:
            mini=fig.add_axes([.805,.06,.15,.095]); ml=monday['0:up'];mh=monday['0:down']; gap=max(mh-ml,span)
            mini.set(xlim=(0,1),ylim=(min(ml,bottom)-gap*.1,max(mh,top)+gap*.1))
            mini.hlines([ml,mh],.05,.8,color=['#3680FF','#F23645'],lw=.8)
            mini.vlines(.45,bottom,top,color='#ABB4C1',lw=4)
            mini.scatter([.45],[r['entry']],s=14,color=col)
            mini.text(.83,mh,'周一高',color='#F23645',fontsize=7.5,va='center');mini.text(.83,ml,'周一低',color='#3680FF',fontsize=7.5,va='center')
            mini.axis('off')
        fig.text(.045,.163,f'5m K线 {stamp(r["signal_time"])}  →  {stamp(r["entry_time"],"%H:%M")} 收盘参考价 {price(r["entry"])}',fontsize=10.5,color=col)
        fig.text(.045,.129,f'15m K线 {stamp(r["quarter_time"])}  →  {stamp(r["quarter_close"],"%H:%M")} 收盘；双方确认 {stamp(r["publication_time"])}',fontsize=10.2)
        reason={0:'截至数据末端仍未退出',1:'止损',2:'跟踪止损',3:'1H反向退出'}[ex['reason']]
        fig.text(.045,.095,f'止损 {price(r["stop"])}  ·  1R = {price(r["risk"])}  ·  {reason} {stamp(ex["time"])}'+('（图外）' if ex['outside'] else '')+f'  ·  全程最大 {ex["peak_r"]:.2f}R',fontsize=9.5,color='#ADB5C2')
        fig.text(.045,.061,bounds+(' · 周一发展中' if r['monday_developing'] else ''),fontsize=9,color='#9AA2AF')
        fig.text(.045,.024,'原始OHLC重绘 · 回标复盘框，非实时成交 · 止损=近5根极值+0.05% · 5R后4ATR跟踪 · Fib仅作参照 · 绿框至少5R仅为刻度',fontsize=8.5,color='#6E7785')
        folder=DEST/r['symbol'];folder.mkdir(exist_ok=True)
        filename=f'{n:03d}_{stamp(r["signal_time"],"%Y%m%d_%H%M")}_{"long" if side==1 else "short"}.png'
        path=folder/filename; fig.canvas.draw(); renderer=fig.canvas.get_renderer()
        for text in [*fig.texts,*named]:
            box=text.get_window_extent(renderer)
            assert 0<=box.x0<=box.x1<=fig.bbox.width and 0<=box.y0<=box.y1<=fig.bbox.height,(path,text.get_text())
        fig.savefig(path,dpi=180);plt.close(fig)
        manifest.append(dict(symbol=r['symbol'],number=n,signal_time=r['signal_time'],side=side,
            file=str(path.relative_to(DEST)),candles=60))
        if len(manifest)%25==0:print('Rendered',len(manifest),flush=True)
    if not limit:dump(DEST/'manifest.json',manifest)
    print('Rendered total',len(manifest),flush=True)


def pack():
    """Offline image browser and per-coin originals; no generated prose report."""
    import html
    import zipfile
    from PIL import Image
    rows=json.loads((DEST/'manifest.json').read_text()); result=[]
    for symbol in ['BTC','ETH']:
        selected=[r for r in rows if r['symbol']==symbol]
        name=f'{symbol}_V2.36_5m_{len(selected)}张横图.zip'
        with zipfile.ZipFile(DEST/name,'w',zipfile.ZIP_DEFLATED) as archive:
            for r in selected:
                path=DEST/r['file']
                with Image.open(path) as im:
                    assert im.size==(2880,1620); im.verify()
                archive.write(path,path.name)
        # Contact sheets remain images, with full-resolution PNG links in viewer.
        for page in range(0,len(selected),12):
            subset=selected[page:page+12]; sheet=Image.new('RGB',(1440,405*((len(subset)+1)//2)), '#000000')
            for j,r in enumerate(subset):
                with Image.open(DEST/r['file']) as im:
                    sheet.paste(im.resize((720,405)),((j%2)*720,(j//2)*405))
            sheet.save(DEST/f'{symbol}_总览_{page//12+1:02d}.jpg',quality=90)
        cards=''.join(f'<a href="{html.escape(r["file"])}" target="_blank"><img loading="lazy" src="{html.escape(r["file"])}"><span>#{r["number"]:03d} · {pd.Timestamp(r["signal_time"]).tz_convert("Asia/Shanghai"):%m-%d %H:%M} · {"多" if r["side"]==1 else "空"}</span></a>' for r in selected)
        (DEST/f'{symbol}.html').write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+symbol+' V2.36 图集</title><style>body{margin:20px;background:#111;color:#ddd;font:16px system-ui}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,600px),1fr));gap:20px}a{color:#ddd;text-decoration:none}img{width:100%;display:block}span{display:block;padding:8px}</style><h2>'+symbol+f' · V2.36 · 5m · {len(selected)}张</h2><p>点击原图放大；北京时间；回标复盘，非实时成交。</p><main>'+cards+'</main>')
        result.append(dict(symbol=symbol,count=len(selected),archive=name,viewer=symbol+'.html'))
    dump(DEST/'validation.json',dict(charts=len(rows),bars_per_chart=60,dimensions=[2880,1620],
        all_png_verified=True,groups=result,training_eligible=False,production_eligible=False))
    print(json.dumps(result,ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','chart-data','render','pack']);parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    if args.action=='prepare':prepare()
    elif args.action=='chart-data':chart_data()
    elif args.action=='render':render(args.limit)
    else:pack()


if __name__=='__main__':main()
