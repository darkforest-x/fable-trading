"""Synthetic-only iPhone acceptance fixture (1.0.0): fake exchange + synthetic monitor modules.

Build mobile with VITE_SPIKE_SERVICE_URL=VITE_SPIKE_CANDLES_HTTP=http://127.0.0.1:18923,
VITE_SPIKE_CANDLES_WS=ws://127.0.0.1:18923/ws/v5/business and VITE_SPIKE_LIVE_QUOTES=off.
Reuses the 1.618 fixture (ManagedExchange, synthetic candles and touches) and adds placeholder
paper accounts, alarms and cloud signals. No credentials, no live service, no network exchange.
`/qa/metrics` reports exchange order writes after seeding; acceptance requires 0.
"""
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import serve_fib_ui_fixture as fib
from yoyo.discipline_app.monitor_feed import MonitorFeed, TRACKS

PORT = 18923
# 2.6.0: extra synthetic 1.618 touches beyond BTC/ETH so the 最新触价 list, the coin strip and the
# per-touch scores (/v1/monitor/touch-scores) can be checked. (coin, side, minutes ago, score)
EXTRA_TOUCHES = [('SOL', 'upper', 3, .81), ('DOGE', 'lower', 25, .33), ('ARB', 'upper', 30*60, .58)]
TOUCH_SCORES = {'BTC': ('upper', .72), 'ETH': ('lower', .46)}


