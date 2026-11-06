"""Read-only projections of three monitor workbench modules for the iPhone app.

Owner 2026-11-06: "纪律交易app重构 只做iphone版 把前端的一些模块也整合到app".
Integrated modules: the 1.618 paper accounts, the market-wide spike alarm list and
the 12H EMA cloud signal board. The source is the same fixed loopback monitor that
``monday_fib.py`` already projects (127.0.0.1:8766), so the phone keeps one HTTPS
origin and one device token.

Boundary rules (enforced here, not in the client):
- GET only. Alarm acknowledgement, test alarms, paper closes and notification
  routes on the monitor are never proxied.
- Whitelisted fields only. Notification receipts/channels, alarm message text,
  model features, rule internals and worker errors do not cross the boundary.
- Paper accounts are simulations (``paper_only``). Nothing here touches the live
  ledger, the order outbox or the exchange client, and nothing here is an order
  signal: cloud signals were rejected as a score on all timeframes (2026-11-05)
  and are shown for observation only.
"""
import json
import math
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import urlopen

SOURCE = 'http://127.0.0.1:8766'
SYMBOL = re.compile(r'[A-Z0-9]{1,40}-USDT-SWAP')
# Monitor track keys in workbench order (yoyo/monitor/static/monday-reclaim-paper.js).
LIVE_TRACKS = ('live-allin3', 'live-wick6x3', 'live-wick3x3', 'live-wick6x3hi', 'live-wick3x3far')
REPLICA_TRACKS = ('spike', 'v3add20', 'allin3', '60', '5', 'v3', 'v3add', 'v3lock')
TRACKS = LIVE_TRACKS + REPLICA_TRACKS
LABELS = {'60': '60m', '5': '5m'}
MAX_ROWS = 60


class MonitorUnavailable(ValueError):
    """The monitor cannot currently supply an honest snapshot."""


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _ms(value):
    value = _num(value)
    return int(value) if value is not None and value > 0 else None


def _symbol(value):
    return value if isinstance(value, str) and SYMBOL.fullmatch(value) else None


def _side(value):
    return 'long' if value == 1 else 'short' if value == -1 else None


def _position(row, closed):
    symbol, side = _symbol(row.get('symbol')), _side(row.get('side'))
    if not symbol or not side:
        return None
    item = dict(id=_ms(row.get('id')), symbol=symbol, side=side, entry=_num(row.get('entry')),
                target=_num(row.get('target')), stage=str(row.get('stage') or ''), funded=row.get('taken') == 1,
                notional=_num(row.get('notional')), fill_ms=_ms(row.get('fill_ms')), score=_num(row.get('score')))
    if closed:
        item.update(exit_price=_num(row.get('exit_price')), exit_ms=_ms(row.get('exit_ms')),
                    reason=str(row.get('reason') or ''), net_bp=_num(row.get('net_bp')), pnl=_num(row.get('pnl')))
    else:
        item.update(mark=_num(row.get('mark')), unrealized_bp=_num(row.get('unrealized_bp')),
                    remaining_notional=_num(row.get('remaining_notional')),
                    # Same rule as monday-reclaim-paper.js `stopPrice`.
                    stop=(_num(row.get('be_price')) or _num(row.get('entry'))) if row.get('be_from_ms')
                    else _num(row.get('backstop')))
    return item


def project_paper(raw, track):
    try:
        tracks = raw['tracks']
        accounts = []
        for key in TRACKS:
            value = tracks.get(key)
            if not isinstance(value, dict):
                continue
            equity, start = _num(value.get('equity')), _num(value.get('start_equity'))
            stats = value.get('stats') if isinstance(value.get('stats'), dict) else {}
            accounts.append(dict(
                key=key, label=LABELS.get(key, key), group='live' if key in LIVE_TRACKS else 'replica',
                equity=equity, start_equity=start,
                return_pct=(equity/start-1)*100 if equity is not None and start else None,
                started_ms=_ms(value.get('started_ms')),
                stats=dict(closed=_num(stats.get('taken_closed')), wins=_num(stats.get('wins')),
                           mean_net_bp=_num(stats.get('mean_net_bp')))))
        if not accounts:
            raise ValueError('no paper accounts')
        detail = tracks.get(track) if isinstance(tracks.get(track), dict) else {}
        # Unfunded rows are score-gate shadows (monday-reclaim-paper.js `funded`), not paper trades.
        funded = lambda r: isinstance(r, dict) and r.get('taken') == 1 and (_num(r.get('notional')) or 0) > 0
        rows = lambda name, closed: [p for p in (_position(r, closed) for r in (detail.get(name) or [])
                                                 if funded(r)) if p][:MAX_ROWS]
        signals = []
        for row in raw.get('signals') or []:
            symbol, side = _symbol(row.get('symbol')), _side(row.get('side'))
            if symbol and side and row.get('status') in ('scored', 'excluded', 'feature_error'):
                signals.append(dict(id=_ms(row.get('id')), symbol=symbol, side=side, level=_num(row.get('level')),
                                    score=_num(row.get('score')), status=row['status'], taken=row.get('taken') == 1,
                                    observed_ms=_ms(row.get('observed_ms'))))
        signals.sort(key=lambda s: -(s['observed_ms'] or 0))
        # Monitor equity excludes floating P&L (monday-reclaim-paper.js header note).
        return dict(now_ms=_ms(raw['now_ms']), paper_only=True, equity_basis='realized', accounts=accounts,
                    detail=dict(key=track, open=rows('open', False), closed=rows('closed', True)),
                    signals=signals[:MAX_ROWS])
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise MonitorUnavailable('模拟盘数据暂不可用') from error


