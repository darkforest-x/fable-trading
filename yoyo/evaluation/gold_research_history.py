"""Reuse registered XAU swap minutes and fetch missing native 5m history.

Uses the repository OKX fetcher's public GET transport and shared throttle.
Only complete cached 5m bins and exchange-confirmed candles are retained.
No content hashing, gap filling, private account access or production writes.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

import pandas as pd

from src.data.fetch_okx import API, _request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/crypto/market/exp-xau-monday-recovery-20261026-v1'
CACHED = ROOT / 'data/kline_deep_1m/okx_XAU_USDT_SWAP_1m_151674.csv'
OHLC = ['open', 'high', 'low', 'close']


def fetch_interval(start, end):
    """Download one missing half-open UTC interval with resumable raw pages."""
    key = start.strftime('%Y%m%d%H%M') + '_' + end.strftime('%Y%m%d%H%M')
    path = OUT / 'pages' / (key + '.json')
    if path.exists():
        return json.loads(path.read_text())
    rows, cursor = [], int(end.timestamp()*1000)
    lower = int(start.timestamp()*1000)
    while cursor > lower:
        data = _request(f'{API}?instId=XAU-USDT-SWAP&bar=5m&limit=300&after={cursor}')
        if data.get('code') != '0':
            raise RuntimeError(f"OKX code {data.get('code')}")
        page = data.get('data', [])
        if not page:
            break
        next_cursor = min(int(r[0]) for r in page)
        if next_cursor >= cursor:
            raise RuntimeError('Non-decreasing history cursor')
        rows.extend(r for r in page if lower <= int(r[0]) < int(end.timestamp()*1000) and r[8]=='1')
        cursor = next_cursor
    path.write_text(json.dumps(rows))
    print(f'Fetched {key}: {len(rows)} confirmed bars', flush=True)
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'pages').mkdir(exist_ok=True)
    if (OUT/'candles.csv.gz').exists():
        raise RuntimeError('Existing completed snapshot; reuse it')
    raw = pd.read_csv(CACHED)
    raw.index = pd.to_datetime(raw.ts, unit='ms', utc=True)
    g = raw.resample('5min')
    cached = g.agg(dict(open='first',high='max',low='min',close='last',volume='sum'))
    cached = cached.loc[g.close.count().eq(5)]
    cached['source'] = 'registered_OKX_1m_aggregate'
    # Verify the reused source is the same instrument/history as the public API.
    for t in [cached.index[0], cached.index[len(cached)//2], cached.index[-1]]:
        r = _request(f'{API}?instId=XAU-USDT-SWAP&bar=5m&limit=1&after={int(t.timestamp()*1000)+1}')
        p = r['data'][0]
        if int(p[0]) != int(t.timestamp()*1000) or any(abs(float(p[k+1])-cached.loc[t,col])>1e-7 for k,col in enumerate(OHLC)):
            raise RuntimeError('Registered minute archive disagrees with native OKX history')
    start = pd.Timestamp('2025-04-01T00:00Z')
    end = pd.Timestamp.now(tz='UTC').floor('5min')
    intervals=[]
    for a,b in [(start,cached.index[0]),(cached.index[-1]+pd.Timedelta(minutes=5),end)]:
        bounds=[a,*pd.date_range(a.normalize()+pd.offsets.MonthBegin(1),b,freq='MS'),b]
        intervals.extend((x,y) for x,y in zip(bounds,bounds[1:]) if x<y)
    pages=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending=[pool.submit(fetch_interval,a,b) for a,b in intervals]
        for task in as_completed(pending):pages.extend(task.result())
    f=pd.DataFrame(pages,columns=['ts',*OHLC,'volume','base_volume','quote_volume','confirm'])
    f.index=pd.to_datetime(f.ts.astype('int64'),unit='ms',utc=True)
    f=f[[*OHLC,'volume']].astype(float)
    f['source']='OKX_native_5m'
    f=pd.concat([f,cached]).sort_index()
    if f.index.has_duplicates:raise RuntimeError('Duplicate source intervals')
    if (f.high<f[OHLC].max(axis=1)).any() or (f.low>f[OHLC].min(axis=1)).any():raise RuntimeError('Invalid OHLC')
    f.index.name='open_time'
    f.to_csv(OUT/'candles.csv.gz')
    stamp=CACHED.stat()
    receipt=dict(instrument='XAU-USDT-SWAP',first=str(f.index[0]),last=str(f.index[-1]),rows=len(f),
        cached_rows=len(cached),source_path=str(CACHED.relative_to(ROOT)),source_size=stamp.st_size,
        source_mtime_ns=stamp.st_mtime_ns,gaps=int((f.index.to_series().diff().dropna()!=pd.Timedelta(minutes=5)).sum()),
        fetched_at=str(pd.Timestamp.now(tz='UTC')),api=API,raw_pages='pages/',content_hashing=False)
    (OUT/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':main()
