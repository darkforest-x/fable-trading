"""Freeze native OKX 5m observations for the owner's cross-coin event study.

Owner explicitly selects 5m-native Monday1.618 levels. Reuse repaired leader
history and the confirmed shared cache. Seed existing resumable fetcher files
only with a contiguous suffix; the unchanged src.data.fetch_okx CLI fills older
missing history. It never writes the live cache. No trading API or hashes.
"""
from pathlib import Path
import argparse
import json
import sqlite3
import subprocess
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-btceth-5m-alt-response-20261101-v1'
RANK=ROOT/'experiments/active/exp-top20-1618-short-marks-20261031-v1/ranking.json'
CACHE=Path.home()/'Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3'
OHLCV=['open','high','low','close','volume'];STEP=pd.Timedelta(minutes=5)

def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n')

def prepare():
    cfg=json.loads((EXP/'config.json').read_text());start=pd.Timestamp(cfg['start']);end=pd.Timestamp(cfg['end_exclusive'])
    specs=json.loads(RANK.read_text())['top20'];folder=EXP/'inputs';folder.mkdir(exist_ok=True)
    receipt=[]
    with sqlite3.connect(CACHE.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        for spec in specs:
            sym=spec['symbol'];part=folder/f'{sym}_USDT_SWAP_5m.part.csv'
            if part.exists() or list(folder.glob(f'okx_{sym}_USDT_SWAP_5m_*.csv')):continue
            raw=[json.loads(r[0]) for r in db.execute('SELECT raw FROM candles WHERE key=? AND t>=? AND t<? ORDER BY t',('https://www.okx.com|'+spec['instId']+'|5m',int(start.timestamp()*1000),int(end.timestamp()*1000)))]
            raw=[r for r in raw if r[8]=='1']
            tail=pd.DataFrame([[int(r[0])]+[float(x) for x in r[1:6]] for r in raw],columns=['ts']+OHLCV).set_index('ts')
            assert len(tail) and not tail.index.duplicated().any()
            sources=[dict(path=str(CACHE),role='read_only_snapshot',rows=len(tail))]
            if sym in ['BTC','ETH']:
                old=ROOT/f'experiments/active/exp-range-v231-fiveyear-20261021-v1/{sym}/candles.csv.gz'
                f=pd.read_csv(old,usecols=['ts']+OHLCV,float_precision='round_trip').set_index('ts')
                f=f[(f.index>=start.timestamp()*1000)&(f.index<end.timestamp()*1000)]
                common=f.index.intersection(tail.index)
                assert len(common)
                np.testing.assert_allclose(f.loc[common,OHLCV],tail.loc[common,OHLCV],rtol=1e-11,atol=1e-10)
                tail=pd.concat([f[f.index<tail.index.min()],tail]).sort_index()
                sources.append(dict(path=str(old),bytes=old.stat().st_size,mtime_ns=old.stat().st_mtime_ns,overlap_rows=len(common)))
            assert (np.diff(tail.index)==300000).all()
            tail=tail.reset_index();tail['open_time']=pd.to_datetime(tail.ts,unit='ms',utc=True).astype(str)
            tail.to_csv(part,index=False)
            receipt.append(dict(symbol=sym,seed_rows=len(tail),start=str(tail.open_time.iloc[0]),end=str(tail.open_time.iloc[-1]),sources=sources))
    write(EXP/'seed_receipt.json',receipt)
    print('Seeded',len(receipt),'symbols',flush=True)

def finalize():
    cfg=json.loads((EXP/'config.json').read_text());start=pd.Timestamp(cfg['start']);end=pd.Timestamp(cfg['end_exclusive'])
    specs=json.loads(RANK.read_text())['top20'];receipts=[];folder=EXP/'frozen';folder.mkdir(exist_ok=True)
    for spec in specs:
        sym=spec['symbol'];files=list((EXP/'inputs').glob(f'okx_{sym}_USDT_SWAP_5m_*.csv'));assert len(files)==1,(sym,files)
        p=files[0];f=pd.read_csv(p,float_precision='round_trip');f.index=pd.DatetimeIndex(pd.to_datetime(f.ts,unit='ms',utc=True)).as_unit('ns');f=f.loc[(f.index>=start)&(f.index<end),OHLCV]
        assert f.index.is_unique and f.index.is_monotonic_increasing and len(f)
        assert (f.index.asi8%STEP.value==0).all() and np.isfinite(f.to_numpy()).all()
        assert f.low.le(f[['open','close']].min(axis=1)).all() and f.high.ge(f[['open','close']].max(axis=1)).all()
        assert f.low.gt(0).all() and f.volume.ge(0).all()
        grid=pd.date_range(start,end,freq=STEP,inclusive='left');missing=grid.difference(f.index)
        internal=pd.date_range(f.index[0],end,freq=STEP,inclusive='left').difference(f.index)
        assert len(internal)==0,(sym,'internal_missing',len(internal))
        f.to_csv(folder/f'{sym}.csv.gz',index_label='open_time')
        receipts.append(dict(symbol=sym,rows=len(f),first=str(f.index[0]),last=str(f.index[-1]),leading_missing=len(missing),internal_missing=0,
                             current_list_time=str(pd.to_datetime(int(spec['list_time']),unit='ms',utc=True)),source=str(p),bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns))
    write(EXP/'source_receipt.json',receipts);print(json.dumps(receipts,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--finalize',action='store_true');a=p.parse_args()
    assert subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(EXP/'config.json')],cwd=ROOT).returncode==0,'Commit builder first'
    finalize() if a.finalize else prepare()