def project_alerts(raw):
    try:
        alerts = []
        for row in raw['alerts'][:MAX_ROWS]:
            side = row.get('side')
            if side not in ('upper', 'lower'):
                continue
            symbols = [s for s in (row.get('symbols') or []) if _symbol(s)]
            alerts.append(dict(id=_ms(row.get('id')), side=side, first_ms=_ms(row.get('first_ms')),
                               last_ms=_ms(row.get('last_ms')), n=_ms(row.get('n')) or len(symbols),
                               symbols=symbols[:80], active=bool(row.get('active')),
                               acknowledged=bool(_ms(row.get('ack_ms'))), test=bool(row.get('test'))))
        return dict(now_ms=_ms(raw['now_ms']), threshold=_ms(raw.get('threshold')),
                    window_minutes=_ms(raw.get('window_minutes')), worker_alive=bool(raw.get('worker_alive')),
                    alerts=alerts)
    except (KeyError, TypeError, AttributeError) as error:
        raise MonitorUnavailable('报警数据暂不可用') from error


def project_cloud(raw):
    try:
        if raw.get('schema') != 1:
            raise ValueError('cloud schema')
        items = []
        for row in raw['items'][:MAX_ROWS*2]:
            symbol, side = _symbol(row.get('symbol')), _side(row.get('side'))
            if not symbol or not side:
                continue
            band = row.get('range') if isinstance(row.get('range'), dict) else {}
            items.append(dict(id=str(row.get('id') or '')[:120], symbol=symbol, side=side,
                              timeframe=str(row.get('timeframe') or '12H')[:8],
                              confirmed_ms=_ms(row.get('confirmed_ms')), entry=_num(row.get('entry')),
                              stop=_num(row.get('stop')), stop_pct=_num(row.get('stop_pct')),
                              target=_num(row.get('target')), planned_rr=_num(row.get('planned_rr')),
                              freshness=str(row.get('freshness') or '')[:16], historical=bool(row.get('historical')),
                              week=str(row.get('week') or '')[:10],
                              range={k: _num(band.get(k)) for k in ('high', 'low', 'upper', 'lower')}))
        status = raw.get('status') if isinstance(raw.get('status'), dict) else {}
        return dict(now_ms=_ms(raw['now_ms']), version=str(raw.get('version') or '')[:60], reference_only=True,
                    next_close_ms=_ms(raw.get('next_close_ms')), total=_ms(raw.get('total')) or len(items),
                    status=dict(state=str(status.get('state') or '')[:16], total=_ms(status.get('total')),
                                completed=_ms(status.get('completed')), last_scan_ms=_ms(status.get('last_scan_ms')),
                                worker_alive=bool(status.get('worker_alive'))),
                    items=items)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise MonitorUnavailable('云带信号暂不可用') from error


def _http(path):
    with urlopen(SOURCE + path, timeout=4) as response:
        return json.load(response)


class MonitorFeed:
    """Short-lived cache in front of the loopback monitor; ``fetch`` is injectable for fixtures."""

    def __init__(self, fetch=_http, ttl=5):
        self._fetch, self._ttl = fetch, ttl
        self._lock = threading.Lock()
        self._cache = {}

    def _read(self, path, project):
        with self._lock:
            hit = self._cache.get(path)
            now = time.time()
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        try:
            value = project(self._fetch(path))
        except (OSError, ValueError) as error:
            raise MonitorUnavailable('监控服务暂未连接，请稍后重试') from error
        with self._lock:
            self._cache[path] = (time.time(), value)
            if len(self._cache) > 32:
                self._cache.pop(next(iter(self._cache)))
        return value

    def paper(self, track):
        if track not in TRACKS:
            raise MonitorUnavailable('未知模拟账户')
        return self._read('/api/monday-reclaim-paper?' + urlencode(dict(list_period='today', track=track)),
                          lambda raw: project_paper(raw, track))

    def alerts(self):
        return self._read('/api/spike-alerts', project_alerts)

    def cloud(self, side, scope):
        query = urlencode(dict(limit=120, side=side, scope=scope, sort='newest'))
        return self._read('/api/cloud-signals?' + query, project_cloud)
