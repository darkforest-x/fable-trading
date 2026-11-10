"""Read the frozen OKX cloud v1.7 records for the shared funded-trade replay."""
import gzip
import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .monday_fib import week_window

TRACK = 'live-cloud-v17'
BOOK = Path(__file__).with_name('static') / 'strategy-booklet'
HISTORY = BOOK / 'cloud-v17-history'
HOUR = 3_600_000
ACTION = {
    'trim': '敞口减仓', 'take_profit': '止盈', 'take_profit_open': '止盈',
    'ema_flip': '云带反转', 'ema_flip_open': '开盘云带反转',
    'stop': '止损', 'stop_gap_open': '开盘止损', 'series_tail_close': '期末结算',
    'delisting_settlement': '退市结算', 'data_gap_markout': '缺口结算',
}


@lru_cache(maxsize=16)
def _read(path, mtime):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as file:
        return json.load(file)


def _json(path):
    return _read(path, path.stat().st_mtime_ns)


def exit_legs(event):
    """Merge child-slot fills only when price, action and native H1 timing agree."""
    groups = defaultdict(list)
    entries = []
    for fill in event['fills']:
        units = abs(fill['netUnits'])
        if not math.isfinite(units) or units <= 0:
            raise ValueError('invalid_cloud_quantity')
        if fill['action'] == 'entry':
            entries.append(units)
        else:
            groups[fill['hourMs'], fill['phase'], fill['action'], fill['fillPrice']].append(units)
    opening = math.fsum(entries)
    if opening <= 0:
        raise ValueError('empty_cloud_entry')
    legs = []
    for (stamp, phase, action, price), units in sorted(groups.items()):
        if phase not in ('open', 'intrabar') or not math.isfinite(price) or price <= 0:
            raise ValueError('invalid_cloud_fill')
        qty = math.fsum(units)
        legs.append(dict(time_ms=stamp, start_ms=stamp, end_ms=stamp + (HOUR if phase == 'intrabar' else 0),
                         price=price, label=ACTION.get(action, action), action=action, phase=phase,
                         quantity=qty, share=qty/opening))
    if not math.isclose(math.fsum(leg['quantity'] for leg in legs), opening, rel_tol=1e-8, abs_tol=1e-9):
        raise ValueError('unreconciled_cloud_exits')
    return legs


def _row(record):
    entry, end = record['entryMs'], record['exitMs']
    interval = record['exitPhase'] == 'intrabar'
    return dict(id=int(record['id'].removeprefix('cloud-v17-')), event=record['eventId'],
                symbol=record['symbol'] + 'USDT', source='OKX', kind='cloud-v17', side=record['side'],
                entry_ms=entry, exit_ms=end, entry_start_ms=entry,
                pnl=record['netPnl'], return_pct=record['netReturnPct'], exit_reason=record['exitReason'],
                entry_rules=dict(status='saved', kind='cloud-v17', signal_close_ms=entry),
                fills=dict(entry_price=record['entryPrice'], exit_price=record['exitPrice'],
                           entry_start_ms=entry, entry_end_ms=entry, exit_start_ms=end,
                           exit_end_ms=end + (HOUR if interval else 0),
                           exit_kind='interval' if interval else 'point', execution_minutes=60))


@lru_cache(maxsize=2)
def _ledger(manifest_stamp, overview_stamp):
    manifest = _json(HISTORY / 'manifest.json')
    overview = _json(BOOK / 'cloud-v17/overview.json')
    records = manifest['records']
    rows = [_row(r) for r in records]
    if len(rows) != manifest['tradeCount'] or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('incomplete_cloud_ledger')
    ranks = {}
    for row in rows:
        item = ranks.setdefault(row['symbol'], dict(symbol=row['symbol'], pnl=0., trades=0))
        item['pnl'] += row['pnl']
        item['trades'] += 1
    meta = dict(track=TRACK, source='OKX', start=records[0]['entryAt'][:10], end=overview['endUtc'][:10],
                trades=len(rows), pnl=manifest['account']['netPnl'], note='云带 v1.7 · 12H 逆向',
                basis='按初始名义金额计算单笔收益，已扣实际成交手续费。',
                limitation='未计资金费与滑点；盘中成交只精确到原始小时。')
    return dict(rows=rows, ranking=sorted(ranks.values(), key=lambda r: (-r['pnl'], r['symbol'])), meta=meta)


def ledger():
    return _ledger((HISTORY / 'manifest.json').stat().st_mtime_ns, (BOOK / 'cloud-v17/overview.json').stat().st_mtime_ns)


def candles(trade):
    from yoyo.discipline_app.strategy_book import project_cloud_candles
    records = _json(HISTORY / 'manifest.json')['records']
    record = records[trade-1] if 0 < trade <= len(records) else None
    if not record or record['id'] != f'cloud-v17-{trade:06d}':
        raise KeyError('trade_not_found')
    event = _json(HISTORY / record['dataUrl'])
    symbol = record['symbol']
    frame = project_cloud_candles(event, _json(HISTORY / 'symbols' / (symbol + '.json.gz')), record, TRACK, trade)
    row = _row(record)
    row['fills']['exit_legs'] = exit_legs(event)
    evidence = event['entryEvidence']
    rails = evidence.get('rails')
    if rails:
        # The frozen rails are L-.618W and H+.618W, with W=H-L.
        width = (rails['upperPrice']-rails['lowerPrice']) / 2.236
        week = week_window(evidence['signalBarOpenMs'])
        frame['weekly_ranges'].append(dict(symbol=row['symbol'], week=rails['week'], state='ready',
            start_ms=week['start'], end_ms=week['end'], expires_ms=week['expires'],
            high=rails['upperPrice']-.618*width, low=rails['lowerPrice']+.618*width,
            upper=rails['upperPrice'], lower=rails['lowerPrice']))
    edge = frame['candles'][-1]['time'] + 12*HOUR
    return dict(**{k:frame[k] for k in ('bar','source','candles','weekly_ranges','references','note','missing_ranges')},
                schema=1, track=TRACK, trade_id=trade, symbol=row['symbol'], timezone='America/New_York',
                window=event['window'], now_ms=edge, last_close_ms=edge, backtestTrade=row)
