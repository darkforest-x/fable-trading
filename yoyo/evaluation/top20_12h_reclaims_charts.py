"""Render every frozen 12h reclaim using real candles and the selected week's level.

All events receive equal 20-before/20-after slots, retaining empty timestamps.
Future context is for owner review only, not a model input. The signal is a
wick/close marker, never an invented Stoch check or cross. No outcome selection.
"""
from __future__ import annotations
from concurrent.futures import ProcessPoolExecutor
import json
import math
import subprocess
from zipfile import ZipFile, ZIP_STORED
import numpy as np
import pandas as pd
from yoyo.evaluation.top20_12h_reclaims import EXP, ROOT, OHLC, STEP, classify, dump
from yoyo.evaluation.btc_1618_early_marks_charts import setup, BG, FG, MUTED, GOLD, FONT

OUT=ROOT/'artifacts/top20-12h-1618-reclaims-20261101'
KINDS={'pure_wick':'纯插针','body_reclaim':'实体收回','body_on_level':'开盘在线'}


def prepare():
    events=pd.read_csv(EXP/'events.csv',float_precision='round_trip');rows=[]
    for symbol,g in events.groupby('symbol',sort=False):
        b=pd.read_csv(EXP/symbol/'bars12.csv.gz',index_col=0,parse_dates=True,float_precision='round_trip')
        for number,r in enumerate(g.sort_values(['signal_open','side']).to_dict('records'),1):
            t=pd.Timestamp(r['signal_open']).tz_convert('UTC');i=b.index.get_loc(t)
            np.testing.assert_allclose(b.loc[t,OHLC].to_numpy(float),[r[c] for c in OHLC],rtol=1e-14,atol=0)
            level=r['monday_high']-1.618*(r['monday_high']-r['monday_low']) if r['side']==1 else r['monday_low']+1.618*(r['monday_high']-r['monday_low'])
            assert np.isclose(level,r['level'],rtol=1e-14,atol=0)
            assert classify(*(r[c] for c in OHLC),level,r['side'])==r['kind']
            frame=b.reindex(pd.date_range(t-20*STEP,periods=41,freq=STEP));near=g[g.week.eq(r['week'])&g.side.eq(r['side'])]
            filename=f'{number:04d}_{t.tz_convert("Asia/Shanghai"):%Y%m%d_%H%M}_{"lower" if r["side"]==1 else "upper"}_{r["kind"]}.png'
            r.update(number=number,total=len(g),filename=filename,relative_path=f'{symbol}/{filename}',
                candles=[dict(time=str(stamp),complete=bool(v.complete) if pd.notna(v.complete) else False,**{c:float(v[c]) for c in OHLC}) for stamp,v in frame.iterrows()],
                nearby=near[['signal_open','low','high']].to_dict('records'))
            rows.append(r)
    return rows


