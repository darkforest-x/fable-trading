"""Monitor modules reach the iPhone app as GET-only, whitelisted projections.

Synthetic monitor payloads only (placeholder symbols and numbers). The point is the
boundary: notification receipts, alarm text, model features and mutation routes must
never cross into the discipline service, and reading them never touches the ledger.
"""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.monitor_feed import (MonitorFeed, MonitorUnavailable, TRACKS, project_alerts,
                                              project_cloud, project_notifications, project_paper,
                                              project_paper_history, project_touch_scores)
from yoyo.discipline_app.strategy_book import (BookUnavailable, downsample, project_breadth_ema20,
                                               project_cloud_backtest, project_cloud_booklet,
                                               project_cloud_candles, project_history)
from test_discipline_service import Clock, market

TOKEN = 'monitor-fixture-token-not-a-secret-0123456789'
AUTH = {'Authorization': f'Bearer {TOKEN}'}


def paper_raw():
    track = lambda equity, **extra: dict(confirm_minutes=60, rule='internal-rule', equity=equity, start_equity=1000.0,
                                         started_ms=1_790_000_000_000, open=0, closed=0, pending_adds=0,
                                         stats=dict(taken_closed=4, wins=3, mean_net_bp=12.5, shadow_closed=99),
                                         record_closed=0, detail_omitted=True, **extra)
    tracks = {key: track(1000.0) for key in TRACKS}
    tracks['live-wick6x3'] = dict(track(960.0), detail_omitted=False, open=[
        dict(id=7, symbol='AAA-USDT-SWAP', side=-1, entry=1.2, target=1.1, stage='open', taken=1, notional=300.0,
             fill_ms=1_790_000_100_000, score=0.8, mark=1.25, unrealized_bp=-41.0, remaining_notional=300.0,
             backstop=1.44, features={'secret_model_input': 1}, note='internal')],
        closed=[dict(id=6, symbol='BBB-USDT-SWAP', side=1, entry=2.0, target=2.2, stage='closed', taken=1,
                     notional=300.0, fill_ms=1_790_000_000_500, exit_price=2.2, exit_ms=1_790_000_090_000,
                     reason='target', net_bp=95.0, pnl=2.85),
                dict(id=5, symbol='bad symbol', side=1, taken=1, notional=1.0),
                dict(id=4, symbol='DDD-USDT-SWAP', side=1, entry=2.0, stage='closed', taken=0, notional=0.0)])
    # live-breadth (breadth_paper.py shape): SOL funded, BTC/ETH comparison rows, six-day holding.
    breadth_row = lambda i, symbol, funded, stage='holding': dict(
        id=9_260_000_000+i, track=9260, symbol=symbol, side=1, entry=100.0, target=None, fill_ms=1_790_000_000_000,
        decision_ms=1_790_518_400_000, stage=stage, taken=int(funded), notional=1000.0, note=None if funded else 'comparison',
        mark=101.0, unrealized_bp=100.0, exit_ms=None, exit_price=None, reason=None, net_bp=None, pnl=None, error=None)
    tracks['live-breadth'] = dict(track(1000.0), detail_omitted=False, confirm_minutes=None,
                                  open=[breadth_row(1, 'SOL-USDT-SWAP', True), breadth_row(2, 'BTC-USDT-SWAP', False),
                                        breadth_row(3, 'ETH-USDT-SWAP', False), dict(breadth_row(4, 'XRP-USDT-SWAP', False), note=None)],
                                  closed=[])
    breadth_config = dict(track='live-breadth', count_min=31, count_max=62, bar_minutes=5, observe_minutes=30, hold_days=6,
                          funded='SOL-USDT-SWAP', shadows=['BTC-USDT-SWAP', 'ETH-USDT-SWAP'], start_equity=1000.0,
                          source='internal-experiment-path', signals=[
                              dict(id=9_260_000_005, symbol='SOL-USDT-SWAP', side=1, candle_ms=1_789_999_000_000, observed_ms=1,
                                   decision_ms=1_790_000_800_000, n=42, new_n=0, status='taken', breadth=True, error='trace'),
                              dict(id=9_260_000_006, symbol='SOL-USDT-SWAP', side=1, candle_ms=1_790_000_100_000, n=40, new_n=3,
                                   status='followers', breadth=True),
                              dict(id=9_260_000_007, symbol='SOL-USDT-SWAP', side=1, candle_ms=1_790_000_150_000, status='internal_state')])
    return dict(now_ms=1_790_000_200_000, tracks=tracks, **{'live-breadth': breadth_config}, live_signals=[
        dict(id=11, symbol='EEE-USDT-SWAP', side=1, level=1.0, score=0.61, status='scored', taken=1, observed_ms=1_790_000_160_000)],
        signals=[
        dict(id=1, symbol='AAA-USDT-SWAP', side=-1, level=1.2, score=0.8, status='scored', taken=1,
             observed_ms=1_790_000_050_000, features={'x': 1}, levels='{}'),
        dict(id=2, symbol='CCC-USDT-SWAP', side=1, level=3.0, score=0.3, status='scored', taken=0,
             observed_ms=1_790_000_150_000)])


