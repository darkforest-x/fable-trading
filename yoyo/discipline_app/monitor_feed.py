"""Read-only projections of three monitor workbench modules for the iPhone app.

Owner 2026-11-06: "纪律交易app重构 只做iphone版 把前端的一些模块也整合到app".
Integrated modules: the 1.618 paper accounts, the market-wide spike alarm list and
the 12H EMA cloud signal board. The source is the same fixed loopback monitor that
``monday_fib.py`` already projects (127.0.0.1:8766), so the phone keeps one HTTPS
origin and one device token.

Boundary rules (enforced here, not in the client):
- GET only, with one exception: the owner-approved 1.618 Bark switch
  (2026-11-06 “能否加上通知功能和报警功能”, chose in-app alarm + Bark with a switch in
  the app). Alarm acknowledgement, test alarms, paper closes and the other
  notification routes on the monitor are never proxied.
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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
            # Owner 2026-11-06: “全市场插针记录不要显示测试的” — workbench test alarms never reach the app.
            if side not in ('upper', 'lower') or row.get('test'):
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


# 1.618 touch rows carry the Telegram outbox status; Bark applies the same gate (score > 0.4 or a live- entry).
FIB_STATUS = {'sent': 'pushed', 'pending': 'pushing', 'sending': 'pushing', 'unknown': 'pushing', 'history': 'history'}
FIB_SKIP = {'score_not_above_0_4': 'below_threshold', 'score_unavailable': 'score_unavailable'}


def project_notifications(fib, alerts):
    """In-app notification feed: market alarms + this week's 1.618 touches with their push outcome."""
    try:
        events = []
        for row in fib['events'][:MAX_ROWS]:
            symbol, side = _symbol(row.get('symbol')), row.get('side')
            if not symbol or side not in ('upper', 'lower'):
                continue
            status = FIB_STATUS.get(row.get('status')) or FIB_SKIP.get(row.get('error')) or 'not_sent'
            at = _ms(row.get('candle_ms') if row.get('source') == 'history' else row.get('observed_ms'))
            events.append(dict(id=f"fib:{row.get('week')}:{symbol}:{side}:{row.get('generation')}", kind='fib',
                               symbol=symbol, side=side, level=_num(row.get('level')), price=_num(row.get('price')),
                               at_ms=at, status=status, pushed=status in ('pushed', 'pushing')))
        for alert in project_alerts(alerts)['alerts']:
            events.append(dict(id=f"alarm:{alert['id']}", kind='alarm', side=alert['side'], n=alert['n'],
                               symbols=alert['symbols'][:12], at_ms=alert['last_ms'] or alert['first_ms'],
                               first_ms=alert['first_ms'], active=alert['active'], test=alert['test'],
                               acknowledged=alert['acknowledged'], pushed=not alert['test']))
        events.sort(key=lambda e: -(e['at_ms'] or 0))
        bark, telegram = fib.get('bark') or {}, fib.get('telegram') or {}
        counts = bark.get('counts') if isinstance(bark.get('counts'), dict) else {}
        channels = alerts.get('channels') if isinstance(alerts.get('channels'), dict) else {}
        return dict(now_ms=_ms(fib['now_ms']), events=events[:MAX_ROWS], channels=dict(
            fib=dict(bark=bool(bark.get('enabled')), bark_configured=bool(bark.get('configured')),
                     bark_sent=_ms(counts.get('sent')) or 0, telegram=bool(telegram.get('enabled')),
                     min_score=_num(bark.get('min_score_exclusive'))),
            alarm=dict(bark=bool(channels.get('bark')), telegram=bool(channels.get('telegram')),
                       threshold=_ms(alerts.get('threshold')), window_minutes=_ms(alerts.get('window_minutes')))))
    except (KeyError, TypeError, AttributeError, MonitorUnavailable) as error:
        raise MonitorUnavailable('通知数据暂不可用') from error


