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


def breadth_rows(t, closed):
    """live-breadth rows as breadth_paper.paper_row shapes them (placeholder prices)."""
    rows = []
    for i, (coin, funded, entry, mark) in enumerate([('SOL', True, 152.40, 157.31), ('BTC', False, 76020.0, 76861.0),
                                                     ('ETH', False, 2007.4, 2046.8)]):
        fill = t - 86400_000*2 - 3600_000*2 - (86400_000*9 if closed else 0)
        row = dict(id=9_260_000_100+i+(10 if closed else 0), track=9260, symbol=coin+'-USDT-SWAP', side=1, entry=entry, target=None,
                   fill_ms=fill, decision_ms=fill + 6*86400_000, stage='closed' if closed else 'holding', taken=int(funded),
                   notional=1000.0, note=None if funded else 'comparison', be_from_ms=None, backstop=None, remaining_notional=None)
        if closed:
            net = [325.0, 140.0, -61.0][i]
            row.update(exit_ms=fill + 6*86400_000, exit_price=entry*(1+net/1e4), reason='time_6d', net_bp=net,
                       pnl=32.5 if funded else 0.0)
        else:
            row.update(mark=mark, unrealized_bp=(mark/entry-1)*1e4)
        rows.append(row)
    return rows


def breadth_config(t):
    statuses = [('taken', 42, 0), ('watching', 37, None), ('followers', 40, 3), ('busy', 33, 0), ('out_of_band', 71, None),
                ('closed', 55, 0)]
    signals = [dict(id=9_260_000_200+i, symbol='SOL-USDT-SWAP', side=1, candle_ms=t - 5400_000*(i*3+1) - (0 if i else 86400_000*2),
                    observed_ms=t, decision_ms=t + 720_000 if status == 'watching' else t - 3600_000, n=n, new_n=new,
                    status=status, breadth=True, score=None, error=None) for i, (status, n, new) in enumerate(statuses)]
    return dict(track='live-breadth', count_min=31, count_max=62, bar_minutes=5, observe_minutes=30, hold_days=6,
                funded='SOL-USDT-SWAP', shadows=['BTC-USDT-SWAP', 'ETH-USDT-SWAP'], start_equity=1000.0, leverage=1.0,
                taker_bp=5.0, paper_only=True, signals=signals, now_ms=t)


def booklet_research():
    """Placeholder research tables for every account (synthetic numbers)."""
    table = lambda k: dict(status='占位研究', note='合成占位数据，仅用于界面验收。', headers=['区间', '样本数', '胜率 %', '平均净收益 bp'],
                           rows=[['2020–2024 · 训练期', 742, 39.9, 352.7], ['2025', 537, 36.3, 219.0], ['2026', 829, 34.1, 147.4]],
                           sections=[dict(title='占位分节', note='占位', headers=['档位', '笔数', '收益 %'], rows=[['A', 10, 12.5], ['B', 7, -3.1]])])
    return dict(version=1, methodology='合成占位方法说明。', period_note='合成占位区间说明。',
                accounts={k: table(k) for k in TRACKS})


def booklet_history(track):
    import math
    start, step = 1_577_836_800_000, 86400_000*3
    points, equity, peak = [], 1000.0, 1000.0
    for i in range(700):
        equity *= 1 + 0.004*math.sin(i/9) + 0.0025
        peak = max(peak, equity)
        points.append([start + i*step, equity, (equity/1000-1)*100, (equity/peak-1)*100, 0.0])
    years = sorted({datetime.utcfromtimestamp(p[0]/1000).year for p in points})
    return dict(schema=1, meta=dict(track=track, note='合成占位曲线，仅用于界面验收。'), refilled=False, points=points,
                monthly=[[f'{y}-{m:02d}', round(math.sin(y+m)*6, 2)] for y in years for m in range(1, 13)],
                yearly=[[str(y), round(math.cos(y)*30, 1)] for y in years],
                summary=dict(start=1000.0, end=points[-1][1], pnl=points[-1][1]-1000, return_pct=points[-1][2],
                             max_drawdown_pct=-min(p[3] for p in points), win_pct=61.5, trades=240, profit_factor=1.62,
                             profit_factor_state='finite', cash_added=0.0, avg_win=42.0, avg_loss=-51.0),
                distribution=dict(labels=[], values=[]), holding=dict(labels=[], values=[]),
                sides=[['做多', 130, 62.3, 640.2], ['做空', 110, 60.0, 410.8]], recent=[])


