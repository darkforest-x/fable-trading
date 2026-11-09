"""Audit historical OKX minute turnover for actual deferred-add fills, ex post only.

For each real VectorBT addition, compare its executed USDT notional with the full
quote turnover in the matching entry minute. The minute is read only after the
frozen fill timestamp and is never used as a signal, gate, or sizing input.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_cash_giveback as cash
from yoyo.evaluation import cloud_close_entry_turnover as turnover
from yoyo.evaluation import cloud_close_double_minute_risk as minute

EXP = cash.ROOT / 'exp-cloud-close-deferred-fill-capacity-audit-20261109-v1'
CACHE = cash.queue.repair.wallet.ROOT / 'data/research/cloud_close_deferred_fill_capacity_audit'
HOUR = 3_600_000

INPUTS = {
    'deferred_median_profit': ('exp-cloud-close-deferred-fill-20261109-v1', 'additions_median_profit.csv.gz'),
    'deferred_median_nonpositive': ('exp-cloud-close-deferred-fill-20261109-v1', 'additions_median_nonpositive.csv.gz'),
    'deferred_median_stress_profit': ('exp-cloud-close-deferred-fill-20261109-v1', 'additions_median_stress_profit.csv.gz'),
    'deferred_median_stress_nonpositive': ('exp-cloud-close-deferred-fill-20261109-v1', 'additions_median_stress_nonpositive.csv.gz'),
    'complete_median_all': ('exp-cloud-close-complete-order-20261109-v1', 'additions_median_all.csv.gz'),
    'complete_median_stress_all': ('exp-cloud-close-complete-order-20261109-v1', 'additions_median_stress_all.csv.gz'),
    'deferred_median_original_price': ('exp-cloud-close-deferred-price-limit-20261109-v1', 'additions_median_original_price.csv.gz'),
    'deferred_median_stress_original_price': ('exp-cloud-close-deferred-price-limit-20261109-v1', 'additions_median_stress_original_price.csv.gz'),
}
REQUIRED = {'underlying_event_id', 'hour', 'actual_notional'}
PARENT_PATHS = cash.ROSTER / 'selected_paths.csv.gz'


def load_orders(root=None):
    """Load all completed add ledgers and deduplicate only the source windows."""
    root = Path(root or cash.ROOT)
    parents = pd.read_csv(PARENT_PATHS, usecols=['event_id', 'symbol', 'entry_ms'], float_precision='round_trip')
    if (parents.groupby('event_id')[['symbol', 'entry_ms']].nunique().gt(1).any().any()):
        raise ValueError('Frozen child paths disagree on parent symbol or entry time')
    parents = parents.drop_duplicates('event_id')
    parents = parents.set_index('event_id')
    parts = []
    for arm, (folder, filename) in INPUTS.items():
        path = root / folder / filename
        if not path.is_file():
            raise FileNotFoundError(f'Input add ledger is not complete: {path}')
        frame = pd.read_csv(path, float_precision='round_trip')
        if not REQUIRED.issubset(frame.columns):
            raise ValueError(f'Unexpected columns in {path}: {sorted(frame.columns)}')
        frame = frame[['underlying_event_id', 'hour', 'actual_notional']].copy()
        frame['arm'] = arm
        frame['source_file'] = str(path.relative_to(root))
        frame['event_id'] = frame.underlying_event_id
        frame['hour'] = pd.to_numeric(frame.hour, errors='raise').astype('int64')
        frame['actual_notional'] = pd.to_numeric(frame.actual_notional, errors='raise')
        if not frame.event_id.isin(parents.index).all():
            raise ValueError(f'Addition parent missing from frozen paths in {path}')
        frame['symbol'] = frame.event_id.map(parents.symbol)
        frame['parent_entry_ms'] = frame.event_id.map(parents.entry_ms).astype('int64')
        if not frame.hour.equals(frame.parent_entry_ms + cash.STEP):
            raise ValueError(f'Add execution hour differs from frozen next 12H open in {path}')
        if (frame.actual_notional.le(0).any() or frame.hour.mod(HOUR).ne(0).any()
                or frame.duplicated(['underlying_event_id', 'hour']).any()):
            raise ValueError(f'Invalid or duplicate add-order row in {path}')
        parts.append(frame)
    orders = pd.concat(parts, ignore_index=True)
    orders['window_id'] = orders.symbol + ':' + orders.hour.astype(str)
    windows = orders[['symbol', 'hour', 'window_id']].drop_duplicates().sort_values(['symbol', 'hour'])
    if windows.window_id.duplicated().any():
        raise ValueError('Window key collision')
    deferred_arms = {'deferred_median_profit', 'deferred_median_nonpositive',
                     'deferred_median_stress_profit', 'deferred_median_stress_nonpositive'}
    complete_arms = {'complete_median_all', 'complete_median_stress_all'}
    fixed_arms = {'deferred_median_original_price', 'deferred_median_stress_original_price'}
    keys = {arm: set(group.window_id) for arm, group in orders.groupby('arm')}
    deferred = set().union(*(keys[arm] for arm in deferred_arms))
    complete = set().union(*(keys[arm] for arm in complete_arms))
    fixed = set().union(*(keys[arm] for arm in fixed_arms))
    if deferred != complete or not fixed.issubset(deferred):
        raise ValueError('All-parent or original-price additions changed the frozen source-window set')
    return orders, windows.reset_index(drop=True)


def _window_cache_path(symbol, hour):
    return CACHE / f'{symbol}-{int(hour)}.json'


def _fetch_window(row, quotes, *, retries=3):
    symbol, hour = str(row.symbol), int(row.hour)
    cached = _window_cache_path(symbol, hour)
    if cached.is_file():
        return json.loads(cached.read_text())
    record = {'window_id': f'{symbol}:{hour}', 'symbol': symbol, 'hour': hour,
              'hour_utc': pd.Timestamp(hour, unit='ms', tz='UTC').isoformat()}
    for attempt in range(retries):
        try:
            bars, price_source = minute.minute_quote(symbol, hour, quotes)
            record['price_source'] = price_source
            if bars is None:
                record['status'] = price_source
                break
            if len(bars) != 60 or not np.array_equal(bars.index.to_numpy(dtype='int64'), hour + np.arange(60, dtype='int64') * 60_000):
                record['status'] = 'incomplete_or_misaligned_minute_bars'
                break
            amounts, volume_source = turnover.volume_hour(symbol, hour, bars, price_source)
            record['volume_source'] = volume_source
            if amounts is None:
                record['status'] = volume_source
                break
            first = bars.iloc[0]
            first_volume = amounts.iloc[0]
            values = np.asarray([first.open, first.high, first.low, first.close,
                                 first_volume['base'], first_volume['quote']], dtype=float)
            if not np.isfinite(values).all() or min(values[4:]) < 0:
                record['status'] = 'invalid_first_minute_volume'
                break
            record.update(status='observed', minute_open_ms=hour,
                          minute_open=float(first.open), minute_high=float(first.high),
                          minute_low=float(first.low), minute_close=float(first.close),
                          minute_base_volume=float(first_volume['base']),
                          minute_quote_volume_usdt=float(first_volume['quote']),
                          minute_zero_quote_volume=bool(first_volume['quote'] == 0))
            break
        except (requests.RequestException, TimeoutError, BadZipFile, OSError, ValueError) as exc:
            if attempt + 1 == retries:
                record.update(status='source_error', error=f'{type(exc).__name__}: {exc}')
            else:
                time.sleep(2 ** attempt)
    CACHE.mkdir(parents=True, exist_ok=True)
    temp = cached.with_suffix('.tmp')
    temp.write_text(json.dumps(record, allow_nan=False))
    temp.replace(cached)
    return record


def _stats(frame):
    observed = frame[frame.status.eq('observed')].copy()
    nonzero = observed[observed.minute_quote_volume_usdt.gt(0)]
    ratios = nonzero.actual_notional.div(nonzero.minute_quote_volume_usdt)
    return {
        'orders': int(len(frame)),
        'observed_orders': int(len(observed)),
        'source_coverage_pct': float(100 * len(observed) / len(frame)) if len(frame) else None,
        'zero_market_turnover_orders': int(observed.minute_zero_quote_volume.sum()) if len(observed) else 0,
        'observed_actual_notional_usdt': float(observed.actual_notional.sum()),
        'notional_to_full_minute_quote_turnover': {
            'orders_with_positive_turnover': int(len(ratios)),
            'p50': float(ratios.quantile(.50)) if len(ratios) else None,
            'p90': float(ratios.quantile(.90)) if len(ratios) else None,
            'p99': float(ratios.quantile(.99)) if len(ratios) else None,
            'max': float(ratios.max()) if len(ratios) else None,
            'orders_over_1pct': int(ratios.gt(.01).sum()),
            'orders_over_10pct': int(ratios.gt(.10).sum()),
            'orders_over_100pct': int(ratios.gt(1.).sum()),
        },
    }


def _source_provenance(receipts):
    """Keep actual source URLs where returned, including API fallback for mismatches."""
    enriched = receipts.copy()
    price_urls, kinds, api_paths, archive_urls = [], [], [], []
    for row in enriched.itertuples(index=False):
        inst = f'{row.symbol}-USDT-SWAP'
        day = pd.Timestamp(int(row.hour), unit='ms', tz='UTC').tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
        archive_url = (f'https://static.okx.com/cdn/okex/traderecords/candlesticks/daily/{day.replace("-", "")}/'
                       f'{inst}-candlesticks-{day}.zip')
        api_path = minute.CACHE / inst / f'api-{int(row.hour)}.json'
        raw_source = getattr(row, 'price_source', '')
        if isinstance(raw_source, str) and raw_source.startswith('https://'):
            price_urls.append(raw_source)
            kinds.append('daily_archive' if 'static.okx.com' in raw_source else 'history_candles_api')
        elif api_path.is_file():
            price_urls.append('https://www.okx.com/api/v5/market/history-candles')
            kinds.append('history_candles_api_cached')
        else:
            price_urls.append('')
            kinds.append('source_url_not_recovered')
        api_paths.append(str(api_path) if api_path.is_file() else '')
        archive_urls.append(archive_url)
    enriched['price_source_url'] = price_urls
    enriched['price_source_kind'] = kinds
    enriched['api_source_cache_path'] = api_paths
    enriched['daily_archive_candidate_url'] = archive_urls
    return enriched


def summarize_existing(*, directory=EXP, orders=None, windows=None):
    """Rebuild the panel and summary from local source receipts, without downloads."""
    orders, windows = (orders, windows) if orders is not None else load_orders()
    receipts_path = directory / 'minute_windows.csv.gz'
    if not receipts_path.is_file():
        raise FileNotFoundError(receipts_path)
    receipts = pd.read_csv(receipts_path, float_precision='round_trip')
    receipts = _source_provenance(receipts)
    if receipts.window_id.duplicated().any() or set(receipts.window_id) != set(windows.window_id):
        raise ValueError('Saved minute receipts do not match current unique symbol/hour input set')
    frame = orders.merge(receipts, on=['window_id', 'symbol', 'hour'], how='left', validate='many_to_one')
    if frame.status.isna().any():
        raise ValueError('Missing minute source status after joining window receipts')
    frame['actual_notional_to_full_minute_quote_turnover'] = np.where(
        frame.status.eq('observed') & frame.minute_quote_volume_usdt.gt(0),
        frame.actual_notional / frame.minute_quote_volume_usdt, np.nan)
    frame['minute_volume_includes_post_entry_trading'] = True
    frame['price_implied_base_units_at_minute_open'] = np.where(
        frame.status.eq('observed'), frame.actual_notional / frame.minute_open, np.nan)
    frame.to_csv(directory / 'order_minute_turnover.csv.gz', index=False)
    receipts.to_csv(directory / 'minute_windows.csv.gz', index=False)
    inputs = {key: {'path': str((cash.ROOT / folder / filename).relative_to(cash.ROOT)),
                    'rows': int((orders.arm == key).sum())}
              for key, (folder, filename) in INPUTS.items()}
    summary = {
        'input_arms': inputs,
        'input_order_rows': int(len(orders)),
        'unique_symbol_hour_windows': int(len(windows)),
        'unique_symbols': int(windows.symbol.nunique()),
        'minute_window_statuses': receipts.status.value_counts().to_dict(),
        'minute_price_source_kinds': receipts.price_source_kind.value_counts().to_dict(),
        'windows_requiring_minute_source': int(len(windows)),
        'by_arm': {arm: _stats(group) for arm, group in frame.groupby('arm', sort=True)},
        'by_arm_and_year': {},
        'limitations': [
            'This is an ex-post diagnostic of actual simulated added-order USDT notional versus total historical quote turnover in the matching first minute. It does not alter entry selection, sizing, or fills.',
            'The quote volume is the entire one-minute interval; much of it occurs after the modeled entry. It is not contemporaneous depth, executable liquidity, or a guaranteed fill-capacity measure.',
            'The additions ledgers persist aggregate actual_notional rather than native base-unit quantities or individual subslot fills. price_implied_base_units_at_minute_open is only a notional/open-price conversion, not a separately observed execution quantity.',
            'Scenario arms represent different hypothetical accounts; orders across arms are not simultaneous and must not be summed as a combined market order.',
            'Only source windows whose 60 native 1m OHLC bars exactly reproduce the frozen H1 are marked observed. Missing, malformed, or conflicting windows remain unknown.',
        ],
    }
    for (arm, year), group in frame.assign(year=pd.to_datetime(frame.hour, unit='ms', utc=True).dt.year).groupby(['arm', 'year']):
        summary['by_arm_and_year'].setdefault(arm, {})[str(year)] = _stats(group)
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    return summary


def run(*, directory=EXP, workers=4):
    if workers < 1 or workers > 4:
        raise ValueError('Use 1..4 workers for shared archive storage')
    orders, windows = load_orders()
    quotes, _ = cash.queue.repair.checked_quotes()
    CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        receipts = list(pool.map(lambda row: _fetch_window(row, quotes), windows.itertuples(index=False)))
    receipt_frame = pd.DataFrame(receipts)
    if receipt_frame.window_id.duplicated().any() or set(receipt_frame.window_id) != set(windows.window_id):
        raise ValueError('Minute source acquisition lost or duplicated a unique symbol/hour')
    directory.mkdir(parents=True, exist_ok=True)
    receipt_frame = _source_provenance(receipt_frame)
    receipt_frame.to_csv(directory / 'minute_windows.csv.gz', index=False)
    return summarize_existing(directory=directory, orders=orders, windows=windows)


def preflight(directory=EXP):
    orders, windows = load_orders()
    print(json.dumps({'all_inputs_present': True, 'arms': {k: int((orders.arm == k).sum()) for k in INPUTS},
                      'order_rows': int(len(orders)), 'unique_symbol_hour_windows': int(len(windows)),
                      'symbols': int(windows.symbol.nunique()), 'cache_dir': str(CACHE),
                      'fixed_original_price_is_subset': True}, indent=2))
    receipts = []
    for row in windows.itertuples(index=False):
        path = _window_cache_path(row.symbol, row.hour)
        if path.is_file():
            receipts.append(json.loads(path.read_text()))
            if len(receipts) >= 3:
                break
    print('cached_window_smoke_count', len(receipts), 'statuses',
          pd.Series([r.get('status') for r in receipts]).value_counts().to_dict())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--rescore-existing', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if sum([args.preflight, args.run, args.rescore_existing]) != 1:
        parser.error('choose exactly one of --preflight, --run, or --rescore-existing')
    if args.preflight:
        preflight()
    elif args.run:
        print(json.dumps(run(workers=args.workers), indent=2))
    else:
        print(json.dumps(summarize_existing(), indent=2))