def alerts_raw():
    return dict(now_ms=1_790_000_200_000, threshold=25, window_minutes=5, cooldown_minutes=60, worker_alive=True,
                channels=dict(telegram=True, bark=False), alerts=[
                    dict(id=3, side='lower', first_ms=1, last_ms=2, n=26, symbols=['AAA-USDT-SWAP', 'x'],
                         telegram='sent', bark='disabled', text='message body', error='trace', ack_ms=5, test=0,
                         active=False),
                    dict(id=4, side='upper', first_ms=1, last_ms=2, n=30, symbols=['AAA-USDT-SWAP'], test=1, active=True)])


def cloud_raw():
    return dict(schema=1, version='ema20-50-12h-placeholder', now_ms=1_790_000_200_000, next_close_ms=1_790_001_000_000,
                total=1, status=dict(state='ready', total=10, completed=10, errors=0, last_scan_ms=9, worker_alive=True,
                                     error_samples=[dict(symbol='ZZZ-USDT-SWAP', reason='internal')],
                                     channels=dict(telegram=True)),
                items=[dict(id='c1', symbol='AAA-USDT-SWAP', side=1, timeframe='12H', confirmed_ms=8, entry=1.0, stop=0.97,
                            stop_pct=3.0, target=1.1, planned_rr=3.3, freshness='recent', historical=False,
                            week='2026-11-02', range=dict(high=1.0, low=0.9, upper=1.06, lower=0.84, state='ready'),
                            notifications=dict(telegram='sent'), eligibility_policy='internal')])


def fib_raw():
    event = lambda symbol, status, error=None, source='live': dict(week='2026-11-02', symbol=symbol, side='lower',
                                                                  level=1.0, price=.99, observed_ms=5, candle_ms=4,
                                                                  source=source, generation=1, status=status,
                                                                  error=error, due_ms=0, message_id=987654)
    return dict(now_ms=10, telegram=dict(enabled=True, configured=True, min_score_exclusive=.4, generation=1),
                bark=dict(enabled=True, configured=True, min_score_exclusive=.4, generation=1,
                          counts=dict(sent=7, skipped=3), recent=[dict(server_timestamp=123, error=None)]),
                events=[event('AAA-USDT-SWAP', 'sent'), event('BBB-USDT-SWAP', 'skipped', 'score_not_above_0_4'),
                        event('CCC-USDT-SWAP', 'skipped', 'expired_or_disabled'),
                        event('DDD-USDT-SWAP', 'history', source='history')])


def history_raw():
    row = lambda i, symbol, side, week='2026-11-02': dict(id=i, week=week, symbol=symbol, side=side, level=1.0, score=.55,
                                                         status='scored', candle_ms=1_789_900_000_000+i, observed_ms=1,
                                                         features={'secret_model_input': 1}, error='trace')
    return dict(rows=[row(9, 'EEE-USDT-SWAP', -1), row(8, 'AAA-USDT-SWAP', -1), row(3, 'OLD-USDT-SWAP', 1, '2026-10-26')],
                total=3, has_more=False, now_ms=1_790_000_200_000, day_start_ms=1_790_000_000_000)


def research_raw():
    return dict(version=1, methodology='方法说明', period_note='区间说明', integrity='internal', accounts={'live-wick6x3': dict(
        status='模拟运行', note='占位说明', headers=['区间', '样本数'], rows=[['2025', 10]],
        sections=[dict(title='分节', headers=['a', 'b'], rows=[[1, 2]])],
        sources=[dict(path='experiments/active/internal', commit='abc123')])})


def booklet_history_raw():
    points = [[1_700_000_000_000 + i*3_600_000, 1000.0 + i, float(i), -0.5, 0.0] for i in range(900)]
    return dict(schema=1, meta=dict(track='live-wick6x3', note='占位', sources=['internal-path']), refilled=False, points=points,
                monthly=[['2024-01', 1.5]], yearly=[['2024', 9.0]], summary=dict(start=1000.0, end=1899.0, return_pct=89.9,
                max_drawdown_pct=4.0, win_pct=60.0, trades=40, profit_factor=1.5, profit_factor_state='finite'),
                distribution=dict(labels=[], values=[]), holding=dict(labels=[], values=[]), sides=[['做多', 20, 60.0, 100.0]],
                recent=[], source_commit='deadbeef')