def breadth_ema20_data():
    import math
    pts = [dict(time=datetime.utcfromtimestamp(1_577_836_800 + i*86400*5).strftime('%Y-%m-%dT%H:%M:%SZ'),
                combined_equity=1 + i*0.012 + 0.08*math.sin(i/7)) for i in range(400)]
    return dict(schema_version='breadth-ema20.v1', strategy_id='breadth-ema20',
                rules=dict(btc_medium=dict(initial_window_min=720, post_window_wait_min=10, initial_touch_count_min=2, new_coins_max=0,
                                           reclaim_fraction_min=.75, max_hold_hours=144),
                           btc_case=dict(initial_window_min=5, post_window_wait_min=30, market_share_28d_min_pct=5.0,
                                         new_coins_to_initial_max_pct=25.0, alt_move_min_pct=2.0, max_hold_hours=144),
                           alt=dict(initial_window_min=5, initial_touch_count_min=2, market_share_28d_min_pct=5.0, hold_hours=6)),
                summaries=dict(all_history_fee_only=dict(event_count=398, growth_pct=466.6, max_drawdown_pct=26.8,
                                                         returns_by_sleeve=dict(BTC=dict(growth_pct=621.7, max_drawdown_pct=36.7),
                                                                                ALT=dict(growth_pct=311.5, max_drawdown_pct=29.3))),
                               **{'2026_actual_funded': dict(event_count=48, btc_event_count=23, alt_event_count=25, win_rate_pct=64.6,
                                                             growth_pct=42.0, max_drawdown_pct=6.6)},
                               calendar_year_fee_only_reset=[dict(year=y, event_count=50+y % 9, btc_event_count=20, alt_event_count=30+y % 9,
                                                                  equal_sleeves_growth_pct=round(math.sin(y)*30, 1)) for y in range(2020, 2027)]),
                equity_curves=dict(all_history_fee_only_settlement=dict(points=pts)), events=[])


BACKTEST_COINS = ['SOL', 'BTC', 'ETH', 'SUI', 'DOGE', 'XRP', 'LINK', 'AVAX', 'OP', 'APT', 'NEAR', 'TIA']


def backtest_rows(track):
    breadth = track == 'live-breadth'
    n = 13 if breadth else 66
    rows = []
    for i in range(n):
        entry = 1_790_174_700_000 - i*86400_000*(9 if breadth else 3)
        exit_ = entry + (6*86400_000 if breadth else 5400_000)
        side = 1 if breadth or i % 3 else -1
        ret = [5.25, -2.31, 8.14, 1.2, -0.7][i % 5]
        coin = 'SOL' if breadth else BACKTEST_COINS[i % len(BACKTEST_COINS)]
        price = 114.46 if coin == 'SOL' else 1.0 + i*0.01
        rules = dict(status='saved', kind='breadth', initial=40+i % 20, new_coins=0) if breadth else \
            dict(status='saved', kind='first_touch', score=round(.52 + (i % 7)*.04, 4))
        rows.append(dict(id=n-i, symbol=coin+'USDT', side=side, entry_ms=entry, exit_ms=exit_, pnl=round(ret*32, 2),
                         return_pct=ret, exit_reason='time_6d' if breadth else None, entry_rules=rules,
                         fills=dict(entry_price=price, exit_price=price*(1+side*ret/100), entry_start_ms=entry, exit_start_ms=exit_)))
    return rows


