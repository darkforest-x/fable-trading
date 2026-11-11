"""Recover 5m OHLC from OKX confirmed 1m monthly archives and audit the prefix before the original H1 first touch."""
import argparse
import csv
import gzip
import io
import json
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
OLD = ROOT / 'experiments/active/exp-spike-okx-midnight-rank-20260915-v1/inputs'
EVENTS = ROOT / 'experiments/active/exp-alt-month-first-week-entries-20261110-v1/events.csv.gz'
DEST = ROOT / 'data/research/month-first-week-okx-archive5m-v2'
BJ, NY = ZoneInfo('Asia/Shanghai'), ZoneInfo('America/New_York')
MIN, STEP = 60000, 300000


def archive_month(ms):
    return datetime.fromtimestamp(ms/1000, BJ).strftime('%Y-%m')


def event_ready(month):
    first = datetime.fromisoformat(month+'-01').replace(tzinfo=NY)
    return int((first+timedelta(days=(-first.weekday())%7+7)).timestamp()*1000)


def atomic_json(path, data):
    path.parent.mkdir(exist_ok=True,parents=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(tmp,'wt') as f: json.dump(data,f,separators=(',',':'))
    os.replace(tmp,path)


def read_zip(data, symbol, month):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [name for name in z.namelist() if name.endswith('.csv')]
        if len(names)!=1: raise ValueError('archive CSV member count')
        with z.open(names[0]) as f: raw=pd.read_csv(f,usecols=['instrument_name','open_time','open','high','low','close','vol','confirm'])
    if set(raw.instrument_name)!={symbol+'-USDT-SWAP'} or not raw.confirm.eq(1).all(): raise ValueError('instrument or confirm mismatch')
    raw=raw.drop_duplicates().sort_values('open_time')
    if raw.empty or raw.open_time.duplicated().any(): raise ValueError('missing or conflicting timestamps')
    a=raw[['open_time','open','high','low','close','vol']].to_numpy(dtype=float)
    t=a[:,0].astype('int64')
    if (not np.isfinite(a).all() or (t%MIN).any() or archive_month(t[0])!=month or archive_month(t[-1])!=month
            or (a[:,3]<=0).any() or (a[:,3]>np.minimum(a[:,1],a[:,4])).any() or (a[:,2]<np.maximum(a[:,1],a[:,4])).any()):
        raise ValueError('invalid monthly candles')
    idx=np.flatnonzero((t%STEP==0)&(np.arange(len(a))<len(a)-4))
    idx=idx[t[idx+4]==t[idx]+4*MIN]
    # Sorted unique minute-aligned rows and exactly four minutes between endpoints imply all five minutes exist.
    return np.column_stack([t[idx],a[idx,1],np.maximum.reduce([a[idx+i,2] for i in range(5)]),
                            np.minimum.reduce([a[idx+i,3] for i in range(5)]),a[idx+4,4],sum(a[idx+i,5] for i in range(5))])


def get_archive(symbol,month,download):
    existing=OLD/(symbol+'-USDT-SWAP')/(month+'.npz')
    cached=DEST/'bars'/symbol/(month+'.npz')
    for path in (cached, existing):
        if path.is_file():
            with np.load(path,allow_pickle=False) as pack: return pack['bar5'],'download_cache' if path==cached else 'local_archive'
    if not download: return None,'not_cached'
    receipt=DEST/'receipts'/symbol/(month+'.json')
    if receipt.is_file() and json.loads(receipt.read_text())['status']=='not_found':return None,'not_found'
    url=f'https://static.okx.com/cdn/okex/traderecords/candlesticks/monthly/{month.replace("-","")}/{symbol}-USDT-SWAP-candlesticks-{month}.zip?v=999'
    for attempt in range(3):
        try:
            r=requests.get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=(5,24))
            if r.status_code==404:
                atomic_json(receipt,dict(status='not_found',url=url));return None,'not_found'
            r.raise_for_status()
            bars=read_zip(r.content,symbol,month)
            cached.parent.mkdir(parents=True,exist_ok=True)
            with cached.with_suffix('.tmp').open('wb') as f: np.savez_compressed(f,bar5=bars)
            os.replace(cached.with_suffix('.tmp'),cached)
            atomic_json(receipt,dict(status='ok',url=url,count=len(bars)))
            return bars,'downloaded_archive'
        except (OSError,ValueError,zipfile.BadZipFile) as exc:
            error=f'{type(exc).__name__}: {exc}'
            if attempt<2:time.sleep(1+attempt)
    atomic_json(receipt,dict(status='error',url=url,error=error))
    return None,'error'