def cloud_forward_raw():
    return dict(schema_version='live-cloud-v17.v1', strategy_id='live-cloud-v17', mode='paper', phase='running',
                heartbeat_ms=1_790_000_200_000, started_ms=1_790_000_000_000,
                rule={'private_or_order_api': True, 'secret': 'discard'}, health={'error': 'discard'},
                account=dict(start_equity=1000.0, equity=1025.0, realized_pnl=5.0, unrealized_pnl=20.0,
                             net_pnl=25.0, return_pct=2.5, open_position_count=1, slot_count=9),
                positions=[dict(event_id='private-event', symbol='AAA-USDT-SWAP', side=1, status='open',
                                entry_ms=1_790_000_100_000, entry_price=10.0, mark_price=12.0, remaining_qty=10.0,
                                initial_notional_usdt=2000.0, stop_price=9.0, target_2r_expiry_ms=1_790_100_000_000,
                                private='discard')],
                recent_trades=[dict(symbol='BBB-USDT-SWAP', side=-1, status='closed', entry_ms=1_790_000_000_000,
                                    entry_price=2.0, exit_price=1.9, exit_ms=1_790_000_100_000,
                                    initial_notional_usdt=1000.0, realized_pnl=12.0, exit_reason='take_profit')],
                recent_decisions=[dict(symbol='AAA-USDT-SWAP', side=1, status='opened', reason='accepted',
                                       confirmed_ms=1_790_000_090_000, first_observed_ms=1_790_000_095_000,
                                       entry_price=10.0, secret='discard')], recent_fills=[{'secret': 'discard'}])


def cloud_guide_raw():
    return dict(title='云带 v1.7 · 12H 逆向', intro='先检查趋势与周一轨道。',
                facts=[dict(label='同时持仓', value='9', unit='个币')],
                flow=[dict(title='等收盘', text='12H 收盘确认。')],
                direction=dict(headers=['检查', '做多', '做空'], rows=[['EMA', 'EMA20 > EMA50', 'EMA20 < EMA50']]),
                cases=[dict(id='cloud-v17-000001', title='示例案例', text='案例说明全文。')],
                risk=['风险口径全文。'], sources=['OKX 12H 来源说明。'])


def cloud_history_raw():
    points = [[1_700_000_000_000 + i * 3_600_000, 1000.0 + i, float(i), -57.8293 if i == 501 else -float(i % 7), 1000.0]
              for i in range(1000)]
    return dict(schema=1, meta=dict(track='live-cloud-v17', basis='hourly_marked',
                                  note='OKX 12H hourly marked source.'), refilled=False, points=points,
                summary=dict(start=1000.0, end=1999.0, pnl=999.0, return_pct=99.9, max_drawdown_pct=57.8293,
                             win_pct=21.0, trades=1252, profit_factor=1.4235, profit_factor_state='finite'),
                yearly=[['2026', 20.0]], monthly=[['2026-11', 3.0]], sides=[['做多', 600, 21.0, 100.0]])


def cloud_overview_raw():
    return dict(metrics=dict(final_usdt=147590.50, payoff_usdt=5.35, grossProfitFactor=1.4235,
                             dd_mark_pct=57.8293, dd_bound_pct=58.54, trades=1252, win_pct=21.0))


def cloud_manifest_raw():
    return dict(schemaVersion=1, strategyId='cloud-v17-history', tradeCount=1,
                account=dict(netPnl=146590.5), source={}, chartWindow={'timeframe': '12h'},
                dataQuality={'tradesWithMissing12hBars': 1}, records=[dict(
                    id='cloud-v17-000001', symbol='AAA', side=1, entryMs=1_700_000_000_000,
                    exitMs=1_700_086_400_000, entryPrice=10.0, exitPrice=11.0,
                    netPnl=30.0, netReturnPct=3.0, exitReason='take_profit', exitReasonLabel='止盈',
                    dataUrl='events/cloud-v17-000001.json.gz')])


def cloud_event_raw():
    start, end = 1_700_000_000_000, 1_700_086_400_000
    return dict(schemaVersion=1, id='cloud-v17-000001', symbol='AAA', side=1,
                symbolDataUrl='../symbols/AAA.json.gz', entryMs=1_700_000_000_000, entryPrice=10.0,
                exitMs=1_700_086_400_000, exitPrice=11.0,
                window=dict(timeframe='12h', startMs=start, endMs=end, expectedBars=3, availableBars=3, missingBars=0),
                fills=[dict(hourMs=start, phase='open', action='entry', childSlot=0, fillPrice=10.0, netUnits=.5),
                       dict(hourMs=start, phase='open', action='entry', childSlot=1, fillPrice=10.0, netUnits=.5),
                       dict(hourMs=start + 43_200_000, phase='intrabar', action='take_profit', childSlot=0,
                            fillPrice=12.0, netUnits=-.5),
                       dict(hourMs=end, phase='intrabar', action='ema_flip', childSlot=1,
                            fillPrice=11.0, netUnits=-.5)],
                entryEvidence=dict(fixedStopPrice=9.0, twoRPrice=12.0,
                                   rails=dict(selectedSideRailPrice=9.5)))


def cloud_symbol_raw():
    start = 1_700_000_000_000
    return dict(schemaVersion=1, symbol='AAA', timeframe='12h',
                columns=['timeMs', 'open', 'high', 'low', 'close', 'ema20', 'ema50', 'emaCount', 'emaReady'],
                rows=[[start, 10.0, 11.0, 9.0, 10.5, 10.1, 10.2, 100, True],
                      [start + 43_200_000, 10.5, 12.0, 10.0, 11.0, 10.3, 10.3, 101, True],
                      [start + 86_400_000, 11.0, 12.0, 10.5, 11.5, 10.5, 10.4, 102, True]])


