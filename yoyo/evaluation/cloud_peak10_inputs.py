"""Freeze public OKX candles used for a historical SPIKE V1.7 chart census.

Use the previously frozen Top20 15m history to supply complete 1H candles.
Prefer native 1H when present in the local public cache. Request only absent
hourly periods, including the older exchange history and the latest week. Use
native 12Hutc to avoid dropping exchange candles that opened at listing or
contain a halt. Freeze native cache rows and resumed REST pages in the experiment.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import gzip
import json
import sqlite3

import numpy as np
import pandas as pd

from yoyo.evaluation.top20_1618_rank import request

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-cloud-top20-peak10r-20261108-v1'
CONFIG = json.loads((EXP / 'config.json').read_text())
RANKING = EXP.parent / CONFIG['universe']
OLD = EXP.parent / 'exp-top20-12h-1618-reclaims-20261101-v1/input_receipt.json'
CACHE = Path.home() / 'Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3'
HOUR = 3_600_000
CUTOFF = int(pd.Timestamp(CONFIG['cutoff_utc']).timestamp() * 1000)
BEGIN = int(pd.Timestamp('2018-01-01T00:00Z').timestamp() * 1000)
OHLC = ['open', 'high', 'low', 'close']


def dump(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def freeze_rows(path, reader):
    if not path.exists():
        temp = path.with_suffix('.tmp')
        with gzip.open(temp, 'wt') as handle:
            json.dump(reader(), handle, separators=(',', ':'))
        temp.replace(path)
    with gzip.open(path, 'rt') as handle:
        return json.load(handle)


def confirmed(rows, step):
    values = {int(r[0]): [float(p) for p in r[1:5]] for r in rows
              if str(r[8]) == '1' and int(r[0]) + step <= CUTOFF}
    return pd.DataFrame.from_dict(values, orient='index', columns=OHLC).sort_index()


def native(folder, spec, bar, step, existing):
    """Fill only absent stretches. An empty exchange page ends that stretch."""
    symbol = spec['instId']
    key = 'https://www.okx.com|' + symbol + '|' + bar

    def cache_rows():
        with sqlite3.connect(CACHE.as_uri() + '?mode=ro', uri=True, timeout=25) as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT raw FROM candles WHERE key=? AND t<? ORDER BY t', (key, CUTOFF))]
    cached = confirmed(freeze_rows(folder / (bar + '-cache.json.gz'), cache_rows), step)
    base = cached.combine_first(existing) if not cached.empty else existing
    grid = np.arange(BEGIN, CUTOFF, step, dtype=np.int64)
    missing = grid[~np.isin(grid, base.index)]
    blocks = np.split(missing, np.flatnonzero(np.diff(missing) != step) + 1)
    pages, raw = 0, []
    for block in blocks:
        if not len(block):
            continue
        left, cursor = int(block[0]), int(block[-1]) + step
        while cursor > left:
            def get():
                return request(dict(instId=symbol, bar=bar, limit=300, after=str(cursor)))['data']
            page = freeze_rows(folder / f'{bar}-{cursor}.json.gz', get)
            pages += 1
            if not page:
                break
            oldest = min(int(r[0]) for r in page)
            if oldest >= cursor:
                raise ValueError((symbol, bar, cursor, oldest))
            raw.extend(page)
            cursor = oldest
    rest = confirmed(raw, step)
    overlap = existing.index.intersection(rest.index)
    diffs = ((existing.loc[overlap] - rest.loc[overlap]).abs()
             > 1e-10 * np.maximum(1, rest.loc[overlap].abs())).any(axis=1) if len(overlap) else []
    if not rest.empty:
        base = rest.combine_first(base)
    base = base.astype(float).sort_index()
    if (base.index.to_numpy(dtype='int64') % step).any() or base.isna().any().any():
        raise ValueError((symbol, bar, 'grid or missing fields'))
    if ((base.low <= 0) | (base.low > base.min(axis=1)) | (base.high < base.max(axis=1))).any():
        raise ValueError((symbol, bar, 'invalid prices'))
    return base, dict(cached=len(cached), rest_pages=pages, rest_rows=len(rest),
                      rest_frozen_overlap=len(overlap), rest_frozen_differences=int(sum(diffs)))


def aggregate(frame, hours, count):
    groups = frame.groupby((frame.index // hours) * hours)
    ohlc = groups.agg(dict(open='first', high='max', low='min', close='last'))
    return ohlc[groups.size() == count]


def one(spec, source):
    folder = EXP / 'inputs' / spec['symbol']
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / 'receipt.json'
    if receipt.exists():
        return json.loads(receipt.read_text())
    path = ROOT / Path(source['source']).relative_to(ROOT)
    if path.stat().st_size != source['bytes']:
        raise ValueError(f'Frozen source size changed: {path}')
    raw = pd.read_csv(path, index_col=0, parse_dates=True, float_precision='round_trip')
    raw.index = raw.index.as_unit('ms').asi8
    assert raw.index.is_unique and raw.index.is_monotonic_increasing
    frozen_hours = aggregate(raw, HOUR, 4)
    hour, h_info = native(folder, spec, '1H', HOUR, frozen_hours)
    empty = pd.DataFrame(columns=OHLC, dtype=float)
    bars, b_info = native(folder, spec, '12Hutc', 12 * HOUR, empty)
    derived = aggregate(hour, 12 * HOUR, 12)
    overlap = bars.index.intersection(derived.index)
    delta = (bars.loc[overlap] - derived.loc[overlap]).abs()
    mismatch = delta.gt(1e-10 * np.maximum(1, bars.loc[overlap].abs())).any(axis=1)
    # Archived old-listing candles can survive after the exchange API boundary
    # moves. Preserve them explicitly and let the EMA engine reset at true gaps.
    native_index = bars.index
    bars = bars.combine_first(derived).sort_index()
    bars['source'] = np.where(bars.index.isin(native_index), 'native12Hutc', 'complete_1H_archive')
    for data, key in [(bars, 'bars12h.csv.gz'), (hour, 'bars1h.csv.gz')]:
        data.to_csv(folder / key, index_label='time')
    meta = dict(symbol=spec['symbol'], rank=spec['rank'], frozen_15m=str(path.relative_to(ROOT)),
                first_ms=int(bars.index.min()), through_ms=int(bars.index.max())+12*HOUR,
                hour=h_info, native12h=b_info, hours=len(hour), bars=len(bars),
                archive_12h=int((bars.source != 'native12Hutc').sum()),
                native_vs_aggregated_overlap=len(overlap), native_vs_aggregated_differences=int(mismatch.sum()))
    dump(receipt, meta)
    print(spec['symbol'], len(bars), '12H bars;', len(hour), 'hourly', flush=True)
    return meta


def main():
    specs = json.loads(RANKING.read_text())['top20']
    sources = {s['symbol']: s for s in json.loads(OLD.read_text())['sources']}
    with ThreadPoolExecutor(max_workers=7) as pool:
        receipts = list(pool.map(lambda s: one(s, sources[s['symbol']]), specs))
    dump(EXP / 'input_coverage.json', receipts)


if __name__ == '__main__':
    main()