def run_symbol(symbol, rows, downloaded, mode):
    months={}
    for e in rows:
        hour=int(datetime.fromisoformat(e['touch']).timestamp()*1000)
        ready=event_ready(e['month'])
        left=min(ready,hour-120*STEP)
        for m in {archive_month(left),archive_month(hour),archive_month(hour+119*STEP),archive_month(ready)}:
            allowed=mode=='all' or e['event_id'] in downloaded or 'retired' in e['cohort'] or 'delisted' in e['cohort']
            months[m]=months.get(m,False) or allowed
    packs={}; counts={}
    for m,download in sorted(months.items()):
        a,status=get_archive(symbol,m,download)
        counts[status]=counts.get(status,0)+1
        if a is not None:packs[m]=a
    for e in rows:
        ready=event_ready(e['month']); hour=int(datetime.fromisoformat(e['touch']).timestamp()*1000)
        needed=sorted({archive_month(ready),archive_month(hour-120*STEP),archive_month(hour),archive_month(hour+119*STEP)})
        avail=[packs[m] for m in needed if m in packs]
        if not avail:continue
        a=np.concatenate(avail); ts=a[:,0].astype('int64')
        w=a[(ts>=hour-120*STEP)&(ts<hour+120*STEP)]
        if not len(w):continue
        prior=a[(ts>=ready)&(ts<hour)]; level=float(e['level'])
        earlier=(prior[:,2]>=level).any() if e['side']=='upper' else (prior[:,3]<=level).any()
        prior_status='earlier_touch' if earlier else 'verified' if len(prior)==(hour-ready)//STEP else 'incomplete'
        record=dict(event_id=e['event_id'],symbol=symbol,source='okx_month_archive',prior_5m_status=prior_status,
                    source_months=[m for m in needed if m in packs],prior_5m_observed=len(prior),candles=w.tolist())
        atomic_json(DEST/'windows'/symbol/(e['month']+'-'+e['side']+'.json.gz'),record)
        counts['event_windows']=counts.get('event_windows',0)+1
    return counts


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['local-retired','all'],default='local-retired')
    parser.add_argument('--supplement-ids',type=Path)
    parser.add_argument('--workers',type=int,default=6)
    args=parser.parse_args()
    extra=set(args.supplement_ids.read_text().splitlines()) if args.supplement_ids else set()
    groups={}
    with gzip.open(EVENTS,'rt') as f:
        for e in csv.DictReader(f):groups.setdefault(e['symbol'],[]).append(e)
    # Work through retired instruments and supplemental event groups first.
    queue=sorted(groups,key=lambda s:not any('retired' in e['cohort'] or 'delisted' in e['cohort'] or e['event_id'] in extra for e in groups[s]))
    counts={};done=0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run_symbol,s,groups[s],extra,args.mode) for s in queue]
        for future in as_completed(futures):
            for k,v in future.result().items():counts[k]=counts.get(k,0)+v
            done+=1
            if done%20==0 or done==len(queue):print(done,counts,flush=True)
    atomic_json(DEST/'archive_receipt.json',dict(mode=args.mode,symbols=done,counts=counts,additional_event_ids=len(extra)))


if __name__=='__main__':main()