def backtest_raw():
    row = dict(id=3, symbol='AAAUSDT', side=-1, entry_ms=1_700_000_000_000, exit_ms=1_700_000_600_000, pnl=-5.0, return_pct=-1.0,
               exit_reason=11, entry_rules=dict(status='saved', score=0.61, kind='first_touch', source_net_bp=-100.0),
               fills=dict(entry_price=1.0, exit_price=1.01, entry_start_ms=1_699_999_700_000))
    return dict(schema=1, track='live-wick6x3', meta=dict(trades=1, pnl=-5.0, note='占位', source='Binance', source_commit='x',
                sources=['internal']), ranking=[], rows=[row, dict(row, symbol='bad')], total=1, offset=0, has_more=False,
                sort='time', direction='desc')


def candles_raw():
    bars = [dict(time=1_700_000_000_000 + i*900_000, open=1.0, high=1.1, low=0.9, close=1.05, volume=10.0) for i in range(5)]
    return dict(schema=1, track='live-wick6x3', trade_id=3, symbol='AAAUSDT', source='Binance', bar='15m', candles=bars,
                weekly_ranges=[dict(symbol='AAAUSDT', week='2023-11-13', state='ready', start_ms=1, end_ms=2, expires_ms=3,
                                    high=1.1, low=0.9, upper=1.22, lower=0.78)],
                backtestTrade=backtest_raw()['rows'][0], note='占位')


def walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def test_paper_projection_is_whitelisted_and_paper_only():
    value = project_paper(paper_raw(), 'live-wick6x3')
    keys = set(walk(value))
    assert value['paper_only'] is True
    assert not keys & {'features', 'rule', 'note', 'levels', 'shadow_closed', 'detail_omitted'}
    assert [a['key'] for a in value['accounts']] == list(TRACKS)
    assert value['accounts'][0]['key'] == 'live-breadth' and value['research'][0]['key'] == 'breadth-ema20'
    live = next(a for a in value['accounts'] if a['key'] == 'live-wick6x3')
    assert live['group'] == 'live' and round(live['return_pct'], 6) == -4.0 and 'open' not in live
    assert live['pnl'] == -40.0 and live['stats']['win_pct'] == 75.0 and live['kind'] == 'fib' and live['title']
    assert next(a for a in value['accounts'] if a['key'] == '60')['label'] == '60m'
    assert value['detail']['open'][0] == dict(id=7, symbol='AAA-USDT-SWAP', side='short', entry=1.2, target=1.1,
                                              stage='open', funded=True, notional=300.0, fill_ms=1_790_000_100_000,
                                              score=0.8, mark=1.25, unrealized_bp=-41.0, remaining_notional=300.0,
                                              stop=1.44, shadow=False, due_ms=None)
    assert [p['symbol'] for p in value['detail']['closed']] == ['BBB-USDT-SWAP']  # shadow and malformed rows dropped
    assert value['equity_basis'] == 'realized'
    assert [s['id'] for s in value['signals']] == [2, 1]


def test_accounts_follow_the_monitor_tracks_new_ones_appear_without_a_release():
    raw = paper_raw()
    raw['tracks']['live-zeta'] = dict(raw['tracks']['spike'])
    raw['tracks']['omega'] = dict(raw['tracks']['spike'])
    raw['tracks']['../bad'] = dict(raw['tracks']['spike'])
    del raw['tracks']['v3lock']
    keys = [a['key'] for a in project_paper(raw, 'live-zeta')['accounts']]
    live = [k for k in TRACKS if k.startswith('live-')]
    assert keys == live + ['live-zeta'] + [k for k in TRACKS if not k.startswith('live-') and k != 'v3lock'] + ['omega']
    with pytest.raises(MonitorUnavailable):
        project_paper(raw, 'v3lock')  # an account the monitor no longer has is an honest 503


def test_allin_refills_are_cash_not_pnl():
    raw = paper_raw()
    raw['tracks']['allin3'] = dict(raw['tracks']['allin3'], equity=1500.0, refills=[dict(equity_before=50.0, equity_after=1000.0)])
    allin = next(a for a in project_paper(raw, 'spike')['accounts'] if a['key'] == 'allin3')
    assert allin['pnl'] == -450.0 and allin['refilled'] is True
    raw['tracks']['allin3']['refills'] = [dict(equity_before=None)]
    assert next(a for a in project_paper(raw, 'spike')['accounts'] if a['key'] == 'allin3')['pnl'] is None


