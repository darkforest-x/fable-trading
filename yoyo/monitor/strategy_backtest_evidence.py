"""Read saved entry gates and native fill geometry for historical chart display.

Sources are the registered strategy-booklet projections and their original
Nautilus CSVs. Match event, side, entry/exit bounds and saved net return before
attaching facts. No score, fill, fee, candle path or account is recalculated.
The archived score is not a causal live score or a win probability.
"""
import csv
from datetime import datetime
from functools import lru_cache
import gzip
import json
import math
from pathlib import Path
import re

VARIANTS = {'60': ('v2_be17',), '5': ('m5_near',), 'v3': ('v3_be17',),
            'v3add': ('v3_be0', 'v3_add5'), 'v3add20': ('v3_be0', 'v3_add5'),
            'v3lock': ('v3_be50',), 'allin3': ('opp1618_P30',), 'spike': ('near_P18',)}
TOP40 = 0.22621664169752762  # Frozen scores.json in be-scalein-20261026-v1.


def millis(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def source_path(track, directory):
    directory = Path(directory)
    if track == 'spike':
        return directory.parent / 'exp-monday-limit-spike-hw-20261026-v1/trades.csv'
    return directory / ('five-minute-fills.csv.gz' if track == '5' else 'native-fills.csv.gz')


def stamp(path):
    try:
        s = path.stat()
        return (str(path), s.st_size, s.st_mtime_ns)
    except OSError:
        return (str(path), None, None)


def read_csv(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as handle:
        yield from csv.DictReader(handle)


@lru_cache(maxsize=512)
def _execution_period(signature, symbol):
    job = json.loads(Path(signature[0]).read_text())['job']
    if job['symbol'] != symbol or job['group'] not in ('crypto_1m', 'crypto_5m'):
        raise ValueError('unsupported_native_period')
    minutes = int(job['execution_minutes'])
    if minutes not in (1, 5):
        raise ValueError('unsupported_native_period')
    return minutes


def entry_start(row, directory):
    if row.get('entry_rules', {}).get('status') != 'saved' or not re.fullmatch(r'[A-Z0-9]{1,32}USDT', row['symbol']):
        return None
    path = Path(directory).parent / 'exp-monday-limit-reclaim-20261024-v1/symbols' / row['symbol'] / 'identity.json'
    try:
        return row['entry_ms'] - _execution_period(stamp(path), row['symbol']) * 60000
    except (OSError, ValueError, KeyError, TypeError):
        return None


@lru_cache(maxsize=3)
def index(signature):
    result = {}
    for r in read_csv(signature[0]):
        key = (r['event'], int(r['side']), millis(r['entry_t']), millis(r['exit_t']))
        variant = r.get('variant') or ('near_P18' if 'order' in r else 'm5_near')
        item = dict(variant=variant, score=float(r['score']), net_bp=float(r['net']),
                    order=float(r['order']) if 'order' in r else None)
        result.setdefault(key, []).append(item)
    return result


def rules(track, row, net_bp, signature):
    unavailable = dict(status='unavailable', score=None, minimum=None, kind=None)
    if signature[1] is None:
        return unavailable
    try:
        choices = index(signature).get((row['event'], row['side'], row['entry_ms'], row['exit_ms']), [])
        choices = [r for r in choices if r['variant'] in VARIANTS[track]
                   and math.isclose(r['net_bp'], float(net_bp), abs_tol=1e-6, rel_tol=1e-10)]
        if len(choices) != 1:
            return unavailable
        r = choices[0]
        add = r['variant'] == 'v3_add5'
        minimum = TOP40 if track in ('60', '5') else .6 if track in ('spike', 'allin3') or add else .5
        if not math.isfinite(r['score']) or not minimum <= r['score'] <= 1:
            return unavailable
        if track == 'spike' and (not math.isfinite(r['order']) or r['order'] < 3):
            return unavailable
        return dict(status='saved', score=r['score'], minimum=minimum,
                    kind='add5' if add else 'first_touch', order=r['order'],
                    minimum_order=3 if track == 'spike' else None,
                    variant=r['variant'], source_net_bp=r['net_bp'])
    except (OSError, ValueError, KeyError, TypeError):
        return unavailable


def native_paths(track, variant, symbol, directory):
    root = Path(directory).parent
    if track == '5':
        return [root / run / 'symbols' / symbol / 'trades.csv.gz' for run in
                ('exp-monday-limit-reclaim-20261024-v1', 'exp-monday-limit-reclaim-olhc-allexits-20261025-v1')]
    run = ('exp-monday-limit-v3-grid-20261025-v1' if track == 'spike' else
           'exp-monday-limit-v3-levels-20261025-v1' if track == 'allin3' else
           'exp-monday-limit-be-lock-20261027-v1' if track == 'v3lock' else
           'exp-monday-limit-be-scalein-20261026-v1')
    return [root / run / path / 'symbols' / symbol / 'trades.csv.gz' for path in ('ohlc', 'olhc')]


@lru_cache(maxsize=12)
def native_index(signature):
    result = {}
    for r in read_csv(signature[0]):
        event = ':'.join(r['trade_id'].split(':')[:3])
        variant = r.get('variant')
        if not variant and ':m5:near_monday_edge:c0' in r['trade_id']:
            variant = 'm5_near'
        if variant:
            result.setdefault((event, variant), []).append(r)
    return result


def fill_geometry(track, row, directory, execution_minutes):
    """Prices are native fills; intrabar times remain source-bar intervals."""
    evidence = row.get('entry_rules', {})
    if evidence.get('status') != 'saved':
        return None
    key = (row['event'], evidence['variant'])
    paths = native_paths(track, evidence['variant'], row['symbol'], directory)
    pair = [native_index(stamp(p)).get(key, []) for p in paths]
    if any(len(p) != 1 for p in pair):
        raise ValueError('ambiguous_native_fill')
    o, l = pair[0][0], pair[1][0]
    def net(r):
        return float(r.get('net_notional_bp', r.get('net_bp'))) - (0 if int(r['reason']) == 4 else 10)
    chosen = l if net(l) < net(o) else o
    if (millis(o['entry_time']) != row['entry_ms'] or millis(chosen['exit_time']) != row['exit_ms']
            or int(o['side']) != row['side']
            or not math.isclose(net(chosen), evidence['source_net_bp'], rel_tol=1e-10, abs_tol=1e-6)):
        raise ValueError('native_fill_lineage_mismatch')
    entry, exit_price = float(o['entry']), float(chosen['exit_price'])
    if not all(math.isfinite(v) and v > 0 for v in (entry, exit_price)):
        raise ValueError('invalid_native_fill_price')
    duration = int(execution_minutes) * 60000
    entry_start = millis(o['entry_interval_start']) if o.get('entry_interval_start') else row['entry_ms'] - duration
    # Only these native reasons are known close-time events. Resting TP/stops
    # retain their full interval, even when finer market data is available.
    close_exit = int(chosen['reason']) in (0, 11, 12, 14)
    exit_start = row['exit_ms'] if close_exit else row['exit_ms'] - duration
    return dict(entry_price=entry, exit_price=exit_price,
                entry_start_ms=entry_start, entry_end_ms=row['entry_ms'],
                exit_start_ms=exit_start, exit_end_ms=row['exit_ms'],
                execution_minutes=int(execution_minutes), exit_kind='close' if close_exit else 'interval')
