"""Native push (2.6.0): idle without the owner's APNs key, JWT never on a command line, no backlog replay.

Synthetic events and a throwaway P-256 key generated in a temp directory; nothing reaches Apple.
"""
import base64
import json
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.push import Apns, PushService, activity_state, alert_for, der_to_raw
from yoyo.discipline_app.notification_journal import MONTH_TOUCH_TYPE, valid_month_touch
from test_discipline_service import Clock, market

TOKEN = 'push-fixture-token-not-a-secret-0123456789abc'
AUTH = {'Authorization': f'Bearer {TOKEN}'}
DEVICE = 'ab' * 32


def keyed(directory):
    subprocess.run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out',
                    str(directory / 'AuthKey_TESTKEY001.p8')], check=True, capture_output=True)
    (directory / 'apns.json').write_text(json.dumps(dict(key_id='TESTKEY001', team_id='L7RP7HZT53', key_file='AuthKey_TESTKEY001.p8')))


class Recorder:
    """Stands in for curl; signs with the real openssl."""
    def __init__(self, status=200, reason=''):
        self.calls, self.status, self.reason = [], status, reason

    def __call__(self, args, **kwargs):
        if args[0] == 'openssl':
            return subprocess.run(args, **kwargs)
        header_file = Path(args[args.index('-H') + 1][1:])
        self.calls.append(dict(args=args, headers=header_file.read_text(), body=json.loads(kwargs['input'])))
        out = (json.dumps(dict(reason=self.reason)) if self.reason else '') + f'\n{self.status}'
        return subprocess.CompletedProcess(args, 0, out.encode(), b'')


def test_der_signature_converts_to_64_raw_bytes(tmp_path):
    keyed(tmp_path)
    der = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(tmp_path / 'AuthKey_TESTKEY001.p8')],
                         input=b'abc', capture_output=True, check=True).stdout
    assert len(der_to_raw(der)) == 64
    with pytest.raises(ValueError):
        der_to_raw(b'\x31\x00')


def test_unconfigured_push_is_idle_and_jwt_stays_off_the_command_line(tmp_path):
    run = Recorder()
    apns = Apns(tmp_path, run=run)
    assert not apns.configured
    keyed(tmp_path)
    assert apns.configured
    jwt = apns.token()
    head, claims, signature = jwt.split('.')
    assert json.loads(base64.urlsafe_b64decode(head + '==')) == dict(alg='ES256', kid='TESTKEY001')
    assert json.loads(base64.urlsafe_b64decode(claims + '=='))['iss'] == 'L7RP7HZT53'
    assert len(base64.urlsafe_b64decode(signature + '==')) == 64
    assert apns.token() == jwt  # reused within 40 minutes
    status, _ = apns.send(DEVICE, dict(aps=dict(alert='x')))
    call = run.calls[0]
    assert status == 200 and jwt in call['headers'] and not any(jwt in a for a in call['args'])
    assert call['args'][-1].startswith('https://api.sandbox.push.apple.com/3/device/')
    assert not list(tmp_path.glob('.h-*'))  # header file removed


class Monitor:
    def __init__(self):
        self.events = []

    def notifications(self):
        return dict(events=self.events)

    def touch_scores(self):
        return dict(scores=[dict(symbol='AAA-USDT-SWAP', side='upper', score=.82)])


def month_touch(identity, *, source='live', created_ms=1_700_000_000_000, occurred_ms=1_700_000_000_000,
                touch_confirmed=True, candle_confirmed=False, symbol='AAA-USDT-SWAP', side='upper'):
    return dict(id=identity, event_id=f'2026-11|{symbol}|{side}', type=MONTH_TOUCH_TYPE,
                month='2026-11', symbol=symbol, side=side, level=100.0, price=101.0,
                candle_extreme=101.0, occurred_ms=occurred_ms, candle_ms=occurred_ms - 1000,
                created_ms=created_ms, expires_ms=1_700_600_000_000, fresh_until_ms=occurred_ms + 90_000,
                source=source, touch_confirmed=touch_confirmed, candle_confirmed=candle_confirmed)