def test_breadth_account_keeps_comparison_rows_hold_progress_and_signal_states():
    value = project_paper(paper_raw(), 'live-breadth')
    account = value['accounts'][0]
    assert account['kind'] == 'breadth' and account['open_count'] == 1
    assert account['breadth_rule'] == dict(count_min=31, count_max=62, bar_minutes=5, observe_minutes=30, hold_days=6,
                                   funded='SOL-USDT-SWAP', shadows=['BTC-USDT-SWAP', 'ETH-USDT-SWAP'], leverage=None, taker_bp=None)
    assert account['hold'] == dict(symbol='SOL-USDT-SWAP', fill_ms=1_790_000_000_000, due_ms=1_790_518_400_000)
    rows = {p['symbol']: p for p in value['detail']['open']}
    assert set(rows) == {'SOL-USDT-SWAP', 'BTC-USDT-SWAP', 'ETH-USDT-SWAP'}  # unfunded non-comparison rows dropped
    assert rows['SOL-USDT-SWAP']['funded'] and not rows['SOL-USDT-SWAP']['shadow']
    assert rows['BTC-USDT-SWAP']['shadow'] and not rows['BTC-USDT-SWAP']['funded']
    assert rows['SOL-USDT-SWAP']['due_ms'] == 1_790_518_400_000
    signals = value['detail']['breadth_signals']
    assert [s['status'] for s in signals] == ['followers', 'taken'] and signals[0]['new_n'] == 3
    assert value['detail']['touches'] == [] and 'source' not in set(walk(value)) and 'error' not in set(walk(value))
    # 2.6.x apps keep decoding the shared touch list unchanged.
    assert [s['id'] for s in value['signals']] == [2, 1]


def test_each_account_lists_its_own_touches():
    raw = paper_raw()
    assert [t['id'] for t in project_paper(raw, 'live-allin3')['detail']['touches']] == [11]
    assert [t['id'] for t in project_paper(raw, 'spike')['detail']['touches']] == [2, 1]


def test_history_pages_keep_breadth_shadows_and_drop_unfunded_fib_rows():
    row = lambda i, taken, note=None, breadth=False: dict(id=i, symbol='AAA-USDT-SWAP', side=1, entry=1.0, stage='closed', taken=taken,
                                                          notional=10.0 if taken else 0.0, note=note, exit_ms=5, pnl=1.0, net_bp=3.0,
                                                          reason='time_6d', breadth=breadth)
    raw = dict(rows=[row(1, 1), row(2, 0, 'comparison'), row(3, 0)], total=9, has_more=True, now_ms=7)
    assert [p['id'] for p in project_paper_history(raw, 'live-breadth', 'closed', True)['positions']] == [1, 2]
    fib = project_paper_history(raw, 'spike', 'closed', False)
    assert [p['id'] for p in fib['positions']] == [1] and fib['total'] == 9 and fib['has_more'] is True
    events = dict(rows=[dict(id=4, symbol='SOL-USDT-SWAP', status='busy', candle_ms=3, n=40, breadth=True)], total=1)
    assert project_paper_history(events, 'live-breadth', 'events', True)['breadth_signals'][0]['status'] == 'busy'


def test_downsample_keeps_extremes_and_ends():
    points = [[i, 1000 + (i % 50) * (-1 if i % 2 else 1), 0.0, 0.0] for i in range(5000)]
    out = downsample(points, 200)
    assert len(out) <= 200 and out[0] == points[0] and out[-1] == points[-1]
    assert min(p[1] for p in out) == min(p[1] for p in points) and max(p[1] for p in out) == max(p[1] for p in points)


def test_breadth_ema20_booklet_rejects_other_ledgers():
    with pytest.raises(ValueError):
        project_breadth_ema20(dict(schema_version='other'))


def test_alert_and_cloud_projections_drop_receipts_text_and_internals():
    alerts = project_alerts(alerts_raw())
    assert not set(walk(alerts)) & {'telegram', 'bark', 'text', 'error', 'channels', 'ack_ms'}
    assert alerts['alerts'][0]['symbols'] == ['AAA-USDT-SWAP'] and alerts['alerts'][0]['acknowledged'] is True
    assert [a['id'] for a in alerts['alerts']] == [3]  # test alarms are dropped
    cloud = project_cloud(cloud_raw())
    assert not set(walk(cloud)) & {'notifications', 'eligibility_policy', 'error_samples', 'channels', 'state_extra'}
    assert cloud['reference_only'] is True and cloud['items'][0]['side'] == 'long'
    assert cloud['items'][0]['range'] == dict(high=1.0, low=0.9, upper=1.06, lower=0.84)
    with pytest.raises(MonitorUnavailable):
        project_cloud(dict(cloud_raw(), schema=2))


def test_notification_feed_maps_push_outcomes_without_receipts():
    value = project_notifications(fib_raw(), alerts_raw())
    assert not set(walk(value)) & {'message_id', 'error', 'server_timestamp', 'recent', 'text', 'ack_ms', 'due_ms'}
    fib = {e['symbol']: e for e in value['events'] if e['kind'] == 'fib'}
    assert {k: (e['status'], e['pushed']) for k, e in fib.items()} == {
        'AAA-USDT-SWAP': ('pushed', True), 'BBB-USDT-SWAP': ('below_threshold', False),
        'CCC-USDT-SWAP': ('not_sent', False), 'DDD-USDT-SWAP': ('history', False)}
    assert fib['DDD-USDT-SWAP']['at_ms'] == 4 and fib['AAA-USDT-SWAP']['at_ms'] == 5
    alarm = next(e for e in value['events'] if e['kind'] == 'alarm')
    assert alarm['id'] == 'alarm:3' and alarm['pushed'] is True and alarm['acknowledged'] is True
    assert value['channels'] == dict(fib=dict(bark=True, bark_configured=True, bark_sent=7, telegram=True, min_score=.4),
                                     alarm=dict(bark=False, telegram=True, threshold=25, window_minutes=5))


