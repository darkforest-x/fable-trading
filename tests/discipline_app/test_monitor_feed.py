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
                                              project_touch_scores)
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
    return dict(now_ms=1_790_000_200_000, tracks=tracks, signals=[
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
    live = next(a for a in value['accounts'] if a['key'] == 'live-wick6x3')
    assert live['group'] == 'live' and round(live['return_pct'], 6) == -4.0 and 'open' not in live
    assert next(a for a in value['accounts'] if a['key'] == '60')['label'] == '60m'
    assert value['detail']['open'][0] == dict(id=7, symbol='AAA-USDT-SWAP', side='short', entry=1.2, target=1.1,
                                              stage='open', funded=True, notional=300.0, fill_ms=1_790_000_100_000,
                                              score=0.8, mark=1.25, unrealized_bp=-41.0, remaining_notional=300.0,
                                              stop=1.44)
    assert [p['symbol'] for p in value['detail']['closed']] == ['BBB-USDT-SWAP']  # shadow and malformed rows dropped
    assert value['equity_basis'] == 'realized'
    assert [s['id'] for s in value['signals']] == [2, 1]


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
                '/api/monday-fib': fib_raw, '/api/monday-reclaim-paper/history': history_raw}

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
                 '/v1/monitor/touch-scores'):
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