class MonthMonitor(Monitor):
    def __init__(self):
        super().__init__()
        self.touches = []
        self.month_bark_enabled = False

    def month_first_week_1618(self):
        return dict(schema='month_first_week_1618.v1', now_ms=1_700_000_000_000,
                    timezone='America/New_York', month='2026-11',
                    window=dict(month='2026-11', start_ms=1, ready_ms=2, expires_ms=3, complete=True),
                    coverage=dict(universe_size=1, ready_symbols=1, incomplete_symbols=0, pending_symbols=0,
                                  expected_1h_bars=12, source='okx'),
                    stream=dict(connected=True, last_message_ms=1, error=None),
                    worker=dict(alive=True, last_cycle_ms=1, error=None, symbol_count=1, ready_count=1,
                                incomplete_count=0, pending_count=0),
                    items=[], touches=list(self.touches),
                    channels=dict(bark=dict(enabled=self.month_bark_enabled, configured=True,
                                             counts=dict(pending=0, sent=int(self.month_bark_enabled), failed=0,
                                                         unknown=0, skipped=0))))

    def month_first_week_1618_events(self, after_id=0, limit=500):
        return [event for event in self.touches if event['id'] > after_id][:limit]

    def set_month_first_week_bark(self, enabled):
        self.month_bark_enabled = enabled
        return self.month_first_week_1618()


class MutableClock:
    def __init__(self, value): self.value = value
    def __call__(self): return self.value


def test_sender_starts_from_now_sends_fresh_events_and_drops_dead_devices(tmp_path):
    keyed(tmp_path)
    run, monitor = Recorder(), Monitor()
    service = PushService(tmp_path, monitor, lambda _: None, apns=Apns(tmp_path, run=run))
    service.register(DEVICE, 'development')
    service.register('cd' * 32, 'production', fib=False)
    monitor.events = [dict(id='fib:old', kind='fib', symbol='OLD-USDT-SWAP', side='lower', at_ms=1000, pushed=True)]
    service.tick()
    assert run.calls == []  # first run only moves the cursor
    monitor.events = [dict(id='fib:new', kind='fib', symbol='AAA-USDT-SWAP', side='upper', at_ms=2000, pushed=True),
                      dict(id='fib:quiet', kind='fib', symbol='BBB-USDT-SWAP', side='upper', at_ms=2500, pushed=False),
                      dict(id='alarm:9', kind='alarm', side='lower', n=27, symbols=['BTC-USDT-SWAP'], at_ms=3000, pushed=True)
                      ] + monitor.events
    service.tick()
    sent = [(c['args'][-1].rsplit('/', 1)[1], c['body']['kind']) for c in run.calls]
    # The fib-off phone gets only the alarm; the unpushed touch is never sent.
    assert sent == [(DEVICE, 'fib'), (DEVICE, 'alarm'), ('cd' * 32, 'alarm')]
    assert run.calls[0]['body']['aps']['alert']['title'] == '1.618 上沿触价 · AAA'
    assert '评分 0.82' in run.calls[0]['body']['aps']['alert']['body']
    run.calls.clear()
    service.tick()
    assert run.calls == []  # nothing new
    run.status, run.reason = 410, 'Unregistered'
    monitor.events.insert(0, dict(id='alarm:10', kind='alarm', side='upper', n=30, symbols=[], at_ms=4000, pushed=True))
    service.tick()
    assert service.status()['devices'] == 0


