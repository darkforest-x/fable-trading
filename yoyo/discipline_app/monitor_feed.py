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
import gzip
import json
import math
import re
import threading
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from .strategy_book import (BookUnavailable, project_backtest, project_booklet, project_breadth_ema20,
                            project_candles, project_cloud_backtest, project_cloud_booklet, project_cloud_candles,
                            project_history)

SOURCE = 'http://127.0.0.1:8766'
SYMBOL = re.compile(r'[A-Z0-9]{1,40}-USDT-SWAP')
TRACK_KEY = re.compile(r'[a-z0-9][a-z0-9-]{0,19}')
# 2.7.0 (owner 2026-11-09 “新策略在 App 里看不到”): the account list follows the monitor's own
# ``tracks``. These tuples only fix the display order of the accounts known today (the web
# picker order, reclaim-strategy-book.js); any account the monitor adds later is appended after
# them, live- keys in the live group, so a new account appears without an app release.
LIVE_TRACKS = ('live-breadth', 'live-allin3', 'live-wick6x3', 'live-wick6x3mid', 'live-wick3x3', 'live-wick6x3hi', 'live-wick3x3far')
REPLICA_TRACKS = ('spike', 'v3add20', 'allin3', '60', '5', 'v3', 'v3add', 'v3lock')
TRACKS = LIVE_TRACKS + REPLICA_TRACKS
CLOUD_TRACK = 'live-cloud-v17'
LABELS = {'60': '60m', '5': '5m', 'breadth-ema20': '全市场下轨反弹'}
# Subtitles copied from reclaim-strategy-book.js TITLES (display text only).
TITLES = {'live-breadth': '全市场宽度信号 · SOL 计资金', 'live-wick6x3': '插针收盘 · 6份3倍', 'live-wick3x3': '插针收盘 · 3份3倍',
          'live-wick6x3mid': '浅穿中点 · 6份3倍', 'live-wick6x3hi': '插针收盘 · 6份3倍 · 高分加仓', 'live-wick3x3far': '插针收盘 · 3份3倍 · 离1.618≥1.5%',
          'live-allin3': '触价前评分 · 模拟运行', '60': '等待回收', '5': '快速确认', 'v3': '分数决定目标', 'v3add': '逆向分步加仓',
          'live-cloud-v17': '云带 v1.7 · 12H 逆向',
          'spike': '排列过滤插针', 'v3lock': '回收后锁住利润', 'allin3': '单仓 · 梭哈3倍', 'v3add20': '20份动态保证金 · 3倍加仓'}
# Strategies with a research booklet but no forward paper account (owner 2026-11-09: “也要能看小册”).
RESEARCH = (dict(key='breadth-ema20', label='全市场下轨反弹', title='BTC + 山寨篮子 · 只有研究小册，无前向账户'),)
BREADTH_STATUS = frozenset(('taken', 'closed', 'followers', 'busy', 'out_of_band', 'watching', 'entering', 'price_missing'))
TOUCH_STATUS = frozenset(('scored', 'excluded', 'feature_error'))
MAX_ROWS = 60
# Owner 2026-11-09 (“有多少显示多少”): an alarm carries every coin (trigger + same-side touches until the cooldown
# ends); the 10-30 04:42 alert (#18) had 117. The bound only guards against a malformed payload (OKX lists a few hundred swaps).
MAX_ALARM_SYMBOLS = 500


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
                target=_num(row.get('target')), stage=str(row.get('stage') or '')[:24], funded=row.get('taken') == 1,
                notional=_num(row.get('notional')), fill_ms=_ms(row.get('fill_ms')), score=_num(row.get('score')),
                # Breadth comparison rows (breadth_paper.py: taken=0, note='comparison') are BTC/ETH shadows
                # that never move the account; ``due_ms`` is the six-day exit time of a holding.
                shadow=row.get('taken') != 1 and row.get('note') == 'comparison',
                due_ms=_ms(row.get('decision_ms')) if row.get('stage') == 'holding' else None)
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


def ordered_tracks(tracks):
    """Known accounts in workbench order, then any new monitor account (live- keys with the live group)."""
    keys = [k for k, v in tracks.items() if isinstance(k, str) and TRACK_KEY.fullmatch(k) and isinstance(v, dict)]
    known = [k for k in TRACKS if k in keys]
    extra = [k for k in keys if k not in TRACKS]
    live = lambda k: k.startswith('live-')
    return ([k for k in known if live(k)] + [k for k in extra if live(k)] +
            [k for k in known if not live(k)] + [k for k in extra if not live(k)])


