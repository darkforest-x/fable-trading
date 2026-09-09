"""Two-stage delivery integration using temporary journals and fake senders.

No network, credentials, market data or model inference is used. Stage clocks
come from synthetic confirmed bars; no later model price enters raw captions.

Explicit enabled=True below exercises legacy delivery with fake senders only.
"""
from copy import deepcopy
from io import BytesIO

from PIL import Image
import pytest
import requests

from model_fixture import model_event
from yoyo.monitor import (BARK_TIMEFRAMES, DIRECT_POLICY, FRESH_MS, MODEL_PROTOCOL,
                          MONITORED_TIMEFRAMES, SIGNAL_PROTOCOL, TIMEFRAMES)
from yoyo.monitor.bark import BarkWorker
from yoyo.monitor.store import Store
from yoyo.monitor.telegram import TelegramWorker

NOTIFICATION_STREAMS = ([('telegram', timeframe) for timeframe in MONITORED_TIMEFRAMES]
                        + [('bark', timeframe) for timeframe in BARK_TIMEFRAMES])


def image_bytes():
    buffer = BytesIO()
    Image.new("RGB", (16, 16), "black").save(buffer, format="PNG")
    return buffer.getvalue()


def activate(store, channel, *, direct=True, timeframe="1H", cutover=0, stream=True):
    method = store.activate_notification_policy if channel == "telegram" else store.activate_bark_policy
    protocol = DIRECT_POLICY if direct else MODEL_PROTOCOL
    method(cutover, protocol=protocol, retire_obsolete=False)
    if stream:
        store.activate_timeframe_policy(timeframe, cutover, protocol=protocol)


def response(channel, code=200):
    class Reply:
        status_code = code
        headers = {"Retry-After": "5"}

        def json(self):
            if channel == "bark":
                return {"code": 200, "timestamp": 1}
            if code == 429:
                return {"ok": False, "error_code": 429, "parameters": {"retry_after": 5}}
            return {"ok": True, "result": {"message_id": 7, "photo": [
                {"file_id": "synthetic-photo", "width": 16, "height": 16}]}}
    return Reply()


def worker(store, channel, calls, sender=None):
    def send(*args, **kwargs):
        calls.append((args, kwargs))
        return response(channel)
    if channel == "telegram":
        return TelegramWorker(store, ("synthetic-token", "synthetic-chat"), sender or send, enabled=True)
    return BarkWorker(store, "synthetic-device", sender or send)


def receipt(store, channel, event):
    table = "outbox" if channel == "telegram" else "bark_outbox"
    with store.connect() as db:
        return dict(db.execute(f"SELECT * FROM {table} WHERE event_id=?",
                               (store.event_id(event),)).fetchone())


def content(call, channel):
    kwargs = call[1]
    if channel == "telegram":
        return kwargs["data"]["caption"] if "files" in kwargs else kwargs["json"]["text"]
    payload = kwargs["json"]
    return "\n".join(payload[key] for key in ("title", "subtitle", "body"))


@pytest.mark.parametrize("channel,timeframe", NOTIFICATION_STREAMS)
@pytest.mark.parametrize("wait", [0, 2])
def test_raw_then_model_are_distinct_once_only_notifications(tmp_path, channel, timeframe, wait):
    store = Store(tmp_path / "monitor.sqlite")
    activate(store, channel, timeframe=timeframe)
    activate(store, channel, direct=False, timeframe=timeframe)
    event = model_event(timeframe=timeframe, wait=wait)
    raw = deepcopy(event["indicator"])
    calls = []
    sender = worker(store, channel, calls)

    assert store.upsert_event(raw, notify=True, bark_notify=True)
    assert sender.deliver_once(raw["bar_close_ms"] + 1000)
    assert not store.upsert_event(raw, notify=True, bark_notify=True)
    assert not sender.deliver_once(raw["bar_close_ms"] + 1001)
    assert store.upsert_event(event, notify=True, bark_notify=True)
    assert sender.deliver_once(event["bar_close_ms"] + 1000)
    assert not store.upsert_event(event, notify=True, bark_notify=True)
    assert not sender.deliver_once(event["bar_close_ms"] + 1001)

    assert len(calls) == 2
    direct_text, confirmed_text = [content(call, channel) for call in calls]
    assert "指标启动 · 未经 YOLO 确认" in direct_text
    assert "收盘价 99.5" in direct_text and "100.5" not in direct_text
    assert "原箭头" not in direct_text and "等待" not in direct_text
    assert f"YOLO 确认 · 等待 {wait} 根" in confirmed_text
    assert "确认 100.5" in confirmed_text and "原箭头 99.5" in confirmed_text
    assert "北京时间" in direct_text and "北京时间" in confirmed_text
    assert store.event_id(raw) != store.event_id(event)
    assert receipt(store, channel, raw)["status"] == receipt(store, channel, event)["status"] == "sent"
    assert sender.status()["sent"] == 2 and sender.status()["historical_sent"] == 0
    other = "bark" if channel == "telegram" else "telegram"
    assert receipt(store, other, raw)["status"] == receipt(store, other, event)["status"] == "pending"