def draw(r):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.collections import PatchCollection
    from matplotlib.ticker import MaxNLocator, FuncFormatter
    folder=OUT/r['symbol'];folder.mkdir(parents=True,exist_ok=True);path=folder/r['filename']
    if path.exists():
        from PIL import Image
        try:
            with Image.open(path) as im:
                assert im.size==(2160,1215)
                # Existing large-price panels fit their outside label; low-price
                # labels need the new inside placement to preserve all digits.
                assert abs(r['level'])>=1 or im.info.get('PriceLabelLayout')=='inside-v2'
                im.verify()
            return dict(event_id=r['event_id'],symbol=r['symbol'],path=str(path),bytes=path.stat().st_size,reused=True)
        except (OSError,AssertionError):pass
    fig=plt.figure(figsize=(16,9),dpi=135);ax=fig.add_axes([.065,.17,.86,.64])
    times=pd.DatetimeIndex([c['time'] for c in r['candles']]);side=r['side'];color='#4ae5a0' if side==1 else '#ff7183'
    ready=pd.Timestamp(r['ready']);expiry=pd.Timestamp(r['expiry']);first=pd.Timestamp(r['first_monday']);level=r['level']
    price=lambda v:f'{v:,.10f}'.rstrip('0').rstrip('.') if abs(v)<1 else f'{v:,.2f}'
    fig.text(.065,.945,f'{r["symbol"]}USDT.P · 12小时    {r["number"]:04d} / {r["total"]}',fontsize=24,weight='bold')
    fig.text(.065,.902,f'{pd.Timestamp(r["signal_open"]):%Y-%m-%d %H:%M} 开盘（北京时间）  |  {"下刺收回" if side==1 else "上刺收回"} · {KINDS[r["kind"]]}',fontsize=17,color=color)
    fig.text(.065,.858,f'1.618 = {price(level)}    周一区间：{price(r["monday_low"])} — {price(r["monday_high"])}    影线占比 {r["wick_fraction"]:.0%}',fontsize=13,color=MUTED)
    ax.axvspan(20.5,40.5,color='#93a4c5',alpha=.075);ax.axvline(20.5,color=FG,lw=.8,ls=':')
    ax.text(.53,.98,'收盘后走势 →',color=MUTED,fontsize=11,transform=ax.transAxes,va='top')
    xs=[];lows=[];highs=[];colors=[];bodies=[]
    for x,c in enumerate(r['candles']):
        if not c['complete']:continue
        o,h,l,cl=(c[k] for k in OHLC);t=times[x]
        col='#48b45d' if first<=t<ready else ('#a7b0c0' if cl>=o else '#626b7d')
        if x==20:col=color
        xs.append(x);lows.append(l);highs.append(h);colors.append(col)
        bodies.append(Rectangle((x-.32,min(o,cl)),.64,max(abs(cl-o),(h-l)*.008)))
    ax.vlines(xs,lows,highs,color=colors,lw=1.2)
    ax.add_collection(PatchCollection(bodies,facecolors=colors,edgecolors=colors,lw=.8))
    xpos=lambda t:float((pd.Timestamp(t)-times[0])/STEP)
    left=max(-.5,xpos(ready)-.5);right=min(40.5,xpos(expiry)-.5)
    if left<right:
        ax.hlines(level,left,right,color=GOLD,lw=1.8)
        ax.hlines(r['monday_high'],left,right,color='#df5363',lw=.9,alpha=.75)
        ax.hlines(r['monday_low'],left,right,color='#5687f0',lw=.9,alpha=.75)
    if right<40.5:ax.hlines(level,max(right,-.5),40.5,color=GOLD,lw=1,ls='--',alpha=.55)
    ax.annotate(f'1.618  {price(level)}',xy=(40.5,level),xytext=(-6,6),textcoords='offset points',fontsize=10,color=GOLD,va='bottom',ha='right',bbox=dict(facecolor=BG,edgecolor='none',alpha=.7,pad=2))
    for n in r['nearby']:
        x=xpos(n['signal_open'])
        if -.5<=x<=40.5 and abs(x-20)>.01:ax.scatter(x,n['low'] if side==1 else n['high'],s=45,facecolors='none',edgecolors=color,lw=1,alpha=.7)
    extreme=r['low'] if side==1 else r['high']
    ax.scatter(20,extreme,s=140,facecolors='none',edgecolors=color,lw=2,zorder=5)
    ax.scatter(20,r['close'],s=45,color=FG,marker='D',zorder=5)
    ax.annotate('插针端点',xy=(20,extreme),xytext=(-64,-30 if side==1 else 30),textcoords='offset points',fontsize=11,color=color,arrowprops=dict(arrowstyle='->',color=color))
    ax.annotate('收盘',xy=(20,r['close']),xytext=(28,12 if side==1 else -20),textcoords='offset points',fontsize=11,color=FG,arrowprops=dict(arrowstyle='-',color=FG))
    ax.set_xlim(-.6,40.6);ax.margins(y=.15);ax.grid(axis='y',alpha=.12,color=MUTED)
    ax.set_xticks(range(0,41,5),[t.tz_convert('Asia/Shanghai').strftime('%m-%d\n%H:%M') for t in times[::5]],color=MUTED,fontsize=10)
    ax.yaxis.set_major_locator(MaxNLocator(7));ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:price(v)));ax.tick_params(axis='y',colors=MUTED,labelsize=10)
    for spine in ax.spines.values():spine.set_color('#243041')
    metrics=[]
    for h,label in [(2,'1天'),(6,'3天'),(14,'7天')]:
        v=r.get(f'return_pct_{h}',np.nan);metrics.append(f'{label}：{v:+.2f}%' if np.isfinite(v) else f'{label}：数据不足')
    fig.text(.065,.092,'收盘后的反向价格变化（上刺按下跌为正）：  '+'    '.join(metrics),fontsize=13,color=FG)
    flags='旧挂牌历史 | ' if r['before_current_listTime'] else ''
    fig.text(.065,.052,flags+'圆圈=越线端点；菱形=当根收盘；小圆=同周同侧其他收回。虚线=该周到期后的原价参考。',fontsize=11,color=MUTED)
    fig.text(.065,.022,'OKX历史OHLC · 周一按12h K线开盘所属纽约日计算 · 未来仅供复盘 · 价格变化不是交易胜率/收益',fontsize=10,color=MUTED)
    fig.savefig(path,dpi=135,pil_kwargs={'compress_level':1},metadata={'PriceLabelLayout':'inside-v2'});plt.close(fig)
    return dict(event_id=r['event_id'],symbol=r['symbol'],path=str(path),bytes=path.stat().st_size,reused=False)


