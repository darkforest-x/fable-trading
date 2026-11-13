"""Take every October H1 wick through the active rail, ignoring later price paths.
Reuse validated 5m warmups. Backfill missing UTC days only from Binance futures
trade-price archives, checking consistency of overlapping quotes and the H1 OHLC.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import io
import sys
import urllib.request
import zipfile
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path[:0]=[str(ROOT),str(HERE.parent/'exp-month-firstweek-intraday-retest-20261112-v1')]
from review import read_raw,load_bars,expiry,BASE,FIVE,HOUR,DAY
DATA=ROOT/'data/research/month-firstweek-yolo-account-20261113-v1'
OLD=ROOT/'data/research/month-firstweek-cloud-dense-20261112-v1'
@lru_cache(maxsize=64)
def daily(symbol,day,period="5m"):
    name=f'{symbol}-{period}-{day}.zip'
    source=f'https://data.binance.vision/data/futures/um/daily/klines/{symbol}/{period}/{name}'
    with urllib.request.urlopen(source,timeout=25) as response:
        archive=zipfile.ZipFile(io.BytesIO(response.read()))
    with archive.open(archive.namelist()[0]) as f:
        df=pd.read_csv(f,usecols=range(5),header=None)
    df=df.apply(pd.to_numeric,errors='coerce').dropna()
    return df.to_numpy(dtype=float)
def recover(symbol,start,end):
    parts=[];dates=pd.date_range(pd.to_datetime(start,unit='ms',utc=True).normalize(),pd.to_datetime(end-1,unit='ms',utc=True).normalize(),freq='D')
    months={d.strftime('%Y-%m'):load_bars(symbol,d.strftime('%Y-%m')) for d in dates.to_period('M').unique()}
    for d in dates:
        left=max(start,d.value//10**6);right=min(end,(d+pd.Timedelta(days=1)).value//10**6)
        grid=np.arange(left,right,FIVE)
        a=months[d.strftime('%Y-%m')]; a=a[(a[:,0]>=left)&(a[:,0]<right)]
        if not np.array_equal(a[:,0],grid):
            patch=daily(symbol,d.strftime('%Y-%m-%d'));patch=patch[(patch[:,0]>=left)&(patch[:,0]<right)]
            if len(a):
                a_by=pd.DataFrame(a).set_index(0);p_by=pd.DataFrame(patch).set_index(0)
                idx=a_by.index.intersection(p_by.index)
                assert np.allclose(a_by.loc[idx],p_by.loc[idx],rtol=1e-10,atol=0)
                a=a_by.combine_first(p_by).sort_index().reset_index().to_numpy()
            else:a=patch
        if not np.array_equal(a[:,0],grid):raise ValueError(f'incomplete {d.date()}')
        parts.append(a)
    raw=np.concatenate(parts)
    assert np.isfinite(raw).all() and (raw[:,1:]>0).all()
    return raw
def run():
    out=DATA/'supplement';out.mkdir(parents=True,exist_ok=True)
    keys=['id','symbol','side','month','touch_ms','rail','near_edge']
    c=pd.read_csv(BASE/'cards.csv.gz',usecols=keys,float_precision='round_trip')
    c=c[c.month.eq('2026-10')]
    old=pd.read_csv(OLD/'manifest.csv.gz',usecols=['id','tape_path','coverage'])
    original=old[old.coverage.eq('complete')].set_index('id').tape_path.to_dict()
    rows=[]
    def analyze(g):
        symbol,signals=g
        hr=read_raw(symbol);hg=hr[:,0].astype('int64');items=[]
        for r in signals.itertuples(index=False):
            j=hg.searchsorted(r.touch_ms);assert hg[j]==r.touch_ms
            o,h,l,close=hr[j,1:5]
            long=r.side=='long'
            wick=min(o,close)-l if long else h-max(o,close)
            inside=min(o,close)>r.rail if long else max(o,close)<r.rail
            known=r.touch_ms+HOUR; stop=min(known+DAY,expiry(r.month))
            if not inside or h<=l or wick/(h-l)<.5 or known>=expiry(r.month):continue
            obj=dict(r._asdict(),known_ms=known,stop_ms=stop,coverage='complete',description='existing continuous tape')
            if r.id in original: obj['tape_path']=original[r.id]
            else:
                path=out/f'{r.id}.npy'
                try:
                    a=recover(symbol,known-5*DAY,stop+FIVE)
                    ix=np.searchsorted(a[:,0],r.touch_ms);z=a[ix:ix+12];assert len(z)==12
                    obj['description']='recovered warmup and full confirmation window'
                    if not np.allclose([z[0,1],z[:,2].max(),z[:,3].min(),z[-1,4]],[o,h,l,close],atol=0,rtol=1e-8):
                        # Some thin equity contracts have officially different opens
                        # at 1h vs first 5m. Verify each input in its native timeframe.
                        date=pd.Timestamp(r.touch_ms,unit='ms',tz='UTC').strftime('%Y-%m-%d')
                        official_h=daily(symbol,date,'1h'); official_5=daily(symbol,date)
                        assert np.allclose(official_h[official_h[:,0]==r.touch_ms,1:5],[[o,h,l,close]],rtol=1e-10,atol=0)
                        expected=official_5[(official_5[:,0]>=r.touch_ms)&(official_5[:,0]<known)]
                        assert expected.shape==z.shape and np.allclose(expected,z,rtol=1e-10,atol=0)
                        obj['description']='official 1h and 5m natively verified; interval opens or lows differ'
                    np.save(path,a,allow_pickle=False);obj['tape_path']=str(path.relative_to(ROOT))
                except Exception as err:
                    obj.update(coverage='unavailable',tape_path='',description=f'{type(err).__name__}: {err}')
            items.append(obj)
        return items
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(analyze,c.groupby('symbol')):rows.extend(result)
    df=pd.DataFrame(rows).sort_values(['known_ms','symbol'])
    assert df.id.is_unique
    df.to_csv(HERE/'october_population.csv',index=False)
    print(len(c),'touch days',len(df),'H1 alerts',df.groupby(['coverage','description']).size().to_string())
if __name__=='__main__':run()
