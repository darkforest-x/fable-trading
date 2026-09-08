"""Runtime failure scenarios: public data integrity and durable notification identity."""
import concurrent.futures
import json

import pytest
import requests

from yoyo.monitor import FRESH_MS, SIGNAL_PROTOCOL, SIGNAL_KIND, MODEL_PROTOCOL, MODEL_KIND
from model_fixture import CONFIRM, model_event
from yoyo.monitor.okx import MarketError, merge_rows, parse_rows
from yoyo.monitor.store import Store
from yoyo.monitor.telegram import TelegramWorker


def event(now=CONFIRM):
    return model_event(close=now)


def activate(store, at=0):
    """Explicit model stream activation; legacy hourly defaults never apply."""
    store.activate_timeframe_policy("1H", 0, protocol=MODEL_PROTOCOL)
    return store.activate_notification_policy(at, protocol=MODEL_PROTOCOL)


def response(code=200, payload=None):
    class Response:
        status_code = code
        def json(self):
            return payload or {"ok": True, "result": {"message_id": 123}}
    return Response()


@pytest.mark.parametrize('channel', ['telegram', 'bark'])
@pytest.mark.parametrize('activation,expected', [(None, 'skipped'), (CONFIRM, 'skipped'),
                                               (CONFIRM + 1, 'skipped'), (CONFIRM - 1, 'skipped'),
                                               (CONFIRM - 1_800_001, 'sent')])
def test_15m_worker_rechecks_stream_activation_and_links_correct_interval(tmp_path, channel, activation, expected):
    from yoyo.monitor.bark import BarkWorker
    store = Store(tmp_path / 'm.sqlite')
    activate(store, 0)
    store.activate_bark_policy(0, protocol=MODEL_PROTOCOL)
    if activation is not None:
        store.activate_timeframe_policy('15m', activation, protocol=MODEL_PROTOCOL)
    signal = model_event(timeframe='15m', close=CONFIRM)
    store.upsert_event(signal, notify=True, bark_notify=True)
    calls = []
    def sender(*args, **kwargs):
        calls.append(kwargs['json'])
        return response(payload={'ok': True, 'result': {'message_id': 123}, 'code': 200, 'timestamp': 3602})
    worker = (TelegramWorker(store, ('fake-token', 'fake-chat'), sender) if channel == 'telegram'
              else BarkWorker(store, 'fake-device', sender))
    assert worker.deliver_once(CONFIRM + 2_000)
    assert not worker.deliver_once(CONFIRM + 3_000)
    status = store.telegram_status() if channel == 'telegram' else store.bark_status()
    assert status[expected] == 1
    assert len(calls) == int(expected == 'sent')
    if calls:
        link = (calls[0]['reply_markup']['inline_keyboard'][0][0]['url'] if channel == 'telegram'
                else calls[0]['url'])
        assert 'interval=15' in link
    untouched = store.bark_status() if channel == 'telegram' else store.telegram_status()
    assert untouched['pending'] == 1


def test_15m_parser_excludes_incomplete_and_future_closes():
    rows = [candle(0), candle(900000, '0'), candle(1800000)]
    assert [r['t'] for r in parse_rows(rows, '15m', 1800000)] == [0]


def test_concurrent_insert_is_one_event_and_one_outbox(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.upsert_event(event(), True), range(30)))
    assert sum(results) == 1
    assert store.event_count() == 1
    assert store.telegram_status()["pending"] == 1


def test_history_does_not_become_notification_on_restart(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), False)
    again = Store(store.path)
    assert not again.upsert_event(event(), True)
    assert again.telegram_status()["pending"] == 0


def test_success_receipt_and_no_resend(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    calls = []
    activate(store, 0)
    worker = TelegramWorker(store, ("secret", "private"), lambda *a, **k: (calls.append(k) or response()))
    assert worker.deliver_once(CONFIRM + 2_000)
    assert not worker.deliver_once(CONFIRM + 3_000)
    assert len(calls) == 1
    assert store.list_events()[0]["notification_status"] == "sent"
    assert "secret" not in json.dumps(store.telegram_status())


def test_timeout_is_unknown_not_retried(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    def timeout(*args, **kwargs):
        raise requests.Timeout("url containing secret must not leak")
    activate(store, 0)
    worker = TelegramWorker(store, ("secret", "private"), timeout)
    assert worker.deliver_once(CONFIRM + 2_000)
    assert not worker.deliver_once(CONFIRM + 3_000)
    assert store.telegram_status()["unknown"] == 1
    assert "secret" not in json.dumps(store.list_events())


def test_429_obeys_retry_after(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    activate(store, 0)
    worker = TelegramWorker(store, ("x", "y"), lambda *a, **k: response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 90}}))
    worker.deliver_once(CONFIRM + 2_000)
    assert not worker.deliver_once(CONFIRM + 3_000)
    assert store.telegram_status()["pending"] == 1


