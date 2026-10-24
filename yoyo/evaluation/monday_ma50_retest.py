"""Run the owner-confirmed Monday1618/SMA50 close-retest through Nautilus.

Task71 and the owner's2026-10-24 answers define one fixed5m setup. Signal
preparation consumes completed candles only; the existing native adapter owns
all fills. Frozen MA6 source identities provide the same633 assets without a
new download or external-drive crawl. Each successful asset is immutable and
resumable. Raw native commissions remain the authority for all net outcomes.
"""
from __future__ import annotations

import argparse
import fcntl
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_ma6_retest import finite, source_frame
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

EXP = ROOT / 'experiments/active/exp-monday-ma50-retest-20261024-v1'
SOURCE = ROOT / 'experiments/active/exp-monday-ma6-retest-20261024-v1/symbols'
STEP = pd.Timedelta(minutes=5)
OHLC = ['open', 'high', 'low', 'close']
CODE = ['yoyo/evaluation/monday_ma50_retest.py',
        'yoyo/evaluation/monday_ma50_signals.py',
        'yoyo/evaluation/monday_ma6_retest.py',
        'yoyo/evaluation/monday_ma6_signals.py',
        'yoyo/evaluation/monday_parameter_signals.py',
        'yoyo/evaluation/monday_15m_volume_rejection.py',
        'yoyo/evaluation/monday_5m_wick_gold_clock.py',
        'yoyo/evaluation/monday_5m_wick_gold.py',
        'yoyo/layers/l3_backtest/nautilus_independent.py']


def source_jobs():
    """Resolve local frozen identities; workers verify file sizes/mtimes."""
    jobs = []
    for path in sorted(SOURCE.glob('*/identity.json')):
        identity = json.loads(path.read_text())
        if not (path.parent / 'done.json').exists():
            raise RuntimeError(f'Incomplete source identity: {path.parent.name}')
        jobs.append((path.parent.name, identity['inputs']))
    if len(jobs) != 633:
        raise RuntimeError(f'Expected633 source identities; got{len(jobs)}')
    priority = {'BTCUSDT': 0, 'ETHUSDT': 1, 'XAUUSD': 2}
    return sorted(jobs, key=lambda x: (priority.get(x[0], 3), x[0]))


def execute(frame, entries, symbol):
    """Quantize fixed geometry and run one native engine, without a pre-pass."""
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    decimals = max(2, min(9, int(np.ceil(-np.log10(frame[OHLC].to_numpy().min()))) + 7))
    tick = 10. ** (-decimals)
    x = entries.copy()
    for col in ['entry', 'stop', 'target_price']:
        x[col] = x[col].round(decimals)
    x['risk'] = x.side * (x.entry - x.stop)
    valid = (x.risk >= tick * .999) & (x.side * (x.target_price - x.entry) >= tick * .999)
    valid &= x.entry_i.lt(x.end_i) & x.stop.gt(0) & x.target_price.gt(0)
    rejected = x.loc[~valid].copy()
    rejected['status'] = 'invalid_tick_geometry'
    x = x.loc[valid].copy()
    if x.empty:
        return pd.DataFrame(), pd.DataFrame(), dict(positions=0, fills=0), rejected
    prices = frame[OHLC].round(decimals).copy()
    prices.index = prices.index + STEP
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False,
                        round_trip_cost=0, target_from_entry=True,
                        intrabar_path='OHLC',
                        fill_maker_fee=0 if symbol == 'XAUUSD' else .0002,
                        fill_taker_fee=0 if symbol == 'XAUUSD' else .0005)
    rows, fills, audit = run_native(
        prices, np.full(len(prices), np.nan), np.zeros(len(prices)), x, policy,
        tick=tick, symbol='XAU' if symbol == 'XAUUSD' else symbol.removesuffix('USDT'),
        entry_order_mode='market', batch_bars=12000)
    audit['rounding_decimals'] = decimals
    return rows, fills, audit, rejected


def exposure(frame, rows):
    """Post-fill diagnostics on complete interior bars, excluding entry/exit."""
    if rows.empty:
        return rows
    x = rows.copy()
    prices = frame[OHLC].to_numpy(float)
    for j, row in x.iterrows():
        window = prices[int(row.entry_i) + 1:int(row.exit_i)]
        mfe = mae = 0.
        if len(window):
            good = window[:, 1].max() if row.side == 1 else window[:, 2].min()
            bad = window[:, 2].min() if row.side == 1 else window[:, 1].max()
            mfe = max(0., row.side * (good - row.entry) / row.risk)
            mae = max(0., -row.side * (bad - row.entry) / row.risk)
        x.loc[j, 'interior_mfe_r'] = mfe
        x.loc[j, 'interior_mae_r'] = mae
        x.loc[j, 'interior_bars'] = len(window)
        mark = row.entry + row.side * mfe * row.risk
        # A hypothetical close of floating profit is market/taker, like entry.
        cost = 0. if row.symbol == 'XAUUSD' else (.0005 * row.entry + .0005 * mark) / row.risk
        x.loc[j, 'interior_fee_positive_mfe'] = mfe > cost
    x['closed'] = x.reason.ne(0)
    x['exit_interval_end'] = pd.to_datetime(x.framework_exit_time, utc=True)
    x['exit_interval_start'] = x.exit_interval_end - STEP
    x['holding_hours'] = (x.exit_interval_end - pd.to_datetime(x.entry_time, utc=True)).dt.total_seconds() / 3600
    x['planned_rr'] = x.side * (x.target_price - x.entry) / x.risk
    x['return_r'] = x.net_r
    x['return_pct'] = x.net_notional_bp / 100
    x['basis'] = np.where(x.symbol.eq('XAUUSD'), 'gross', 'net')
    x.loc[x.symbol.eq('XAUUSD'), ['net_r', 'net_notional_bp', 'fee_r']] = np.nan
    x['entry_interval_start'] = x.entry_time  # Market fill is at confirmation close.
    x['target_unreachable'] = False
    x['policy'] = 'opposite_monday_full'
    x['parent_id'] = x.trade_id
    return x