def _refills(value):
    """Allin top-ups are cash, not P&L (monday-reclaim-paper.js ``refillTotal``); None when unreadable."""
    refills = value.get('refills')
    if refills is None:
        return 0.0
    if not isinstance(refills, list):
        return None
    total = 0.0
    for r in refills:
        before, after = (_num(r.get('equity_before')), _num(r.get('equity_after'))) if isinstance(r, dict) else (None, None)
        if before is None or after is None:
            return None
        total += after - before
    return total


def _breadth_rule(config):
    if not isinstance(config, dict) or _num(config.get('hold_days')) is None:
        return None
    shadows = [s for s in (config.get('shadows') or []) if _symbol(s)]
    return dict(count_min=_num(config.get('count_min')), count_max=_num(config.get('count_max')),
                bar_minutes=_num(config.get('bar_minutes')), observe_minutes=_num(config.get('observe_minutes')),
                hold_days=_num(config.get('hold_days')), funded=_symbol(config.get('funded')), shadows=shadows[:4],
                leverage=_num(config.get('leverage')), taker_bp=_num(config.get('taker_bp')))


def _account(key, value, config):
    equity, start = _num(value.get('equity')), _num(value.get('start_equity'))
    stats = value.get('stats') if isinstance(value.get('stats'), dict) else {}
    closed, wins = _num(stats.get('taken_closed')), _num(stats.get('wins'))
    added = _refills(value)
    pnl = equity - start - added if equity is not None and start is not None and added is not None else None
    rule = _breadth_rule(config)
    funded_open = [r for r in (value.get('open') or []) if isinstance(r, dict) and r.get('taken') == 1]
    hold = None
    if rule:
        holding = next((r for r in funded_open if r.get('stage') == 'holding'), None)
        if holding:
            hold = dict(symbol=_symbol(holding.get('symbol')), fill_ms=_ms(holding.get('fill_ms')),
                        due_ms=_ms(holding.get('decision_ms')))
    return dict(key=key, label=LABELS.get(key, key), title=TITLES.get(key, ''),
                group='live' if key.startswith('live-') else 'replica', kind='breadth' if rule else 'fib',
                equity=equity, start_equity=start, pnl=pnl, refilled=bool(added),
                return_pct=pnl/start*100 if pnl is not None and start else None,
                started_ms=_ms(value.get('started_ms')), open_count=len(funded_open),
                stats=dict(closed=closed, wins=wins, mean_net_bp=_num(stats.get('mean_net_bp')),
                           win_pct=wins/closed*100 if closed and wins is not None else None),
                breadth_rule=rule, hold=hold)


def _touch(row):
    symbol, side = _symbol(row.get('symbol')), _side(row.get('side'))
    if not symbol or not side or row.get('status') not in TOUCH_STATUS:
        return None
    return dict(id=_ms(row.get('id')), symbol=symbol, side=side, level=_num(row.get('level')),
                score=_num(row.get('score')), status=row['status'], taken=row.get('taken') == 1,
                observed_ms=_ms(row.get('observed_ms')))


def _breadth_signal(row):
    if not isinstance(row, dict) or row.get('status') not in BREADTH_STATUS:
        return None
    return dict(id=_ms(row.get('id')), symbol=_symbol(row.get('symbol')), status=row['status'],
                candle_ms=_ms(row.get('candle_ms')), observed_ms=_ms(row.get('observed_ms')),
                decision_ms=_ms(row.get('decision_ms')), n=_num(row.get('n')), new_n=_num(row.get('new_n')))


def _keep(row, breadth):
    """1.618 accounts list funded rows only (unfunded rows are score-gate shadows, monday-reclaim-paper.js
    ``funded``); the breadth account also keeps its BTC/ETH comparison rows."""
    if not isinstance(row, dict):
        return False
    if row.get('taken') == 1 and (_num(row.get('notional')) or 0) > 0:
        return True
    return breadth and row.get('note') == 'comparison'