def test_stale_outbox_is_skipped_without_network(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    def forbidden(*args, **kwargs):
        pytest.fail("expired event sent")
    activate(store, 0)
    worker = TelegramWorker(store, ("x", "y"), forbidden)
    worker.deliver_once(CONFIRM + FRESH_MS + 1)
    assert store.list_events()[0]["notification_status"] == "skipped"


def test_interrupted_send_becomes_unknown(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    assert store.claim(CONFIRM + 2_000)
    again = Store(store.path)
    again.recover_outbox()
    assert again.telegram_status()["unknown"] == 1
    assert again.claim(CONFIRM + 3_000) is None


def candle(ts=0, confirm="1", close="2"):
    return [str(ts), "2", "3", "1", close, "5", "0", "0", confirm]


def test_only_confirmed_and_closed_candles():
    rows = [candle(), candle(3600000, "0"), candle(7200000)]
    assert [x["t"] for x in parse_rows(rows, "1H", 7200000)] == [0]


def test_conflicting_same_time_quotes_fail_closed():
    with pytest.raises(MarketError, match="conflicting"):
        parse_rows([candle(), candle(close="2.1")], "1H", 3600000)


def test_gap_resets_warmup_without_filling():
    rows = parse_rows([candle(0), candle(3600000), candle(10800000)], "1H", 14400000)
    result, gaps = merge_rows([], rows, "1H")
    assert gaps == 1 and len(result) == 1 and result[0]["t"] == 10800000


def test_revised_confirmed_history_fails_closed():
    a = parse_rows([candle()], "1H", 3600000)
    b = parse_rows([candle(close="2.1")], "1H", 3600000)
    with pytest.raises(MarketError, match="revised"):
        merge_rows(a, b, "1H")


def test_nonfinite_data_rejected():
    with pytest.raises(MarketError, match="value"):
        parse_rows([candle(close="NaN")], "1H", 3600000)


@pytest.mark.parametrize("payload", [[1], {"ok": True, "result": None}, {"ok": True, "result": {}},
                                     {"ok": False, "error_code": "invalid"}])
def test_malformed_receipt_never_strands_sending(tmp_path, payload):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    activate(store, 0)
    worker = TelegramWorker(store, ("x", "y"), lambda *a, **k: response(200, payload))
    worker.deliver_once(CONFIRM + 2_000)
    assert store.telegram_status()["unknown"] == 1
    assert store.telegram_status().get("sending", 0) == 0


def test_chart_marks_old_close_stale_at_read_time(tmp_path):
    from yoyo.monitor.service import Monitor
    store = Store(tmp_path / "m.sqlite")
    monitor = Monitor(store)
    monitor.client.clock = lambda: 7200000
    monitor.charts[("BTC-USDT-SWAP", "1H")] = {"state": {"bar_close_ms": 3600000, "stale": False}, "candles": []}
    assert monitor.chart("BTC-USDT-SWAP", "1H")["state"]["stale"]


def test_persisted_market_does_not_look_current_after_clock_advances(tmp_path):
    from yoyo.monitor.service import Monitor
    store = Store(tmp_path / "m.sqlite")
    store.upsert_market(dict(symbol="BTC-USDT-SWAP", timeframe="1H", phase="ready", stale=False, bar_close_ms=3600000))
    monitor = Monitor(store)
    monitor.client.clock = lambda: 7200000
    assert monitor.markets()[0]["stale"]
    assert monitor.status()["counts"].get("ready", 0) == 0


@pytest.mark.parametrize('changes', [
    {'kind': 'entry'}, {'kind': 'exit'}, {'kind': 'release'}, {'kind': 'retest'},
    {'protocol': 'imacd-zero-axis-monitor-v2'}, {'kind': 'zero_breakout'},
    {'previous_md': .11}, {'previous_sb': .11}, {'md': .1}, {'md': 0},
    {'confirmed': False}, {'side': 'short'}, {'near_zero_bars': 11},
    {'focus_qualified_before': False}, {'tv_marker_visible': False},
    {'tv_show_focus': False}, {'tv_show_marks': True}, {'tv_profile': 'unknown'},
    {'source_kind': 'entry'}, {'tv_marker': 'system_start'},
])
def test_noncanonical_queue_items_cannot_reach_telegram(tmp_path, changes):
    store = Store(tmp_path / 'm.sqlite')
    bad = event()
    bad["indicator"].update(changes)
    store.upsert_event(bad, True)
    activate(store, 0)
    worker = TelegramWorker(store, ('fake', 'fake'), lambda *a, **k: pytest.fail('noncanonical signal sent'))
    worker.deliver_once(CONFIRM + 2_000)
    assert store.list_events()[0]['notification_status'] == 'skipped'


def test_policy_cutover_is_persistent_and_preserves_old_receipts(tmp_path):
    store = Store(tmp_path / 'm.sqlite')
    old = dict(event(), protocol='old', kind='exit')
    store.upsert_event(old, True)
    newer = event(CONFIRM + 10_800_000)
    store.upsert_event(newer, True)
    cutoff = activate(store, CONFIRM + 400_000)
    assert cutoff == CONFIRM + 400_000
    assert newer['indicator']['bar_close_ms'] > cutoff
    assert activate(Store(store.path), CONFIRM + 4_400_000) == cutoff
    assert store.event_count() == 2
    assert store.telegram_status()['skipped'] == 1
    assert store.telegram_status()['pending'] == 1
    activate(store, 0)
    worker = TelegramWorker(store, ('fake', 'fake'), lambda *a, **k: response())
    assert worker.deliver_once(CONFIRM + 10_802_000)
    assert store.telegram_status(protocol=MODEL_PROTOCOL)['sent'] == 1
    activate(store, CONFIRM + 5_400_000)
    assert store.telegram_status(protocol=MODEL_PROTOCOL)['sent'] == 1


def test_pre_cutover_fresh_event_is_not_replayed_as_new_notification(tmp_path):
    store = Store(tmp_path / 'm.sqlite')
    activate(store, CONFIRM + 1_000)
    store.upsert_event(event(), True)
    activate(store, 0)
    worker = TelegramWorker(store, ('fake', 'fake'), lambda *a, **k: pytest.fail('history replayed'))
    worker.deliver_once(CONFIRM + 2_000)
    assert store.list_events()[0]['notification_status'] == 'skipped'


def test_default_signal_api_and_counts_do_not_relabel_old_events(tmp_path, monkeypatch):
    from yoyo.monitor.server import create_app
    from yoyo.monitor import telegram
    from fastapi import HTTPException
    monkeypatch.setattr(telegram, 'credentials', lambda: None)
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    store.upsert_event(event(), False)
    store.upsert_event(event()['indicator'], False)
    store.upsert_event(dict(event(), protocol='old', kind='entry'), False)
    store.upsert_event(dict(event(), kind='release'), False)
    store.upsert_event(dict(event(), protocol='old'), False)
    assert store.count_since(0) == 1
    assert store.count_since(0, kind=MODEL_KIND, protocol=MODEL_PROTOCOL) == 1
    endpoint = next(r.endpoint for r in app.routes if getattr(r, 'path', '') == '/api/signals')
    result = endpoint(limit=200)
    assert result['total'] == 1 and len(result['items']) == 1
    assert result['items'][0]['kind'] == MODEL_KIND and result['items'][0]['protocol'] == MODEL_PROTOCOL
    with pytest.raises(HTTPException):
        endpoint(limit=200, kind='exit')


def test_delivery_waits_for_explicit_policy_activation(tmp_path):
    store = Store(tmp_path / 'm.sqlite')
    store.upsert_event(event(), True)
    worker = TelegramWorker(store, ('fake', 'fake'), lambda *a, **k: pytest.fail('unactivated policy sent'))
    assert not worker.deliver_once(CONFIRM + 2_000)
    assert store.telegram_status()['pending'] == 1
    assert store.telegram_status().get('sending', 0) == 0


def test_model_confirmation_can_notify_after_original_md_already_left_zero(tmp_path):
    store = Store(tmp_path / 'm.sqlite')
    activate(store, 0)
    visible = event()
    visible["indicator"].update(zero_bars=0, previous_md=.05, dense=False, htf_allowed=False)
    store.upsert_event(visible, True)
    calls = []
    worker = TelegramWorker(store, ('fake', 'fake'), lambda *a, **k: (calls.append(k) or response()))
    assert worker.deliver_once(CONFIRM + 2_000)
    assert len(calls) == 1 and store.telegram_status()['sent'] == 1
    assert '0 →' not in calls[0]['json']['text']


def test_raw_visible_release_alone_cannot_notify(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    activate(store)
    store.upsert_event(event()["indicator"], True)
    worker = TelegramWorker(store, ("fake", "fake"), lambda *a, **k: pytest.fail("raw arrow sent"))
    assert worker.deliver_once(CONFIRM + 1000)
    assert store.telegram_status()["skipped"] == 1
    with store.connect() as db:
        assert db.execute("SELECT error FROM outbox").fetchone()[0] == "not_model_confirmed_signal"
