"""Complete absent native pre-entry turnover, reusing existing observations and bounded H1 queries."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_liquid_priority_inputs as local
from yoyo.evaluation import cloud_close_prior_minute_liquidity as minute

EXP = local.EXP.parent/'exp-cloud-close-liquid-priority-history-20261109-v1'
CACHE = minute.CACHE.parent/'cloud_close_prior_volume_api'
H = local.wallet.HOUR
LOCK = threading.Lock()
CLIENT = threading.local()
NEXT = 0.


def windows(frame):
    for sym,rows in frame.groupby('symbol'):
        stamps = np.unique(rows.prior_hour_ms)
        i=0
        while i<len(stamps):
            j=stamps.searchsorted(stamps[i]+300*H)
            yield sym,stamps[i:j]
            i=j


def fetch(job):
    global NEXT
    sym,times = job
    start,end = int(times[0]),int(times[-1]+H)
    path = CACHE/f'{sym}-{start}-{end}.json'
    if not path.exists():
        if not hasattr(CLIENT,'session'):
            CLIENT.session=requests.Session()
        for attempt in range(5):
            with LOCK:
                now=time.monotonic()
                wait=max(0.,NEXT-now)
                NEXT=now+wait+.18
            time.sleep(wait)
            try:
                res=CLIENT.session.get('https://www.okx.com/api/v5/market/history-candles',
                     params=dict(instId=sym+'-USDT-SWAP',bar='1H',after=end,limit=(end-start)//H),timeout=(8,25))
                res.raise_for_status()
                body=res.json()
                if body.get('code')=='50011':
                    raise requests.RequestException('Public market-data request rate limited')
                if body.get('code') not in ('0','51001'):
                    raise ValueError(f'Unexpected public history response {sym}: {body.get("code")}')
                wanted=set(times)
                body['data']=[r for r in body['data'] if int(r[0]) in wanted]
                local.repair.archive.prices.safe_json(path,body)
                break
            except requests.RequestException:
                if attempt==4:
                    raise
                time.sleep(2**attempt)
    body=json.loads(path.read_text())
    return sym,times,body


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed old/new venue turnover reconciliation')
    grid=pd.read_csv(local.EXP/'opportunities.csv.gz',dtype={'symbol':str})
    quotes,_=local.repair.checked_quotes()
    # Only final prior-hour receipts from this study, with 60 fully measured minutes, may transfer.
    receipt = EXP/'transferred_minutes.csv.gz'
    if receipt.exists():
        transferred = pd.read_csv(receipt,index_col='event_id').prior_turnover_usdt
    else:
        prior = pd.read_csv(minute.EXP/'prior_liquidity.csv.gz',index_col='event_id')
        transferred = prior.loc[prior.status.eq('observed'),'mean_usdt'].mul(60)
    values = grid.event_id.map(transferred)
    ready = grid.prior_turnover_usdt.isna() & values.notna()
    grid.loc[ready,'prior_turnover_usdt'] = values[ready]
    grid.loc[ready,'source'] = 'verified_prior_60m'
    if not receipt.exists():
        grid.loc[ready,['event_id','prior_turnover_usdt']].to_csv(receipt,index=False)
    misses=grid[grid.prior_turnover_usdt.isna()]
    keys=dict(zip(zip(grid.symbol,grid.prior_hour_ms),grid.index))
    if len(keys)!=len(grid):
        raise ValueError('Duplicate native candidate H1 across the eligibility pool')
    CACHE.mkdir(exist_ok=True,parents=True)
    counts={}
    jobs=list(windows(misses))
    print('missing',len(misses),'windows',len(jobs),flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for n,(sym,stamps,body) in enumerate(pool.map(fetch,jobs)):
            ts,ohlc=quotes(sym)
            valid={int(r[0]):r for r in body['data'] if len(r)>=9 and str(r[8])=='1'}
            for t in stamps:
                r=valid.get(int(t)); idx=ts.searchsorted(t)
                why='missing_official_previous_hour'
                if r is not None:
                    arr=np.asarray(r[1:8],dtype=float)
                    if idx==len(ts) or ts[idx]!=t or not np.allclose(arr[:4],ohlc[idx],rtol=1e-9,atol=1e-15):
                        why='frozen_H1_differs'
                    elif not np.isfinite(arr).all() or min(arr[4:])<0:
                        why='invalid_volume'
                    else:
                        why='official_H1'
                        grid.at[keys[(sym,t)],'prior_turnover_usdt']=arr[6]
                grid.at[keys[(sym,t)],'source']=why
                counts[why]=counts.get(why,0)+1
            if n and n%500==0:
                print('checked windows',n,counts,flush=True)
    grid.to_csv(EXP/'opportunities.csv.gz',index=False)
    local.repair.archive.prices.safe_json(EXP/'summary.json',dict(total=len(grid),covered=int(grid.prior_turnover_usdt.notna().sum()),
        sources=grid.source.value_counts().to_dict(),public_queries=len(jobs),before_decision=True))


if __name__=='__main__':
    main()
