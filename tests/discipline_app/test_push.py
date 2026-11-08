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
    assert http.post('/v1/push/activity', headers=AUTH, json=dict(token=DEVICE, environment='development', position_id='p1')).json()['activities'] == 1
    assert http.post('/v1/push/device/remove', headers=AUTH, json=dict(token=DEVICE)).json() == dict(configured=False, devices=0, activities=0)
