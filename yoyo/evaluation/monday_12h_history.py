"""Reuse registered OKX caches; fetch only the missing BTC/ETH 5m tail.

Public GET transport is the repository fetcher. Complete 1m groups only;
no interpolation, content hashing, private writes or production cache writes.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import pandas as pd
from src.data.fetch_okx import API, _request

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'data/crypto/market/exp-monday-12h-partial-recovery-20261026-v1'
END=pd.Timestamp('2026-10-26T15:00Z')
OHLC=['open','high','low','close']

def sources(symbol):
    market='data/crypto/market/'
    if symbol=='BTC':
        base=[(market+'kline_preholdout_okx_5m/okx_BTC_USDT_SWAP_5m_341567.csv',5),
              (market+'kline_holdout_btc5m/okx_BTC_USDT_SWAP_5m_52992.csv',5)]
    else:
        base=[(market+'kline_lowtf_20260922/okx_ETH_USDT_SWAP_1m_1402560.csv',1)]
    return base+[(market+f'kline_lowtf_20260922/okx_api_{symbol}_USDT_SWAP_1m_20260831T1600_20260922T1400.csv',1),
        (market+f'research-spike_v128_bb_slope_20260924/okx_api_{symbol}_USDT_SWAP_1m_20260922T1359_20260923T1600.csv',1)]

def fetch(symbol,start,end):
    path=DATA/'pages'/f'{symbol}_{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}.json'
    if path.exists():return json.loads(path.read_text())
    lower=int(start.timestamp()*1000);upper=int(end.timestamp()*1000);cursor=upper;rows=[]
    while cursor>lower:
        r=_request(f'{API}?instId={symbol}-USDT-SWAP&bar=5m&limit=300&after={cursor}')
        if r.get('code')!='0':raise RuntimeError(str(r.get('code')))
        p=r['data']
        if not p:break
        nxt=min(int(x[0]) for x in p)
        if nxt>=cursor:raise RuntimeError('Non-decreasing cursor')
        rows.extend(x for x in p if lower<=int(x[0])<upper and x[8]=='1');cursor=nxt
    path.write_text(json.dumps(rows));return rows

def build(symbol):
    out=DATA/symbol;out.mkdir(exist_ok=True)
    if (out/'candles.csv.gz').exists():return
    frames=[];receipt=[]
    for name,minutes in sources(symbol):
        p=ROOT/name;r=pd.read_csv(p);r.index=pd.to_datetime(r.ts,unit='ms',utc=True)
        f=r[[*OHLC,'volume']].astype(float)
        if minutes==1:
            g=f.resample('5min');f=g.agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
            f=f[g.close.count().eq(5)]
        f['source']=name;frames.append(f)
        receipt.append(dict(path=name,size=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns,retained_rows=len(f)))
    f=pd.concat(frames).sort_index()
    # Shared boundary minutes may overlap; any conflicting price rejects reuse.
    dup=f[f.index.duplicated(False)]
    if len(dup) and dup.groupby(level=0)[OHLC].nunique().gt(1).any().any():raise RuntimeError('Conflicting cache overlap')
    f=f[~f.index.duplicated(keep='first')]
    start=f.index[-1]+pd.Timedelta(minutes=5)
    bounds=[start,*pd.date_range(start.ceil('D'),END,freq='7D'),END]
    intervals=[(a,b) for a,b in zip(bounds,bounds[1:]) if a<b]
    with ThreadPoolExecutor(max_workers=3) as pool:
        pages=sum(list(pool.map(lambda ab:fetch(symbol,*ab),intervals)),[])
    r=pd.DataFrame(pages,columns=['ts',*OHLC,'volume','base','quote','confirm'])
    r.index=pd.to_datetime(r.ts.astype('int64'),unit='ms',utc=True)
    r=r[[*OHLC,'volume']].astype(float);r['source']='OKX_native_5m_tail'
    f=pd.concat([f,r]).sort_index();f=f[f.index<END]
    if f.index.has_duplicates:raise RuntimeError('Duplicate source bars')
    if (f.high<f[OHLC].max(axis=1)).any() or (f.low>f[OHLC].min(axis=1)).any():raise RuntimeError('Invalid OHLC')
    f.index.name='open_time';f.to_csv(out/'candles.csv.gz')
    meta=dict(instrument=symbol+'-USDT-SWAP',first=str(f.index[0]),last=str(f.index[-1]),rows=len(f),
        gaps=int(f.index.to_series().diff().dropna().ne(pd.Timedelta(minutes=5)).sum()),sources=receipt,
        fetched_rows=len(r),api=API,content_hashing=False)
    (out/'receipt.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta),flush=True)

def main():
    DATA.mkdir(parents=True,exist_ok=True);(DATA/'pages').mkdir(exist_ok=True)
    for symbol in ['BTC','ETH']:
        build(symbol)
        # Repair only missing registered intervals using exact native candles.
        # Preserve receipts and raw repair pages; never interpolate absent data.
        out=DATA/symbol;f=pd.read_csv(out/'candles.csv.gz',index_col='open_time',parse_dates=True)
        d=f.index.to_series().diff();missing=[]
        for t in d[d>pd.Timedelta(minutes=5)].index:
            missing.extend(fetch(symbol,t-d.loc[t]+pd.Timedelta(minutes=5),t))
        if missing:
            r=pd.DataFrame(missing,columns=['ts',*OHLC,'volume','base','quote','confirm'])
            r.index=pd.to_datetime(r.ts.astype('int64'),unit='ms',utc=True)
            r=r[[*OHLC,'volume']].astype(float);r['source']='OKX_native_5m_gap_repair'
            f=pd.concat([f,r]).sort_index()
            if f.index.has_duplicates:raise RuntimeError('Repair overlaps source')
            f.index.name='open_time';f.to_csv(out/'candles.csv.gz')
            meta=json.loads((out/'receipt.json').read_text());meta.update(repaired_rows=len(r),rows=len(f),
                gaps=int(f.index.to_series().diff().dropna().ne(pd.Timedelta(minutes=5)).sum()))
            (out/'receipt.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta),flush=True)

if __name__=='__main__':main()
