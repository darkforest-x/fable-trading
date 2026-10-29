"""Read-only projections of the registered strategy-booklet capital ledgers.

Rank funded settlements by saved monetary net P&L, excluding cash additions.
No fills, fees, scores or allocations are recalculated. Chart windows read the
original registered Binance files, never the live OKX display feed. Optional
pandas is imported only on a candle request, keeping monitor startup light.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
from pathlib import Path
from threading import BoundedSemaphore
from functools import lru_cache

from fastapi import HTTPException, Query

ROOT = Path(__file__).resolve().parents[2]
LEDGERS = ROOT / 'experiments/active/exp-strategy-booklet-paths-20261027-v1'
IDENTITIES = ROOT / 'experiments/active/exp-monday-limit-reclaim-20261024-v1/symbols'
TRACKS = ('60', '5', 'v3', 'v3add', 'spike', 'v3lock', 'allin3', 'v3add20')
PERIODS = {'1m': 60000, '2m': 120000, '3m': 180000, '5m': 300000, '15m': 900000, '1H': 3600000, '4H': 14400000,
           '12H': 43200000, '1D': 86400000}
SLOTS = BoundedSemaphore(2)


def ledger(track, directory=LEDGERS):
    if track not in TRACKS:
        raise ValueError('unknown_strategy')
    directory = Path(directory)
    paths = [directory / 'manifest.json', directory / f'{track}-ledger.csv.gz']
    return _ledger(track, str(directory), tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths))


@lru_cache(maxsize=8)
def _ledger(track, directory, stamp):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    meta = dict(manifest['accounts'][track])
    with gzip.open(directory / f'{track}-ledger.csv.gz', 'rt') as handle:
        source = list(csv.DictReader(handle))
    rows, ranking = [], {}
    for n, r in enumerate(source):
        if float(r['notional']) <= 0:
            continue
        row = dict(id=n, symbol=r['symbol'], side=int(float(r['side'])),
                   entry_ms=int(float(r['entry_ms'])), exit_ms=int(r['time_ms']),
                   pnl=float(r['pnl']), return_pct=100 * float(r['pnl']) / float(r['notional']),
                   event=r['trade_id'])
        if (not all(math.isfinite(row[k]) for k in ('pnl', 'return_pct'))
                or row['entry_ms'] <= 0 or row['exit_ms'] < row['entry_ms'] or row['side'] not in (-1, 1)):
            raise ValueError('invalid_saved_ledger')
        rows.append(row)
        rank = ranking.setdefault(row['symbol'], dict(symbol=row['symbol'], pnl=0., trades=0))
        rank['pnl'] += row['pnl']
        rank['trades'] += 1
    if len(rows) != meta['funded']:
        raise ValueError('incomplete_saved_ledger')
    ranking = sorted(ranking.values(), key=lambda r: (-r['pnl'], r['symbol']))
    meta = {k: meta[k] for k in ('track', 'start', 'end', 'note', 'sources')}
    meta.update(source='Binance', trades=len(rows), pnl=sum(r['pnl'] for r in rows),
                source_commit=manifest['source_commit'],
                basis='与策略小册相同的历史资金账本；按币种累计净盈亏排名，补款不计收益。百分比为净盈亏除以开仓名义金额；费用沿用原研究已扣口径。',
                limitation='原研究部分评分存在事后信息；保留旧版口径，仅供回看，不代表当前策略或实盘收益。')
    return dict(meta=meta, rows=rows, ranking=ranking)


def page(track, *, sort='time', direction='desc', symbol='', offset=0, limit=50, directory=LEDGERS):
    value = ledger(track, directory)
    rows = [r for r in value['rows'] if not symbol or r['symbol'] == symbol]
    key = 'entry_ms' if sort == 'time' else 'pnl'
    rows = sorted(rows, key=lambda r: (r[key], r['entry_ms'], r['id']), reverse=direction == 'desc')
    top = value['ranking'][0]['symbol'] if value['ranking'] else None
    focus = max((r for r in value['rows'] if r['symbol'] == top),
                key=lambda r: (r['entry_ms'], r['id']), default=None)
    return dict(schema=1, track=track, meta=value['meta'], ranking=value['ranking'], focus=focus,
                rows=rows[offset:offset + limit], total=len(rows), offset=offset,
                has_more=offset + limit < len(rows), sort=sort, direction=direction, symbol=symbol)


def aggregate(frame, step, native):
    """UTC-open buckets require every original bar; never fill missing candles."""
    import pandas as pd
    if step < native or step % native:
        raise ValueError('cannot_split_source_candles')
    if frame.empty:
        return frame
    grouped = frame.groupby(frame.index // step * step)
    bars = grouped.agg(dict(open='first', high='max', low='min', close='last', volume='sum'))
    counts = grouped.size()
    return bars[counts == step // native].set_axis(pd.Index(bars.index[counts == step // native], name='time'))


@lru_cache(maxsize=12)
def _candles(identity_path, identity_stamp, source_stamps, start, end, period):
    import pandas as pd
    import numpy as np
    job = json.loads(Path(identity_path).read_text())['job']
    if job['group'] not in ('crypto_1m', 'crypto_5m'):
        raise ValueError('unsupported_saved_source')
    native = int(job['execution_minutes']) * 60000
    chunks = []
    for path in job['paths']:
        # Explicit identity paths retain migrated symlinks. No directory scan,
        # network fallback, data write or content hashing on an offline disk.
        for part in pd.read_csv(path, usecols=['ts', 'open', 'high', 'low', 'close', 'volume'],
                                chunksize=50000, float_precision='round_trip'):
            selected = part[(part.ts >= start) & (part.ts < end)]
            if len(selected):
                chunks.append(selected)
    if not chunks:
        raise ValueError('empty_saved_window')
    frame = pd.concat(chunks).set_index('ts').sort_index()
    duplicate = frame[frame.index.duplicated(keep=False)]
    if len(duplicate) and duplicate.groupby(level=0).nunique().gt(1).any().any():
        raise ValueError('conflicting_saved_bars')
    frame = frame[~frame.index.duplicated()]
    if (not np.isfinite(frame.to_numpy()).all() or (frame.index % native != 0).any()
            or (frame[['open', 'high', 'low', 'close']] <= 0).any().any()
            or (frame.volume < 0).any() or (frame.high < frame[['open', 'low', 'close']].max(axis=1)).any()
            or (frame.low > frame[['open', 'high', 'close']].min(axis=1)).any()):
        raise ValueError('invalid_saved_candles')
    bars = aggregate(frame, PERIODS[period], native)
    # Historical Monday rays are computed from complete original Monday data,
    # using the same NY/DST boundary as this strategy's saved source identity.
    from .monday_fib import week_window
    weeks = []
    for stamp in pd.to_datetime(frame.index, unit='ms', utc=True).tz_convert('America/New_York').normalize().unique():
        if stamp.dayofweek != 0:
            continue
        window = week_window(int(stamp.timestamp() * 1000))
        monday = frame[(frame.index >= window['start']) & (frame.index < window['end'])]
        if len(monday) != (window['end'] - window['start']) // native:
            continue
        high, low = float(monday.high.max()), float(monday.low.min())
        weeks.append(dict(symbol=job['symbol'], week=stamp.strftime('%Y-%m-%d'), state='ready',
                          start_ms=window['start'], end_ms=window['end'], expires_ms=window['expires'],
                          high=high, low=low, upper=high + .618 * (high - low), lower=low - .618 * (high - low),
                          high_ms=int(monday.high.idxmax()), low_ms=int(monday.low.idxmin())))
    return dict(candles=[dict(time=int(t), **r) for t, r in bars.to_dict('index').items()], weekly_ranges=weeks)


def candles(track, trade, period, *, directory=LEDGERS, identities=IDENTITIES):
    row = next((r for r in ledger(track, directory)['rows'] if r['id'] == trade), None)
    if row is None:
        raise KeyError('trade_not_found')
    import re
    if not re.fullmatch(r'[A-Z0-9]{1,32}USDT', row['symbol']):
        raise ValueError('unsupported_saved_symbol')
    step = PERIODS[period]
    if (row['exit_ms'] - row['entry_ms']) // step > 5000:
        raise ValueError('window_too_large')
    identity = Path(identities) / row['symbol'] / 'identity.json'
    job = json.loads(identity.read_text())['job']
    if job['symbol'] != row['symbol']:
        raise ValueError('source_symbol_mismatch')
    stamps = tuple((Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in job['paths'])
    start = row['entry_ms'] // step * step - 240 * step
    end = row['exit_ms'] // step * step + 81 * step
    from .monday_fib import week_window
    source_start = min(start, week_window(row['entry_ms'])['start'])
    minute_view = step < int(job['execution_minutes']) * 60000
    value = _candles(str(identity), identity.stat().st_mtime_ns, stamps, source_start, end,
                     '5m' if minute_view else period)
    if minute_view:
        from yoyo.data.binance_minute_window import read
        import pandas as pd
        import numpy as np
        # Keep original Monday rays. The finer display comes from real 1m
        # archives of the same contract, not interpolation of saved 5m bars.
        start = max(start, value['candles'][0]['time'])
        minute = pd.DataFrame(read(row['symbol'], start, end)).set_index('time')
        reference = pd.DataFrame(value['candles']).set_index('time')
        rebuilt = aggregate(minute, 300000, 60000)
        overlap = reference.index.intersection(rebuilt.index)
        columns = ['high', 'low', 'close', 'volume']
        if not len(overlap) or not np.allclose(reference.loc[overlap,columns], rebuilt.loc[overlap,columns],rtol=1e-7,atol=1e-10):
            raise ValueError('minute_source_mismatch')
        bars = aggregate(minute, step, 60000)
        display = [dict(time=int(t), **r) for t,r in bars.to_dict('index').items()]
    else:
        display = [b for b in value['candles'] if start <= b['time'] < end]
    if not display:
        raise ValueError('empty_saved_window')
    return dict(schema=1, track=track, trade_id=trade, symbol=row['symbol'], source='Binance',
                bar=period, timezone='America/New_York', candles=display,
                weekly_ranges=value['weekly_ranges'], now_ms=display[-1]['time'] + step,
                last_close_ms=display[-1]['time'] + step, backtestTrade=row,
                note='币安历史行情；仅聚合完整 K 线。标记为原账本记录时间，切换分钟周期不改变原回测成交精度。')


def install(app):
    @app.get('/api/strategy-backtest')
    def history(track: str = Query(..., pattern='^(' + '|'.join(TRACKS) + ')$'),
                sort: str = Query('time', pattern='^(time|pnl)$'),
                direction: str = Query('desc', pattern='^(asc|desc)$'),
                symbol: str = Query('', pattern=r'^([A-Z0-9]{1,32}USDT)?$'),
                offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=100)):
        try:
            return page(track, sort=sort, direction=direction, symbol=symbol, offset=offset, limit=limit)
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, '历史回测账本暂不可读，请重试') from None

    @app.get('/api/strategy-backtest/candles')
    def chart(track: str = Query(..., pattern='^(' + '|'.join(TRACKS) + ')$'),
              trade: int = Query(..., gt=0), period: str = Query('15m', pattern='^(' + '|'.join(PERIODS) + ')$')):
        if not SLOTS.acquire(False):
            raise HTTPException(503, '历史行情正在读取，请稍后重试')
        try:
            return candles(track, trade, period)
        except KeyError:
            raise HTTPException(404, '没有找到这笔回测交易') from None
        except (OSError, ValueError):
            raise HTTPException(503, '这段历史行情暂不可读或不完整；分钟行情首次读取可能较慢，请重试或切换更大周期') from None
        finally:
            SLOTS.release()
