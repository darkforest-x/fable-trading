"""Fetch only the disputed H1 bars of the 12h month-week study.

Minute OHLC must tile the requested hour and reproduce its four H1 prices.
Unreconciled hours retain the conservative hourly result. Reuse the registered
Binance 1m day cache before requesting public perpetual kline data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import io
from pathlib import Path
from urllib.parse import quote
import zipfile

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
DAYS = ROOT / 'data/crypto/market/kline_binance_um1m_touchdays_20261027'
CACHE = ROOT / 'data/research/monthweek-12h-first-touch-minute-disputes-20261115'


def from_day(source, start):
    df = pd.read_csv(source, header=None, usecols=range(5))
    df = df.apply(pd.to_numeric, errors='coerce').dropna()
    return df.loc[df[0].between(start, start+3_600_000, inclusive='left')].to_numpy(float)


def valid(raw, start, reference):
    if raw.shape != (60, 5) or not np.array_equal(raw[:,0], start+np.arange(60)*60_000):
        return False
    o, h, l, c = raw[:,1:].T
    if not np.isfinite(raw).all() or np.any(l<=0) or np.any(h<np.maximum(o,c)) or np.any(l>np.minimum(o,c)):
        return False
    return np.allclose([o[0], h.max(), l.min(), c[-1]], reference, rtol=5e-10, atol=0)


@lru_cache(maxsize=2048)
def _lookup(symbol, start, reference):
    cache = CACHE / symbol / f'{start}.npy'
    if cache.exists():
        raw = np.load(cache, allow_pickle=False)
        if valid(raw, start, reference):
            return raw
    day = datetime.fromtimestamp(start/1000, timezone.utc).strftime('%Y-%m-%d')
    local = DAYS / symbol / (day + '.csv')
    if local.is_file():
        raw = from_day(local, start)
        if valid(raw, start, reference):
            return raw
    name = quote(symbol, safe='')
    archive = f'https://data.binance.vision/data/futures/um/daily/klines/{name}/1m/{name}-1m-{day}.zip'
    endpoints = [('https://www.binance.com/fapi/v1/klines',
                  dict(symbol=symbol, interval='1m', startTime=start, endTime=start+3_599_999, limit=60)),
                 (archive, {})]
    for url, params in endpoints:
        try:
            r = requests.get(url, params=params, timeout=(4, 12))
            r.raise_for_status()
            if params:
                raw = np.asarray([row[:5] for row in r.json()], dtype=float)
            else:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z, z.open(z.namelist()[0]) as f:
                    raw = from_day(f, start)
            if valid(raw, start, reference):
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix('.tmp')
                with tmp.open('wb') as dest:
                    np.save(dest, raw)
                tmp.replace(cache)
                return raw
        except (requests.RequestException, ValueError, zipfile.BadZipFile):
            continue
    return None


def minute_lookup(symbol, hourly_bar):
    return _lookup(symbol, int(hourly_bar[0]), tuple(hourly_bar[1:]))
