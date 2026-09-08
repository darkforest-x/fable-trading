"""Synthetic image delivery checks, with no network, credentials or market data."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import sqlite3

from PIL import Image
import pytest
import requests

from yoyo.monitor import SIGNAL_PROTOCOL, TV_PROFILE_ID
from yoyo.monitor.store import Store
from yoyo.monitor.telegram import TelegramWorker, message, markup


def event():
    return dict(protocol=SIGNAL_PROTOCOL, symbol='TEST-USDT-SWAP', timeframe='15m', kind='tv_start',
                side='short', price=100.5, bar_open_ms=2_700_000, bar_close_ms=3_600_000,
                detected_at_ms=3_601_000, near_zero_bars=13, md=-.2, sb=-.02,
                previous_md=.05, previous_sb=.08, focus_band=.1, confirmed=True, ready=True,
                focus_qualified_before=True, tv_marker_visible=True, tv_show_focus=True,
                tv_show_marks=False, source_kind='release', tv_marker='focus_release', tv_profile=TV_PROFILE_ID)


def png(color='black'):
    output = BytesIO()
    Image.new('RGB', (32, 32), color).save(output, format='PNG')
    return output.getvalue()


def response(code=200, payload=None):
    class Reply:
        status_code = code
        def json(self):
            return payload if payload is not None else {'ok': True, 'result': {
                'message_id': 123, 'photo': [{'file_id': 'fake-photo', 'width': 32, 'height': 32}]}}
    return Reply()


def queued(path, photo=True):
    store = Store(path / 'm.sqlite')
    store.activate_notification_policy(0)
    store.activate_bark_policy(0)
    store.activate_timeframe_policy('15m', 0)
    store.upsert_event(event(), notify=True, bark_notify=True, telegram_photo=png() if photo else None)
    return store


def test_caption_is_three_lines_and_button_owns_url():
    text = message(event())
    assert len(text.splitlines()) == 3 and len(text) < 200
    assert '100.5' in text and '13 根' in text and '15m' in text and '向下启动' in text
    assert 'https' not in text and '高周期' not in text and '精确零轴' not in text
    assert markup(event())['inline_keyboard'][0][0]['url'].endswith('interval=15')


def test_photo_survives_restart_and_sends_once_with_caption(tmp_path):
    queued(tmp_path)
    store = Store(tmp_path / 'm.sqlite')
    calls = []
    worker = TelegramWorker(store, ('fake-token', 'fake-chat'), lambda *a, **k: (calls.append((a, k)) or response()))
    assert worker.deliver_once(3_602_000)
    assert not worker.deliver_once(3_603_000)
    args, kwargs = calls[0]
    assert args[0].endswith('/sendPhoto') and 'json' not in kwargs
    assert kwargs['files']['photo'] == ('imacd-signal.png', png(), 'image/png')
    assert kwargs['data']['caption'] == message(event())
    assert json.loads(kwargs['data']['reply_markup']) == markup(event())
    assert kwargs['allow_redirects'] is False
    assert store.telegram_status()['sent'] == 1 and store.bark_status()['pending'] == 1
    assert 'png' not in store.list_events()[0]


def test_photo_429_retry_uses_exact_same_bytes_and_no_duplicate_text(tmp_path):
    store = queued(tmp_path)
    calls = []
    def sender(*args, **kwargs):
        calls.append((args, kwargs))
        return response(429, {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 5}}) if len(calls) == 1 else response()
    worker = TelegramWorker(store, ('fake', 'fake'), sender)
    worker.deliver_once(3_602_000)
    assert not worker.deliver_once(3_606_999)
    assert worker.deliver_once(3_607_000)
    assert calls[0][1]['files']['photo'][1] == calls[1][1]['files']['photo'][1] == png()
    assert all(call[0][0].endswith('/sendPhoto') for call in calls)
    assert store.telegram_status()['sent'] == 1


@pytest.mark.parametrize('failure', ['timeout', 'server_error_success_body', 'missing_photo'])
def test_uncertain_upload_never_falls_back_to_another_send(tmp_path, failure):
    store = queued(tmp_path)
    calls = []
    def sender(*args, **kwargs):
        calls.append(args[0])
        if failure == 'timeout':
            raise requests.Timeout('must not echo secrets')
        if failure == 'server_error_success_body':
            return response(503)
        return response(payload={'ok': True, 'result': {'message_id': 123}})
    worker = TelegramWorker(store, ('fake', 'fake'), sender)
    assert worker.deliver_once(3_602_000)
    assert not worker.deliver_once(3_603_000)
    assert len(calls) == 1 and calls[0].endswith('/sendPhoto')
    assert store.telegram_status()['unknown'] == 1
    assert store.bark_status()['pending'] == 1


@pytest.mark.parametrize('corrupt', [False, True])
def test_missing_or_corrupt_local_image_falls_back_before_first_request(tmp_path, corrupt):
    store = queued(tmp_path, photo=corrupt)
    if corrupt:
        with store.connect() as db:
            db.execute("UPDATE telegram_media SET png=?", (b'corrupt',))
    calls = []
    worker = TelegramWorker(store, ('fake', 'fake'), lambda *a, **k: (calls.append((a, k)) or response()))
    assert worker.deliver_once(3_602_000)
    assert len(calls) == 1 and calls[0][0][0].endswith('/sendMessage')
    assert calls[0][1]['json']['text'] == message(event())
    assert store.telegram_status()['sent'] == 1


def test_concurrent_duplicates_cannot_replace_frozen_photo_or_history(tmp_path):
    store = queued(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert not any(pool.map(lambda _: store.upsert_event(event(), notify=True, telegram_photo=png('red')), range(10)))
    row = store.claim(3_602_000)
    assert row['png'] == png() and row['photo_sha256'] == hashlib.sha256(png()).hexdigest()
    other = Store(tmp_path / 'history.sqlite')
    other.upsert_event(event())
    assert not other.upsert_event(event(), notify=True, telegram_photo=png())
    assert other.telegram_media_status()['snapshots'] == other.telegram_status()['pending'] == 0


def test_media_write_failure_rolls_back_event_and_both_queues(tmp_path):
    store = Store(tmp_path / 'm.sqlite')
    with store.connect() as db:
        db.execute("CREATE TRIGGER broken_media BEFORE INSERT ON telegram_media BEGIN SELECT RAISE(ABORT,'synthetic_failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_event(event(), notify=True, bark_notify=True, telegram_photo=png())
    assert store.event_count() == 0
    assert store.telegram_status()['pending'] == store.bark_status()['pending'] == 0


def test_legacy_database_pending_without_media_remains_deliverable(tmp_path):
    store = queued(tmp_path, photo=False)
    with store.connect() as db:
        db.execute('DROP TABLE telegram_media')
    restored = Store(store.path)
    worker = TelegramWorker(restored, ('fake', 'fake'), lambda *a, **k: response())
    assert worker.deliver_once(3_602_000)
    assert restored.telegram_status()['sent'] == 1
