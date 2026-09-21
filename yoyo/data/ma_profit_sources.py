"""Prepare immutable research sources for the owner's profit-filter expansion.

This utility never writes production K-line caches. Existing local archives
remain read-only. Optional new 3m data comes from checksum-verified official
Binance monthly archives through August 2026, not from synthetic prices.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil

from yoyo.data.binance_um_archives import fetch_universe
from yoyo.datasets.ma_profit_pipeline import ROOT, committed, digest, dump


def freeze_local(plan_path):
    """Inventory canonical local 15m and 5m CSVs without changing any source."""
    committed([Path(__file__), plan_path])
    folders = ['data/kline_preholdout_binance_um5m/series', 'data/kline_fetched',
               'data/kline_deep', 'data/kline_preholdout_archive15m',
               'data/kline_preholdout_archive15m_202109_202306']
    rows = []
    for folder in folders:
        for path in sorted((ROOT / folder).glob('*.csv')):
            match = re.match(r'(binance_um|okx)_(.+)_(5m|15m)_.*\.csv$', path.name)
            if not match:
                continue
            venue, symbol, interval = match.groups()
            rows.append({'source_path': str(path.relative_to(ROOT)), 'symbol': symbol,
                         'venue': venue, 'bar_minutes': int(interval[:-1]),
                         'sha256': digest(path), 'size_bytes': path.stat().st_size})
    for minutes in (15, 5):
        selected = [r for r in rows if r['bar_minutes'] == minutes]
        dump(plan_path.parent / f'sources_local_{minutes}m.json', {'sources': selected})
        print(json.dumps({'minutes': minutes, 'sources': len(selected)}), flush=True)


def fetch_three_minute(plan_path, workers=8):
    """Fetch a new bounded archive corpus without altering old archive contracts."""
    commit = committed([Path(__file__), ROOT / 'yoyo/data/binance_um_archives.py', plan_path])
    plan = json.loads(plan_path.read_text())
    if not plan['owner_authorization']['all_historical_data_authorized']:
        raise ValueError('historical-data authorization absent')
    if shutil.disk_usage(ROOT).free < 40 * 1024 ** 3:
        raise RuntimeError('less than40GiB free before3m archive expansion')
    output = plan_path.parent / 'inputs/binance_3m_full'
    summary = fetch_universe(output_dir=output, archive_start='2019-09-01T00:00:00Z',
        archive_end_inclusive='2026-08-31T23:59:59Z', archive_max_exclusive='2026-09-01T00:00:00Z',
        # Legacy argument name: this is now only the explicit requested ceiling.
        # The owner removed historical holdout restrictions on September19.
        holdout_start='2026-09-01T00:00:00Z', workers=workers, interval='3m')
    sources = []
    for row in summary['results']:
        if row['status'] != 'complete':
            continue
        path = Path(row['output_path']).resolve()
        sources.append({'source_path': str(path.relative_to(ROOT)), 'symbol': row['symbol'],
            'venue': 'binance_um', 'bar_minutes': 3, 'sha256': row['output_sha256'],
            'first_time': row['first_time'], 'last_time': row['last_time'], 'rows': row['rows']})
    dump(plan_path.parent / 'sources_archive_3m.json', {'sources': sources})
    dump(plan_path.parent / 'archive3m_receipt.json', {'builder_commit': commit,
        'provider_documentation': 'https://github.com/binance/binance-public-data',
        'archive_summary_sha256': digest(output / 'archive_fetch_summary.json'),
        'sources_manifest_sha256': digest(plan_path.parent / 'sources_archive_3m.json'),
        'symbols_complete': summary['symbols_complete'], 'rows': summary['rows'],
        'legacy_holdout_fields_mean_explicit_archive_ceiling_only': True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('freeze-local', 'fetch-3m'))
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if args.command == 'freeze-local':
        freeze_local(args.plan)
    else:
        fetch_three_minute(args.plan, args.workers)


if __name__ == '__main__':
    main()
