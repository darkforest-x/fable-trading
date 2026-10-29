"""Describe the 2026-08-22 FHE flash move using existing Binance market data.

This is an ex-post market observation, not a trading simulation or signal rule.
Read OHLCV for the Shanghai calendar day, compare simultaneous 5m candles,
and measure recovery using subsequent candle closes. No execution, profits,
fees, queue position, or liquidation mechanism is inferred from these bars.
Existing recent/tail archives are merged only on exact OHLCV agreement.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/active/exp-fhe-flash-20260822-20261029-v1'
COLS = ['open', 'high', 'low', 'close', 'volume']
START = pd.Timestamp('2026-08-22', tz='Asia/Shanghai').tz_convert('UTC')
END = START + pd.Timedelta(days=1)
EVENT = pd.Timestamp('2026-08-22 13:10', tz='Asia/Shanghai').tz_convert('UTC')


def read_symbol(job):
    """Read the observed day only; future recovery fields are outcome labels."""
    symbol, paths = job
    parts, receipts = [], []
    for path in paths:
        stat = path.stat()
        frame = pd.read_csv(path, usecols=['ts', *COLS], float_precision='round_trip')
        frame = frame[frame.ts.between(START.value // 10**6, END.value // 10**6 - 1)]
        parts.append(frame)
        receipts.append(dict(path=str(path.relative_to(ROOT)), bytes=stat.st_size,
                             mtime_ns=stat.st_mtime_ns, observed_day_rows=len(frame)))
    frame = pd.concat(parts).sort_values('ts')
    dup = frame[frame.ts.duplicated(keep=False)]
    if len(dup) and dup.groupby('ts')[COLS].nunique().gt(1).any().any():
        raise ValueError(f'Conflicting source overlap: {symbol}')
    frame = frame.drop_duplicates('ts').set_index('ts')
    frame.index = pd.to_datetime(frame.index, unit='ms', utc=True)
    frame.index.name = 'time_utc'
    if frame.empty:
        return symbol, frame, receipts
    if not (frame.high >= frame[COLS[:4]].max(axis=1)).all():
        raise ValueError(f'Invalid high: {symbol}')
    if not (frame.low <= frame[COLS[:4]].min(axis=1)).all():
        raise ValueError(f'Invalid low: {symbol}')
    frame['symbol'] = symbol
    frame['drop_pct'] = 100 * (frame.low / frame.open - 1)
    frame['close_pct'] = 100 * (frame.close / frame.open - 1)
    # Only subsequent closes, never a possibly pre-trough high from this candle.
    subsequent = pd.concat([frame.close.reindex(frame.index + pd.Timedelta(minutes=m)).set_axis(frame.index)
                            for m in (5, 10, 15)], axis=1)
    frame['best_subsequent_close_15m'] = subsequent.max(axis=1)
    frame['rebound_to_subsequent_close_pct'] = 100 * (frame.best_subsequent_close_15m / frame.low - 1)
    frame['time_shanghai'] = frame.index.tz_convert('Asia/Shanghai').astype(str)
    return symbol, frame, receipts


def main():
    directories = [ROOT / 'data/crypto/market' / name / 'series' for name in
                   ('kline_binance_um5m_forward_20260922', 'research-spike_v128_recent_20260923')]
    jobs = {}
    for directory in directories:
        if not directory.is_dir():
            raise FileNotFoundError(f'Registered data unavailable: {directory}')
        for path in sorted(directory.glob('*.csv*')):
            jobs.setdefault(path.name.split('.')[0], []).append(path)
    OUT.mkdir(exist_ok=True)
    frames, sources, absent = [], {}, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for symbol, frame, receipts in pool.map(read_symbol, sorted(jobs.items())):
            sources[symbol] = receipts
            if frame.empty:
                absent.append(symbol)
            else:
                frames.append(frame)
    all_bars = pd.concat(frames).sort_index()
    all_bars.to_csv(OUT / 'day_bars.csv.gz', compression='gzip')
    simultaneous = all_bars.loc[[EVENT]].sort_values('drop_pct')
    simultaneous.to_csv(OUT / 'same_1310.csv')
    noon = all_bars[(all_bars.index >= EVENT - pd.Timedelta(minutes=70)) &
                   (all_bars.index < EVENT + pd.Timedelta(minutes=50))]
    noon.sort_values('drop_pct').groupby('symbol', sort=False).head(1).to_csv(OUT / 'noon_worst.csv')
    all_bars.sort_values('drop_pct').groupby('symbol', sort=False).head(1).to_csv(OUT / 'day_worst.csv')
    minute_path = ROOT / 'data/crypto/market/kline_binance_um1m_touchdays_20261027/FHEUSDT/2026-08-22.csv'
    minute = pd.read_csv(minute_path)
    minute.index = pd.to_datetime(minute.open_time, unit='ms', utc=True)
    minute.loc[EVENT - pd.Timedelta(minutes=15):EVENT + pd.Timedelta(minutes=20)].to_csv(OUT / 'fhe_1m.csv')
    ticks_path = ROOT / 'data/crypto/market/aggtrades_binance_um_touchbars_20261029/FHEUSDT/2026-08-22.csv.gz'
    ticks = pd.read_csv(ticks_path)
    ticks['time_shanghai'] = pd.to_datetime(ticks.ts, unit='ms', utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
    first_touch = ticks[ticks.price <= 0.01694594].iloc[0]
    low = ticks.loc[ticks.price.idxmin()]
    target = ticks[(ticks.ts > low.ts) & (ticks.price >= 0.02354)].iloc[0]
    peak = ticks.loc[ticks.price.idxmax()]
    touch_window = ticks[(ticks.ts >= EVENT.value // 10**6) &
                         (ticks.ts < (EVENT + pd.Timedelta(minutes=5)).value // 10**6)]
    five = simultaneous[simultaneous.symbol == 'FHEUSDT'].iloc[0]
    assert abs(touch_window.qty.sum() - five.volume) < 1e-5
    assert touch_window.price.min() == five.low and touch_window.price.max() == five.high
    before = minute.loc[EVENT - pd.Timedelta(minutes=60):EVENT - pd.Timedelta(minutes=1)]
    crash = minute.loc[EVENT:EVENT + pd.Timedelta(minutes=1)]
    summary = dict(
        study='Ex-post price observation; not a strategy backtest',
        builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        date_shanghai='2026-08-22', event_shanghai='13:10–13:15',
        listed_symbols=len(jobs), observed_symbols=len(frames), absent_symbols=absent,
        event_symbols=len(simultaneous), event_median_close_pct=float(simultaneous.close_pct.median()),
        event_drop_3pct=int((simultaneous.drop_pct <= -3).sum()),
        event_drop_5pct=int((simultaneous.drop_pct <= -5).sum()),
        event_drop_10pct=int((simultaneous.drop_pct <= -10).sum()),
        fhe=dict(first_touch=first_touch.to_dict(), low=low.to_dict(), first_target_after_low=target.to_dict(),
                 peak=peak.to_dict(), seconds_touch_to_target=(target.ts-first_touch.ts)/1000,
                 seconds_low_to_target=(target.ts-low.ts)/1000,
                 crash_two_minute_quote_volume=float(crash.quote_volume.sum()),
                 crash_taker_sell_share=float(1-crash.taker_buy_quote_volume.sum()/crash.quote_volume.sum()),
                 previous_hour_median_minute_quote_volume=float(before.quote_volume.median()),
                 tick_bar_volume_high_low_parity=True),
        limitations='Existing archived universe, not an exhaustive point-in-time exchange listing. Prices are trades, not mark/index prices or order-book depth. Recovery is retrospective, not executable returns. No news/forced-liquidation attribution from price alone.',
        sources=sources, extra_sources=[str(minute_path.relative_to(ROOT)), str(ticks_path.relative_to(ROOT))],
        training_eligible=False, production_eligible=False)
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, default=str) + '\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('sources','absent_symbols')}, indent=2, default=str))
    print(simultaneous[['symbol','open','low','close','drop_pct','close_pct','rebound_to_subsequent_close_pct']].head(15).to_string(index=False))


if __name__ == '__main__':
    main()