@pytest.fixture
def client(tmp_path):
    book = Book(Path(tmp_path) / 'paper.sqlite3', Clock())
    market(book)
    calls = []
    def paper_with_week():
        raw = paper_raw()
        for s in raw['signals']:
            s['week'] = '2026-11-02'
        return raw
    payloads = {'/api/monday-reclaim-paper': paper_with_week, '/api/spike-alerts': alerts_raw, '/api/cloud-signals': cloud_raw,
                '/api/monday-fib': fib_raw, '/api/monday-reclaim-paper/history': history_raw,
                '/static/strategy-booklet/research.json': research_raw,
                '/static/strategy-booklet/live-wick6x3-history.json': booklet_history_raw,
                '/api/strategy-backtest': backtest_raw, '/api/strategy-backtest/candles': candles_raw}

    def fetch(path, body=None):
        calls.append((path, body) if body is not None else path)
        if path == '/api/monday-fib/bark':
            return {}
        return payloads[path.split('?')[0]]()

    app = create_app(book, TOKEN, market_enabled=False, monitor_feed=MonitorFeed(fetch, ttl=60))
    return TestClient(app), book, calls, app


def test_monitor_routes_need_token_are_get_only_and_never_touch_the_ledger(client):
    http, book, calls, app = client
    before = book.snapshot()
    for path in ('/v1/monitor/paper', '/v1/monitor/alerts', '/v1/monitor/cloud', '/v1/monitor/notifications',
                 '/v1/monitor/touch-scores', '/v1/monitor/paper/history?track=live-wick6x3&tab=closed',
                 '/v1/monitor/booklet?track=live-wick6x3', '/v1/monitor/backtest?track=live-wick6x3',
                 '/v1/monitor/backtest/candles?track=live-wick6x3&trade=3&period=15m'):
        assert http.get(path).status_code == 401
        assert http.get(path, headers=AUTH).status_code == 200
        assert http.post(path, headers=AUTH, json={}).status_code == 405
    monitor_routes = [r for r in app.routes if getattr(r, 'path', '').startswith('/v1/monitor')]
    # Read-only except the single owner-approved 1.618 Bark switch.
    assert {(r.path, frozenset(r.methods)) for r in monitor_routes if r.methods != {'GET'}} == {
        ('/v1/monitor/fib-bark', frozenset({'POST'}))}
    assert http.get('/v1/monitor/paper?track=../x', headers=AUTH).status_code == 422
    assert http.get('/v1/monitor/paper?track=unknown', headers=AUTH).status_code == 503
    assert http.get('/v1/monitor/cloud?side=short', headers=AUTH).status_code == 200
    assert any(isinstance(c, str) and 'side=-1' in c and 'scope=live' in c for c in calls)
    # Upstream reads are cached briefly and never mutate the discipline ledger.
    count = len(calls)
    http.get('/v1/monitor/alerts', headers=AUTH)
    assert len(calls) == count
    after = book.snapshot()
    assert before['positions'] == after['positions'] and before['pending'] == after['pending']
    assert before['daily'] == after['daily']


def test_bark_switch_forwards_only_a_boolean_and_needs_the_token(client):
    http, book, calls, _ = client
    before = book.snapshot()
    assert http.post('/v1/monitor/fib-bark', json={'enabled': False}).status_code == 401
    for bad in ({'enabled': 'no'}, {'enabled': False, 'channel': 'telegram'}, {}):
        assert http.post('/v1/monitor/fib-bark', headers=AUTH, json=bad).status_code == 422
    response = http.post('/v1/monitor/fib-bark', headers=AUTH, json={'enabled': False})
    assert response.status_code == 200 and 'channels' in response.json()
    assert ('/api/monday-fib/bark', {'enabled': False}) in calls
    after = book.snapshot()
    assert before['positions'] == after['positions'] and before['pending'] == after['pending']


def test_unreachable_monitor_is_an_honest_503(tmp_path):
    book = Book(Path(tmp_path) / 'paper.sqlite3', Clock())

    def down(_path):
        raise OSError('connection refused')

    http = TestClient(create_app(book, TOKEN, market_enabled=False, monitor_feed=MonitorFeed(down)))
    response = http.get('/v1/monitor/alerts', headers=AUTH)
    assert response.status_code == 503 and response.json()['detail'] == '监控服务暂未连接，请稍后重试'


