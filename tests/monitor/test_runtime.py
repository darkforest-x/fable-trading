"""Runtime failure scenarios: public data integrity and durable notification identity."""
import concurrent.futures
import json

import pytest
import requests

from yoyo.monitor import FRESH_MS
from yoyo.monitor.okx import MarketError, merge_rows, parse_rows
from yoyo.monitor.store import Store
from yoyo.monitor.telegram import TelegramWorker


def event(now=3600000):
    return dict(protocol="test-v1", symbol="BTC-USDT-SWAP", timeframe="1H", kind="entry",
                side="long", price=100.5, bar_close_ms=now, detected_at_ms=now + 1000,
                near_zero_bars=12, dense=True, htf_allowed=True)


def response(code=200, payload=None):
    class Response:
        status_code = code
        def json(self):
            return payload or {"ok": True, "result": {"message_id": 123}}
    return Response()


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
    worker = TelegramWorker(store, ("secret", "private"), lambda *a, **k: (calls.append(k) or response()))
    assert worker.deliver_once(3602000)
    assert not worker.deliver_once(3603000)
    assert len(calls) == 1
    assert store.list_events()[0]["notification_status"] == "sent"
    assert "secret" not in json.dumps(store.telegram_status())


def test_timeout_is_unknown_not_retried(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    def timeout(*args, **kwargs):
        raise requests.Timeout("url containing secret must not leak")
    worker = TelegramWorker(store, ("secret", "private"), timeout)
    assert worker.deliver_once(3602000)
    assert not worker.deliver_once(3603000)
    assert store.telegram_status()["unknown"] == 1
    assert "secret" not in json.dumps(store.list_events())


def test_429_obeys_retry_after(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    worker = TelegramWorker(store, ("x", "y"), lambda *a, **k: response(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 90}}))
    worker.deliver_once(3602000)
    assert not worker.deliver_once(3603000)
    assert store.telegram_status()["pending"] == 1


def test_stale_outbox_is_skipped_without_network(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    def forbidden(*args, **kwargs):
        pytest.fail("expired event sent")
    worker = TelegramWorker(store, ("x", "y"), forbidden)
    worker.deliver_once(3600000 + FRESH_MS + 1)
    assert store.list_events()[0]["notification_status"] == "skipped"


def test_interrupted_send_becomes_unknown(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), True)
    assert store.claim(3602000)
    again = Store(store.path)
    again.recover_outbox()
    assert again.telegram_status()["unknown"] == 1
    assert again.claim(3603000) is None


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
    worker = TelegramWorker(store, ("x", "y"), lambda *a, **k: response(200, payload))
    worker.deliver_once(3602000)
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
