"""Native iPhone push for the discipline app (owner 2026-11-08 “除了3全做”, item 4).

What it sends: exactly the events the in-app banner already raises (``/v1/monitor/notifications``):
1.618 touches whose Telegram/Bark push went out (``pushed``) and non-test market alarms, plus Live
Activity updates for an open position the phone chose to follow (mark, P&L, stop). Nothing here
places, changes or closes orders; it only reads the monitor projection and the ledger snapshot.

Disabled until the owner adds an APNs auth key (Apple Developer → Keys → Apple Push Notifications
service). Creating that key is the owner's step; it is never committed. Layout, under the service
runtime directory (outside git)::

    push/apns.json   {"key_id": "ABC123DEFG", "team_id": "L7RP7HZT53", "key_file": "AuthKey_ABC123DEFG.p8"}
    push/AuthKey_ABC123DEFG.p8   (chmod 600)
    push/devices.json            device + Live Activity tokens registered by the app

Signing uses the system ``openssl`` (ES256 JWT, reused for 40 minutes; APNs accepts 20–60) and
delivery the system ``curl --http2``, so the locked service environment gains no dependency. The
JWT travels in a 0600 header file, never on a command line.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

TOPIC = 'com.spike.discipline.native'
DEVICE = re.compile(r'[0-9a-f]{64,200}')
HOSTS = {'development': 'https://api.sandbox.push.apple.com', 'production': 'https://api.push.apple.com'}
JWT_SECONDS = 40 * 60
ACTIVITY_EVERY = 30


class PushUnavailable(ValueError):
    """Push is not configured or the request was invalid."""


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def der_to_raw(der):
    """ECDSA DER signature (openssl output) → JOSE raw r||s, 32 bytes each."""
    if len(der) < 8 or der[0] != 0x30:
        raise ValueError('bad signature')
    index = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7f)
    parts = []
    for _ in range(2):
        if der[index] != 0x02:
            raise ValueError('bad signature')
        length = der[index + 1]
        value = der[index + 2:index + 2 + length].lstrip(b'\0')
        if len(value) > 32:
            raise ValueError('bad signature')
        parts.append(value.rjust(32, b'\0'))
        index += 2 + length
    return parts[0] + parts[1]


class Apns:
    def __init__(self, directory, run=subprocess.run, clock=time.time):
        self.directory, self._run, self._clock = Path(directory), run, clock
        self._jwt = (0, None)
        self._lock = threading.Lock()

    def _config(self):
        try:
            config = json.loads((self.directory / 'apns.json').read_text())
            key = self.directory / Path(str(config['key_file'])).name
            if not (re.fullmatch(r'[A-Z0-9]{10}', str(config['key_id'])) and re.fullmatch(r'[A-Z0-9]{10}', str(config['team_id']))
                    and key.is_file()):
                return None
            return dict(key_id=config['key_id'], team_id=config['team_id'], key=key)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @property
    def configured(self):
        return self._config() is not None

    def token(self):
        with self._lock:
            made, value = self._jwt
            if value and self._clock() - made < JWT_SECONDS:
                return value
            config = self._config()
            if not config:
                raise PushUnavailable('推送密钥未配置')
            now = int(self._clock())
            head = _b64(json.dumps(dict(alg='ES256', kid=config['key_id']), separators=(',', ':')).encode())
            body = _b64(json.dumps(dict(iss=config['team_id'], iat=now), separators=(',', ':')).encode())
            signing = f'{head}.{body}'.encode()
            result = self._run(['openssl', 'dgst', '-sha256', '-sign', str(config['key'])], input=signing,
                               capture_output=True, timeout=10)
            if result.returncode != 0:
                raise PushUnavailable('推送签名失败')
            value = f'{head}.{body}.{_b64(der_to_raw(result.stdout))}'
            self._jwt = (now, value)
            return value

    def send(self, device, payload, environment='development', push_type='alert', topic=TOPIC, priority=10, collapse=None):
        """POST one notification; returns (http_status, apns_reason)."""
        if not DEVICE.fullmatch(device) or environment not in HOSTS:
            raise PushUnavailable('设备令牌无效')
        headers = [f'authorization: bearer {self.token()}', f'apns-topic: {topic}', f'apns-push-type: {push_type}',
                   f'apns-priority: {priority}', 'content-type: application/json']
        if collapse:
            headers.append(f'apns-collapse-id: {collapse[:64]}')
        handle, path = tempfile.mkstemp(dir=self.directory, prefix='.h-')
        try:
            os.chmod(path, 0o600)
            with os.fdopen(handle, 'w') as file:
                file.write('\n'.join(headers) + '\n')
            result = self._run(['curl', '-sS', '--http2', '--max-time', '10', '-o', '-', '-w', '\n%{http_code}',
                                '-H', f'@{path}', '--data-binary', '@-', f'{HOSTS[environment]}/3/device/{device}'],
                               input=json.dumps(payload, ensure_ascii=False).encode(), capture_output=True, timeout=15)
        finally:
            Path(path).unlink(missing_ok=True)
        text = result.stdout.decode(errors='replace').rsplit('\n', 1)
        status = int(text[-1]) if text[-1].isdigit() else 0
        reason = ''
        if len(text) == 2 and text[0].strip():
            try:
                reason = str(json.loads(text[0]).get('reason') or '')
            except ValueError:
                reason = ''
        return status, reason


class Devices:
    """Registered phones and followed positions; one JSON file, written atomically."""

    def __init__(self, directory):
        self.path = Path(directory) / 'devices.json'
        self._lock = threading.Lock()

    def load(self):
        try:
            value = json.loads(self.path.read_text())
            return dict(devices=list(value.get('devices') or []), activities=list(value.get('activities') or []),
                        cursor=value.get('cursor'))
        except (OSError, ValueError, AttributeError):
            return dict(devices=[], activities=[], cursor=None)

    def save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, path = tempfile.mkstemp(dir=self.path.parent, prefix='.devices-')
        with os.fdopen(handle, 'w') as file:
            json.dump(value, file)
        os.chmod(path, 0o600)
        os.replace(path, self.path)

    def update(self, change):
        with self._lock:
            value = self.load()
            change(value)
            self.save(value)
            return value


def alert_for(event, scores):
    """Banner text for one notification event (same wording as the in-app banner)."""
    clock = time.strftime('%H:%M', time.localtime((event.get('at_ms') or 0) / 1000))
    if event.get('kind') == 'alarm':
        edge = '上插' if event.get('side') == 'upper' else '下插'
        coins = '、'.join(s.split('-')[0] for s in (event.get('symbols') or [])[:4])
        return dict(title=f"全市场{edge}报警 · {event.get('n') or 0} 个币", body=f'{clock} · {coins}' if coins else clock)
    coin = (event.get('symbol') or '').split('-')[0]
    edge = '上沿' if event.get('side') == 'upper' else '下沿'
    score = scores.get((event.get('symbol'), event.get('side')))
    tail = f' · 评分 {score:.2f}' if isinstance(score, (int, float)) else ''
    return dict(title=f'1.618 {edge}触价 · {coin}', body=f'{clock}{tail}')


def activity_state(snapshot, position_id):
    """Live Activity content state for one open position, or None once it is closed."""
    position = next((p for p in snapshot.get('positions') or [] if p.get('id') == position_id), None)
    if not position:
        return None
    number = lambda v: float(v) if v not in (None, '') else None
    quote = (snapshot.get('quotes') or {}).get(position.get('symbol')) or {}
    entry, margin = number(position.get('entry')), number(position.get('margin'))
    pnl = number(position.get('pnl')) or 0.0
    return dict(mark=number(quote.get('last')) or entry or 0.0, pnl=pnl, roe=(pnl / margin * 100) if margin else None,
                stop=number(position.get('stop')) or 0.0, updated=time.time())


class PushService:
    def __init__(self, directory, monitor, books, apns=None, clock=time.time):
        self.directory = Path(directory)
        self.apns = apns or Apns(self.directory)
        self.devices = Devices(self.directory)
        self.monitor, self.books, self._clock = monitor, books, clock
        self._stop = threading.Event()
        self._thread = None
        self._sent = {}

    # Registration (authenticated routes) -----------------------------------------------------
    def status(self):
        value = self.devices.load()
        return dict(configured=self.apns.configured, devices=len(value['devices'] or []), activities=len(value['activities'] or []))

    def register(self, token, environment, fib=True, alarm=True):
        if not DEVICE.fullmatch(token) or environment not in HOSTS:
            raise PushUnavailable('设备令牌无效')
        def change(value):
            value['devices'] = [d for d in value['devices'] if d.get('token') != token][-9:] + [
                dict(token=token, environment=environment, fib=bool(fib), alarm=bool(alarm), at=int(self._clock()))]
        self.devices.update(change)
        return self.status()

    def remove(self, token):
        self.devices.update(lambda v: v.update(devices=[d for d in v['devices'] if d.get('token') != token],
                                               activities=[a for a in v['activities'] if a.get('token') != token]))
        return self.status()

    def follow(self, token, environment, account, position_id):
        if not DEVICE.fullmatch(token) or environment not in HOSTS or not re.fullmatch(r'[\w.:-]{1,80}', position_id or ''):
            raise PushUnavailable('实时活动令牌无效')
        def change(value):
            value['activities'] = [a for a in value['activities'] if a.get('token') != token][-4:] + [
                dict(token=token, environment=environment, account=account or 'primary', position=position_id)]
        self.devices.update(change)
        return self.status()

    # Sender ------------------------------------------------------------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name='spike-push', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(10):
            try:
                self.tick()
            except Exception:  # never let a push failure reach the trading service
                pass

    def tick(self):
        if not self.apns.configured:
            return
        value = self.devices.load()
        self._notify(value)
        self._activities(value)

    def _notify(self, value):
        events = [e for e in self.monitor.notifications().get('events') or [] if e.get('pushed') and not e.get('test')]
        newest = max((e.get('at_ms') or 0 for e in events), default=0)
        cursor = value.get('cursor')
        if cursor is None:  # first run: start from now, never replay the backlog
            self.devices.update(lambda v: v.update(cursor=newest))
            return
        fresh = sorted((e for e in events if (e.get('at_ms') or 0) > cursor), key=lambda e: e.get('at_ms') or 0)
        if not fresh:
            return
        try:
            scores = {(s['symbol'], s['side']): s.get('score') for s in self.monitor.touch_scores().get('scores') or []}
        except Exception:
            scores = {}
        dead = set()
        for event in fresh[-5:]:
            text = alert_for(event, scores)
            kind = 'alarm' if event.get('kind') == 'alarm' else 'fib'
            payload = dict(aps=dict(alert=text, sound='default', **({'interruption-level': 'time-sensitive'} if kind == 'alarm' else {})),
                           kind=kind, symbol=event.get('symbol'))
            for device in value['devices']:
                if device.get(kind, True) and device['token'] not in dead:
                    status, reason = self.apns.send(device['token'], payload, device['environment'], collapse=str(event.get('id')))
                    if status == 410 or reason in ('BadDeviceToken', 'Unregistered'):
                        dead.add(device['token'])
        self.devices.update(lambda v: v.update(cursor=max(newest, cursor),
                                               devices=[d for d in v['devices'] if d.get('token') not in dead]))

    def _activities(self, value):
        finished = set()
        for activity in value['activities']:
            token = activity['token']
            try:
                state = activity_state(self.books(activity['account']).snapshot(), activity['position'])
            except Exception:
                continue
            last = self._sent.get(token)
            now = self._clock()
            if state is None:
                payload = dict(aps=dict(timestamp=int(now), event='end', **({'content-state': last[1]} if last else {}),
                                        **{'dismissal-date': int(now) + 15 * 60}))
                self.apns.send(token, payload, activity['environment'], push_type='liveactivity',
                               topic=TOPIC + '.push-type.liveactivity', priority=10)
                finished.add(token)
                continue
            key = (round(float(state['mark'] or 0), 10), round(float(state['pnl'] or 0), 2), state['stop'])
            if last and last[0] == key and now - last[2] < 300:
                continue
            if last and now - last[2] < ACTIVITY_EVERY:
                continue
            status, reason = self.apns.send(token, dict(aps=dict(timestamp=int(now), event='update', **{'content-state': state})),
                                            activity['environment'], push_type='liveactivity',
                                            topic=TOPIC + '.push-type.liveactivity', priority=5)
            if status == 410 or reason in ('BadDeviceToken', 'Unregistered'):
                finished.add(token)
            self._sent[token] = (key, state, now)
        if finished:
            self.devices.update(lambda v: v.update(activities=[a for a in v['activities'] if a.get('token') not in finished]))