@pytest.mark.parametrize("timeframe", BARK_TIMEFRAMES)
def test_bark_sends_raw_then_model_while_default_telegram_stays_off(tmp_path, timeframe):
    store = Store(tmp_path / "monitor.sqlite")
    for channel in ("telegram", "bark"):
        activate(store, channel, timeframe=timeframe)
        activate(store, channel, direct=False, timeframe=timeframe)
    event = model_event(timeframe=timeframe)
    raw = event["indicator"]
    calls = []
    sender = worker(store, "bark", calls)
    telegram = TelegramWorker(store, ("synthetic-token", "synthetic-chat"),
                              lambda *a, **k: pytest.fail("owner-disabled Telegram sent"))
    store.upsert_event(raw, notify=True, bark_notify=True)
    assert sender.deliver_once(raw["bar_close_ms"] + 1000)
    assert not telegram.deliver_once(raw["bar_close_ms"] + 1000)
    assert len(calls) == 1 and "未经 YOLO 确认" in content(calls[0], "bark")
    store.upsert_event(event, notify=True, bark_notify=True)
    assert sender.deliver_once(event["bar_close_ms"] + 1000)
    assert not telegram.deliver_once(event["bar_close_ms"] + 1000)
    assert len(calls) == 2 and "YOLO 确认 · 等待 2 根" in content(calls[1], "bark")
    assert receipt(store, "telegram", raw)["status"] == "pending"
    assert receipt(store, "telegram", event)["status"] == "pending"
    assert sender.status()["sent"] == 2
    assert telegram.status()["enabled"] is False


@pytest.mark.parametrize("channel", ["telegram", "bark"])
@pytest.mark.parametrize("invalid", ["no_direct_policy", "no_direct_stream", "before_cutover",
                                     "at_cutover", "expired", "future", "unconfirmed", "wrong_grid"])
def test_ineligible_raw_is_claimed_and_skipped_without_send(tmp_path, channel, invalid):
    store = Store(tmp_path / "monitor.sqlite")
    raw = deepcopy(model_event()["indicator"])
    end = raw["bar_close_ms"]
    # A model policy enables the worker but cannot authorize the raw stage.
    activate(store, channel, direct=False)
    cutover = end + 1 if invalid == "before_cutover" else end if invalid == "at_cutover" else 0
    if invalid != "no_direct_policy":
        activate(store, channel, cutover=cutover, stream=invalid != "no_direct_stream")
    now = end + 1000
    if invalid == "expired":
        now = end + FRESH_MS + 1
    elif invalid == "future":
        now = end - 1
        raw["detected_at_ms"] = now
    elif invalid == "unconfirmed":
        raw["confirmed"] = False
    elif invalid == "wrong_grid":
        raw["bar_open_ms"] += 1
    calls = []
    sender = worker(store, channel, calls)
    store.upsert_event(raw, notify=True, bark_notify=True)
    assert sender.deliver_once(now)
    assert not calls
    assert receipt(store, channel, raw)["status"] == "skipped"
    assert not sender.deliver_once(now + 1)


@pytest.mark.parametrize("channel", ["telegram", "bark"])
def test_old_raw_policy_cannot_enable_or_replay_new_direct_stage(tmp_path, channel):
    store = Store(tmp_path / "monitor.sqlite")
    method = store.activate_notification_policy if channel == "telegram" else store.activate_bark_policy
    method(0, protocol=SIGNAL_PROTOCOL)
    raw = model_event()["indicator"]
    store.upsert_event(raw, notify=True, bark_notify=True)
    calls = []
    sender = worker(store, channel, calls)
    assert not sender.deliver_once(raw["bar_close_ms"] + 1000)
    assert not calls and receipt(store, channel, raw)["status"] == "pending"