def process(job):
    from yoyo.evaluation.monday_ma50_signals import prepare
    symbol, inputs = job
    started = time.monotonic()
    cfg = json.loads((EXP / 'config.json').read_text())
    folder = EXP / 'symbols' / symbol
    folder.mkdir(parents=True, exist_ok=True)
    stamps = [file_stamp(p['path']) for p in inputs]
    for old, current in zip(inputs, stamps):
        if any(old[k] != current[k] for k in ['bytes', 'mtime_ns']):
            raise RuntimeError(f'Frozen source changed: {symbol} {old["path"]}')
    identity = finite(dict(config=cfg, inputs=stamps, code=[file_stamp(ROOT / p) for p in CODE]))
    if (folder / 'done.json').exists():
        if json.loads((folder / 'identity.json').read_text()) != identity:
            raise RuntimeError(f'Cached identity changed; refusing replay: {symbol}')
        return dict(symbol=symbol, cached=True)
    frame, quality = source_frame(symbol, [p['path'] for p in inputs])
    dump(folder / 'data-quality.json', finite(quality))
    parts = {key: [] for key in ['opportunities', 'entries', 'trades', 'fills', 'invalid']}
    audits = []
    chunks = frame.groupby('quality_segment', sort=True) if symbol == 'XAUUSD' else [(0, frame)]
    for segment, chunk in chunks:
        prepared = prepare(chunk.copy(), symbol, cfg, quality.get('blocked_weeks', []))
        opp, entries = prepared['opportunities'], prepared['entries']
        for table in (opp, entries):
            if table.empty:
                continue
            for col in ['trade_id', 'pair_id', 'opportunity_id', 'signal_trade_id']:
                if col in table:
                    table[col] = f'q{segment}-' + table[col].astype(str)
            table['quality_segment'] = int(segment)
        parts['opportunities'].append(opp)
        parts['entries'].append(entries)
        dest = folder / f'segment-{segment}'
        dest.mkdir(exist_ok=True)
        dump(dest / 'signal-diagnostics.json', finite(prepared['diagnostics']))
        if entries.empty:
            continue
        rows, fills, audit, invalid = execute(chunk, entries, symbol)
        rows = exposure(chunk, rows)
        dump(dest / 'native-audit.json', finite(audit))
        audits.append(audit)
        parts['trades'].append(rows)
        parts['fills'].append(fills)
        parts['invalid'].append(invalid)
    for name, tables in parts.items():
        tables = [table for table in tables if not table.empty]
        result = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
        result.to_csv(folder / f'{name}.csv.gz', index=False)
    done = dict(symbol=symbol, source_rows=len(frame), first=str(frame.index[0]), last=str(frame.index[-1]),
                opportunities=sum(len(t) for t in parts['opportunities']),
                entries=sum(len(t) for t in parts['entries']),
                positions=sum(a['positions'] for a in audits), fills=sum(a['fills'] for a in audits),
                invalid=sum(len(t) for t in parts['invalid']), seconds=time.monotonic() - started)
    dump(folder / 'identity.json', identity)
    dump(folder / 'done.json', finite(done))
    return done


def run(symbols=None, workers=1):
    config = 'experiments/active/exp-monday-ma50-retest-20261024-v1/config.json'
    for path in CODE + [config]:
        if subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder/config before historical execution')
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *CODE, config], cwd=ROOT).returncode:
        raise RuntimeError('Builder/config differs from committed source')
    jobs = source_jobs()
    if symbols:
        jobs = [job for job in jobs if job[0] in symbols]
        if set(symbols) != {job[0] for job in jobs}:
            raise ValueError('Unknown requested asset')
    jobs = [job for job in jobs if not (EXP / 'symbols' / job[0] / 'done.json').exists()]
    failures = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process, job): job[0] for job in jobs}
        for n, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = dict(symbol=symbol, error=repr(exc))
                dump(EXP / 'symbols' / symbol / 'failure.json', result)
                failures.append(result)
            print(json.dumps(dict(progress=f'{n}/{len(jobs)}', **result)), flush=True)
    if failures:
        raise RuntimeError(f'{len(failures)} assets failed; successful assets remain frozen')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='*')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--analyze-after', action='store_true')
    args = parser.parse_args()
    lock = (EXP / 'native.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError('This experiment already has an active native/analysis process')
    state = dict(status='native_running', pid=os.getpid(), workers=args.workers,
                 started_at=str(pd.Timestamp.now(tz='UTC')), symbols=args.symbols,
                 analyze_after=args.analyze_after,
                 source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip())
    dump(EXP / 'run-state.json', state)
    try:
        run(args.symbols, args.workers)
        if args.analyze_after:
            state['status'] = 'validating_and_analyzing'
            dump(EXP / 'run-state.json', state)
            from yoyo.evaluation.monday_ma50_analysis import analyze
            analyze()
        state['status'] = 'local_results_complete' if args.analyze_after else 'native_scope_complete'
    except Exception as exc:
        state.update(status='failed', error=repr(exc))
        raise
    finally:
        state['updated_at'] = str(pd.Timestamp.now(tz='UTC'))
        dump(EXP / 'run-state.json', state)
        lock.close()


if __name__ == '__main__':
    main()
