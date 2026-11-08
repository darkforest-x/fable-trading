"""Binance futures candles for a saved breadth entry or one of its recorded basket members."""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, Query
from starlette.responses import JSONResponse

from yoyo.data.breadth_chart_sources import OHLC, read_source_windows, source_entries
from .strategy_backtest import PERIODS, ROOT, SLOTS, aggregate, weekly_ranges

LEDGER = ROOT / 'yoyo/monitor/static/strategy-booklet/breadth-ema20-data.json'
BTC_RECEIPT = ROOT / 'experiments/active/exp-monday-breadth-extension-20261108-v1/btc/price_receipt.json'
ALT_RECEIPTS = ROOT / 'data/research/breadth_constituent_sixday_20261108/checkpoints-v1'
FIVE = 300_000


def ms(iso):
    return int(datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp() * 1000)


@lru_cache(maxsize=1)
def _windows(ledger, stamp):
    data = json.loads(ledger.read_text())
    if data['strategy_id'] != 'breadth-ema20':
        raise ValueError('invalid_ledger')
    windows = {}
    for event in data['events']:
        key = event['event_id']
        if event['asset'] == 'BTC':
            windows[key, 'BTCUSDT'] = ms(event['entry_time']), ms(event['exit_time'])
        else:
            for member in event['members']:
                windows[key, member['symbol']] = ms(member['entry_time']), ms(member['selected_exit_time'])
    return windows


def source_frame(receipt, start, end):
    import numpy as np
    import pandas as pd
    frames = []
    for item in source_entries(receipt):
        meta = item['meta']
        if meta.get('has_low') is False:
            continue
        if meta.get('first_utc') and ms(meta['first_utc']) >= end:
            continue
        if meta.get('last_utc') and ms(meta['last_utc']) + FIVE <= start:
            continue
        for path in item['candidates']:
            try:
                frame = read_source_windows(path, [(start, end)], columns=(*OHLC, 'volume'))
            except (OSError, ValueError, EOFError):
                continue
            if set(OHLC).issubset(frame.columns) and not frame.empty:
                frames.append(frame)
            break
    if not frames:
        raise ValueError('no_registered_candles')
    frame = pd.concat(frames, ignore_index=True)
    if 'volume' not in frame:
        frame['volume'] = np.nan
    frame = frame.set_index('ts').sort_index()
    valid = ((frame.index % FIVE == 0) & np.isfinite(frame[list(OHLC)]).all(axis=1)
             & (frame.low > 0) & (frame.low <= frame[['open','close','high']].min(axis=1))
             & (frame.high >= frame[['open','close','low']].max(axis=1)))
    frame = frame.loc[valid]
    frame.loc[~np.isfinite(frame.volume) | (frame.volume < 0), 'volume'] = np.nan
    # OHLC-only repairs can overlap the same exchange candle with known volume.
    grouped = frame.groupby(level=0)
    conflicts = grouped[list(OHLC)].nunique().gt(1).any(axis=1)
    return grouped.first().loc[~conflicts]


@lru_cache(maxsize=18)
def _chart_window(symbol, start, end, step, receipt_path, stamp):
    import numpy as np
    import pandas as pd
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('symbol') != symbol or receipt.get('venue') != 'Binance USD-M' or receipt.get('interval') != '5m':
        raise ValueError('source_mismatch')
    from .monday_fib import week_window
    native = FIVE
    source_start = min(start // FIVE * FIVE, week_window(start)['start'])
    frame = source_frame(receipt, source_start, (end + FIVE - 1) // FIVE * FIVE)
    weeks = weekly_ranges(frame, symbol, FIVE)
    if step < FIVE:
        from yoyo.data.binance_minute_window import read
        minute = pd.DataFrame(read(symbol, start // FIVE * FIVE, (end + FIVE - 1) // FIVE * FIVE))
        if minute.empty:
            raise ValueError('no_minute_candles')
        minute = minute.set_index('time').sort_index()
        rebuilt = aggregate(minute, FIVE, 60_000)
        overlap = rebuilt.index.intersection(frame.index)
        if overlap.empty or not np.allclose(frame.loc[overlap,list(OHLC)], rebuilt.loc[overlap,list(OHLC)], rtol=1e-7, atol=1e-10):
            raise ValueError('minute_source_mismatch')
        frame, native = minute, 60_000
    frame = frame.loc[(frame.index >= start) & (frame.index < end)]
    bars = aggregate(frame, step, native)
    if bars.empty:
        raise ValueError('empty_window')
    known_volume = frame.volume.notna().groupby(frame.index // step * step).all()
    records = []
    for bar in bars.itertuples():
        records.append(dict(time=int(bar.Index), open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                            volume=bar.volume if known_volume.at[bar.Index] else None))
    return records, weeks


def candles(event_id, symbol, period):
    entry, exit_time = _windows(LEDGER, LEDGER.stat().st_mtime_ns)[event_id, symbol]
    step = PERIODS[period]
    receipt = BTC_RECEIPT if symbol == 'BTCUSDT' else ALT_RECEIPTS / (symbol + '.json')
    start = entry // step * step - 240 * step
    end = exit_time // step * step + 81 * step
    bars, weeks = _chart_window(symbol, start, end, step, receipt, receipt.stat().st_mtime_ns)
    if not any(entry // step * step <= bar['time'] <= exit_time // step * step for bar in bars):
        raise ValueError('missing_trade_window')
    close = bars[-1]['time'] + step
    return dict(schema=1, source='Binance', track='breadth-ema20', event_id=event_id, symbol=symbol,
                bar=period, entry_ms=entry, exit_ms=exit_time, candles=bars, weekly_ranges=weeks, levels=[],
                last_close_ms=close, now_ms=close)


def install(app):
    @app.get('/api/breadth-replay/candles')
    def saved_chart(event_id: str = Query(..., max_length=64), symbol: str = Query(..., max_length=64),
                    period: str = Query('15m', pattern='^(' + '|'.join(PERIODS) + ')$')):
        if not SLOTS.acquire(False):
            raise HTTPException(503, '历史行情正在读取，请稍后重试')
        try:
            return JSONResponse(candles(event_id, symbol, period))
        except KeyError:
            raise HTTPException(404, '该交易没有这个成分币') from None
        except (ValueError, OSError, EOFError):
            raise HTTPException(503, '该周期的原始 Binance 行情暂不可读，请重试或切换周期') from None
        finally:
            SLOTS.release()
