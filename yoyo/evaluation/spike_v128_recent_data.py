"""Immutable recent research input for the V12.8 two-month audit.

Reuse the frozen 638-symbol Binance USD-M universe and verified local five-minute
archives, then fetch missing official monthly archives and closed REST candles.
Inputs use only OHLCV opened before the fixed cutoff; this module never writes
the VPS-owned live cache or resumes the previously stopped forward experiment.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation import spike_v10_4_study as source
from yoyo.evaluation import spike_v104_forward as prior
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v128-recent-20260923-v1')
CONFIG = EXP / 'config.json'
OLD_RECENT = Path('data/kline_binance_um5m_forward_20260922')
COLS = ['ts', 'open', 'high', 'low', 'close', 'volume']


def merge_rows(frames, start, end):
    """Merge exact source rows and reject conflicting duplicate timestamps."""
    if not frames:
        return pd.DataFrame(columns=COLS)
    frame = pd.concat(frames, ignore_index=True)[COLS]
    frame = frame.loc[(frame.ts >= start.value // 10**6)
                      & (frame.ts + 300_000 <= end.value // 10**6)]
    duplicate = frame.loc[frame.ts.duplicated(keep=False)]
    if len(duplicate.drop_duplicates()) != duplicate.ts.nunique():
        raise ValueError('conflicting duplicate OHLCV rows')
    frame = frame.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    if len(frame) and (frame.ts.astype('int64') % 300_000).any():
        raise ValueError('unaligned five-minute clock')
    return frame


def fetch_one(symbol, old_path, cfg):
    root = Path(cfg['data_dir'])
    audit_path = root / 'audits' / f'{symbol}.json'
    config_hash = prior.digest(CONFIG)
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        if audit['config_sha256'] != config_hash:
            raise ValueError('data resume config mismatch')
        if prior.digest(Path(audit['path'])) != audit['sha256']:
            raise ValueError('data resume content mismatch')
        return audit
    start, end = pd.Timestamp(cfg['warmup_start']), pd.Timestamp(cfg['end'])
    old = Path(old_path)
    frames = [pd.read_csv(old, usecols=COLS)]
    inputs = [{'path': str(old), 'sha256': prior.digest(old)}]
    cache_audit = OLD_RECENT / 'audits' / f'{symbol}.json'
    cached = None
    months = []
    if cache_audit.exists():
        receipt = json.loads(cache_audit.read_text())
        if receipt.get('status') == 'complete':
            cached_path = Path(receipt['path'])
            if prior.digest(cached_path) != receipt['sha256']:
                raise ValueError('prior research cache hash mismatch')
            cached = pd.read_csv(cached_path, usecols=COLS)
            frames.append(cached)
            inputs.append({'path': str(cached_path), 'sha256': receipt['sha256']})
            months = receipt['months']
    if cached is None:
        for month in cfg['archive_months']:
            frame, audit = prior.archives._download_month(
                symbol=symbol, month=month, download_dir=root / 'downloads', interval='5m')
            months.append(audit)
            if frame is not None:
                frames.append(frame[COLS])
    rest_start = pd.Timestamp(cfg['rest_start'])
    if cached is not None and len(cached):
        rest_start = max(rest_start, pd.Timestamp(int(cached.ts.max()), unit='ms', tz='UTC') + pd.Timedelta(minutes=5))
    rest = prior.fetch_rest(symbol, rest_start, end)
    if rest is not None and len(rest):
        frames.append(rest[COLS])
    combined = merge_rows(frames, start, end)
    path = root / 'series' / f'{symbol}.csv.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
    recent_start = pd.Timestamp(cfg['start']).value // 10**6
    recent = combined.loc[combined.ts >= recent_start]
    audit = {'symbol': symbol, 'config_sha256': config_hash, 'path': str(path),
             'sha256': prior.digest(path), 'inputs': inputs, 'months': months,
             'rows': len(combined), 'recent_rows': len(recent),
             'first_ms': None if combined.empty else int(combined.ts.min()),
             'last_ms': None if combined.empty else int(combined.ts.max()),
             'rest_start': rest_start.isoformat(), 'rest_rows': 0 if rest is None else len(rest),
             'rest_status': 'unknown_or_delisted' if rest is None else 'ok',
             'status': 'complete', 'fetched_at': pd.Timestamp.now(tz='UTC').isoformat()}
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    prior.dump(audit_path, audit)
    return audit


def run(workers=8, symbols=None):
    if not _committed((Path(__file__), CONFIG, EXP / 'PROJECT_PLAN.md',
                       Path('tests/evaluation/test_spike_v128_recent_data.py'))):
        raise ValueError('commit fetcher, tests, config and plan before fetching')
    cfg = json.loads(CONFIG.read_text())
    files = source.series_files()
    selected = sorted(files if symbols is None else symbols)
    done, failed = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, s, files[s], cfg): s for s in selected}
        for count, future in enumerate(as_completed(futures), 1):
            try:
                done.append(future.result())
            except Exception as exc:
                failed.append({'symbol': futures[future], 'error': repr(exc)})
            if count % 20 == 0 or count == len(selected):
                print(json.dumps({'done': count, 'total': len(selected), 'failed': len(failed)}), flush=True)
    manifest = {'config_sha256': prior.digest(CONFIG), 'requested': selected, 'failed': failed,
                'streams': sorted(done, key=lambda x: x['symbol']),
                'generated_at': pd.Timestamp.now(tz='UTC').isoformat()}
    root = Path(cfg['data_dir']); root.mkdir(parents=True, exist_ok=True)
    prior.dump(root / ('manifest.json' if symbols is None else 'smoke_manifest.json'), manifest)
    if failed:
        raise RuntimeError(f'{len(failed)} input streams failed; recorded, no silent substitution')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--symbols', nargs='+')
    args = parser.parse_args()
    run(args.workers, args.symbols)
