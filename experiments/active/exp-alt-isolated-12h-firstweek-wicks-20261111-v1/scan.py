"""Descriptive Binance monthly-first-week event census. No simulated orders.

Formation: UTC-aligned OHLC from complete 15m candles, NY first Monday week as
in Pine V1.5. The inward wick uses this bar's OHLC. Isolation, which depends on
subsequent bars, is retrospective. Reversal labels use only the NEXT seven
days' 15m high/low, relative to the signal close; no data from the wick itself.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from yoyo.evaluation.month_first_week_touch_review import bounds

DATA = ROOT / 'data/research/alt-isolated-12h-wicks-20261111/binance/series'
CACHE = ROOT / 'data/research/alt-isolated-12h-wicks-20261111/scanned'
SLOT = 900_000
CUTOFF = int(pd.Timestamp('2026-11-11T10:30Z').timestamp()*1000)
PERIODS = (720,240,60,30,15)
CAL = [(str(m),*[int(s.timestamp()*1000)//SLOT for s in bounds(str(m))])
       for m in pd.period_range('2019-08','2026-11',freq='M')]
WEEK = 7*96
THRESHOLDS = (10,20,30)


def aggregate(q,k):
    groups=q.reshape(-1,k,4)
    return np.column_stack((groups[:,0,0],groups[:,:,1].max(axis=1),
                            groups[:,:,2].min(axis=1),groups[:,-1,3]))


def scan(symbol,raw):
    raw=raw[raw[:,0]<CUTOFF]
    if len(raw)==0:
        return [],[]
    times=raw[:,0].astype(np.int64)
    if (times%SLOT).any() or (np.diff(times)<=0).any():
        raise ValueError(f'{symbol}: timestamp order/alignment')
    slots=times//SLOT
    origin=slots[0]//48*48
    last=slots[-1]+1
    q=np.full((((last-origin+47)//48)*48,4),np.nan)
    values=np.asarray(raw[:,1:5],dtype=float)
    if (not np.isfinite(values).all() or (values[:,2]<=0).any() or
        (values[:,1]<np.maximum(values[:,0],values[:,3])).any() or
        (values[:,2]>np.minimum(values[:,0],values[:,3])).any()):
        raise ValueError(f'{symbol}: invalid OHLC')
    q[slots-origin]=values
    missing=np.r_[0,np.cumsum(np.isnan(q[:,0]))]
    agg={tf:aggregate(q,tf//15) for tf in PERIODS}
    events,coverage=[],[]
    for month,start,ready,end in CAL:
        if ready<=slots[0] or start>=last or ready>=last:
            continue
        a,b,c=start-origin,ready-origin,min(end,last)-origin
        row=dict(symbol=symbol,month=month,full_month=end<=last,status='valid')
        coverage.append(row)
        if a<0 or missing[b]!=missing[a]:
            row['status']='incomplete_anchor';continue
        if missing[c]!=missing[b]:
            row['status']='gapped_active_window';continue
        H=q[a:b,1].max();L=q[a:b,2].min()
        if H<=L:
            row['status']='flat_anchor';continue
        row.update(week_high=H,week_low=L)
        rails={'lower':L-.618*(H-L),'upper':H+.618*(H-L)}
        hits={}
        for side,rail in rails.items():
            eps=abs(rail)*1e-12
            mask=(q[b:c,2]<=rail+eps)&(q[b:c,1]>=rail-eps)
            hits[side]=np.flatnonzero(mask)+b if rail>0 else np.empty(0,dtype=int)
        combined=np.union1d(hits['lower'],hits['upper'])
        if not len(combined):
            continue
        # UTC+8 calendar date of the first literal touch (either rail).
        days=(origin+combined+32)//96
        daily=combined[np.r_[True,np.diff(days)!=0]]
        daily=np.setdiff1d(daily,np.intersect1d(hits['lower'],hits['upper']))
        for tf in PERIODS:
            k=tf//15; frame=agg[tf]
            for side,rail in rails.items():
                touch=hits[side]
                if not len(touch):
                    continue
                sign=1 if side=='lower' else -1
                bins,ix=np.unique(touch//k,return_index=True)
                first=touch[ix]
                admitted=np.isin(first,daily) & (bins*k>=b) & ((bins+1)*k<=c)
                for i,touch_pos in zip(bins[admitted],first[admitted]):
                    o,h,l,close=frame[i];eps=rail*1e-12
                    wick=min(o,close)-l if sign==1 else h-max(o,close)
                    body=abs(o-close)
                    pure=(min(o,close)>rail+eps and l<rail-eps) if sign==1 else (max(o,close)<rail-eps and h>rail+eps)
                    ratio=wick/(h-l) if h>l else 0.
                    long_wick=pure and ratio>=.5-1e-12 and wick+eps>=2*body
                    nbh=i>=2 and (i-2)*k>=b and (i+3)*k<=c
                    isolated=False
                    if nbh:
                        neighbours=frame[[i-2,i-1,i+1,i+2]]
                        isolated=bool((neighbours[:,2]>rail+eps).all() if sign==1 else (neighbours[:,1]<rail-eps).all())
                    ev=dict(symbol=symbol,month=month,tf=tf,side=side,touch_ms=int((origin+touch_pos)*SLOT),
                            open_ms=int((origin+i*k)*SLOT),close_ms=int((origin+(i+1)*k)*SLOT),level=rail,
                            week_high=H,week_low=L,open=o,high=h,low=l,close=close,wick_pct=100*ratio,
                            long_wick=bool(long_wick),neighbours_known=bool(nbh),isolated=isolated,
                            month_unique=bool(row['full_month'] and len(bins)==1),month_complete=bool(row['full_month']))
                    j=(i+1)*k
                    ev['follow7']=bool(j+WEEK<=last-origin and missing[j+WEEK]==missing[j])
                    if ev['follow7']:
                        future=q[j:j+WEEK]
                        favorable=(future[:,1]/close-1)*100 if sign==1 else (1-future[:,2]/close)*100
                        ev['mfe7']=float(favorable.max())
                        ev['mfe3']=float(favorable[:288].max())
                        ev['end7']=float((future[-1,3]/close-1)*sign*100)
                        retouch=future[:,2]<=rail+eps if sign==1 else future[:,1]>=rail-eps
                        first_ret=np.flatnonzero(retouch)
                        n=int(first_ret[0]) if len(first_ret) else WEEK
                        ev['max_before_retouch']=float(favorable[:n].max()) if n else 0.
                        # On a shared 15m candle only its opening price has provable precedence.
                        if n<WEEK and (future[n,0]-rail)*sign>eps:
                            ev['max_before_retouch']=max(ev['max_before_retouch'],float((future[n,0]/close-1)*sign*100))
                    events.append(ev)
    return events,coverage


def sources(symbol):
    return [p for p in (DATA/(symbol+'.npy'),DATA.parent/'tail'/(symbol+'.npy')) if p.is_file()]


def signature(parts):
    return [[p.parent.name,p.stat().st_size,p.stat().st_mtime_ns] for p in parts]


def process(symbol):
    parts=sources(symbol)
    store=CACHE/symbol
    store.mkdir(parents=True,exist_ok=True)
    receipt=store/'done.json'
    state=signature(parts)
    if receipt.exists() and json.loads(receipt.read_text())==state:
        return symbol
    arrays=[np.load(p,mmap_mode='r') for p in parts]
    raw=arrays[0]
    if len(arrays)==2 and len(arrays[1]):
        # Prefer the recent exchange snapshot on overlapping timestamps;
        # retain archived rows for any holes in that snapshot.
        tail=arrays[1]
        pos=np.searchsorted(raw[:,0],tail[0,0])
        keep=~np.isin(raw[pos:,0],tail[:,0])
        merged=np.concatenate((raw[pos:][keep],tail))
        raw=np.concatenate((raw[:pos],merged[np.argsort(merged[:,0])]))
    events,coverage=scan(symbol,raw)
    for name,rows in [('events',events),('coverage',coverage)]:
        tmp=store/(name+'.tmp')
        pd.DataFrame(rows).to_csv(tmp,index=False,compression='gzip')
        tmp.replace(store/(name+'.csv.gz'))
    tmp=store/'done.tmp'
    tmp.write_text(json.dumps(state))
    tmp.replace(receipt)
    return symbol


def run(files,workers):
    if not files:return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(process,files,chunksize=2):pass


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--workers',type=int,default=6)
    p.add_argument('--watch',action='store_true')
    a=p.parse_args()
    eligible=set(pd.read_csv(DATA.parent/'universe_eligible.csv').symbol)
    while True:
        available=({p.stem for p in DATA.glob('*.npy')} | {p.stem for p in (DATA.parent/'tail').glob('*.npy')}) & eligible
        todo=[]
        for symbol in sorted(available):
            receipt=CACHE/symbol/'done.json'
            if not receipt.exists() or json.loads(receipt.read_text())!=signature(sources(symbol)):
                todo.append(symbol)
        if todo: print('scan batch',len(todo),'of',len(available),flush=True)
        run(todo,a.workers)
        if not a.watch:break
        time.sleep(5)