@pytest.mark.parametrize("stage", ["raw", "model"])
def test_photo_caption_uses_its_own_stage_and_clock(tmp_path, stage):
    store = Store(tmp_path / "monitor.sqlite")
    activate(store, "telegram")
    activate(store, "telegram", direct=False)
    event = model_event(side="short")
    selected = event["indicator"] if stage == "raw" else event
    png = image_bytes()
    store.upsert_event(selected, notify=True, telegram_photo=png)
    calls = []
    sender = worker(store, "telegram", calls)
    assert sender.deliver_once(selected["bar_close_ms"] + 1000)
    assert calls[0][0][0].endswith("/sendPhoto")
    assert calls[0][1]["files"]["photo"][1] == png
    assert ("未经 YOLO 确认" in content(calls[0], "telegram")) == (stage == "raw")
    assert "空头" in content(calls[0], "telegram")
    assert sender.status()["snapshots"] == 1


@pytest.mark.parametrize("channel", ["telegram", "bark"])
def test_direct_rate_limit_retries_same_stage_once(tmp_path, channel):
    store = Store(tmp_path / "monitor.sqlite")
    activate(store, channel)
    raw = model_event()["indicator"]
    store.upsert_event(raw, notify=True, bark_notify=True)
    calls = []
    def send(*args, **kwargs):
        calls.append((args, kwargs))
        return response(channel, 429 if len(calls) == 1 else 200)
    sender = worker(store, channel, calls, send)
    now = raw["bar_close_ms"] + 1000
    assert sender.deliver_once(now)
    assert not sender.deliver_once(now + 4999)
    assert sender.deliver_once(now + 5000)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert receipt(store, channel, raw)["status"] == "sent"


@pytest.mark.parametrize("channel", ["telegram", "bark"])
def test_uncertain_direct_send_is_not_retried_or_confused_with_model(tmp_path, channel):
    store = Store(tmp_path / "monitor.sqlite")
    activate(store, channel)
    activate(store, channel, direct=False)
    event = model_event()
    raw = event["indicator"]
    calls = []
    def send(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise requests.Timeout("synthetic-private-value")
        return response(channel)
    sender = worker(store, channel, calls, send)
    store.upsert_event(raw, notify=True, bark_notify=True)
    assert sender.deliver_once(raw["bar_close_ms"] + 1000)
    assert not sender.deliver_once(raw["bar_close_ms"] + 2000)
    assert receipt(store, channel, raw)["status"] == "unknown"
    assert "synthetic-private-value" not in receipt(store, channel, raw)["error"]
    store.upsert_event(event, notify=True, bark_notify=True)
    assert sender.deliver_once(event["bar_close_ms"] + 1000)
    assert len(calls) == 2 and receipt(store, channel, event)["status"] == "sent"
    assert receipt(store, channel, raw)["status"] == "unknown"


@pytest.mark.parametrize("channel", ["telegram", "bark"])
def test_status_counts_both_current_stages_but_preserves_old_raw_receipt(tmp_path, channel):
    store = Store(tmp_path / "monitor.sqlite")
    old = deepcopy(model_event()["indicator"])
    cutoff = old["bar_close_ms"]
    activate(store, channel, cutover=cutoff)
    activate(store, channel, direct=False)
    png = image_bytes()
    store.upsert_event(old, notify=True, bark_notify=True, telegram_photo=png)
    # Preserve a historical receipt from before the new notification contract.
    if channel == "telegram":
        store.finish(store.event_id(old), "sent", message_id=1)
    else:
        store.finish_bark(store.event_id(old), "sent", server_timestamp=1)
    event = model_event(close=old["bar_close_ms"] + 5 * TIMEFRAMES["1H"])
    raw = event["indicator"]
    calls = []
    sender = worker(store, channel, calls)
    for row in (raw, event):
        store.upsert_event(row, notify=True, bark_notify=True, telegram_photo=png)
        assert sender.deliver_once(row["bar_close_ms"] + 1000)
    status = sender.status()
    assert status["sent"] == 2 and status["historical_sent"] == 1
    if channel == "telegram":
        assert status["snapshots"] == 2
    assert receipt(store, channel, old)["status"] == "sent"
