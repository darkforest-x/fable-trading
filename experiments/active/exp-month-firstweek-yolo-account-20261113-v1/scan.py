"""Replay the most recent complete NY rail month with the original detector.

Only timestamps, known H1 direction and 5m OHLC through the decision are inputs.
For each bar use original W18/W19 render/parse; no price/outcome prefilter. A
fresh 4/5-bar core + 2 later bars requires at least six post-alert candles.
Stop after first valid box. Cache all decisions including no-box dispositions.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OLD = HERE.parent / 'exp-month-firstweek-owner-yolo-20261113-v1'
PILOT = HERE.parent / 'exp-month-firstweek-yolo-confirm-20261113-v1'
sys.path[:0] = [str(ROOT), str(OLD)]
from windows import expiry
from yoyo.layers.l1_detection.render import render_chart
from yoyo.monitor.yolo_detector import YoloDetector, RenderedWindow, parse_prediction, PREDICT_PARAMETERS, MODEL_CLASSES

SOURCE = ROOT/'data/research/month-firstweek-cloud-dense-20261112-v1'
CACHE = ROOT/'data/research/month-firstweek-yolo-account-20261113-v1/decisions'
STEP = 300_000
DAY = 24*60*60*1000

def population():
    m = pd.read_csv(HERE/'october_population.csv',usecols=['id','symbol','month','side','known_ms','near_edge','stop_ms','coverage','tape_path'],float_precision='round_trip')
    m=m[m.month.eq('2026-10')].sort_values(['known_ms','symbol'])
    assert len(m)>=359 and m.id.is_unique
    return m[m.coverage.eq('complete')]

def frozen_history():
    known={}
    for folder in (OLD,OLD/'ruler'):
        cases=pd.read_csv(folder/'cohort.csv',usecols=['case_id','symbol','side','known_ms'])
        rows=pd.read_csv(folder/'first_in_day.csv').query('tf==5').merge(cases,on=['case_id','symbol'],validate='one_to_one')
        for r in rows.itertuples():
            known[(r.symbol,r.side,int(r.known_ms))]=(bool(r.found),int(r.read_ms) if r.found else None)
    return known

def scan(r,det,batch_size):
    raw=np.load(ROOT/r.tape_path)
    ts=raw[:,0].astype('int64')
    assert np.all(np.diff(ts)==STEP)
    bars=pd.DataFrame(raw[:,1:],columns=['open','high','low','close'])
    for n in (20,60,120):
        bars[f'sma{n}']=bars.close.rolling(n,min_periods=n).mean()
        bars[f'ema{n}']=bars.close.ewm(span=n,adjust=False).mean()
    stop=min(r.known_ms+DAY,expiry(r.month)-1,int(r.stop_ms))
    indices=np.flatnonzero((ts+STEP>=r.known_ms+6*STEP)&(ts+STEP<=stop))
    frames=0
    for start in range(0,len(indices),batch_size//2):
        batch=[]
        for i in indices[start:start+batch_size//2]:
            for length in (18,19):
                sl=slice(i-length+1,i+1)
                im,tr=render_chart(bars.iloc[sl])
                batch.append((i,RenderedWindow(im,tr,tuple(map(int,ts[sl])),'')))
        results=det._model.predict(source=[b.image for _,b in batch],batch=len(batch),device=det._device,**PREDICT_PARAMETERS)
        frames+=len(batch)
        assert len(results)==len(batch)
        passing=[]
        for pred,(i,w) in zip(results,batch):
            boxes=pred.boxes
            if boxes is None or not len(boxes):continue
            obs=parse_prediction(boxes.xywhn.cpu().numpy(),boxes.cls.cpu().numpy(),boxes.conf.cpu().numpy(),w,r.symbol,'5m')
            for box in obs:
                if box['structural_pass'] and box['side']==r.side and box['core_start_ms']>=r.known_ms:
                    passing.append(int(ts[i]+STEP))
        if passing:return min(passing),frames
    return None,frames

def cache_path(r):
    return CACHE/f'{r.id}.json'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--shards',type=int,default=1)
    p.add_argument('--shard',type=int,default=0)
    p.add_argument('--device')
    p.add_argument('--batch',type=int,default=8)
    p.add_argument('--collect',action='store_true')
    args=p.parse_args()
    m=population()
    if args.collect:
        rows=[json.loads(cache_path(r).read_text()) for r in m.itertuples()]
        result=m.merge(pd.DataFrame(rows),on='id',validate='one_to_one')
        result.to_csv(HERE/'october_decisions.csv',index=False)
        signals=result[result.found].rename(columns={'id':'trade_id'}).copy()
        signals['confirm_close_ms']=signals.confirm_close_ms.astype('int64');signals['pool']='october'
        assert (signals.confirm_close_ms>signals.known_ms).all() and (signals.confirm_close_ms<=signals.stop_ms).all()
        signals[['trade_id','symbol','side','confirm_close_ms','pool','near_edge','tape_path','known_ms','month']].to_csv(HERE/'october_signals.csv',index=False)
        print('signals',len(signals),'of',len(m))
        return
    assert 0<=args.shard<args.shards and args.batch>=2 and args.batch%2==0
    det=YoloDetector()
    assert det.warmup()['ready'] and det._model.names==MODEL_CLASSES
    if args.device:det._device=args.device
    CACHE.mkdir(parents=True,exist_ok=True)
    already=frozen_history()
    for num,r in enumerate(m.itertuples()):
        dest=cache_path(r)
        if num%args.shards!=args.shard or dest.exists(): continue
        t=time.monotonic()
        key=(r.symbol,r.side,int(r.known_ms))
        if key in already:
            found,stamp=already[key];source='frozen_24h';frames=0
        else:
            stamp,frames=scan(r,det,args.batch)
            found=stamp is not None;source='original_yolo'
        record=dict(id=r.id,found=found,confirm_close_ms=stamp,source=source,frames=frames)
        tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(record));tmp.replace(dest)
        print(num,r.id,found,frames,round(time.monotonic()-t,2),flush=True)

if __name__=='__main__':main()
