"""All859 paired5m/15m touch charts, three targets, verified ZIP/TG delivery.

Owner2026-10-30 explicitly requested every chart in one folder, compressed
and sent to the configured Telegram group, then added far edge/opposite1618.
Reuse the saved target ledger and old approved chart colors. Detail panels
show <=21h to retain candle width; an explicitly labelled15m high/low path
shows the entire observation horizon, with all three target levels/times.
Telegram upload follows the repository's verified-title, pending-receipt and
no-uncertain-retry protocol (range_v237_btc_gallery / paper_2026_trade_charts).
API reference https://core.telegram.org/bots/api#senddocument (50MB multipart).
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import io
import json
import multiprocessing
from pathlib import Path
import subprocess
import time
import zipfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.font_manager import FontProperties
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd
from PIL import Image

from yoyo.evaluation.btc_1618_all_targets import DEST, NAMES
from yoyo.evaluation.btc_1618_return_charts import BG,PANEL,INK,MUTED,GOLD,BLUE,GREEN,RED,location,label

BUNDLE=DEST/'BTC_1618_859charts_5m_15m'
COLORS=[BLUE,'#b8a0ef','#ed9bc5']
TARGET_LABELS=['T1 近侧周一边界','T2 周一另一端','T3 对侧 1.618']
DATA=None
STAMP=None


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    tmp.replace(path)


def init():
    global DATA,STAMP
    font=FontProperties(fname='/Library/Fonts/Arial Unicode.ttf')
    plt.rcParams.update({'font.family':font.get_name(),'axes.unicode_minus':False})
    series=[]
    for minutes in [5,15]:
        f=pd.read_csv(DEST/f'inputs_{minutes}m.csv.gz')
        f.index=pd.to_datetime(f.pop('time'),utc=True)
        series.append(f)
    DATA=(*series,pd.read_csv(DEST/'events_targets.csv').to_dict('records'))
    STAMP=subprocess.check_output(['git','log','-1','--format=%H','--',__file__],text=True).strip()


def candles(ax,data,span):
    x=np.arange(len(data));a=data[['open','high','low','close']].to_numpy()
    colors=np.where(a[:,3]>=a[:,0],'#76bfa9','#c78087')
    ax.add_collection(LineCollection([[(i,l),(i,h)] for i,(_,h,l,_) in zip(x,a)],colors=colors,linewidths=.75,zorder=4))
    bodies=[]
    for i,(o,_,_,c) in zip(x,a):
        y=min(o,c);h=max(abs(c-o),span*.0006)
        bodies.append([(i-.32,y),(i+.32,y),(i+.32,y+h),(i-.32,y+h)])
    ax.add_collection(PolyCollection(bodies,facecolors=colors,edgecolors=colors,linewidths=.2,zorder=5))


def axes_style(ax):
    ax.set_facecolor(PANEL);ax.grid(axis='y',color='#65778f',alpha=.14,lw=.7)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y,_:f'{y:,.0f}'))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
    ax.tick_params(colors=MUTED,labelsize=11.5,length=0,pad=7)
    for spine in ax.spines.values():spine.set_color('#334052')


def _render_one(index):
    five,fifteen,records=DATA;r=records[index];number=r['number'];t=pd.Timestamp(r['start'])
    side=r['side'];upper=side=='upper';end=pd.Timestamp(r['observation_end']);expiry=pd.Timestamp(r['expiry'])
    dirname='上沿_408次' if upper else '下沿_451次'
    name=f'{number:04d}_{t:%Y%m%d_%H%M}_{side}_{r["near_status"]}.png'
    output=BUNDLE/dirname/name;receipt=DEST/'render_receipts'/f'{number:04d}.json'
    if output.exists() and receipt.exists():
        old=json.loads(receipt.read_text())
        if old.get('renderer_commit')==STAMP and old['bytes']==output.stat().st_size:return old
    output.parent.mkdir(parents=True,exist_ok=True)
    formation=pd.Timestamp(r['week']).tz_localize('America/New_York')+pd.Timedelta(days=1)
    left=max(t-pd.Timedelta(hours=3),formation)
    last=pd.Timestamp(r['episode_last_bar'])+pd.Timedelta(minutes=15)
    successes=[pd.Timestamp(r[n+'_15m']) for n in NAMES if r[n+'_status']=='returned']
    desired=max([t+pd.Timedelta(hours=6),last+pd.Timedelta(hours=1)]+[v+pd.Timedelta(hours=2) for v in successes])
    right=min(desired,t+pd.Timedelta(hours=18),end)
    frames=[f[(f.index>=left)&(f.index<right)] for f in [five,fifteen]]
    overview=fifteen[(fifteen.index>=left)&(fifteen.index<end)]
    assert len(overview)>0
    lo=min(frames[0].low.min(),r['level'],r['near_price']);hi=max(frames[0].high.max(),r['level'],r['near_price'])
    span=hi-lo;ymin=lo-.17*span;ymax=hi+.23*span
    fig=plt.figure(figsize=(22,12),facecolor=BG)
    ax5=fig.add_axes([.055,.415,.44,.315]);ax15=fig.add_axes([.54,.415,.44,.315],sharey=ax5)
    ax15.tick_params(labelleft=False)
    side_text='上沿' if upper else '下沿'
    status={'returned':'已返回近边界','not_returned':'当周未返回近边界','pending':'本周未结束 · 待观察'}[r['near_status']]
    fig.text(.045,.955,f'{number:04d} / 0859  ·  BTC {side_text} 1.618 触达',fontsize=24,color=INK,fontweight='bold')
    fig.text(.975,.955,status,fontsize=16,color=GREEN if r['near_status']=='returned' else RED if r['near_status']=='not_returned' else GOLD,ha='right')
    fig.text(.045,.914,f'OKX BTC-USDT 永续  |  纽约周一 {r["week"]}  |  周一区间 {r["monday_low"]:,.1f} — {r["monday_high"]:,.1f} USDT',fontsize=14,color=MUTED)
    fig.text(.045,.879,f'触达：15m {t.tz_convert("Asia/Shanghai"):%m/%d %H:%M}  /  5m {pd.Timestamp(r["touch_5m"]).tz_convert("Asia/Shanghai"):%m/%d %H:%M}    ·    本次 1.618 = {r["level"]:,.1f}',fontsize=14.5,color=GOLD)
    for j,n in enumerate(NAMES):
        state=r[n+'_status']
        message=('已到达 '+pd.Timestamp(r[n+'_5m']).tz_convert('Asia/Shanghai').strftime('%m/%d %H:%M')) if state=='returned' else ('当周未到达' if state=='not_returned' else '截至数据末尾待观察')
        fig.text(.055+j*.314,.809,f'{TARGET_LABELS[j]}  {r[n+"_price"]:,.1f}\n{message}',fontsize=14,color=COLORS[j],linespacing=1.8,
                 bbox=dict(facecolor=PANEL,edgecolor='#334052',boxstyle='round,pad=.55',lw=.7))
    for ax,data,minutes in zip([ax5,ax15],frames,[5,15]):
        axes_style(ax);ax.set_title(f'{minutes} 分钟 K 线 · 触达细节',loc='left',pad=14,color=INK,fontsize=16)
        candles(ax,data,span);ax.axhspan(r['monday_low'],r['monday_high'],color=BLUE,alpha=.10,zorder=0)
        ax.axhline(r['level'],color=GOLD,ls='--',lw=1.25,zorder=2)
        ax.text(.012,r['level'],f' 1.618  {r["level"]:,.1f} ',transform=ax.get_yaxis_transform(),color=GOLD,fontsize=11.5,va='bottom',
                bbox=dict(facecolor=PANEL,edgecolor='none',pad=2),zorder=9)
        for j,n in enumerate(NAMES):
            px=r[n+'_price']
            if ymin<=px<=ymax:
                ax.axhline(px,color=COLORS[j],lw=1.1,alpha=.95)
                ax.text(.012,px,f' T{j+1}  {px:,.1f} ',transform=ax.get_yaxis_transform(),color=COLORS[j],fontsize=11.5,va='bottom',
                        bbox=dict(facecolor=PANEL,edgecolor='none',pad=2),zorder=9)
            if r[n+'_status']=='returned':
                rt=pd.Timestamp(r[n+f'_{minutes}m'])
                if left<=rt<right:
                    q=location(data,rt);ax.scatter(q,px,s=75,marker='D',color=COLORS[j],edgecolor=BG,zorder=12)
                    # Colored price-line labels and header times identify each diamond.
                    # Extra text at the diamond can overlap when far targets arrive together.
        hit=pd.Timestamp(r['touch_5m']) if minutes==5 else t
        q=location(data,hit);qend=location(data,min(last-pd.Timedelta(minutes=minutes),right-pd.Timedelta(minutes=minutes)))
        ax.axvspan(q-.5,qend+.5,color=GOLD,alpha=.08)
        ax.scatter(q,r['level'],s=125,facecolors='none',edgecolors=GOLD,lw=2,zorder=12)
        label(ax,q,r['level'],f'触达 {hit.tz_convert("Asia/Shanghai"):%H:%M}',GOLD,12,38 if upper else -35)
        ax.set_ylim(ymin,ymax);ax.set_xlim(-1,len(data)+1)
        hours=3 if right-left>pd.Timedelta(hours=12) else 2 if right-left>pd.Timedelta(hours=7) else 1
        stamps=pd.date_range(left.tz_convert('UTC').ceil('h'),right.tz_convert('UTC'),freq=f'{hours}h',inclusive='left')
        ax.set_xticks([location(data,s) for s in stamps],stamps.tz_convert('Asia/Shanghai').strftime('%H:%M'))
    fig.text(.055,.371,f'细节窗口：{left.tz_convert("Asia/Shanghai"):%m/%d %H:%M} — {right.tz_convert("Asia/Shanghai"):%m/%d %H:%M}。超出窗口的目标到达点见下方全程图。',fontsize=12.5,color=MUTED)
    ax=fig.add_axes([.055,.135,.925,.155]);axes_style(ax)
    ax.set_title('全程观察 · 15m 高低价范围 + 收盘价（含三档目标，时间轴连续）',loc='left',pad=13,color=INK,fontsize=14)
    limit_label=('纽约周界 ' if end==expiry else '数据截止（本周未完） ')+end.tz_convert('Asia/Shanghai').strftime('%m/%d %H:%M')
    fig.text(.98,.31,limit_label,ha='right',color=MUTED,fontsize=12)
    x=np.arange(len(overview));ax.fill_between(x,overview.low.to_numpy(),overview.high.to_numpy(),color='#849ab8',alpha=.4)
    ax.plot(x,overview.close.to_numpy(),color='#c9d4e3',lw=.75)
    ax.axhspan(r['monday_low'],r['monday_high'],color=BLUE,alpha=.10)
    ax.axhline(r['level'],color=GOLD,ls='--',lw=1)
    ax.scatter(location(overview,t),r['level'],s=75,facecolors='none',edgecolors=GOLD,lw=1.6,zorder=8)
    for j,n in enumerate(NAMES):
        ax.axhline(r[n+'_price'],color=COLORS[j],lw=1,alpha=.9)
        if r[n+'_status']=='returned':
            rt=pd.Timestamp(r[n+'_15m']);q=location(overview,rt)
            ax.scatter(q,r[n+'_price'],s=45,marker='D',color=COLORS[j],edgecolor=BG,zorder=8)
            ax.annotate(f'T{j+1}',(q,r[n+'_price']),xytext=(5,6),textcoords='offset points',color=COLORS[j],fontsize=10,fontweight='bold')
    bottom=min(overview.low.min(),r['level'],*[r[n+'_price'] for n in NAMES]);top=max(overview.high.max(),r['level'],*[r[n+'_price'] for n in NAMES])
    pad=(top-bottom)*.12;ax.set_ylim(bottom-pad,top+pad);ax.set_xlim(-1,len(overview)+1)
    ax.axvline(len(overview)-.5,color=MUTED,ls=':',lw=1)
    ticks=np.unique(np.linspace(0,len(overview)-1,6,dtype=int))
    ax.set_xticks(ticks,overview.index[ticks].tz_convert('Asia/Shanghai').strftime('%m/%d %H:%M'))
    fig.text(.045,.065,'金色圆圈/带：本次触达及其连续15m触达段；菱形：首次到达目标。北京时间，标注为对应K线起始时间。',color=MUTED,fontsize=12.5)
    fig.text(.045,.033,'仅计原纽约周内；同一后续价格运动可对应多次触达。不设止损，不代表交易胜率。5m与15m为同一事件的两种视图。',color=MUTED,fontsize=12.5)
    fig.savefig(output,dpi=160,facecolor=BG);plt.close(fig)
    result=dict(number=number,event_id=r['event_id'],file=str(output.relative_to(BUNDLE)),bytes=output.stat().st_size,
                renderer_commit=STAMP,side=side,start=r['start'],level=r['level'],detail_start=str(left),detail_end=str(right),
                observation_end=str(end),detail_bars_5m=len(frames[0]),detail_bars_15m=len(frames[1]),overview_bars_15m=len(overview),
                **{n+'_status':r[n+'_status'] for n in NAMES})
    save(receipt,result);return result


def render_one(index):
    """Release figures even if one unusual sample fails before savefig."""
    try:
        return _render_one(index)
    finally:
        plt.close('all')


def render(workers=4,numbers=None):
    records=pd.read_csv(DEST/'events_targets.csv');indices=[n-1 for n in numbers] if numbers else list(range(len(records)))
    with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),initializer=init) as pool:
        futures={pool.submit(render_one,i):i for i in indices};completed=0;rows=[]
        try:
            for f in as_completed(futures):
                rows.append(f.result());completed+=1
                if completed%25==0 or completed==len(indices):print(f'rendered {completed}/{len(indices)}',flush=True)
        except BaseException:
            for f in futures:f.cancel()
            raise
    if numbers:return
    rows.sort(key=lambda r:r['number']);assert len(rows)==859
    save(DEST/'gallery_manifest.json',rows)
    index=records.merge(pd.DataFrame(rows)[['number','file']],on='number',validate='one_to_one')
    index.to_csv(BUNDLE/'index.csv',index=False,encoding='utf-8-sig')
    summary=json.loads((DEST/'targets_summary.json').read_text());save(BUNDLE/'counts.json',summary)
    (BUNDLE/'阅读说明.txt').write_text('BTC 1.618全部859次触达；上沿408张，下沿451张。\n每张左5m、右15m，下面为完整观察期的15m价格路径。\nT1最近周一边界；T2周一另一端；T3对侧1.618。\n只计纽约下周一前到达，数据截止2026-10-30北京时间12:00，未结束样本保留待观察。\n图片编号与index.csv一致；浅金色带为同一个15m连续触达段，5m仅细化时刻。\n同一回撤可对应此前多次触达；不包含止损、成交或收益判断。\nTG各ZIP均可单独解压到同一个文件夹。\n',encoding='utf-8')


def pack():
    rows=json.loads((DEST/'gallery_manifest.json').read_text());assert len(rows)==859
    assert len({r['file'] for r in rows})==859
    for r in rows:
        with Image.open(BUNDLE/r['file']) as im:assert im.size==(3520,1920);im.verify()
    extra=['index.csv','counts.json','阅读说明.txt']
    full=DEST/'BTC_1618_ALL_859_5m15m.zip'
    with zipfile.ZipFile(full,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for r in rows:z.write(BUNDLE/r['file'],BUNDLE.name+'/'+r['file'])
        for file in extra:z.write(BUNDLE/file,BUNDLE.name+'/'+file)
    with zipfile.ZipFile(full) as z:assert z.testzip() is None
    parts=[];batch=[];size=0
    for r in rows:
        if batch and size+r['bytes']>44_000_000:parts.append(batch);batch=[];size=0
        batch.append(r);size+=r['bytes']
    if batch:parts.append(batch)
    archives=[]
    for i,batch in enumerate(parts,1):
        path=DEST/f'BTC_1618_{i:02d}of{len(parts):02d}_{len(batch)}charts.zip'
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
            for r in batch:z.write(BUNDLE/r['file'],BUNDLE.name+'/'+r['file'])
            for file in extra:z.write(BUNDLE/file,BUNDLE.name+'/'+file)
        with zipfile.ZipFile(path) as z:
            assert z.testzip() is None and sum(x.endswith('.png') for x in z.namelist())==len(batch)
        assert path.stat().st_size<49_000_000
        archives.append(dict(file=path.name,count=len(batch),part=i,parts=len(parts),bytes=path.stat().st_size,first=batch[0]['number'],last=batch[-1]['number']))
    save(DEST/'delivery.json',dict(charts=859,upper=408,lower=451,dimensions=[3520,1920],all_png_verified=True,all_zip_crc_verified=True,
         full_archive=dict(file=full.name,bytes=full.stat().st_size),archives=archives))
    print(json.dumps(archives),flush=True)


def send():
    """Explicit owner requested delivery; verified receipt or stop, no blind retry."""
    import requests
    from yoyo.notify import _load
    delivery=json.loads((DEST/'delivery.json').read_text());assert delivery['charts']==859
    creds=_load()
    if creds is None:raise RuntimeError('Telegram configuration missing')
    token,chat=creds;url='https://api.telegram.org/bot'+token+'/'
    try:response=requests.post(url+'getChat',json={'chat_id':chat},timeout=(10,20)).json()
    except Exception as exc:raise RuntimeError('getChat failed: '+type(exc).__name__) from None
    if not response.get('ok'):raise RuntimeError('getChat rejected')
    target=response['result'];assert target.get('title')=='Yolo均线密集交易系统' and target.get('type') in ['group','supergroup','channel']
    rp=DEST/'telegram_receipt.json'
    receipt=json.loads(rp.read_text()) if rp.exists() else dict(target_id=target['id'],target_title=target['title'],actions=[],complete=False)
    assert receipt['target_id']==target['id']
    for r in delivery['archives']:
        path=DEST/r['file'];assert path.stat().st_size==r['bytes']<49_000_000
        prior=next((a for a in receipt['actions'] if a['file']==r['file']),None)
        if prior:
            if prior['status']=='sent' and prior['bytes']==r['bytes']:continue
            raise RuntimeError('Previous upload uncertain or rejected; no automatic resend')
        action=dict(r,status='pending');receipt['actions'].append(action);save(rp,receipt)
        caption=(f'BTC 1.618 全部859次触达｜第{r["part"]}/{r["parts"]}包｜{r["count"]}张（编号{r["first"]:04d}—{r["last"]:04d}）\n'
            '每张左5m、右15m，附完整当周价格路径；上沿408张、下沿451张。\n'
            '同周到达T1近侧边界：上151/408、下259/451；T2周一另一端：上34、下94；T3对侧1.618：上22、下46。\n'
            '2024-10-30至2026-10-30北京时间12:00；只计纽约下周一前，待观察另列。\n'
            '3520×1920原图，每包可单独解压到同一文件夹，附index.csv；不是带止损的交易胜率。')
        try:
            with path.open('rb') as handle:
                result=requests.post(url+'sendDocument',data={'chat_id':chat,'caption':caption},
                    files={'document':(path.name,handle,'application/zip')},timeout=(15,60)).json()
        except Exception as exc:
            action.update(status='unknown',error_type=type(exc).__name__);save(rp,receipt)
            raise RuntimeError('Upload response uncertain; no automatic resend') from None
        if not result.get('ok'):
            action.update(status='rejected',error_code=result.get('error_code'));save(rp,receipt)
            raise RuntimeError('Telegram rejected upload')
        message=result['result'];doc=message.get('document',{})
        if message['chat']['id']!=target['id'] or doc.get('file_name')!=path.name or doc.get('file_size')!=r['bytes']:
            action['status']='unknown';save(rp,receipt);raise RuntimeError('Upload acknowledgement mismatch')
        link=f'https://t.me/c/{str(target["id"]).removeprefix("-100")}/{message["message_id"]}'
        action.update(status='sent',message_id=message['message_id'],message_url=link,sent_at=message['date']);save(rp,receipt)
        print(json.dumps({k:action[k] for k in ['file','status','message_url','count']}),flush=True)
    assert sum(r['count'] for r in receipt['actions'] if r['status']=='sent')==859
    receipt['complete']=True;save(rp,receipt)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['render','pack','send'])
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--numbers',type=int,nargs='*')
    args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__]).returncode:raise RuntimeError('Commit builder first')
    if args.action=='render':render(args.workers,args.numbers)
    else:globals()[args.action]()