def test_activity_state_and_routes_need_the_token(tmp_path):
    snapshot = dict(positions=[dict(id='p1', symbol='ETH-USDT-SWAP', entry='2000', stop='1960', pnl='18.6', margin='200')],
                    quotes={'ETH-USDT-SWAP': dict(last='2007.4')})
    state = activity_state(snapshot, 'p1')
    assert state['mark'] == 2007.4 and state['pnl'] == 18.6 and abs(state['roe'] - 9.3) < 1e-9 and state['stop'] == 1960
    assert activity_state(snapshot, 'gone') is None
    assert alert_for(dict(kind='alarm', side='upper', n=3, symbols=['BTC-USDT-SWAP'], at_ms=0), {})['title'] == '全市场上插报警 · 3 个币'

    book = Book(Path(tmp_path) / 'paper.sqlite3', Clock())
    market(book)
    http = TestClient(create_app(book, TOKEN, market_enabled=False, monitor_feed=Monitor()))
    assert http.get('/v1/push/status').status_code == 401
    assert http.post('/v1/push/device', json=dict(token=DEVICE, environment='development')).status_code == 401
    assert http.post('/v1/push/device', headers=AUTH, json=dict(token='xyz', environment='development')).status_code == 422
    assert http.post('/v1/push/device', headers=AUTH, json=dict(token=DEVICE, environment='staging')).status_code == 422
    body = http.post('/v1/push/device', headers=AUTH, json=dict(token=DEVICE, environment='development', fib=False)).json()
    assert body == dict(configured=False, devices=1, activities=0)
    prefs = http.post('/v1/push/preferences', headers=AUTH,
                      json=dict(token=DEVICE, fib=False, alarm=True, month_first_week_touch=False)).json()
    assert prefs['preferences'] == dict(fib=False, alarm=True, month_first_week_touch=False)
    assert http.post('/v1/push/preferences', headers=AUTH,
                     json=dict(token='cd' * 32, month_first_week_touch=False)).status_code == 409
    assert http.post('/v1/push/activity', headers=AUTH, json=dict(token=DEVICE, environment='development', position_id='p1')).json()['activities'] == 1
    assert http.post('/v1/push/device/remove', headers=AUTH, json=dict(token=DEVICE)).json() == dict(configured=False, devices=0, activities=0)


def test_month_first_week_snapshot_and_notifications_are_additive_and_legacy_feed_stays_unchanged(tmp_path):
    source = MonthMonitor()
    source.events = [dict(id=f'weekly-{i}', kind='fib', at_ms=1_700_000_000_001 + i) for i in range(120)]
    source.touches = [dict(month_touch(1, source='history'),
                           channels=dict(bark=dict(status='sent', error=None, attempts=1)))]
    book = Book(Path(tmp_path) / 'paper.sqlite3', Clock())
    market(book)
    http = TestClient(create_app(book, TOKEN, market_enabled=False, monitor_feed=source))
    headers = AUTH
    assert http.get('/v1/monitor/month-first-week-1618', headers=headers).status_code == 200
    snapshot = http.get('/v1/monitor/month-first-week-1618', headers=headers).json()
    assert snapshot['schema'] == 'month_first_week_1618.v1'
    assert 'coverage' in snapshot and 'window' in snapshot and snapshot['touches'][0]['source'] == 'history'
    legacy = http.get('/v1/monitor/notifications', headers=headers).json()
    assert all(event.get('kind') != MONTH_TOUCH_TYPE for event in legacy['events'])
    unified = http.get('/v1/notifications', headers=headers).json()
    assert any(event.get('kind') == MONTH_TOUCH_TYPE and event['source'] == 'history' for event in unified['events'])
    event = next(event for event in unified['events'] if event.get('kind') == MONTH_TOUCH_TYPE)
    assert event['apns_status'] == 'not_subscribed' and event['channels']['bark']['status'] == 'sent'


def test_month_first_week_bark_switch_is_authenticated_and_independent(tmp_path):
    source = MonthMonitor()
    book = Book(Path(tmp_path) / 'paper.sqlite3', Clock())
    market(book)
    http = TestClient(create_app(book, TOKEN, market_enabled=False, monitor_feed=source))
    path = '/v1/monitor/month-first-week-1618/bark'
    assert http.post(path, json=dict(enabled=True)).status_code == 401
    response = http.post(path, headers=AUTH, json=dict(enabled=True))
    assert response.status_code == 200
    monthly = response.json()['channels']['month_first_week_touch']
    assert source.month_bark_enabled is True
    assert monthly['bark'] is True and monthly['bark_configured'] is True and monthly['bark_sent'] == 1
    assert monthly['apns_configured'] is False
    assert http.get('/v1/monitor/notifications', headers=AUTH).status_code == 200  # old phone route remains


