"""Bark channel delivery and privacy scenarios; all pushes are synthetic."""
import json
import stat
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from yoyo.monitor import FRESH_MS, MODEL_PROTOCOL, SIGNAL_KIND
from model_fixture import CONFIRM, model_event
from yoyo.monitor.bark import BarkWorker, credentials, message, save_configuration
from yoyo.monitor.store import Store


def event(close=CONFIRM, **changes):
    original_changes = {key: changes.pop(key) for key in ("confirmed", "previous_sb") if key in changes}
    return model_event(close=close, original_changes=original_changes, **changes)


def response(status=200, payload=None, headers=None):
    class Response:
        status_code = status
        def json(self):
            return payload if payload is not None else {"code": 200, "message": "success", "timestamp": 3602}
    out = Response()
    out.headers = headers or {}
    return out


def queued(tmp_path, signal=None, activation=0):
    store = Store(tmp_path / "m.sqlite")
    store.activate_bark_policy(activation, protocol=MODEL_PROTOCOL)
    store.activate_timeframe_policy("1H", 0, protocol=MODEL_PROTOCOL)
    store.upsert_event(signal or event(), notify=True, bark_notify=True)
    return store


def test_success_uses_private_post_and_does_not_consume_telegram(tmp_path):
    store = queued(tmp_path)
    calls = []
    worker = BarkWorker(store, "fake-private-device", lambda *a, **k: (calls.append((a, k)) or response()))
    assert worker.deliver_once(CONFIRM + 2000)
    assert not worker.deliver_once(CONFIRM + 3000)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("https://api.day.app/push",)
    assert kwargs["json"]["device_key"] == "fake-private-device"
    assert kwargs["allow_redirects"] is False
    assert "100.5" in kwargs["json"]["body"] and "等待 2 根" in kwargs["json"]["subtitle"]
    assert kwargs["json"]["url"] == "https://www.tradingview.com/chart/"
    assert kwargs["json"]["body"].endswith("interval=60")
    assert "原箭头 99.5" in kwargs["json"]["body"]
    assert "YOLO 确认" in kwargs["json"]["subtitle"]
    assert store.list_events()[0]["bark_notification_status"] == "sent"
    assert store.list_events()[0]["notification_status"] == "pending"
    assert "fake-private-device" not in json.dumps(worker.status())
    assert "fake-private-device" not in json.dumps(store.list_events())
    assert worker.status()["acceptance"] == "bark_server_accepted_not_device_receipt"


@pytest.mark.parametrize("timeframe,interval", [("15m", "15"), ("1H", "60"), ("4H", "240")])
@pytest.mark.parametrize("direct", [True, False])
def test_mobile_app_link_keeps_exact_web_chart_and_copy_symbol(timeframe, interval, direct):
    signal = event(timeframe=timeframe, symbol="ZK-USDT-SWAP")
    if direct:
        signal["kind"] = SIGNAL_KIND
    payload = message(signal)
    # TradingView's AASA excludes nonempty symbol queries from /chart/.
    assert payload["url"] == "https://www.tradingview.com/chart/"
    assert payload["copy"] == "OKX:ZKUSDT.P"
    assert "autoCopy" not in payload
    assert "action" not in payload  # action=none would disable tap navigation.
    fallback = urlsplit(payload["body"].split("网页备用：", 1)[1])
    assert (fallback.scheme, fallback.netloc, fallback.path) == (
        "https", "www.tradingview.com", "/chart/")
    assert parse_qs(fallback.query) == {"symbol": ["OKX:ZKUSDT.P"], "interval": [interval]}
    assert timeframe in payload["title"]
    assert ("未经 YOLO 确认" in payload["subtitle"]) == direct