def backtest(q):
    rows = backtest_rows(q['track'])
    if q.get('sort') == 'pnl':
        rows.sort(key=lambda r: r['pnl'], reverse=q.get('direction') != 'asc')
    elif q.get('direction') == 'asc':
        rows.reverse()
    offset = int(q.get('offset', 0))
    page = rows[offset:offset+50]
    return dict(schema=1, track=q['track'], meta=dict(track=q['track'], start='2023-01-01', end='2026-09-29', source='Binance',
                                                      trades=len(rows), pnl=sum(r['pnl'] for r in rows), note='合成占位回测。',
                                                      basis='合成占位口径。', limitation='合成占位数据，仅用于界面验收。'),
                rows=page, total=len(rows), offset=offset, has_more=offset+50 < len(rows), sort=q.get('sort'), direction=q.get('direction'))


def backtest_candles(q):
    import math
    row = next(r for r in backtest_rows(q['track']) if r['id'] == int(q['trade']))
    step = {'5m': 300_000, '15m': 900_000, '1H': 3600_000, '4H': 14400_000}[q.get('period', '1H')]
    start, end = row['entry_ms']//step*step - 160*step, row['exit_ms']//step*step + 60*step
    price, bars = row['fills']['entry_price']*0.97, []
    for i, t in enumerate(range(start, end, step)):
        o = price
        price = max(o*0.5, o*(1 + 0.004*math.sin(i/6) + 0.0012*math.cos(i*1.7)))
        bars.append(dict(time=t, open=o, high=max(o, price)*1.002, low=min(o, price)*0.998, close=price, volume=1000+i))
    p = row['fills']['entry_price']
    week = start + 3*86400_000
    return dict(schema=1, track=q['track'], trade_id=row['id'], symbol=row['symbol'], source='Binance', bar=q.get('period', '1H'),
                candles=bars, weekly_ranges=[dict(week='2026-09-21', state='ready', start_ms=week, end_ms=week+86400_000,
                                                  expires_ms=week+7*86400_000, high=p*1.04, low=p*0.99, upper=p*1.07, lower=p*0.96)],
                backtestTrade=row, note='合成占位行情。')


