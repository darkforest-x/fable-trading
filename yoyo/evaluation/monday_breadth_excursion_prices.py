"""Binance USD-M five-minute open, high and close for the six-day breadth study."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_bar_rebounds as source


COLS = ['open','high','close']
NOVEMBER = pd.Timestamp('2026-11-01',tz='UTC')
END = pd.Timestamp('2026-11-04',tz='UTC')


def read_quotes(path):
    data = pd.read_csv(path,usecols=['ts',*COLS])
    return data.set_index(pd.to_datetime(data.ts,unit='ms',utc=True))[COLS]


def november(asset, october):
    symbol = asset+'USDT'
    key = source.INPUT/(symbol+'-november-through-04')
    cache = key.with_suffix('.csv.gz')
    receipt = key.with_suffix('.json')
    times = pd.date_range(NOVEMBER-source.STEP,END,freq=source.STEP)
    if not(cache.is_file() and receipt.is_file()):
        raw,proof = source.api.Client('binance',source.INPUT).get(dict(
            symbol=symbol,interval='5m',startTime=times[0].value//10**6,
            endTime=times[-1].value//10**6,limit=len(times)))
        quotes = source.api.canonical([[int(r[0]),*[float(v) for v in r[1:6]]] for r in raw])
        source.api.validate(quotes,5,(END+source.STEP).value//10**6)
        observed = pd.DatetimeIndex(quotes.open_time)
        if not observed.as_unit('ns').equals(times.as_unit('ns')) or not np.allclose(quotes[COLS].iloc[0],october.iloc[-1],rtol=1e-10):
            raise ValueError('November REST history failed coverage or overlap checks: '+asset)
        quotes.to_csv(cache,index=False)
        receipt.write_text(json.dumps(proof,indent=2)+'\n')
    result = read_quotes(cache)
    if not result.index.as_unit('ns').equals(times.as_unit('ns')) or not np.allclose(result.iloc[0],october.iloc[-1],rtol=1e-10):
        raise ValueError('Invalid November quote cache: '+asset)
    return result.iloc[1:]


def prices(asset):
    symbol = asset+'USDT'
    root = source.base.MARKET
    paths = list((root/'kline_preholdout_binance_um5m/series').glob(f'binance_um_{symbol}_5m_*.csv'))
    if len(paths)!=1:
        raise ValueError('Expected one historical file: '+symbol)
    prefix = read_quotes(paths[0])
    if asset=='SOL':
        for name in ('SOLUSDT-20220225-20220301.csv.gz','SOLUSDT-20220331-20220403.csv.gz'):
            fill = read_quotes(source.INPUT/name)
            overlap = prefix.index.intersection(fill.index)
            if len(overlap)!=2 or not np.allclose(prefix.loc[overlap],fill.loc[overlap],rtol=1e-10):
                raise ValueError('Historical SOL overlap mismatch')
            prefix = pd.concat([prefix,fill.loc[fill.index.difference(overlap)]]).sort_index()
    else:
        forward = read_quotes(root/'kline_binance_um5m_forward_20260922/series'/(symbol+'.csv'))
        prefix = pd.concat([prefix,forward])
    minute = read_quotes(source.base.ROOT/'data/research/ma6_knot_1m_20261011/series'
                         /f'binance_um_{symbol}_1m_2059200.csv')
    october_start = pd.Timestamp('2026-10-01',tz='UTC')
    grouped = minute.loc[prefix.index[-1]:october_start-pd.Timedelta(minutes=1)].resample(source.STEP)
    bridge = grouped.agg({'open':'first','high':'max','close':'last'}).where(grouped.open.count().eq(5),axis=0)
    if not np.allclose(bridge.iloc[0],prefix.iloc[-1],rtol=1e-8):
        raise ValueError('Historical and minute overlap mismatch for '+asset)
    october = read_quotes(source.INPUT/(symbol+'.csv.gz'))
    frame = pd.concat([prefix,bridge.iloc[1:],october,november(asset,october)])
    expected = pd.date_range(frame.index[0],END,freq=source.STEP)
    if not frame.index.as_unit('ns').equals(expected.as_unit('ns')):
        raise ValueError('Incomplete five-minute clock: '+asset)
    if not np.isfinite(frame).all().all() or (frame<=0).any().any() or (frame.high<frame[['open','close']].max(axis=1)).any():
        raise ValueError('Incomplete or invalid quote geometry: '+asset)
    prior = source.prices(asset)
    if not np.allclose(frame.loc[prior.index,['open','close']],prior[['open','close']],rtol=1e-10):
        raise ValueError('Previous study price history differs: '+asset)
    return frame


def save_receipts(out):
    receipts = {a:json.loads((source.INPUT/(a+'USDT-november-through-04.json')).read_text())
                for a in source.ASSETS}
    (out/'november_sources.json').write_text(json.dumps(receipts,indent=2)+'\n')