def contacts(symbol,rows):
    from PIL import Image,ImageDraw,ImageFont
    folder=OUT/symbol/'overview';folder.mkdir(exist_ok=True)
    font=ImageFont.truetype(FONT,26);paths=[]
    for page in range(math.ceil(len(rows)/8)):
        subset=rows[page*8:page*8+8];canvas=Image.new('RGB',(1600,1870),BG);d=ImageDraw.Draw(canvas)
        d.text((30,16),f'{symbol} 12h · 全部样本按时间顺序 · 总览 {page+1}/{math.ceil(len(rows)/8)}',font=font,fill=FG)
        for k,r in enumerate(subset):
            with Image.open(OUT/r['relative_path']) as im:canvas.paste(im.convert('RGB').resize((800,450)),((k%2)*800,65+(k//2)*450))
        path=folder/f'{page+1:03d}.jpg';canvas.save(path,quality=90);paths.append(str(path.relative_to(OUT)))
    return paths


def run():
    assert not (OUT/'delivery.json').exists(),'Existing gallery complete; reuse delivery.'
    OUT.mkdir(parents=True,exist_ok=True)
    receipt={name:dict(bytes=(EXP/name).stat().st_size,mtime_ns=(EXP/name).stat().st_mtime_ns) for name in ['events.csv','config.json']}
    if (OUT/'render_inputs.json').exists():assert json.loads((OUT/'render_inputs.json').read_text())==receipt
    else:dump(OUT/'render_inputs.json',receipt)
    rows=prepare();results=[]
    with ProcessPoolExecutor(max_workers=4,initializer=setup) as pool:
        for i,r in enumerate(pool.map(draw,rows,chunksize=8),1):
            results.append(r)
            if i%100==0:print('rendered',i,'/',len(rows),flush=True)
    idx=pd.DataFrame(rows).drop(columns=['candles','nearby']);idx.to_csv(OUT/'index.csv',index=False,encoding='utf-8-sig')
    bycoin=[];overviews=[]
    for symbol,g in idx.groupby('symbol',sort=False):
        ov=contacts(symbol,g.to_dict('records'));overviews+=ov;zip_path=OUT/f'{symbol}_12h_全部{len(g)}张.zip'
        with ZipFile(zip_path,'w',ZIP_STORED) as z:
            for r in g.itertuples():z.write(OUT/r.relative_path,r.relative_path)
            for p in ov:z.write(OUT/p,p)
            z.writestr('index.csv',g.to_csv(index=False).encode('utf-8-sig'))
        bycoin.append(dict(symbol=symbol,charts=len(g),archive=str(zip_path),bytes=zip_path.stat().st_size))
    complete=OUT/f'全部20币_12h_1.618收回_{len(rows)}张.zip'
    with ZipFile(complete,'w',ZIP_STORED) as z:
        for r in rows:z.write(OUT/r['relative_path'],r['relative_path'])
        for p in overviews:z.write(OUT/p,p)
        z.write(OUT/'index.csv','index.csv');z.write(EXP/'config.json','config.json');z.write(EXP/'summary.json','summary.json')
    from PIL import Image
    for r in results:
        with Image.open(r['path']) as im:assert im.size==(2160,1215);im.verify()
    with ZipFile(complete) as z:assert z.testzip() is None;assert sum(p.endswith('.png') for p in z.namelist())==len(rows)
    dump(OUT/'delivery.json',dict(charts=len(rows),unique_events=len(set(r['event_id'] for r in rows)),reused_completed_charts=sum(r['reused'] for r in results),source=str(EXP),generator_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),all_archive=str(complete),all_archive_bytes=complete.stat().st_size,coins=bycoin,overviews=overviews,dimensions=[2160,1215],source_ohlc_and_level_verified=True,all_png_decoded=True,archive_integrity=True,training_eligible=False,production_eligible=False))
    print('DELIVERED',len(rows),str(complete),flush=True)


if __name__=='__main__':run()