def test_touch_scores_cover_this_week_per_touch_without_model_internals(client):
    """2.6.0 item 1: one score per (symbol, edge) of this NY week, recent page first, older pages after."""
    http, book, calls, _ = client
    before = book.snapshot()
    body = http.get('/v1/monitor/touch-scores', headers=AUTH).json()
    assert body['week'] == '2026-11-02'
    keyed = {(s['symbol'], s['side']): s for s in body['scores']}
    # short (-1) touches the upper 1.618, long (+1) the lower; the recent page wins over an older duplicate.
    assert set(keyed) == {('AAA-USDT-SWAP', 'upper'), ('CCC-USDT-SWAP', 'lower'), ('EEE-USDT-SWAP', 'upper')}
    assert keyed[('AAA-USDT-SWAP', 'upper')]['score'] == .8 and keyed[('EEE-USDT-SWAP', 'upper')]['score'] == .55
    assert not {'features', 'error', 'levels', 'taken', 'id'} & set(walk(body))
    assert any(isinstance(c, str) and c.startswith('/api/monday-reclaim-paper/history?') and 'offset=0' in c for c in calls)
    # Older pages are cached between refreshes; the ledger is never touched.
    count = sum(isinstance(c, str) and 'history' in c for c in calls)
    http.app.state.monitor._cache.clear()
    http.get('/v1/monitor/touch-scores', headers=AUTH)
    assert sum(isinstance(c, str) and 'history' in c for c in calls) == count
    after = book.snapshot()
    assert before['positions'] == after['positions'] and before['pending'] == after['pending']


def test_touch_scores_projection_drops_other_weeks_and_bad_rows():
    rows = [dict(week='2026-11-02', symbol='AAA-USDT-SWAP', side=1, score=.4, status='scored', candle_ms=5),
            dict(week='2026-10-26', symbol='BBB-USDT-SWAP', side=1, score=.9, status='scored', candle_ms=4),
            dict(week='2026-11-02', symbol='bad', side=1, score=.9, status='scored'),
            dict(week='2026-11-02', symbol='CCC-USDT-SWAP', side=-1, score=None, status='pending'), 'junk']
    out = project_touch_scores(rows)
    assert out['week'] == '2026-11-02' and [s['symbol'] for s in out['scores']] == ['AAA-USDT-SWAP']
    assert out['scores'][0]['side'] == 'lower'


def test_strategy_frame_routes_project_booklet_backtest_and_candles_without_internals(client):
    http, book, calls, _ = client
    before = book.snapshot()
    booklet = http.get('/v1/monitor/booklet?track=live-wick6x3', headers=AUTH).json()
    assert booklet['key'] == 'live-wick6x3' and booklet['paper'] is True and len(booklet['flow']) == 4
    assert booklet['facts'][1]['label'] == '入场分门槛'
    assert len(booklet['history']['points']) <= 480 and booklet['history']['points'][0][1] == 1000.0
    assert booklet['research']['table']['rows'] == [['2025', 10]] and booklet['research']['sections'][0]['title'] == '分节'
    assert not {'sources', 'source_commit', 'commit', 'path', 'integrity'} & set(walk(booklet))
    trades = http.get('/v1/monitor/backtest?track=live-wick6x3&sort=pnl', headers=AUTH).json()
    assert [r['coin'] for r in trades['rows']] == ['AAA'] and trades['rows'][0]['side'] == 'short'
    assert trades['rows'][0]['exit_reason'] is None and trades['rows'][0]['score'] == 0.61
    assert not {'source_commit', 'sources', 'entry_rules', 'ranking'} & set(walk(trades))
    assert any(isinstance(c, str) and c.startswith('/api/strategy-backtest?') and 'sort=pnl' in c and 'limit=50' in c for c in calls)
    chart = http.get('/v1/monitor/backtest/candles?track=live-wick6x3&trade=3&period=15m', headers=AUTH).json()
    assert len(chart['candles']) == 5 and chart['trade']['entry_price'] == 1.0 and chart['weekly_ranges'][0]['upper'] == 1.22
    assert http.get('/v1/monitor/backtest/candles?track=live-wick6x3&trade=3&period=1m', headers=AUTH).status_code == 422
    assert http.get('/v1/monitor/booklet?track=../x', headers=AUTH).status_code == 422
    assert http.get('/v1/monitor/booklet?track=nope', headers=AUTH).status_code == 503
    history = http.get('/v1/monitor/paper/history?track=live-wick6x3&tab=events', headers=AUTH).json()
    assert history['tab'] == 'events' and history['positions'] == []
    assert any(isinstance(c, str) and 'history?' in c and 'tab=events' in c and 'scope=all' in c for c in calls)
    after = book.snapshot()
    assert before['positions'] == after['positions'] and before['pending'] == after['pending']


def test_cloud_forward_projection_keeps_legacy_realized_fields_and_adds_marked_values():
    payload = cloud_forward_raw()
    calls = []
    def fetch(path, body=None):
        calls.append(path)
        if path == '/static/strategy-booklet/live-cloud-v17-forward.json':
            return payload
        raise KeyError(path)

    feed = MonitorFeed(fetch, ttl=60)
    snapshot = feed.paper('live-cloud-v17')
    account = snapshot['accounts'][0]
    assert snapshot['equity_basis'] == 'realized'
    assert (account['equity'], account['pnl'], account['return_pct']) == (1005.0, 5.0, .5)
    assert (account['marked_equity'], account['marked_pnl'], account['unrealized_pnl']) == (1025.0, 25.0, 20.0)
    assert account['open_count'] == 1 and snapshot['detail']['open'][0]['unrealized_bp'] == 100.0
    assert snapshot['detail']['closed'][0]['reason'] == 'take_profit'
    assert snapshot['detail']['cloud_events'][0]['status'] == 'opened'
    assert not set(walk(snapshot)) & {'private', 'private_or_order_api', 'recent_fills', 'secret', 'rule', 'health'}
    assert calls == ['/static/strategy-booklet/live-cloud-v17-forward.json']


