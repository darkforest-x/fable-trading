"""Freeze OKX BTC swap research bars without writing any production cache.

Source: OKX public history-candles API, native 5m rows with confirm=1.
Reuse two existing local snapshots only after comparing duplicate OHLCV;
fetch every missing UTC grid interval. Preserve raw responses and SHA256
receipts. No interpolation, alternate venue, or trading API is permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1'
URL = 'https://www.okx.com/api/v5/market/history-candles'
SEEDS = ['data/kline_preholdout_okx_5m/okx_BTC_USDT_SWAP_5m_341567.csv',
         'data/kline_holdout_btc5m/okx_BTC_USDT_SWAP_5m_52992.csv']
COLS = ['open', 'high', 'low', 'close', 'volume']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def acquire():
    """Read fixed historical rows and fill missing bars using the same venue."""
    start = pd.Timestamp('2023-08-20T21:00:00Z')
    end = pd.Timestamp('2026-09-19T21:00:00Z')
    out = EXP / 'data'
    raw = out / 'raw'
    raw.mkdir(parents=True, exist_ok=True)
    pieces, seeds = [], []
    for rel in SEEDS:
        path = ROOT / rel
        d = pd.read_csv(path)
        d.index = pd.to_datetime(d.ts, unit='ms', utc=True)
        d = d.loc[(d.index >= start) & (d.index < end), COLS]
        pieces.append(d)
        seeds.append(dict(path=rel, sha256=sha(path), rows=len(d)))
    both = pd.concat(pieces).sort_index()
    duplicates = both.loc[both.index.duplicated(keep=False)]
    conflict = duplicates.groupby(level=0)[COLS].nunique().gt(1).any(axis=1)
    if conflict.any():
        raise ValueError(f'conflicting seed rows: {int(conflict.sum())}')
    frame = both.loc[~both.index.duplicated(keep='last')]
    grid = pd.date_range(start, end, freq='5min', inclusive='left')
    missing = grid.difference(frame.index)
    print(f'seed_rows={len(frame)} missing={len(missing)}', flush=True)
    intervals = []
    for stamp in missing:
        if not intervals or stamp != intervals[-1][1]:
            intervals.append([stamp, stamp + pd.Timedelta(minutes=5)])
        else:
            intervals[-1][1] += pd.Timedelta(minutes=5)
    session = requests.Session()
    receipts = []
    for low, high in intervals:
        cursor = int(high.timestamp()*1000)
        bound = int(low.timestamp()*1000)
        while cursor > bound:
            path = raw / f'{cursor}.json'
            params = dict(instId='BTC-USDT-SWAP', bar='5m', after=cursor, limit=300)
            if path.exists():
                payload = json.loads(path.read_text())
            else:
                for attempt in range(5):
                    try:
                        response = session.get(URL, params=params, timeout=30)
                        response.raise_for_status()
                        payload = response.json()
                        if payload.get('code') != '0':
                            raise ValueError(str(payload))
                        break
                    except (requests.RequestException, ValueError):
                        if attempt == 4:
                            raise
                        time.sleep(1 + attempt)
                path.write_text(json.dumps(payload, separators=(',', ':')))
                time.sleep(.15)
            rows = payload.get('data', [])
            if not rows or min(int(r[0]) for r in rows) >= cursor:
                raise ValueError(f'no older source rows at {cursor}')
            selected = [r for r in rows if bound <= int(r[0]) < cursor]
            if any(r[8] != '1' for r in selected):
                raise ValueError('unconfirmed source candle')
            d = pd.DataFrame([[float(v) for v in r[1:6]] for r in selected],
                             columns=COLS,
                             index=pd.to_datetime([int(r[0]) for r in selected], unit='ms', utc=True))
            pieces.append(d)
            receipts.append(dict(path=str(path.relative_to(ROOT)), sha256=sha(path), params=params, rows=len(selected)))
            cursor = min(int(r[0]) for r in rows)
            if len(receipts) % 10 == 0:
                print(f'pages={len(receipts)} oldest={pd.Timestamp(cursor,unit="ms",tz="UTC")}', flush=True)
    frame = pd.concat(pieces).sort_index()
    frame = frame.loc[~frame.index.duplicated(keep='last')]
    frame = frame.loc[(frame.index >= start) & (frame.index < end)]
    if not frame.index.equals(grid):
        raise ValueError(f'grid mismatch: missing={len(grid.difference(frame.index))}')
    a = frame.to_numpy()
    if not np.isfinite(a).all() or (frame[COLS[:4]] <= 0).any().any():
        raise ValueError('nonfinite or nonpositive prices')
    if ((frame.low > frame[['open','close']].min(axis=1)) |
        (frame.high < frame[['open','close']].max(axis=1)) | (frame.volume < 0)).any():
        raise ValueError('invalid OHLCV geometry')
    frame.index.name = 'open_time'
    target = out / 'okx_btc_usdt_swap_5m.csv.gz'
    frame.to_csv(target, compression=dict(method='gzip', mtime=0))
    receipt = dict(start=str(start), end_exclusive=str(end), rows=len(frame), expected=len(grid),
                   missing=0, duplicate_conflicts=0, initial_missing=len(missing), seeds=seeds,
                   pages=receipts, source=URL, symbol='BTC-USDT-SWAP', source_timezone='UTC',
                   output=str(target.relative_to(ROOT)), sha256=sha(target),
                   generated_at=pd.Timestamp.now(tz='UTC').isoformat())
    (out/'source_receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k not in ['pages','seeds']},indent=2), flush=True)


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    acquire()