def project_paper(raw, track, cloud=None):
    try:
        tracks = raw['tracks']
        accounts = [_account(key, tracks[key], raw.get(key)) for key in ordered_tracks(tracks)]
        if isinstance(cloud, dict) and not any(a['key'] == 'live-cloud-v17' for a in accounts):
            accounts.append({k: v for k, v in cloud.items() if k not in ('positions', 'closed', 'decisions')})
            rank = {key: float(i) for i, key in enumerate(TRACKS)}
            rank[CLOUD_TRACK] = rank.get('live-breadth', 0) + 0.5
            accounts.sort(key=lambda a: rank.get(a['key'], len(rank)))
        if not accounts:
            raise ValueError('no paper accounts')
        if track not in tracks or not isinstance(tracks[track], dict):
            raise MonitorUnavailable('未知模拟账户')
        detail = tracks[track]
        breadth = _breadth_rule(raw.get(track)) is not None
        rows = lambda name, closed: [p for p in (_position(r, closed) for r in (detail.get(name) or [])
                                                 if _keep(r, breadth)) if p][:MAX_ROWS]
        touches = [t for t in (_touch(r) for r in raw.get('signals') or [] if isinstance(r, dict)) if t]
        touches.sort(key=lambda s: -(s['observed_ms'] or 0))
        # live-allin3 scores its own touches before the touch candle (live_signals, monday-reclaim-paper.js
        # ``signalRows``); every other 1.618 account shares the monitor's touch list.
        own = raw.get('live_signals') if track == 'live-allin3' else raw.get('signals')
        own = [t for t in (_touch(r) for r in own or [] if isinstance(r, dict)) if t]
        own.sort(key=lambda s: -(s['observed_ms'] or 0))
        config = raw.get(track) if isinstance(raw.get(track), dict) else {}
        signals = [b for b in (_breadth_signal(r) for r in config.get('signals') or []) if b] if breadth else []
        signals.sort(key=lambda s: -(s['candle_ms'] or 0))
        # Monitor equity excludes floating P&L (monday-reclaim-paper.js header note).
        return dict(now_ms=_ms(raw['now_ms']), paper_only=True, equity_basis='mixed' if isinstance(cloud, dict) else 'realized', accounts=accounts,
                    research=[dict(r) for r in RESEARCH],
                    detail=dict(key=track, open=rows('open', False), closed=rows('closed', True),
                                touches=[] if breadth else own[:MAX_ROWS], breadth_signals=signals[:MAX_ROWS]),
                    # Kept for 2.6.x apps: the shared 1.618 touch list, unchanged.
                    signals=touches[:MAX_ROWS])
    except MonitorUnavailable:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise MonitorUnavailable('模拟盘数据暂不可用') from error