def test_month_touch_apns_only_new_live_fresh_touch_confirmed_rows_send_once(tmp_path):
    now = 1_700_000_000.0
    clock = MutableClock(now)
    keyed(tmp_path)
    recorder, monitor = Recorder(), MonthMonitor()
    service = PushService(tmp_path, monitor, lambda _: None, apns=Apns(tmp_path, run=recorder), clock=clock)
    service.register(DEVICE, 'development', month_first_week_touch=True)
    # The initial snapshot seeds a cursor and never sends its history/backlog.
    monitor.touches = [month_touch(1, source='live', created_ms=int(now * 1000) - 1000,
                                   occurred_ms=int(now * 1000) - 1000)]
    service.tick()
    assert recorder.calls == []

    # A live touch is generated after startup and is pushable even while its 1m candle is open.
    clock.value += 1
    event = month_touch(2, created_ms=int(clock.value * 1000), occurred_ms=int(clock.value * 1000),
                        candle_confirmed=False, symbol='BBB-USDT-SWAP')
    monitor.touches.append(event)
    service.tick()
    assert len(recorder.calls) == 1
    body = recorder.calls[0]['body']
    assert body['kind'] == body['type'] == MONTH_TOUCH_TYPE
    assert body['event_id'] == event['event_id'] and body['symbol'] == event['symbol']
    assert body['aps']['alert']['title'] == '月首周 1.618 触价 · BBB'
    assert service.month_journal.delivery_status(event['event_id'])['apns'] == 'sent'
    assert service.month_journal.delivery_statuses([event['event_id'], 'missing']) == {
        event['event_id']: {'apns': 'sent', 'apns_counts': {'sent': 1}},
        'missing': {'apns': 'not_subscribed', 'apns_counts': {}}}
    service.tick()
    assert len(recorder.calls) == 1


def test_month_touch_apns_preferences_expiry_and_ambiguous_delivery_do_not_replay(tmp_path):
    now = 1_700_000_000.0
    clock = MutableClock(now)
    monitor = MonthMonitor()
    old = month_touch(1, source='live', created_ms=int(now * 1000) - 1_000, occurred_ms=int(now * 1000) - 1_000)
    assert valid_month_touch(old)
    assert valid_month_touch({**old, 'source': 'history'})  # valid fact, but source history cannot enqueue
    assert not valid_month_touch({**old, 'touch_confirmed': False})

    # Disable the phone before the next source row is ingested; no monthly delivery is queued.
    keyed(tmp_path)
    recorder = Recorder(status=0)
    service = PushService(tmp_path, monitor, lambda _: None, apns=Apns(tmp_path, run=recorder), clock=clock)
    service.register(DEVICE, 'development', month_first_week_touch=True)
    monitor.touches = [old]
    service.tick()  # first snapshot watermark
    service.set_preferences(DEVICE, fib=True, alarm=True, month_first_week_touch=False)
    clock.value += 1
    disabled = month_touch(2, created_ms=int(clock.value * 1000), occurred_ms=int(clock.value * 1000),
                           symbol='BBB-USDT-SWAP')
    monitor.touches.append(disabled)
    service.tick()
    assert recorder.calls == []
    assert service.month_journal.delivery_status(disabled['event_id'])['apns'] == 'not_subscribed'

    # A newly enabled subscription sends a new touch once; an ambiguous result becomes terminal unknown.
    service.set_preferences(DEVICE, fib=True, alarm=True, month_first_week_touch=True)
    clock.value += 1
    uncertain = month_touch(3, created_ms=int(clock.value * 1000), occurred_ms=int(clock.value * 1000),
                            symbol='CCC-USDT-SWAP')
    monitor.touches.append(uncertain)
    service.tick()
    assert len(recorder.calls) == 1
    assert service.month_journal.delivery_status(uncertain['event_id'])['apns'] == 'unknown'
    service.tick()
    assert len(recorder.calls) == 1
