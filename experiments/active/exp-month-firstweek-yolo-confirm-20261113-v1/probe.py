"""Fixed offline feasibility sample; no tuning, training, future pixels or monitor mutations.
Use the original close-MA Grade-A full40 detector, native renderer and parser.
Each of 12 after-freeze signal-days (6 per side systematic in time) is read
on EVERY 5m/15m close over the next 4h; no selection by outcome or formula.
The fresh-shape count requires *core start* >= H1 direction-known timestamp.
"""
import hashlib
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import cv2
from PIL import Image, ImageDraw, ImageFont
from yoyo.layers.l1_detection.render import render_chart
from yoyo.monitor.yolo_detector import (RenderedWindow,parse_prediction,YoloDetector,
    MODEL_CLASSES,MODEL_PATH,PREDICT_PARAMETERS)

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OLD=HERE.parent/'exp-month-firstweek-cloud-dense-20261112-v1'
sys.path.insert(0,str(OLD))
from observe import DATA, M, F, resample, avg, ew, expiry
DEST=ROOT/'data/research/month-firstweek-yolo-confirm-20261113-v1'
PICT=ROOT/'output/month-firstweek-yolo-confirm-20261113'
BATCH=6

def sample(man):
    start=pd.Timestamp('2026-09-09T00:00Z').value//1000000
    g=man[man.coverage.eq('complete') & man.known_ms.ge(start)].copy()
    g=g[g.known_ms+240*M < g.month.map(expiry)]
    out=[];used=set();days=set()
    for _,group in g.sort_values(['known_ms','id']).groupby('side',sort=True):
        for block in np.array_split(np.arange(len(group)),6):
            for loc in sorted(block,key=lambda k:(abs(k-(block[0]+block[-1])/2),k)):
                r=group.iloc[loc];date=r.id.split('|')[1]
                if r.symbol not in used and date not in days:
                    out.append(r);used.add(r.symbol);days.add(date);break
    a=pd.DataFrame(out).sort_values('known_ms')
    assert len(a)==12 and a.id.is_unique and a.symbol.is_unique and a.side.value_counts().eq(6).all()
    return a

def makechart(win,p,tf,r,reading):
    img=win.image.copy();h,w=img.shape[:2]
    x,y,bh,bv=[p[k] for k in ['prediction_cx_norm','prediction_cy_norm','prediction_w_norm','prediction_h_norm']]
    cv2.rectangle(img,(int((x-bh/2)*w),int((y-bv/2)*h)),(int((x+bh/2)*w),int((y+bv/2)*h)),(174,115,35),3)
    sheet=Image.new('RGB',(w,h+105),'white')
    sheet.paste(Image.fromarray(cv2.cvtColor(img,cv2.COLOR_BGR2RGB)),(0,105))
    d=ImageDraw.Draw(sheet)
    def txt(y,words,n):d.text((22,y),words,font=ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc',n),fill='#313942')
    stamp=pd.Timestamp(reading,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')
    txt(8,f'{r.symbol}   ·   {tf} 分钟   ·   '+('看多' if r.side=='long' else '看空')+'   ·   旧 YOLO 的首次同向框',27)
    txt(55,f'北京时间 {stamp} 才能看到此框。图到该根收盘为止；框标的是此前的密集核心。置信分 {p["confidence"]:.2f}',19)
    PICT.mkdir(parents=True,exist_ok=True);sheet.save(PICT/f'first-{tf}m.png')

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    m=sample(pd.read_csv(DATA/'manifest.csv.gz'))
    m[['id','symbol','side','known_ms']].to_csv(HERE/'sample.csv',index=False)
    det=YoloDetector(); status=det.warmup(); assert status['ready'] and det._model.names==MODEL_CLASSES
    outcomes=[]; ledger=[]; examples=set(); print('model ready',status['device'],flush=True)
    for r in m.itertuples():
        raw=np.load(DATA/'tapes'/f'{r.id}.npy')
        for tf,a in ((15,resample(raw)),(5,raw)):
            k=r.known_ms; step=tf*M; c=a[:,4]; ts=a[:,0].astype('int64')
            fields=pd.DataFrame(a[:,1:],columns=['open','high','low','close'])
            for n in (20,60,120):
                fields[f'sma{n}']=avg(c,n);fields[f'ema{n}']=pd.Series(c).ewm(span=n,adjust=False).mean().to_numpy()
            endpoints=np.flatnonzero((ts>=k)&(ts+step<=k+240*M))
            assert len(endpoints)==240//tf
            allp=[]; batch=[]
            def score():
                if not batch: return
                result=det._model.predict(source=[w.image for i,w in batch],device=det._device,batch=len(batch),**PREDICT_PARAMETERS)
                assert len(result)==len(batch)
                for pred,(i,win) in zip(result,batch):
                    bx=pred.boxes
                    if bx is None or not len(bx):continue
                    proposals=parse_prediction(bx.xywhn.cpu().numpy(),bx.cls.cpu().numpy(),bx.conf.cpu().numpy(),win,r.symbol,str(tf)+'m')
                    for p in proposals:
                        stamp=int(ts[i]+step);fresh=p['core_start_ms']>=k
                        ok=p['structural_pass'] and p['side']==r.side and fresh
                        q=dict(id=r.id,tf=tf,decision_ms=stamp,delay=(stamp-k)//M,gate=bool(ok),
                               fresh_core=bool(fresh),**p)
                        if ok and tf not in examples:
                            makechart(win,p,tf,r,stamp);examples.add(tf)
                        allp.append(q)
                batch.clear()
            for i in endpoints:
                for n in (18,19):
                    sl=slice(i-n+1,i+1); image,tr=render_chart(fields.iloc[sl])
                    win=RenderedWindow(image,tr,tuple(map(int,ts[sl])),hashlib.sha256(image.tobytes()).hexdigest())
                    batch.append((i,win))
                    if len(batch)==BATCH:score()
            score(); ledger.extend(allp)
            valid=[p for p in allp if p['gate']]
            chosen=min(valid,key=lambda p:(p['decision_ms'],-p['confidence'])) if valid else {}
            outcomes.append(dict(id=r.id,tf=tf,side=r.side,known_ms=k,closed_bars=len(endpoints),images=2*len(endpoints),
                boxes=len(allp),any_directional=any(p['structural_pass'] and p['side']==r.side for p in allp),
                found=bool(valid),first_ms=chosen.get('decision_ms'),delay=chosen.get('delay'),conf=chosen.get('confidence')))
        print(r.id,'done',flush=True)
    o=pd.DataFrame(outcomes);o.to_csv(HERE/'pilot.csv',index=False)
    pd.DataFrame(ledger).to_csv(DEST/'boxes.csv.gz',index=False)
    assert o.images.sum()==1536 and len(o)==24
    print(o.groupby('tf')[['images','boxes','any_directional','found']].sum(),flush=True)
if __name__=='__main__': main()