def project_paper_history(raw, track, tab, breadth):
    """Older records of one account (the monitor's history pages: rows before today's start)."""
    try:
        rows = [r for r in raw['rows'] if isinstance(r, dict)]
        positions, touches, signals = [], [], []
        if tab == 'closed':
            positions = [p for p in (_position(r, True) for r in rows if _keep(r, breadth)) if p]
        elif breadth:
            signals = [b for b in (_breadth_signal(r) for r in rows if r.get('breadth') is True) if b]
        else:
            touches = [t for t in (_touch(r) for r in rows) if t]
        total = _ms(raw.get('total')) or 0
        return dict(track=track, tab=tab, total=total, has_more=bool(raw.get('has_more')), now_ms=_ms(raw.get('now_ms')),
                    count=len(rows), positions=positions, touches=touches, breadth_signals=signals)
    except (KeyError, TypeError, AttributeError) as error:
        raise MonitorUnavailable('历史记录暂不可用') from error


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
                               trigger_n=_ms(row.get('trigger_n')) or _ms(row.get('n')) or len(symbols),
                               complete=row.get('complete') is not False,
                               symbols=symbols[:MAX_ALARM_SYMBOLS], active=bool(row.get('active')),
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
        # Saved backtest K-lines and the frozen breadth ledger are slow first reads on the monitor.
        slow = path.startswith('/api/strategy-backtest') or path.startswith('/static/')
        with urlopen(SOURCE + path, timeout=30 if slow else 4) as response:
            if path.endswith('.json.gz'):
                return json.loads(gzip.decompress(response.read()))
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

    def _read(self, path, project, load=None, ttl=None):
        with self._lock:
            hit = self._cache.get(path)
            now = time.time()
            if hit and now - hit[0] < (self._ttl if ttl is None else ttl):
                return hit[1]
        try:
            value = load() if load else project(self._fetch(path))
        except MonitorUnavailable:
            raise
        except BookUnavailable as error:
            raise MonitorUnavailable(str(error)) from None
        except (OSError, ValueError) as error:
            raise MonitorUnavailable('监控服务暂未连接，请稍后重试') from error
        with self._lock:
            self._cache[path] = (time.time(), value)
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
        return value

    def _raw_paper(self, track):
        # Raw snapshot (5 s) shared by the account projection and the booklet's rule facts.
        path = '/api/monday-reclaim-paper?' + urlencode(dict(list_period='today', track=track))
        return self._read('raw:' + path, None, lambda: self._fetch(path))

    def _asset(self, path):
        """Read one published JSON or gzip JSON asset through the injectable monitor source."""
        value = self._fetch(path)
        if isinstance(value, (bytes, bytearray)):
            payload = gzip.decompress(value) if path.endswith('.json.gz') else bytes(value)
            return json.loads(payload)
        if not isinstance(value, dict):
            raise MonitorUnavailable('策略数据文件格式无效')
        return value

    @staticmethod
    def _cloud_account(raw):
        if (not isinstance(raw, dict) or raw.get('schema_version') != 'live-cloud-v17.v1' or
                raw.get('strategy_id') != 'live-cloud-v17' or raw.get('mode') != 'paper'):
            raise MonitorUnavailable('云带模拟账户版本不匹配')
        account = raw.get('account') if isinstance(raw.get('account'), dict) else {}
        marked_equity, start = _num(account.get('equity')), _num(account.get('start_equity'))
        marked_pnl, realized, floating = (_num(account.get('net_pnl')), _num(account.get('realized_pnl')),
                                          _num(account.get('unrealized_pnl')))
        if marked_equity is None or start is None or start <= 0 or marked_pnl is None or realized is None or floating is None:
            raise MonitorUnavailable('云带模拟净值暂不可用')
        opened = [p for p in (raw.get('positions') or []) if isinstance(p, dict) and p.get('status') == 'open']
        closed = [p for p in (raw.get('recent_trades') or []) if isinstance(p, dict) and p.get('status') == 'closed']
        decisions = [d for d in (raw.get('recent_decisions') or []) if isinstance(d, dict)]
        return dict(key='live-cloud-v17', label='live-cloud-v17', title='云带 v1.7 · 12H 逆向',
                    group='live', kind='cloud', equity=start + realized, start_equity=start, pnl=realized,
                    return_pct=100 * realized / start, marked_equity=marked_equity, marked_pnl=marked_pnl,
                    marked_return_pct=_num(account.get('return_pct')), unrealized_pnl=floating,
                    started_ms=_ms(raw.get('started_ms')), heartbeat_ms=_ms(raw.get('heartbeat_ms')),
                    phase=raw.get('phase') if raw.get('phase') in ('running', 'watching', 'paused', 'degraded', 'stopped') else 'unknown',
                    open_count=_ms(account.get('open_position_count')) or 0, slot_count=_ms(account.get('slot_count')),
                    stats=dict(closed=None, wins=None, mean_net_bp=None, win_pct=None),
                    positions=opened[:MAX_ROWS], closed=closed[:MAX_ROWS], decisions=decisions[:MAX_ROWS])

    @staticmethod
    def _cloud_position(row, closed=False):
        symbol, side = _symbol(row.get('symbol')), _side(row.get('side'))
        entry_ms = _ms(row.get('entry_ms'))
        entry, mark = _num(row.get('entry_price')), _num(row.get('mark_price'))
        if not symbol or not side or entry_ms is None or entry is None:
            return None
        notional = _num(row.get('initial_notional_usdt'))
        remaining = _num(row.get('remaining_qty'))
        pnl = _num(row.get('realized_pnl'))
        if closed:
            exit_price, exit_ms = _num(row.get('exit_price')), _ms(row.get('exit_ms'))
            return dict(id=entry_ms, symbol=symbol, side=side, entry=entry, target=None, stage='closed', funded=True,
                        notional=notional, fill_ms=entry_ms, score=None, exit_price=exit_price, exit_ms=exit_ms,
                        reason=str(row.get('exit_reason') or '')[:40],
                        net_bp=pnl / notional * 10_000 if pnl is not None and notional and notional > 0 else None,
                        pnl=pnl)
        unrealized = (1 if side == 'long' else -1) * (mark - entry) * remaining if mark is not None and remaining is not None else None
        return dict(id=entry_ms, symbol=symbol, side=side, entry=entry, target=None, stage='open', funded=True,
                    notional=notional, fill_ms=entry_ms, score=None, mark=mark,
                    unrealized_bp=unrealized / notional * 10_000 if unrealized is not None and notional and notional > 0 else None,
                    remaining_notional=mark * remaining if mark is not None and remaining is not None else None,
                    stop=_num(row.get('stop_price')), due_ms=_ms(row.get('target_2r_expiry_ms')))

    @classmethod
    def _cloud_paper_snapshot(cls, raw):
        account = cls._cloud_account(raw)
        opened = [p for p in (cls._cloud_position(r) for r in account.pop('positions')) if p]
        closed = [p for p in (cls._cloud_position(r, True) for r in account.pop('closed')) if p]
        decisions = []
        for row in account.pop('decisions'):
            symbol, side = _symbol(row.get('symbol')), _side(row.get('side'))
            when = _ms(row.get('first_observed_ms') or row.get('confirmed_ms'))
            if symbol and side and when:
                decisions.append(dict(id=when, symbol=symbol, side=side,
                                      status=str(row.get('status') or 'unknown')[:32],
                                      reason=str(row.get('reason') or '')[:80] or None,
                                      time_ms=when, entry_price=_num(row.get('entry_price'))))
        now = _ms(raw.get('heartbeat_ms')) or _ms(raw.get('started_ms')) or 0
        return dict(now_ms=now, paper_only=True, equity_basis='realized', accounts=[account], research=[],
                    detail=dict(key='live-cloud-v17', open=opened, closed=closed, touches=[], breadth_signals=[],
                                cloud_events=decisions), signals=[])

    def paper(self, track):
        if not TRACK_KEY.fullmatch(track or ''):
            raise MonitorUnavailable('未知模拟账户')
        if track == 'live-cloud-v17':
            return self._read('cloud-paper', None, lambda: self._cloud_paper_snapshot(
                self._asset('/static/strategy-booklet/live-cloud-v17-forward.json')))
        # Validated against the monitor's own tracks inside the projection (unknown → 503).
        def load():
            raw = self._raw_paper(track)
            cloud = None
            try:
                cloud = self._cloud_account(self._asset('/static/strategy-booklet/live-cloud-v17-forward.json'))
            except (MonitorUnavailable, OSError, ValueError, KeyError):
                # A missing cloud asset must not take the existing 1.618 monitor off-line.
                pass
            return project_paper(raw, track, cloud)
        return self._read('paper:' + track, None, load)

    def _breadth(self, track):
        raw = self._raw_paper(track)
        if not isinstance(raw.get('tracks'), dict) or track not in raw['tracks']:
            raise MonitorUnavailable('未知模拟账户')
        return isinstance(raw.get(track), dict) and _num(raw[track].get('hold_days')) is not None

    def paper_history(self, track, tab, offset):
        """Records before today (closed trades or touches/signals), newest first, 50 per page."""
        if not TRACK_KEY.fullmatch(track or '') or tab not in ('closed', 'events'):
            raise MonitorUnavailable('未知模拟账户')
        if track == 'live-cloud-v17':
            # The live snapshot deliberately publishes only the latest 30 fills and 20 decisions;
            # the complete frozen 1,252-trade history is served by the booklet's backtest route.
            return dict(track=track, tab=tab, total=0, has_more=False, now_ms=None, count=0,
                        positions=[], touches=[], breadth_signals=[], cloud_events=[])
        breadth = self._breadth(track)
        scope = 'all' if tab == 'events' or breadth else 'funded'
        query = urlencode(dict(track=track, tab=tab, scope=scope, sort='time_desc', limit=50, offset=int(offset)))
        return self._read('/api/monday-reclaim-paper/history?' + query,
                          lambda raw: project_paper_history(raw, track, tab, breadth), ttl=30)

    def booklet(self, track, view='legacy'):
        """Strategy booklet: overview facts, rules, saved equity path and the original research tables."""
        if track == 'breadth-ema20':
            return self._read('booklet:' + track, None, lambda: project_breadth_ema20(
                self._fetch('/static/strategy-booklet/breadth-ema20-data.json')), ttl=600)
        if track == 'live-cloud-v17':
            def load_cloud_booklet():
                guide = self._asset('/static/strategy-booklet/cloud-v17/guide.json')
                overview = self._asset('/static/strategy-booklet/cloud-v17/overview.json')
                history_raw = self._asset('/static/strategy-booklet/live-cloud-v17-history.json')
                return project_cloud_booklet(guide, overview, history_raw, include_hourly_curve=view == 'native-v2')
            return self._read('booklet:' + track + ':' + view, None, load_cloud_booklet, ttl=600)
        if not TRACK_KEY.fullmatch(track or ''):
            raise MonitorUnavailable('未知策略')

        def load():
            raw = self._raw_paper(track)
            if not isinstance(raw.get('tracks'), dict) or track not in raw['tracks']:
                raise MonitorUnavailable('未知策略')
            research = self._read('/static/strategy-booklet/research.json', lambda v: v, ttl=600)
            history, error = None, None
            try:
                history = self._read(f'/static/strategy-booklet/{track}-history.json',
                                     lambda v: project_history(v, track), ttl=600)
            except MonitorUnavailable as failure:
                error = str(failure) or '历史资金曲线暂时无法读取'
            return project_booklet(track, LABELS.get(track, track), TITLES.get(track, ''), raw, research, history, error)
        return self._read('booklet:' + track, None, load, ttl=60)

    def backtest(self, track, sort, direction, offset):
        if not TRACK_KEY.fullmatch(track or '') or sort not in ('time', 'pnl') or direction not in ('asc', 'desc'):
            raise MonitorUnavailable('未知策略')
        if track == 'live-cloud-v17':
            path = '/static/strategy-booklet/cloud-v17-history/manifest.json'
            def project_cloud_page(value):
                overview = self._asset('/static/strategy-booklet/cloud-v17/overview.json')
                return project_cloud_backtest(value, track, sort, direction, offset, overview=overview)
            return self._read(f'cloud-backtest:{sort}:{direction}:{offset}',
                              project_cloud_page,
                              load=lambda: project_cloud_page(self._asset(path)),
                              ttl=600)
        query = urlencode(dict(track=track, sort=sort, direction=direction, offset=int(offset), limit=50))
        return self._read('/api/strategy-backtest?' + query, lambda v: project_backtest(v, track), ttl=300)

    def backtest_candles(self, track, trade, period):
        if not TRACK_KEY.fullmatch(track or '') or (track == 'live-cloud-v17' and period != '12H') or (track != 'live-cloud-v17' and period not in ('5m', '15m', '1H', '4H')):
            raise MonitorUnavailable('未知策略')
        if track == 'live-cloud-v17':
            def load_cloud_chart():
                base = '/static/strategy-booklet/cloud-v17-history'
                manifest = self._asset(base + '/manifest.json')
                records = manifest.get('records') if isinstance(manifest, dict) else None
                record = next((r for r in records or [] if isinstance(r, dict) and r.get('id') == f'cloud-v17-{trade:06d}'), None)
                if record is None:
                    raise BookUnavailable('未找到这笔云带历史交易')
                event_url = record.get('dataUrl')
                if not isinstance(event_url, str) or not re.fullmatch(r'events/cloud-v17-[0-9]{6}\.json\.gz', event_url):
                    raise BookUnavailable('云带交易事件引用无效')
                event = self._read('cloud-event:' + event_url, lambda v: v,
                                   load=lambda: self._asset(base + '/' + event_url), ttl=3600)
                symbol = record.get('symbol')
                if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]{1,32}', symbol):
                    raise BookUnavailable('云带交易币种无效')
                symbol_data = self._read('cloud-symbol:' + symbol, lambda v: v,
                                         load=lambda: self._asset(base + '/symbols/' + symbol + '.json.gz'), ttl=3600)
                return project_cloud_candles(event, symbol_data, record, track, trade)
            return self._read(f'cloud-candles:{trade}', None, load_cloud_chart, ttl=3600)
        query = urlencode(dict(track=track, trade=int(trade), period=period))
        return self._read('/api/strategy-backtest/candles?' + query,
                          lambda v: project_candles(v, track, int(trade)), ttl=600)

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