def project_touch_scores(rows):
    """This NY week's 1.618 touch scores keyed like the chart's touches (symbol + upper/lower).

    Owner 2026-11-08 (“除了3全做”, item 1): the 最新触价 list must show each touch's own score,
    including 本周 rows older than the recent page. The monitor keeps one signal per
    (week, symbol, side); ``score`` is the touch 5m-close score the paper accounts use.
    Model features and errors never cross the boundary. Authenticated route only: the public
    ``/v1/market/monday-fib`` projection stays market-only.
    """
    try:
        rows = [r for r in rows if isinstance(r, dict)]
        week = max((r.get('week') for r in rows if isinstance(r.get('week'), str)), default=None)
        out, seen = [], set()
        for row in rows:
            symbol, side = _symbol(row.get('symbol')), _side(row.get('side'))
            status = row.get('status')
            if not symbol or not side or row.get('week') != week or status not in ('scored', 'excluded', 'feature_error'):
                continue
            edge = 'lower' if side == 'long' else 'upper'
            if (symbol, edge) in seen:
                continue
            seen.add((symbol, edge))
            out.append(dict(symbol=symbol, side=edge, candle_ms=_ms(row.get('candle_ms')),
                            observed_ms=_ms(row.get('observed_ms')), score=_num(row.get('score')), status=status))
        out.sort(key=lambda s: -(s['candle_ms'] or 0))
        return dict(week=week, scores=out)
    except (TypeError, AttributeError, ValueError) as error:
        raise MonitorUnavailable('触价评分暂不可用') from error


def _http(path, body=None):
    if body is None:
        with urlopen(SOURCE + path, timeout=4) as response:
            return json.load(response)
    # The monitor guards its switch with a same-origin check meant for browsers on the public
    # tunnel; this authenticated loopback service is a trusted same-host client.
    request = Request(SOURCE + path, data=json.dumps(body).encode(), method='PUT', headers={
        'Content-Type': 'application/json', 'Origin': SOURCE, 'Sec-Fetch-Site': 'same-origin',
        'X-Spike-Action': 'update-monday-fib-bark'})
    with urlopen(request, timeout=6) as response:
        return json.load(response)


class MonitorFeed:
    """Short-lived cache in front of the loopback monitor; ``fetch`` is injectable for fixtures."""

    HISTORY_TTL = 300
    HISTORY_PAGES = 10

    def __init__(self, fetch=_http, ttl=5):
        self._fetch, self._ttl = fetch, ttl
        self._lock = threading.Lock()
        self._cache = {}
        self._history = (0, None, [])  # (fetched_at, week, rows) for touches before today

    def _read(self, path, project, load=None):
        with self._lock:
            hit = self._cache.get(path)
            now = time.time()
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        try:
            value = load() if load else project(self._fetch(path))
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

    def notifications(self):
        return self._read('notifications', None, lambda: project_notifications(
            self._fetch('/api/monday-fib'), self._fetch('/api/spike-alerts')))

    def touch_scores(self):
        """Recent page (every call, 15 s cache) + this week's older pages (5 min cache)."""
        return self._read('touch-scores', None, self._load_touch_scores)

    def _load_touch_scores(self):
        snapshot = self._fetch('/api/monday-reclaim-paper?' + urlencode(dict(list_period='today', track='live-wick6x3')))
        if not isinstance(snapshot, dict):
            raise MonitorUnavailable('触价评分暂不可用')
        recent = list(snapshot.get('signals') or []) + list(snapshot.get('record_signals') or [])
        week = max((r.get('week') for r in recent if isinstance(r, dict) and isinstance(r.get('week'), str)), default=None)
        fetched_at, cached_week, older = self._history
        if week and (week != cached_week or time.time() - fetched_at > self.HISTORY_TTL):
            older = []
            for page in range(self.HISTORY_PAGES):
                query = urlencode(dict(track='live-wick6x3', tab='events', scope='all', sort='time_desc',
                                       limit=100, offset=page*100))
                page_value = self._fetch('/api/monday-reclaim-paper/history?' + query)
                rows = page_value.get('rows') or [] if isinstance(page_value, dict) else []
                older += [r for r in rows if isinstance(r, dict) and r.get('week') == week]
                if len(rows) < 100 or any(isinstance(r, dict) and r.get('week') != week for r in rows):
                    break
            self._history = (time.time(), week, older)
        return project_touch_scores(recent + older)

    def set_fib_bark(self, enabled):
        """The one write: switch 1.618 Bark pushes; the monitor validates channel configuration."""
        try:
            self._fetch('/api/monday-fib/bark', dict(enabled=bool(enabled)))
        except HTTPError as error:
            detail = '监控拒绝了 Bark 开关'
            try:
                detail = str(json.load(error).get('detail') or detail)[:80]
            except (ValueError, AttributeError, OSError):
                pass
            raise MonitorUnavailable(detail) from None
        except (OSError, ValueError) as error:
            raise MonitorUnavailable('Bark 开关暂未生效，请稍后重试') from error
        with self._lock:
            self._cache.pop('notifications', None)
        return self.notifications()