def test_missing_cloud_asset_does_not_break_existing_paper_accounts():
    def fetch(path, body=None):
        if path.startswith('/api/monday-reclaim-paper?'):
            return paper_raw()
        if path == '/static/strategy-booklet/live-cloud-v17-forward.json':
            raise OSError('404')
        raise KeyError(path)

    snapshot = MonitorFeed(fetch, ttl=60).paper('live-wick6x3')
    assert snapshot['detail']['key'] == 'live-wick6x3'
    assert snapshot['equity_basis'] == 'realized'
    assert not any(a['key'] == 'live-cloud-v17' for a in snapshot['accounts'])


def test_cloud_booklet_is_hourly_marked_and_legacy_view_withholds_misleading_curve():
    history = cloud_history_raw()
    guide, overview = cloud_guide_raw(), cloud_overview_raw()
    current = project_cloud_booklet(guide, overview, history)
    assert current['history']['drawdown_basis'] == 'hourly_marked'
    assert current['history']['summary']['max_drawdown_pct'] == 57.8293
    assert current['history']['summary']['max_drawdown_bound_pct'] == 58.54
    assert max(len(current['history']['points']), 0) <= 480
    assert min(p[2] for p in current['history']['points']) == -57.829
    assert next(f for f in current['facts'] if f['label'] == '金额盈亏比')['value'] == '5.35'
    assert current['research']['table']['headers'] == ['检查', '做多', '做空']
    assert current['research']['risk'] == guide['risk'] and current['research']['sources'] == guide['sources']

    old = project_cloud_booklet(guide, overview, history, include_hourly_curve=False)
    assert old['history'] is None and '升级' in old['history_error'] and '12H' in old['history_error']
    assert old['research']['table']['headers'] == ['历史指标', '值']
    assert any(section['title'] == '入场条件 · 多空方向' for section in old['research']['sections'])
    assert old['flow'] == current['flow']


def test_cloud_backtest_and_12h_chart_keep_original_timestamps_columns_and_price_levels():
    manifest = cloud_manifest_raw()
    overview = cloud_overview_raw()
    page = project_cloud_backtest(manifest, 'live-cloud-v17', overview=overview)
    assert page['total'] == 1 and page['rows'][0]['id'] == 1
    assert page['rows'][0]['side'] == 'long' and page['rows'][0]['exit_reason_label'] == '止盈'
    assert '58.54%' in page['meta']['limitation']

    event, symbol = cloud_event_raw(), cloud_symbol_raw()
    chart = project_cloud_candles(event, symbol, manifest['records'][0], 'live-cloud-v17', 1)
    assert chart['bar'] == '12H' and len(chart['candles']) == 3
    assert chart['candles'][-1]['time'] == event['window']['endMs']  # endMs is inclusive
    assert chart['candles'][1]['ema20'] == 10.3
    assert chart['expected_bars'] == chart['available_bars'] == 3 and chart['missing_bars'] == 0
    assert [x['title'] for x in chart['references']] == ['入场', '退出', '固定止损', '2R', '信号侧周一轨道']
    assert [(x['action'], x['label'], x['share']) for x in chart['execution_legs']] == [
        ('entry', '开仓', 1.0), ('take_profit', '止盈', .5), ('ema_flip', '云带反转', .5)]
    assert [x['time_ms'] for x in chart['execution_legs']] == [
        event['entryMs'], event['window']['startMs'] + 43_200_000, event['window']['endMs']]

    malformed = dict(symbol, columns=list(symbol['columns']))
    malformed['columns'][5] = 'ema21'
    with pytest.raises(BookUnavailable, match='字段'):
        project_cloud_candles(event, malformed, manifest['records'][0], 'live-cloud-v17', 1)


def test_alarm_projection_keeps_every_coin_and_the_trigger_count():
    """Owner 2026-11-09 (“有多少显示多少”): the app gets the complete alarm, not the first 80 coins."""
    coins = [f'C{k}-USDT-SWAP' for k in range(117)]
    raw = alerts_raw()
    raw['alerts'] = [dict(id=18, side='lower', first_ms=1, last_ms=2, n=117, symbols=coins, trigger_n=25, complete=True,
                          active=False, ack_ms=3, test=0)]
    alarm = project_alerts(raw)['alerts'][0]
    assert (alarm['n'], len(alarm['symbols']), alarm['trigger_n'], alarm['complete']) == (117, 117, 25, True)
    raw['alerts'][0].pop('trigger_n'); raw['alerts'][0].pop('complete')
    alarm = project_alerts(raw)['alerts'][0]
    assert (alarm['trigger_n'], alarm['complete']) == (117, True), 'older monitor: the trigger count defaults to n'
