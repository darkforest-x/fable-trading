"""Collect receipt-bound main-board history for the fixed SPIKE V1/V8 study.

Source: BaoStock 0.9.3 official stock-basic, historical-universe, calendar and
daily OHLC APIs. A historical start-date census is unioned with stock-basic
IPO/delisting metadata; current listing status never filters the universe.
Daily unadjusted/HFQ pairs use the existing validated A-share collector.
No signal, outcome, selection score or live-market service is touched here.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import multiprocessing
from pathlib import Path
import time

import pandas as pd

from yoyo.evaluation.ashare_data import (
    BAOSTOCK_VERSION, _fetch_worker, baostock_session, board_for_code,
    cached_query, query_timeout,
)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def freeze(destination, config):
    """Freeze a retrospective census; metadata is for eligibility, not features."""
    destination = Path(destination)
    with baostock_session() as client:
        def query(name, params, filename):
            request = dict(provider='baostock', version=BAOSTOCK_VERSION, method=name, **params)
            with query_timeout(60):
                return cached_query(destination / filename, request,
                                    lambda: getattr(client, name)(**params))
        start = query('query_all_stock', {'day': config['start']}, 'universe_start.csv')
        end = query('query_all_stock', {'day': config['end']}, 'universe_end.csv')
        basic = query('query_stock_basic', {}, 'stock_basic.csv')
        calendars = []
        for year in range(int(config['history_start'][:4]), int(config['end'][:4]) + 1):
            lo = max(config['history_start'], f'{year}-01-01')
            hi = min(config['end'], f'{year}-12-31')
            calendars.append(query('query_trade_dates', {'start_date': lo,
                             'end_date': hi}, f'calendars/{year}.csv'))
        calendar = pd.concat(calendars, ignore_index=True)
        calendar.to_csv(destination / 'calendar.csv', index=False)
    required = {'code', 'ipoDate', 'outDate', 'type', 'code_name'}
    if not required.issubset(basic) or basic.code.duplicated().any():
        raise ValueError('stock-basic census is malformed')
    eligible = basic.loc[basic.code.map(board_for_code).isin(['main_sh', 'main_sz'])
                         & basic.type.eq('1') & basic.ipoDate.le(config['end'])
                         & (basic.outDate.eq('') | basic.outDate.ge(config['start']))].copy()
    snapshots = pd.concat([start, end]).drop_duplicates('code')
    snapshots = snapshots.loc[snapshots.code.map(board_for_code).isin(['main_sh', 'main_sz'])]
    names = dict(zip(eligible.code, eligible.code_name))
    names.update(dict(zip(snapshots.code, snapshots.code_name)))
    codes = sorted(names)
    if not codes or not {'calendar_date', 'is_trading_day'}.issubset(calendar):
        raise ValueError('empty main-board census or malformed exchange calendar')
    if not calendar.loc[calendar.calendar_date.eq(config['end']), 'is_trading_day'].eq('1').any():
        raise ValueError('requested end is not a confirmed exchange session')
    meta = basic.set_index('code').to_dict('index')
    manifest = dict(codes=codes, names=names, count=len(codes),
                    board_counts=pd.Series([board_for_code(c) for c in codes]).value_counts().to_dict(),
                    start_snapshot_count=len(start.loc[start.code.map(board_for_code).isin(['main_sh','main_sz'])]),
                    end_snapshot_count=len(end.loc[end.code.map(board_for_code).isin(['main_sh','main_sz'])]),
                    source='BaoStock 0.9.3', start=config['start'], end=config['end'],
                    rule='historical start/end main-board census union stock-basic IPO/outDate interval; no current status filter',
                    listings={c: meta.get(c, {}) for c in codes},
                    metadata_missing=[c for c in codes if c not in meta])
    path = destination / 'universe.json'
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError('universe changed; use a new data directory')
    save(path, manifest)
    return manifest


def collect(config_path, destination, workers=4):
    """Collect every frozen security with per-file receipts and explicit errors."""
    config = json.loads(Path(config_path).read_text())
    destination = Path(destination)
    universe = freeze(destination, config)
    save(destination / 'collection_progress.json', dict(status='collecting', completed=0,
         expected=len(universe['codes']), errors=0))
    began = time.monotonic()
    records = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = {pool.submit(_fetch_worker, (str(destination), code,
                   config['history_start'], config['end'])): code for code in universe['codes']}
        for future in as_completed(futures):
            code = futures[future]
            try:
                record = future.result()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                record = dict(code=code, error=f'{type(exc).__name__}: {exc}')
            path = destination / 'daily' / (code + '.csv')
            if 'error' not in record:
                record['daily_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                if not record.get('rows'):
                    record['error'] = 'empty_history'
            records.append(record)
            save(destination / 'collection_records.json', records)
            progress = dict(status='collecting', completed=len(records), expected=len(futures),
                            errors=sum('error' in r for r in records),
                            elapsed_seconds=round(time.monotonic()-began, 1))
            save(destination / 'collection_progress.json', progress)
            if len(records) % 10 == 0 or len(records) == len(futures):
                print(json.dumps(progress), flush=True)
    progress['status'] = 'complete' if not progress['errors'] else 'incomplete'
    save(destination / 'collection_progress.json', progress)
    print(json.dumps(progress), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1,2,3,4), default=4)
    args = parser.parse_args()
    collect(args.config, args.destination, args.workers)