def ny_week():
    date = datetime.now(ZoneInfo('America/New_York'))
    monday = (date - timedelta(days=date.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.date().isoformat(), int(monday.timestamp()*1000)


def touch_ms(minutes):
    """A synthetic touch time inside the current NY week (never before Monday 01:00)."""
    _, start = ny_week()
    return max(start + 3600_000, int(time.time()*1000) - minutes*60_000)


def with_extra_touches(raw):
    raw = dict(raw)
    if raw.get('items'):
        raw['items'] = list(raw['items']) + [dict(
            symbol=c+'-USDT-SWAP', high=101, low=97, price=100, stale=False,
            touches={side: dict(week=raw['week'], symbol=c+'-USDT-SWAP', side=side, source='live', observed_ms=touch_ms(m))})
            for c, side, m, _ in EXTRA_TOUCHES]
    return raw


def score_signal(i, coin, side, score, minutes):
    week, _ = ny_week()
    at = touch_ms(minutes) if minutes is not None else int(time.time()*1000) - 60_000
    return dict(id=700+i, week=week, symbol=coin+'-USDT-SWAP', side=-1 if side == 'upper' else 1, level=.5, score=score,
                status='scored', taken=0, observed_ms=at, candle_ms=at//60_000*60_000)


def synthetic_monitor():
    now = lambda: int(time.time()*1000)
    coins = ['SOL', 'DOGE', 'ARB', 'SUI', 'XRP', 'ADA', 'LINK', 'AVAX', 'OP', 'APT', 'NEAR', 'TIA']
    equities = dict(zip(TRACKS, [1016.03, 960.24, 920.95, 960.24, 932.76, 1016.03, 978.97, 778.99, 944.30, 965.37,
                                  975.82, 979.09, 980.53]))

    def paper(track):
        t = now()
        position = lambda i, coin, side, closed: dict(
            id=100+i, symbol=coin+'-USDT-SWAP', side=side, entry=1.2+i*.1, target=1.1+i*.1, stage='closed' if closed else 'breakeven' if i % 2 else 'open',
            taken=1, notional=300.0, fill_ms=t-3600_000*(i+1), score=.62+i*.03, mark=1.21+i*.1, unrealized_bp=(-1)**i*35.0,
            remaining_notional=300.0, backstop=1.44+i*.1, be_from_ms=t if i % 2 else None, be_price=None,
            exit_price=1.15+i*.1, exit_ms=t-1800_000*(i+1), reason=['target', 'breakeven', 'failed_reclaim'][i % 3],
            net_bp=[95.0, -12.0, -48.0][i % 3], pnl=[2.85, -.36, -1.44][i % 3])
        tracks = {key: dict(equity=equity, start_equity=1000.0, started_ms=t-86400_000*7, open=[], closed=[],
                            stats=dict(taken_closed=12, wins=7, mean_net_bp=8.5)) for key, equity in equities.items()}
        tracks[track]['open'] = [position(i, c, (-1)**i, False) for i, c in enumerate(coins[:2])]
        tracks[track]['closed'] = [position(i, c, (-1)**(i+1), True) for i, c in enumerate(coins[2:6])]
        signals = [dict(id=500+i, symbol=c+'-USDT-SWAP', side=(-1)**i, level=.5+i*.01, score=round(.38+i*.045, 3),
                        status='scored' if i != 7 else 'excluded', taken=1 if i % 3 else 0, observed_ms=t-600_000*(i+1))
                   for i, c in enumerate(coins)]
        week, _ = ny_week()
        for s in signals:
            s.update(week=week, candle_ms=s['observed_ms']//60_000*60_000)
        extra = [score_signal(i, c, side, score, None) for i, (c, (side, score)) in enumerate(TOUCH_SCORES.items())]
        extra += [score_signal(5+i, c, side, score, m) for i, (c, side, m, score) in enumerate(EXTRA_TOUCHES) if m < 600]
        return dict(now_ms=t, tracks=tracks, signals=extra + signals)

    def history(query):
        """Older touches of this week (the 本周 rows); only ARB in the fixture."""
        offset = int(dict(x.split('=') for x in query.split('&')).get('offset', 0))
        rows = [score_signal(9+i, c, side, score, m) for i, (c, side, m, score) in enumerate(EXTRA_TOUCHES) if m >= 600]
        return dict(rows=rows if offset == 0 else [], total=len(rows), has_more=False, now_ms=int(time.time()*1000))

    def alerts():
        t = now()
        rows = [dict(id=40, side='lower', first_ms=t-300_000, last_ms=t-60_000, n=27, active=True, ack_ms=None, test=0,
                     symbols=[c+'-USDT-SWAP' for c in ['BTC', 'ETH', *coins, 'WIF', 'PEPE', 'BONK', 'ORDI', 'SEI', 'INJ', 'JUP', 'PYTH', 'STRK', 'ENA', 'W', 'ZK', 'ONDO']])]
        rows += [dict(id=39-i, side=['lower', 'upper'][i % 2], first_ms=t-3600_000*(3+i*9), last_ms=t-3600_000*(3+i*9)+300_000,
                      n=31-i, active=False, ack_ms=t if i != 1 else None, test=1 if i == 1 else 0,
                      symbols=[c+'-USDT-SWAP' for c in coins]) for i in range(6)]
        return dict(now_ms=t, threshold=25, window_minutes=5, worker_alive=True, channels=dict(telegram=True, bark=True), alerts=rows)

    def cloud():
        t = now()
        items = [dict(id=f'cloud-{i}', symbol=c+'-USDT-SWAP', side=(-1)**i, timeframe='12H', confirmed_ms=t-43200_000*i-3600_000,
                      entry=.4123*(i+1), stop=.4123*(i+1)*(1-(-1)**i*.03), stop_pct=3.0, target=.4123*(i+1)*(1+(-1)**i*.072),
                      planned_rr=2.4, freshness='recent' if i < 3 else 'history', historical=i >= 3, week='2026-11-02',
                      range=dict(high=.43*(i+1), low=.39*(i+1), upper=.455*(i+1), lower=.365*(i+1)))
                 for i, c in enumerate(coins[:9])]
        return dict(schema=1, version='ema20-50-12h-placeholder', now_ms=t, next_close_ms=t+5400_000, total=len(items),
                    status=dict(state='ready', total=420, completed=412, last_scan_ms=t-120_000, worker_alive=True), items=items)

    state = dict(bark=True, extra=[])

    def alerts_with_extra():
        value = alerts()
        value['alerts'] = state['extra'] + value['alerts']
        return value

    def fib():
        t = now()
        event = lambda i, coin, status, error=None: dict(week='2026-11-02', symbol=coin+'-USDT-SWAP', side=['lower', 'upper'][i % 2],
                                                         level=.5+i*.01, price=.5, observed_ms=t-900_000*(i+1), candle_ms=t-900_000*(i+1),
                                                         source='live', generation=1, status=status, error=error, due_ms=0, message_id=None)
        events = [event(i, c, 'sent' if i % 3 == 0 else 'skipped', None if i % 3 == 0 else 'score_not_above_0_4') for i, c in enumerate(coins)]
        events = [e for e in state['extra_fib']] + events if state.get('extra_fib') else events
        return dict(now_ms=t, telegram=dict(enabled=True, configured=True, min_score_exclusive=.4),
                    bark=dict(enabled=state['bark'], configured=True, min_score_exclusive=.4, counts=dict(sent=668, skipped=540)),
                    events=events)

    def bark(body):
        state['bark'] = bool(body['enabled'])
        return {}

    def fetch(path, body=None):
        if path == '/api/monday-fib/bark':
            return bark(body)
        return routes[path.split('?')[0]](path)

    routes = {'/api/monday-reclaim-paper': lambda p: paper(dict(x.split('=') for x in p.split('?')[1].split('&'))['track']),
              '/api/monday-reclaim-paper/history': lambda p: history(p.split('?')[1]),
              '/api/spike-alerts': lambda p: alerts_with_extra(), '/api/cloud-signals': lambda p: cloud(),
              '/api/monday-fib': lambda p: fib()}
    feed = MonitorFeed(fetch, ttl=2)
    feed.qa_state = state
    return feed


def qa_trigger(feed, kind):
    """Inject a brand-new alarm or 1.618 push so the in-app alert can be observed (fixture only)."""
    t = int(time.time()*1000)
    if kind == 'alarm':
        feed.qa_state['extra'].insert(0, dict(id=900+len(feed.qa_state['extra']), side='upper', first_ms=t-120_000, last_ms=t, n=29,
                                              active=True, ack_ms=None, test=0, symbols=['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP']))
    else:
        feed.qa_state.setdefault('extra_fib', []).insert(0, dict(week='2026-11-02', symbol='SUI-USDT-SWAP', side='lower', level=.47, price=.47,
                                                                observed_ms=t, candle_ms=t, source='live', generation=len(feed.qa_state.get('extra_fib', []))+2,
                                                                status='sent', error=None, due_ms=0, message_id=None))
    feed._cache.clear()
    return dict(ok=True, kind=kind)


def main():
    original = fib.create_app
    project = fib.project_snapshot
    fib.project_snapshot = lambda raw, now: project(with_extra_touches(raw), now)

    def create_app(*args, **kwargs):
        feed = synthetic_monitor()
        app = original(*args, monitor_feed=feed, **kwargs)
        app.post('/qa/notify/{kind}')(lambda kind: qa_trigger(feed, 'alarm' if kind == 'alarm' else 'fib'))
        return app
    fib.create_app = create_app
    fib_run = fib.uvicorn.run
    fib.uvicorn.run = lambda app, **kwargs: fib_run(app, **dict(kwargs, port=PORT))
    if '--activity' not in sys.argv:
        sys.argv.append('--activity')
    fib.main()


if __name__ == '__main__':
    main()