def synthetic_monitor():
    now = lambda: int(time.time()*1000)
    coins = ['SOL', 'DOGE', 'ARB', 'SUI', 'XRP', 'ADA', 'LINK', 'AVAX', 'OP', 'APT', 'NEAR', 'TIA']
    equities = dict(zip(TRACKS, [1024.10, 515.35, 960.24, 920.95, 960.24, 932.76, 1016.03, 978.97, 778.99, 944.30, 965.37,
                                  975.82, 979.09, 980.53]))

    def paper(track):
        t = now()
        position = lambda i, coin, side, closed: dict(
            id=100+i, symbol=coin+'-USDT-SWAP', side=side, entry=1.2+i*.1, target=1.1+i*.1, stage='closed' if closed else 'breakeven' if i % 2 else 'open',
            taken=1, notional=300.0, fill_ms=t-3600_000*(i+1), score=.62+i*.03, mark=1.21+i*.1, unrealized_bp=(-1)**i*35.0,
            remaining_notional=300.0, backstop=1.44+i*.1, be_from_ms=t if i % 2 else None, be_price=None,
            exit_price=1.15+i*.1, exit_ms=t-1800_000*(i+1), reason=['target', 'breakeven', 'failed_reclaim'][i % 3],
            net_bp=[95.0, -12.0, -48.0][i % 3], pnl=[2.85, -.36, -1.44][i % 3])
        tracks = {key: dict(equity=equity, confirm_minutes=5 if key == '5' else 60, start_equity=1000.0, started_ms=t-86400_000*7, open=[], closed=[],
                            stats=dict(taken_closed=12, wins=7, mean_net_bp=8.5)) for key, equity in equities.items()}
        if track != 'live-breadth':
            tracks[track]['open'] = [position(i, c, (-1)**i, False) for i, c in enumerate(coins[:2])]
            tracks[track]['closed'] = [position(i, c, (-1)**(i+1), True) for i, c in enumerate(coins[2:6])]
        # 2.7.0: live-breadth (breadth_paper.py shape) — SOL funded, BTC/ETH comparison rows, day 2 of 6.
        tracks['live-breadth'].update(confirm_minutes=None, rule='live-breadth',
                                      stats=dict(taken_closed=3, wins=2, mean_net_bp=241.0, shadow_closed=9),
                                      open=breadth_rows(t, False), closed=breadth_rows(t, True) if track == 'live-breadth' else [])
        signals = [dict(id=500+i, symbol=c+'-USDT-SWAP', side=(-1)**i, level=.5+i*.01, score=round(.38+i*.045, 3),
                        status='scored' if i != 7 else 'excluded', taken=1 if i % 3 else 0, observed_ms=t-600_000*(i+1))
                   for i, c in enumerate(coins)]
        week, _ = ny_week()
        for s in signals:
            s.update(week=week, candle_ms=s['observed_ms']//60_000*60_000)
        extra = [score_signal(i, c, side, score, None) for i, (c, (side, score)) in enumerate(TOUCH_SCORES.items())]
        extra += [score_signal(5+i, c, side, score, m) for i, (c, side, m, score) in enumerate(EXTRA_TOUCHES) if m < 600]
        return dict(now_ms=t, tracks=tracks, signals=extra + signals, live_signals=signals[:6],
                    model=dict(threshold=.2262), breakeven_offset_bp=17.0, v3=dict(min_score=.5),
                    **{k: dict(min_score=.5 if k.startswith('live-wick') else .6) for k in ('live-allin3', 'allin3', 'spike',
                       'live-wick6x3', 'live-wick3x3', 'live-wick6x3hi', 'live-wick3x3far')},
                    **{'live-breadth': breadth_config(t)})

    def history(query):
        """Older touches of this week (the 本周 rows); only ARB in the fixture. 2.7.0 adds older closed trades."""
        q = dict(x.split('=') for x in query.split('&'))
        offset, t = int(q.get('offset', 0)), now()
        if q.get('tab') == 'closed':
            if q.get('track') == 'live-breadth':
                rows = [dict(r, id=r['id']+50, fill_ms=r['fill_ms']-86400_000*30, exit_ms=r['exit_ms']-86400_000*30)
                        for r in breadth_rows(t, True)]
            else:
                rows = [dict(id=300+offset+i, symbol=c+'-USDT-SWAP', side=(-1)**i, entry=.8+i*.05, target=.84+i*.05, stage='closed',
                             taken=1, notional=250.0, fill_ms=t-86400_000*(2+i+offset), exit_ms=t-86400_000*(2+i+offset)+5400_000,
                             exit_price=.83+i*.05, reason=['target', 'failed_reclaim', 'breakeven'][i % 3],
                             net_bp=[210.0, -96.0, -8.0][i % 3], pnl=[5.25, -2.40, -.20][i % 3]) for i, c in enumerate(coins)]
            return dict(rows=rows if offset < 100 else [], total=len(rows) + (100 if q.get('track') != 'live-breadth' else 0),
                        has_more=offset < 100 and q.get('track') != 'live-breadth', now_ms=t)
        if q.get('track') == 'live-breadth':
            rows = [dict(r, id=r['id']+40, candle_ms=r['candle_ms']-86400_000*20) for r in breadth_config(t)['signals'][:3]]
            return dict(rows=rows if offset == 0 else [], total=len(rows), has_more=False, now_ms=t)
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
        if path.startswith('/static/strategy-booklet/'):
            return fetch_static(path)
        return routes[path.split('?')[0]](path)

    def fetch_static(path):
        name = path.rsplit('/', 1)[1]
        if name == 'research.json':
            return booklet_research()
        if name == 'breadth-ema20-data.json':
            return breadth_ema20_data()
        if name.endswith('-history.json'):
            return booklet_history(name[:-len('-history.json')])
        raise OSError('not in fixture')

    routes = {'/api/monday-reclaim-paper': lambda p: paper(dict(x.split('=') for x in p.split('?')[1].split('&'))['track']),
              '/api/strategy-backtest': lambda p: backtest(dict(x.split('=') for x in p.split('?')[1].split('&'))),
              '/api/strategy-backtest/candles': lambda p: backtest_candles(dict(x.split('=') for x in p.split('?')[1].split('&'))),
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
