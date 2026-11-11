"""Compare local Binance 15m coverage with each official monthly archive and supply missing rows."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import unquote
import zipfile

import numpy as np
import pandas as pd
import requests

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from yoyo.data.binance_um_vision_listing import files
DATA=ROOT/'data/research/alt-isolated-12h-wicks-20261111/binance'
OUT=DATA/'archive_fill'
KEYS=DATA/'monthly_keys'
AUDIT=DATA/'monthly_audit'
BASE='https://data.binance.vision/'
STEP=900000
for folder in (OUT,KEYS,AUDIT):
    folder.mkdir(exist_ok=True,parents=True)


def get_keys(sym):
    path=KEYS/(sym+'.json')
    if not path.exists():
        for attempt in range(5):
            try:
                keys=files(sym)
                path.with_suffix('.tmp').write_text(json.dumps(keys))
                path.with_suffix('.tmp').replace(path)
                break
            except Exception:
                if attempt==4:raise
                time.sleep(1+attempt)
    return json.loads(path.read_text())


def get_archive(key):
    for attempt in range(4):
        try:
            resp=requests.get(BASE+key,timeout=(8,35));resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
                with archive.open(next(m for m in archive.namelist() if m.endswith('.csv'))) as member:
                    rows=pd.read_csv(member,header=None,usecols=range(5),dtype=str)
            numeric=pd.to_numeric(rows.iloc[:,0],errors='coerce')
            arr=rows.loc[numeric.notna()].to_numpy(dtype='float64')
            assert arr.ndim==2 and len(arr)>0 and (np.diff(arr[:,0])>0).all()
            return arr
        except Exception:
            if attempt==3:raise
            time.sleep(1+attempt)


def fill(sym):
    path=DATA/'series'/(sym+'.npy')
    local=np.load(path,mmap_mode='r')[:,0].astype(np.int64) if path.is_file() else np.array([],dtype=np.int64)
    sig=path.stat().st_mtime_ns if path.is_file() else 0
    audit=AUDIT/(sym+'.json')
    if audit.is_file() and json.loads(audit.read_text()).get('local_signature')==sig:
        return
    records=get_keys(sym)
    patches=[];counts={};absent=[]
    for key in records:
        month=key[-11:-4]
        if month>'2026-09':continue
        m=pd.Timestamp(month+'-01',tz='UTC')
        begin,end=[int(x.timestamp()*1000) for x in (m,m+pd.offsets.MonthBegin(1))]
        left,right=np.searchsorted(local,[begin,end])
        existing=local[left:right]
        if len(existing)==(end-begin)//STEP:continue
        candles=get_archive(key)
        times=candles[:,0].astype(np.int64)
        if times.min()<begin or times.max()>=end or (times%STEP).any():raise ValueError((sym,key))
        keep=~np.isin(times,existing,assume_unique=True)
        if keep.any():patches.append(candles[keep])
        counts[month]=[len(candles),int(keep.sum())]
        if len(candles)<(end-begin)//STEP:absent.append(month)
    result=np.concatenate(patches) if patches else np.empty((0,5))
    target=OUT/(sym+'.npy')
    if len(result) or target.exists():
        tmp=target.with_suffix('.tmp')
        with tmp.open('wb') as f:np.save(f,result)
        tmp.replace(target)
    usage=dict(symbol=sym,local_signature=sig,official_archive_months_through_sep=sum(k[-11:-4]<='2026-09' for k in records),
               official_first=records[0][-11:-4] if records else None,official_last=records[-1][-11:-4] if records else None,
               checked_partial_months=counts,non_full_official_archive=absent,added_bars=len(result))
    audit.with_suffix('.tmp').write_text(json.dumps(usage))
    audit.with_suffix('.tmp').replace(audit)


def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('operation',choices=['keys','fill','watch'])
    p.add_argument('--workers',type=int,default=10)
    args=p.parse_args()
    eligible=pd.read_csv(DATA/'universe_eligible.csv').symbol.tolist()
    while True:
        if args.operation=='keys':
            todo=[s for s in eligible if not (KEYS/(s+'.json')).exists()]
            work=get_keys
        else:
            local={p.stem:p.stat().st_mtime_ns for p in (DATA/'series').glob('*.npy')}
            scope=eligible if args.operation=='fill' else [s for s in eligible if s in local]
            todo=[s for s in scope if not (AUDIT/(s+'.json')).exists() or
                  json.loads((AUDIT/(s+'.json')).read_text()).get('local_signature')!=local.get(s,0)]
            work=fill
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            fs={pool.submit(work,s):s for s in todo}
            for n,f in enumerate(as_completed(fs),1):
                try:f.result()
                except Exception as exc:print('failed',fs[f],str(exc)[:200],flush=True)
                if n%50==0: print(args.operation,n,len(todo),flush=True)
        if args.operation!='watch':return
        time.sleep(6)


if __name__=='__main__':main()
