"""Export the requested two-year BTC V2.37 causal signals as real-OHLC PNGs.

Reuse frozen signal occurrences, five-minute candles and Nautilus initial-stop
or entry+120minute exits. No signal regeneration, new fills or return filtering.
One image per primary event, including shared entry clocks across both lanes.
Monday/Fib context uses the existing causal pre-entry snapshot, not admission.
Preparation runs in the existing native environment; plotting uses main .venv.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'experiments/active/exp-range-v237-twoyear-20261022-v1'
ENTRIES = ROOT/'experiments/active/exp-range-v237-fixed-exits-20261022-v1'
OUTCOMES = ROOT/'experiments/active/exp-range-v237-2h-scaleout-20261022-v1'
DEST = ROOT/'artifacts/btc_v237_twoyear_20261022'
COUNTS = {5: 1745, 15: 711}


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    temporary.replace(path)


def prepare():
    import numpy as np
    import pandas as pd
    from yoyo.evaluation.pepe_range_exits import aggregate, signals
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.range_framework import file_stamp

    paths = [SOURCE/'BTC/candles.csv.gz', ENTRIES/'BTC/occurrences.csv',
             OUTCOMES/'BTC/hold/trades.csv.gz']
    five = pd.read_csv(paths[0], float_precision='round_trip')
    five.index = pd.DatetimeIndex(pd.to_datetime(five.pop('ts'), unit='ms', utc=True)+pd.Timedelta(minutes=5))
    assert (five.index.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all()
    occurrences = pd.read_csv(paths[1], float_precision='round_trip')
    occurrences = occurrences[occurrences.population.eq('signal') & occurrences['mode'].eq('causal')]
    trades = pd.read_csv(paths[2], float_precision='round_trip').set_index('pair_id')
    assert trades.index.is_unique and occurrences.event_id.is_unique
    assert occurrences.groupby('lane').size().to_dict() == COUNTS
    frames = {5: five, 15: aggregate(five, 15)}
    arrows = {lane: signals(frame) for lane, frame in frames.items()}
    grid, names, weeks, developing = monday_levels(five, [0,.5,.618,.705,1.13,1.27,1.618],
                                                   'America/New_York', bar_minutes=5)
    DEST.mkdir(parents=True, exist_ok=True)
    charts = []
    for lane, frame in frames.items():
        opened = frame.index-pd.Timedelta(minutes=lane)
        source = occurrences[occurrences.lane.eq(lane)].sort_values(['publication_ns','signal_ns','event_id'])
        accepted = {int(r.signal_ns): int(r.side) for r in source.itertuples()}
        other = 15 if lane == 5 else 5
        for number, row in enumerate(source.to_dict('records'), 1):
            i = int(row['entry_i']); side = int(row['side']); t = trades.loc[row['pair_id']]
            signal_time = pd.Timestamp(row['signal_ns'], tz='UTC')
            match_time = pd.Timestamp(row['matched_ns'], tz='UTC')
            entry_time = pd.Timestamp(row['publication_ns'], tz='UTC')
            assert entry_time == five.index[i] == pd.Timestamp(row['entry_time'])
            assert entry_time == max(signal_time+pd.Timedelta(minutes=lane), match_time+pd.Timedelta(minutes=other))
            assert abs((signal_time-match_time).total_seconds()) <= 25*60
            assert arrows[lane].loc[signal_time+pd.Timedelta(minutes=lane)] == side
            assert arrows[other].loc[match_time+pd.Timedelta(minutes=other)] == side
            for name in ('entry','stop','risk'):
                assert abs(float(row[name])-float(t[name])) < 1e-7
            assert abs(float(five.close.iloc[i])-row['entry']) < 1e-7
            assert t.side == side and int(t.entry_i) == i
            assert i < int(t.exit_i) <= i+24 and int(t.reason) in (0,1)
            if int(t.reason) == 0:
                assert int(t.exit_i) == i+24
            center = int(frame.index.searchsorted(entry_time, side='left'))
            lo = max(0, min(center-20, len(frame)-60)); hi = lo+60
            assert len(frame.iloc[lo:hi]) == 60
            assert opened[lo] <= min(signal_time,match_time) and five.index[int(t.exit_i)] <= frame.index[hi-1]
            bars = [dict(time=opened[k].isoformat(), ny_day=int(opened[k].tz_convert('America/New_York').dayofweek),
                         arrow=accepted.get(int(opened[k].value),0),
                         **{c:float(frame.iloc[k][c]) for c in ['open','high','low','close']}) for k in range(lo,hi)]
            local = signal_time.tz_convert('Asia/Shanghai')
            filename = f'BTC_{lane}m/{number:04d}_{local:%Y%m%d_%H%M}_{"long" if side==1 else "short"}.png'
            r = dict(number=number,total=COUNTS[lane],lane=lane,event_id=row['event_id'],pair_id=int(row['pair_id']),
                     side=side,signal_time=signal_time.isoformat(),matched_time=match_time.isoformat(),
                     entry_time=entry_time.isoformat(),entry=row['entry'],stop=row['stop'],risk=row['risk'],
                     exit_time=five.index[int(t.exit_i)].isoformat(),exit_price=float(t.exit_price),
                     exit_reason=int(t.reason),gross_r=float(t.gross_r),net_r=float(t.net_r),
                     candles=bars,levels=[dict(key=name,price=float(grid[i,k])) for k,name in enumerate(names) if np.isfinite(grid[i,k])],
                     monday_date=weeks[i],monday_developing=bool(developing[i]),file=filename)
            charts.append(r)
    cfg = json.loads((SOURCE/'config.json').read_text())
    save(DEST/'chart_data.json',dict(start=cfg['start'],end=cfg['end'],charts=charts))
    save(DEST/'preparation.json',dict(charts=len(charts),counts=COUNTS,sources=[file_stamp(p) for p in paths],
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip(),
        all_entries_and_outcomes_reconciled=True,all_signal_pairs_verified=True,
        all_windows_exactly_60_bars=True,all_exits_in_view=True,source_unchanged=True))
    print('Prepared',len(charts),'causal BTC charts; all pairs, entries and native exits verified',flush=True)


def setup_plot():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from yoyo.evaluation.btc_annual_signal_charts import FONT
    font_manager.fontManager.addfont(FONT)
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=FONT).get_name(),
        'axes.unicode_minus':False,'figure.facecolor':'black','axes.facecolor':'black',
        'savefig.facecolor':'black','text.color':'#D9DADB','font.size':10})


def draw(r):
    import pandas as pd
    import matplotlib.pyplot as plt
    from matplotlib import ticker
    from matplotlib.patches import Rectangle
    from yoyo.evaluation.btc_annual_signal_landscape import label, spread_labels, COLORS
    bars=r['candles']; lane=r['lane']; side=r['side']; col=COLORS[side]
    other=15 if lane==5 else 5
    start=pd.Timestamp(bars[0]['time'])
    x=lambda s:(pd.Timestamp(s)-start).total_seconds()/(lane*60)-.5
    stamp=lambda s,fmt='%m-%d %H:%M':pd.Timestamp(s).tz_convert('Asia/Shanghai').strftime(fmt)
    price=lambda p:f'{p:,.1f}'
    sx=x(r['signal_time'])+.5; mx=x(r['matched_time']); ex=x(r['entry_time']); end=x(r['exit_time'])
    low=min(min(b['low'] for b in bars),r['stop'],r['exit_price'])
    high=max(max(b['high'] for b in bars),r['stop'],r['exit_price'])
    span=high-low; ymin=low-span*.17; ymax=high+span*.17
    fig=plt.figure(figsize=(16,9),dpi=180)
    fig.text(.045,.955,f'BTCUSDT.P   V2.37   {lane}m   #{r["number"]:04d} / {r["total"]}   '+('多头' if side==1 else '空头'),fontsize=19,color=col,va='top')
    fig.text(.955,.95,stamp(r['signal_time'],'%Y-%m-%d %H:%M')+'  ·  北京时间',fontsize=12,ha='right',va='top')
    fig.text(.045,.91,f'{lane}m 勾叉 {stamp(r["signal_time"])}  ↔  {other}m 勾叉 {stamp(r["matched_time"])}',fontsize=11,color='#FFED46')
    fig.text(.955,.91,'黄 K = 本笔信号  ·  蓝色带 = 配对 K  ·  紫线 = 双方确认 / 入场',fontsize=10,color='#9AA2AF',ha='right')
    ax=fig.add_axes([.055,.26,.90,.61]);ax.set(xlim=(-1,77),ylim=(ymin,ymax))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=8))
    ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:,.1f}'))
    ax.tick_params(axis='both',colors='#7F8793',length=0,labelsize=9,pad=8)
    ticks=list(range(0,60,6))+[59]
    ax.set_xticks(ticks,[stamp(bars[j]['time'],'%m-%d\n%H:%M') for j in ticks])
    ax.grid(color='#15191E',lw=.5,linestyle=(0,(1,5)))
    for sp in ax.spines.values():sp.set_visible(False)
    ax.axvspan(mx,mx+other/lane,color='#458CDB',alpha=.17,zorder=0)
    ax.axvline(ex,color='#B099F0',lw=.8,linestyle=(0,(4,4)))
    ax.text(ex,.987,'入场 '+stamp(r['entry_time'],'%H:%M'),transform=ax.get_xaxis_transform(),color='#B099F0',
            fontsize=9,ha='center',va='top',bbox=dict(facecolor='black',edgecolor='none',pad=2))
    ax.add_patch(Rectangle((ex,min(r['entry'],r['stop'])),end-ex,r['risk'],facecolor='#F36C7A',edgecolor='#F36C7A',lw=.65,alpha=.13,zorder=1))
    if r['gross_r']>0:
        ax.add_patch(Rectangle((ex,min(r['entry'],r['exit_price'])),end-ex,abs(r['entry']-r['exit_price']),
            facecolor='#36C9A3',edgecolor='#36C9A3',lw=.65,alpha=.15,zorder=1))
    named=[]
    for lv,y in spread_labels([p for p in r['levels'] if ymin<p['price']<ymax],ymin,ymax):
        key=lv['key'];lc='#F23645' if key=='0:down' else '#3680FF' if key=='0:up' else '#737B88'
        ax.hlines(lv['price'],-.5,60,color=lc,lw=.8 if key.startswith('0:') else .5,alpha=.85)
        ax.plot([60,61],[lv['price'],y],color=lc,lw=.5)
        named.append(ax.text(61.3,y,label(key)+'  '+price(lv['price']),fontsize=8.7,color=lc,va='center'))
    for j,b in enumerate(bars):
        primary=abs(j-sx)<1e-6
        cc='#FFED46' if primary else '#4CAF50' if b['ny_day']==0 else '#B2B5BE' if b['ny_day']>=5 else '#858995'
        ax.vlines(j,b['low'],b['high'],color=cc,lw=1 if primary else .8,zorder=3)
        a,z=sorted([b['open'],b['close']])
        ax.add_patch(Rectangle((j-.33,a),.66,max(z-a,span*.00012),facecolor=cc,edgecolor=cc,lw=.6,zorder=3))
        if b['arrow']:
            s=b['arrow'];ax.annotate('✓' if s==1 else '×',xy=(j,b['low'] if s==1 else b['high']),
                xytext=(0,-11 if s==1 else 11),textcoords='offset points',color=COLORS[s],fontsize=16 if primary else 10,
                ha='center',va='center',alpha=1 if primary else .35,zorder=5)
    ax.hlines(r['entry'],ex,ex+6,color=col,lw=.7,zorder=6)
    named.append(ax.text(ex+6.5,r['entry'],price(r['entry']),fontsize=9,color=col,va='center',
        bbox=dict(facecolor='black',edgecolor='none',pad=1),zorder=8))
    ax.scatter([ex],[r['entry']],marker='^' if side==1 else 'v',s=38,color=col,zorder=8)
    for rr in [-1,1,2,3]:
        p=r['entry']+side*rr*r['risk']
        if ymin<p<ymax:
            ax.hlines(p,ex,end,color='#F36C7A' if rr<0 else '#36C9A3',lw=.5,linestyles=(0,(3,4)),alpha=.6)
    ax.plot([ex,end],[r['entry'],r['exit_price']],color='#D7DCE2',lw=.6,linestyle=(0,(3,4)),alpha=.6)
    ax.scatter([end],[r['exit_price']],marker='D',s=36,facecolor='black',edgecolor='white',lw=.9,zorder=9)
    named.append(ax.annotate(f'{r["gross_r"]:+.2f}R',xy=(end,r['exit_price']),xytext=(9,-18 if side==1 else 18),
        textcoords='offset points',fontsize=10,color='white',zorder=9,
        bbox=dict(facecolor='black',edgecolor='none',pad=1)))
    monday={lv['key']:lv['price'] for lv in r['levels'] if lv['key'].startswith('0:')}
    if len(monday)==2:
        mini=fig.add_axes([.80,.067,.15,.103]);ml=monday['0:up'];mh=monday['0:down'];gap=max(mh-ml,span)
        mini.set(xlim=(0,1),ylim=(min(ml,low)-gap*.12,max(mh,high)+gap*.12))
        mini.hlines([ml,mh],.05,.8,color=['#3680FF','#F23645'],lw=.8)
        mini.vlines(.42,low,high,color='#ABB4C1',lw=4);mini.scatter([.42],[r['entry']],s=16,color=col)
        mini.text(.83,mh,'周一高',color='#F23645',fontsize=7.5,va='center')
        mini.text(.83,ml,'周一低',color='#3680FF',fontsize=7.5,va='center');mini.axis('off')
    reason='止损 · 5m 区间 '+stamp(pd.Timestamp(r['exit_time'])-pd.Timedelta(minutes=5))+'–'+stamp(r['exit_time'],'%H:%M') if r['exit_reason']==1 else '满 2 小时 · '+stamp(r['exit_time'])
    fig.text(.045,.174,f'确认后入场 {stamp(r["entry_time"])}  {price(r["entry"])}  ·  止损 {price(r["stop"])}  ·  1R = {price(r["risk"])}',fontsize=11,color=col)
    fig.text(.045,.137,f'{reason}  ·  出场 {price(r["exit_price"])}  ·  毛 {r["gross_r"]:+.2f}R / 净 {r["net_r"]:+.2f}R',fontsize=10.5)
    bounds='   /   '.join(label(k)+' '+price(v) for k,v in monday.items())
    fig.text(.045,.10,bounds+(' · 周一发展中' if r['monday_developing'] else '') if bounds else '周一范围尚未形成',fontsize=9.5,color='#9AA2AF')
    fig.text(.045,.064,'60 根真实 K 线 · 初始风险红框 / 实现正价差绿框 · ◆ 出场 · 盘中止损按原 5m 回测撮合，时间标至该 5m 区间',fontsize=8.8,color='#9AA2AF')
    fig.text(.045,.029,'V2.37 确认后入场研究 · 近 5 根已收盘 5m 极值 + 0.05% 止损 · 无固定 TP / 推保本 · 2 小时上限 · 往返费用 0.2% · 周一/Fib 为入场前快照，仅作参照',fontsize=8.4,color='#6E7785')
    path=DEST/r['file'];path.parent.mkdir(exist_ok=True)
    fig.canvas.draw();renderer=fig.canvas.get_renderer()
    for item in [*fig.texts,*named]:
        box=item.get_window_extent(renderer)
        assert 0<=box.x0<=box.x1<=fig.bbox.width and 0<=box.y0<=box.y1<=fig.bbox.height,(path,item.get_text())
    fig.savefig(path,dpi=180);plt.close(fig)
    return r['file']


def render(workers=4, limit=0):
    from concurrent.futures import ProcessPoolExecutor
    rows=json.loads((DEST/'chart_data.json').read_text())['charts']
    if limit:
        rows=[rows[0],rows[1],rows[1744],rows[1745],rows[-1]][:limit]
    with ProcessPoolExecutor(max_workers=workers,initializer=setup_plot) as pool:
        for i,_ in enumerate(pool.map(draw,rows,chunksize=4),1):
            if i%100==0:print('Rendered',i,'/',len(rows),flush=True)
    if not limit:
        save(DEST/'manifest.json',[{k:v for k,v in r.items() if k not in ('candles','levels')} for r in rows])
    print('Rendered total',len(rows),flush=True)


def pack():
    import io
    import zipfile
    import pandas as pd
    from PIL import Image
    rows=json.loads((DEST/'manifest.json').read_text())
    assert len(rows)==sum(COUNTS.values()) and len({r['event_id'] for r in rows})==len(rows)
    archives=[]
    for lane,count in COUNTS.items():
        selected=[r for r in rows if r['lane']==lane];assert len(selected)==count
        batches=[];batch=[];size=0
        for r in selected:
            path=DEST/r['file']
            with Image.open(path) as im:
                assert im.size==(2880,1620);im.verify()
            if batch and size+path.stat().st_size>44_000_000:
                batches.append(batch);batch=[];size=0
            batch.append(r);size+=path.stat().st_size
        if batch:batches.append(batch)
        for j,batch in enumerate(batches,1):
            name=f'BTC_V2.37_{lane}m_{j:02d}of{len(batches):02d}_{len(batch)}charts.zip'
            with zipfile.ZipFile(DEST/name,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
                for r in batch:z.write(DEST/r['file'],r['file'])
                index=pd.DataFrame([{k:r[k] for k in ('number','event_id','signal_time','matched_time','entry_time','side','entry','stop','exit_time','exit_price','exit_reason','gross_r','net_r','file')} for r in batch])
                for key in ('signal_time','matched_time','entry_time','exit_time'):
                    index[key]=pd.to_datetime(index[key],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
                z.writestr('index.csv',index.to_csv(index=False).encode('utf-8-sig'))
            with zipfile.ZipFile(DEST/name) as z:
                assert z.testzip() is None
                assert sum(p.endswith('.png') for p in z.namelist())==len(batch)
                assert len(pd.read_csv(io.BytesIO(z.read('index.csv'))))==len(batch)
            size=(DEST/name).stat().st_size;assert size<49_000_000
            archives.append(dict(file=name,lane=lane,count=len(batch),part=j,parts=len(batches),bytes=size,
                                 first=batch[0]['number'],last=batch[-1]['number']))
        # A small contact sheet for inspection; the delivered archives keep originals.
        sample=[selected[k] for k in [0,1,len(selected)//4,len(selected)//2,3*len(selected)//4,len(selected)-1]]
        sheet=Image.new('RGB',(1440,1215),'black')
        for j,r in enumerate(sample):
            with Image.open(DEST/r['file']) as im:sheet.paste(im.resize((720,405)),((j%2)*720,(j//2)*405))
        sheet.save(DEST/f'BTC_{lane}m_samples.jpg',quality=93)
    save(DEST/'validation.json',dict(charts=len(rows),counts=COUNTS,dimensions=[2880,1620],bars_per_chart=60,
        all_png_verified=True,all_archives_crc_verified=True,all_exits_in_view=True,archives=archives,
        start='2024-10-22T10:15:00+08:00',end='2026-10-22T10:15:00+08:00',
        exit_policy='initial SL or120minutes, causal entries, no TP/BE/ATR/H1 exits',
        training_eligible=False,production_eligible=False))
    print(json.dumps(archives),flush=True)


def send():
    """Explicitly requested ZIP delivery; pending/unknown responses never resend.

    Reuses the established private credential loader and getChat title gate.
    sendDocument Message acknowledgements verify name, byte size and target.
    """
    import requests
    from yoyo.notify import _load
    check=json.loads((DEST/'validation.json').read_text())
    assert check['charts']==2456 and sum(a['count'] for a in check['archives'])==2456
    creds=_load()
    if creds is None:raise RuntimeError('Configured Telegram credentials missing')
    token,chat=creds;url='https://api.telegram.org/bot'+token+'/'
    try:response=requests.post(url+'getChat',json={'chat_id':chat},timeout=(10,20)).json()
    except Exception as exc:raise RuntimeError('getChat failed: '+type(exc).__name__) from None
    if not response.get('ok'):raise RuntimeError('getChat rejected')
    target=response['result']
    if target.get('title')!='Yolo均线密集交易系统' or target.get('type') not in ('channel','supergroup','group'):
        raise RuntimeError('Configured target differs from verified owner group')
    receipt_path=DEST/'telegram_receipt.json'
    receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else dict(target_id=target['id'],
        target_title=target['title'],actions=[],complete=False)
    assert receipt['target_id']==target['id']
    for r in check['archives']:
        path=DEST/r['file'];assert path.stat().st_size==r['bytes']<49_000_000
        prior=next((a for a in receipt['actions'] if a['file']==r['file']),None)
        if prior:
            if prior['status']=='sent' and prior['bytes']==r['bytes']:continue
            raise RuntimeError('Previous upload uncertain or rejected; no automatic resend')
        action=dict(file=r['file'],bytes=r['bytes'],count=r['count'],status='pending')
        receipt['actions'].append(action);save(receipt_path,receipt)
        caption=(f'BTCUSDT.P V2.37｜{r["lane"]}m｜第 {r["part"]}/{r["parts"]} 包｜{r["count"]} 张\n'
            f'近两年：2024-10-22 10:15 至 2026-10-22 10:15，北京时间。5m共1745张，15m共711张。\n'
            '每张60根真实K线，2880×1620横图；黄K为主信号、蓝带为配对K，紫线为双方确认后的实际入场。'
            '标注入场、止损、出场与毛/净R；Fib线标比例和价格，周一高低点附范围小图。\n'
            '本套出场：初始止损或入场满2小时；无固定TP/推保本。费用0.2%。关键线仅作参照。'
            '每包可单独解压，附北京时间index.csv。')
        try:
            with path.open('rb') as h:
                result=requests.post(url+'sendDocument',data={'chat_id':chat,'caption':caption},
                    files={'document':(path.name,h,'application/zip')},timeout=(15,60)).json()
        except Exception as exc:
            action.update(status='unknown',error_type=type(exc).__name__);save(receipt_path,receipt)
            raise RuntimeError('Upload response uncertain; no automatic resend') from None
        if not result.get('ok'):
            action.update(status='rejected',error_code=result.get('error_code'));save(receipt_path,receipt)
            raise RuntimeError('Telegram rejected upload: '+str(result.get('error_code')))
        message=result['result'];doc=message.get('document',{})
        if message['chat']['id']!=target['id'] or doc.get('file_name')!=path.name or doc.get('file_size')!=r['bytes']:
            action.update(status='unknown');save(receipt_path,receipt);raise RuntimeError('Unexpected upload receipt')
        username=target.get('username');peer=str(target['id']).removeprefix('-100')
        link=f'https://t.me/{username}/{message["message_id"]}' if username else f'https://t.me/c/{peer}/{message["message_id"]}'
        action.update(status='sent',message_id=message['message_id'],message_url=link,sent_at=message['date'],telegram_bytes=doc['file_size'])
        save(receipt_path,receipt);print(json.dumps({k:action[k] for k in ('file','status','message_id','message_url')}),flush=True)
    receipt.update(complete=True,sent_images=sum(a['count'] for a in receipt['actions'] if a['status']=='sent'))
    assert receipt['sent_images']==2456
    save(receipt_path,receipt)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','render','pack','send'])
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder before generating artifacts')
    if args.action=='render':render(args.workers,args.limit)
    else:globals()[args.action]()
