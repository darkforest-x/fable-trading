"""Binance USD-M actual funding rate and mark per published settlement.

Only recorded settlement events enter PnL, using the actual timestamp in
milliseconds and the mark price in the exchange's funding history. Cache by
calendar month so all pools reuse the same immutable records.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import json
import os
from pathlib import Path
import time
import tempfile
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
ENDPOINT='https://fapi.binance.com/fapi/v1/fundingRate'
CACHE=ROOT/'data/research/month-firstweek-yolo-account-20261113-v1/funding'

@lru_cache(maxsize=800)
def month(symbol,period):
    CACHE.mkdir(parents=True,exist_ok=True)
    path=CACHE/f'{symbol}_{period}.json'
    if not path.exists():
        p=pd.Period(period,'M')
        first=int(p.start_time.value//1_000_000)
        last=min(int(p.end_time.value//1_000_000),int(time.time()*1000))
        if first>last:raise ValueError('future funding month')
        result=[];cursor=first
        while cursor<=last:
            url=ENDPOINT+'?'+urlencode(dict(symbol=symbol,startTime=cursor,endTime=last,limit=1000))
            for attempt in range(5):
                try:
                    url_to_read=url if attempt%2==0 else url.replace('fapi.binance.com','www.binance.com')
                    with urlopen(Request(url_to_read,headers={'User-Agent':'fable-offline-research'}),timeout=15) as res:
                        part=json.load(res)
                    break
                except OSError:
                    if attempt==4:raise
                    time.sleep(2**attempt)
            if not isinstance(part,list):raise RuntimeError(str(part))
            if not part:break
            assert all(r['symbol']==symbol and cursor<=int(r['fundingTime'])<=last for r in part)
            result.extend(part)
            cursor=int(part[-1]['fundingTime'])+1
            if len(part)<1000:break
        # Include cutoff even for zero-event months; never equate a request
        # error with a valid response containing no published settlements.
        with tempfile.NamedTemporaryFile('w',dir=CACHE,delete=False) as f:
            json.dump(dict(asof_ms=last,events=result),f,separators=(',',':'))
        Path(f.name).replace(path)
    content=json.loads(path.read_text())
    d=np.array([[int(r['fundingTime']),float(r['fundingRate']),float(r['markPrice'])] for r in content['events']],dtype=float).reshape(-1,3)
    assert np.isfinite(d).all() and (d[:,2]>0).all() and (np.diff(d[:,0])>0).all()
    return d,content['asof_ms']

def lookup(symbol,start_ms,end_ms):
    """Return settlements within inclusive endpoints; caller handles fill ordering."""
    lo=pd.Timestamp(int(start_ms),unit='ms').to_period('M')
    hi=pd.Timestamp(int(end_ms),unit='ms').to_period('M')
    parts=[]
    for period in pd.period_range(lo,hi,freq='M'):
        d,cutoff=month(symbol,str(period))
        if cutoff<min(int(end_ms),period.end_time.value//1_000_000):
            raise ValueError('funding artifact does not extend through the requested close')
        parts.append(d[(d[:,0]>=start_ms)&(d[:,0]<=end_ms)])
    return np.concatenate(parts)

def main():
    p=argparse.ArgumentParser();p.add_argument('--jobs',type=int,default=3);args=p.parse_args()
    photos=pd.read_csv(HERE/'primary_signals.csv')
    pop=pd.read_csv(HERE/'october_population.csv')
    months=set()
    for r in pd.concat([photos.rename(columns={'confirm_close_ms':'from_ms'}),pop.rename(columns={'known_ms':'from_ms'})]).itertuples():
        t=pd.Timestamp(int(r.from_ms),unit='ms')
        months.update((r.symbol,str(m)) for m in pd.period_range(t.to_period('M'),(t+pd.Timedelta(days=8)).to_period('M'),freq='M'))
    # Public UM history is rate-limited per IP. Read sequentially at a
    # sustainable rate; other processes can still serve already cached records.
    def prefetch(key):
        if not (CACHE/f'{key[0]}_{key[1]}.json').exists():
            time.sleep(2.5)
        return month(*key)
    with ThreadPoolExecutor(max_workers=min(args.jobs,1)) as exe:
        for i,(key,obs) in enumerate(zip(sorted(months),exe.map(prefetch,sorted(months)))):
            if i%25==0:print(i,len(months),key,len(obs[0]),flush=True)
if __name__=='__main__':main()