@pytest.mark.parametrize("changes,now,activation", [
    ({"kind": "zero_breakout"}, CONFIRM + 2000, 0),
    ({"kind": "entry"}, CONFIRM + 2000, 0),
    ({"confirmed": False}, CONFIRM + 2000, 0),
    ({"near_zero_bars": 11}, CONFIRM + 2000, 0),
    ({"previous_sb": .2}, CONFIRM + 2000, 0),
    ({}, CONFIRM + 2000, CONFIRM),
    ({}, CONFIRM + 2000, CONFIRM + 1000),
    ({}, CONFIRM + FRESH_MS + 1, 0),
    ({"detected_at_ms": CONFIRM - 10000}, CONFIRM - 1, 0),
])
def test_noneligible_never_reaches_bark(tmp_path, changes, now, activation):
    store = queued(tmp_path, event(**changes), activation)
    worker = BarkWorker(store, "fake-private-device", lambda *a, **k: pytest.fail("ineligible signal sent"))
    assert worker.deliver_once(now)
    assert store.bark_status()["skipped"] == 1
    assert store.telegram_status()["pending"] == 1


@pytest.mark.parametrize("http,payload", [
    (200, []), (200, {"code": 200}), (200, {"code": True, "timestamp": 1}),
    (200, {"code": 200, "timestamp": True}), (200, {"code": 200, "timestamp": 0}),
    (200, {"code": 500, "timestamp": 1}), (500, {"code": 200, "timestamp": 1}),
    (302, {"code": 200, "timestamp": 1}),
])
def test_uncertain_response_is_not_automatically_retried(tmp_path, http, payload):
    store = queued(tmp_path)
    worker = BarkWorker(store, "fake-private-device", lambda *a, **k: response(http, payload))
    assert worker.deliver_once(CONFIRM + 2000)
    assert not worker.deliver_once(CONFIRM + 3000)
    assert store.bark_status()["unknown"] == 1
    assert store.telegram_status()["pending"] == 1


def test_timeout_is_redacted_and_not_retried(tmp_path):
    store = queued(tmp_path)
    def fail(*args, **kwargs):
        raise requests.Timeout("fake-private-device in remote error")
    worker = BarkWorker(store, "fake-private-device", fail)
    assert worker.deliver_once(CONFIRM + 2000)
    assert not worker.deliver_once(CONFIRM + 3000)
    assert store.bark_status()["unknown"] == 1
    with store.connect() as db:
        error = db.execute("SELECT error FROM bark_outbox").fetchone()[0]
    assert "fake-private-device" not in error


def test_definite_rejection_and_rate_limit_are_separate(tmp_path):
    store = queued(tmp_path)
    worker = BarkWorker(store, "fake-private-device", lambda *a, **k: response(429, headers={"Retry-After": "90"}))
    assert worker.deliver_once(CONFIRM + 2000)
    assert not worker.deliver_once(CONFIRM + 3000)
    worker.sender = lambda *a, **k: response(400, {"message": "private details must not be stored"})
    assert worker.deliver_once(CONFIRM + 92000)
    assert store.bark_status()["failed"] == 1
    with store.connect() as db:
        error = db.execute("SELECT error FROM bark_outbox").fetchone()[0]
    assert error == "bark_rejected_400"


def test_no_channel_activation_leaves_queue_unclaimed(tmp_path):
    store = Store(tmp_path / "m.sqlite")
    store.upsert_event(event(), bark_notify=True)
    worker = BarkWorker(store, "fake-private-device", lambda *a, **k: pytest.fail("missing cutover sent"))
    assert not worker.deliver_once(CONFIRM + 2000)
    assert store.bark_status()["pending"] == 1


def test_private_configuration_roundtrip_and_disable(tmp_path):
    save_configuration("https://api.day.app/fake-private-device/", tmp_path)
    p = tmp_path / "bark.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert credentials(tmp_path) == "fake-private-device"
    p.write_text(json.dumps({"device_key": "fake-private-device", "enabled": False}))
    assert credentials(tmp_path) is None
    p.write_text("[]")
    assert credentials(tmp_path) is None


@pytest.mark.parametrize("endpoint", [
    "http://api.day.app/fake-private-device/", "https://unrelated.test/fake-private-device/",
    "https://api.day.app/fake-private-device/?extra=1", "https://api.day.app/x/y",
])
def test_configuration_rejects_wrong_destination_without_printing_it(tmp_path, endpoint):
    with pytest.raises(ValueError, match="^invalid_bark_endpoint$"):
        save_configuration(endpoint, tmp_path)
    assert not (tmp_path / "bark.json").exists()
