"""Read-only projection of the existing Monday monitor for the mobile chart.

The fixed loopback source owns NY Monday anchors and first-touch persistence.
No new scanner, account access, notification settings or execution inputs live here.
Only public market fields cross this boundary; private notification receipts do not.
"""
from datetime import datetime, timedelta
import json
import math
import re
import threading
import time
from urllib.request import urlopen
from zoneinfo import ZoneInfo


class FibUnavailable(ValueError):
    """The monitor cannot currently supply an honest current-week snapshot."""


def project_snapshot(raw, now_ms, *, wick_anchors=None):
    try:
        monday = datetime.strptime(raw['week'], '%Y-%m-%d').replace(tzinfo=ZoneInfo('America/New_York'))
        week_start = int(monday.timestamp() * 1000)
        next_week = int((monday + timedelta(days=7)).timestamp() * 1000)
        if (raw['timezone'] != 'America/New_York' or monday.weekday() != 0
                or not week_start <= now_ms < next_week or abs(raw['now_ms'] - now_ms) > 60000):
            raise ValueError('obsolete week')
        monday_end = int((monday + timedelta(days=1)).timestamp() * 1000)
        items = []
        for row in raw['items']:
            if not re.fullmatch(r'[A-Z0-9]{1,40}-USDT-SWAP', row['symbol']):
                continue
            high, low = row.get('high'), row.get('low')
            touches = {}
            for side in ('upper', 'lower'):
                event = row.get('touches', {}).get(side)
                if not event or event.get('week') != raw['week'] or event.get('symbol') != row['symbol'] or event.get('side') != side:
                    continue
                source = event.get('source')
                occurred = event.get('candle_ms') if source == 'history' else event.get('observed_ms')
                if source not in ('live', 'history') or not isinstance(occurred, (int, float)) or not week_start <= occurred <= now_ms:
                    continue
                touches[side] = dict(side=side, source=source, occurred_ms=occurred)
                if wick_anchors is not None and raw['monday_complete']:
                    # Persisted touch levels survive a monitor baseline reload.
                    start, observed, level = event.get('candle_ms'), event.get('observed_ms'), event.get('level')
                    if (type(start) is int and start % 60_000 == 0 and type(observed) is int
                            and monday_end <= start <= observed <= now_ms
                            and type(level) in (int, float) and math.isfinite(level) and level > 0):
                        span = 3_600_000 if source == 'history' and start % 3_600_000 == 0 else 60_000
                        end = min(start + span, observed // 60_000 * 60_000 + 60_000)
                        wick_anchors[row['symbol'], side] = (raw['week'], level, start, end)
            if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (high, low)) or high <= low:
                continue
            if not touches:
                continue
            items.append(dict(symbol=row['symbol'], high=high, low=low,
                              upper=high + .618 * (high-low), lower=low - .618 * (high-low),
                              price=row.get('price'), stale=bool(row.get('stale', True)), touches=touches))
        items.sort(key=lambda r: (-max(e['occurred_ms'] for e in r['touches'].values()), r['symbol']))
        stream = raw.get('stream', {})
        return dict(now_ms=raw['now_ms'], week=raw['week'], timezone='America/New_York',
                    week_start_ms=week_start, monday_end_ms=monday_end,
                    next_week_ms=next_week, monday_complete=bool(raw['monday_complete']),
                    connected=bool(stream.get('connected')), last_message_ms=stream.get('last_message_ms'), items=items)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise FibUnavailable('1.618 监控数据暂不可用') from error


class MondayFibFeed:
    def __init__(self):
        self._lock = threading.Lock()
        self._cached = None
        self._anchors = {}
        self._at = 0

    def _snapshot(self, now):
        # Caller holds the lock so the chart and its stop anchors share a generation.
        if self._cached is not None and now-self._at < 3 and now*1000 < self._cached['next_week_ms']:
            return
        try:
            with urlopen('http://127.0.0.1:8766/api/monday-fib', timeout=3) as response:
                raw = json.load(response)
            anchors = {}
            result = project_snapshot(raw, int(now*1000), wick_anchors=anchors)
        except (OSError, ValueError) as error:
            raise FibUnavailable('1.618 监控暂未连接，请稍后重试') from error
        self._cached, self._anchors, self._at = result, anchors, now

    def read(self):
        with self._lock:
            self._snapshot(time.time())
            return self._cached

    def wick_anchor(self, symbol, side, now_ms):
        with self._lock:
            self._snapshot(now_ms / 1000)
            edge = 'lower' if side == 'long' else 'upper'
            anchor = self._anchors.get((symbol, edge))
            if not anchor:
                raise FibUnavailable('尚无本周该方向的首次触价记录，请手动填写止损')
            return anchor
