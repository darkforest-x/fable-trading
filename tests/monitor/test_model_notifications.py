"""Notification cutover and two-clock eligibility; every sender is synthetic.
Explicit enabled=True below exercises legacy delivery with fake senders only.
"""
from copy import deepcopy

import pytest

from model_fixture import CONFIRM, model_event
from yoyo.monitor import FRESH_MS, MODEL_PROTOCOL, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor.bark import BarkWorker
from yoyo.monitor.store import Store
from yoyo.monitor.telegram import TelegramWorker


def worker_and_store(tmp_path, channel, event, *, model_policy=True, timeframe_policy=True):
    store = Store(tmp_path / "m.sqlite")
    protocol = MODEL_PROTOCOL if model_policy else SIGNAL_PROTOCOL
    store.activate_notification_policy(0, protocol=protocol)
    store.activate_bark_policy(0, protocol=protocol)
    if timeframe_policy:
        store.activate_timeframe_policy(event["timeframe"], 0, protocol=protocol)
    store.upsert_event(event, notify=True, bark_notify=True)
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"message_id": 1}} if channel == "telegram" else {"code": 200, "timestamp": 1}
    def sender(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()
    worker = (TelegramWorker(store, ("synthetic-token", "synthetic-chat"), sender, enabled=True) if channel == "telegram"
              else BarkWorker(store, "synthetic-device", sender))
    return worker, store, calls


@pytest.mark.parametrize("channel", ["telegram", "bark"])
@pytest.mark.parametrize("timeframe", ["15m", "1H", "4H"])
def test_confirmed_endpoint_clock_can_be_fresh_after_original_arrow_expired(tmp_path, channel, timeframe):
    step = TIMEFRAMES[timeframe]
    event = model_event(timeframe=timeframe, close=100 * step, wait=3)
    now = event["bar_close_ms"] + 1000
    assert now - event["indicator"]["bar_close_ms"] > FRESH_MS
    worker, store, calls = worker_and_store(tmp_path, channel, event)
    assert worker.deliver_once(now)
    assert len(calls) == 1
    assert not worker.deliver_once(now + 1)
    assert worker.status()["sent"] == 1


@pytest.mark.parametrize("channel", ["telegram", "bark"])
@pytest.mark.parametrize("invalid", ["raw", "missing_proof", "wrong_weight", "pending", "future_core",
                                     "direction_mismatch", "unaligned", "missing_input_hash", "missing_detection_id"])
def test_raw_or_unproven_signal_cannot_reach_any_sender(tmp_path, channel, invalid):
    event = model_event()
    if invalid == "raw":
        event = deepcopy(event["indicator"])
    elif invalid == "missing_proof":
        del event["model"]
    elif invalid == "wrong_weight":
        event["model"]["model_sha256"] = "different-model"
    elif invalid == "pending":
        event["model"]["status"] = "pending"
    elif invalid == "future_core":
        event["model"]["core_end_ms"] = event["bar_open_ms"] + TIMEFRAMES[event["timeframe"]]
    elif invalid == "direction_mismatch":
        event["model"]["side"] = "short"
    elif invalid == "missing_input_hash":
        del event["model"]["input_pixel_sha256"]
    elif invalid == "missing_detection_id":
        del event["model"]["detection_id"]
    else:
        # Relative clocks still agree: the absolute exchange-bar grid must fail.
        for item, keys in ((event, ("bar_open_ms", "bar_close_ms")),
                           (event["indicator"], ("bar_open_ms", "bar_close_ms", "focus_start_ms")),
                           (event["model"], ("window_start_ms", "window_end_ms", "core_start_ms",
                                             "core_end_ms", "confirmation_close_ms"))):
            for key in keys:
                item[key] += 1
    worker, store, calls = worker_and_store(tmp_path, channel, event)
    assert worker.deliver_once(CONFIRM + 1000)
    assert not calls
    assert worker.status()["sent"] == 0


@pytest.mark.parametrize("channel", ["telegram", "bark"])
def test_old_channel_policy_does_not_authorize_new_model_protocol(tmp_path, channel):
    worker, store, calls = worker_and_store(tmp_path, channel, model_event(), model_policy=False)
    assert not worker.deliver_once(CONFIRM + 1000)
    assert not calls


@pytest.mark.parametrize("channel", ["telegram", "bark"])
@pytest.mark.parametrize("timeframe", ["15m", "1H", "4H"])
def test_model_timeframe_needs_its_own_cutover_even_on_legacy_hourly_stream(tmp_path, channel, timeframe):
    event = model_event(timeframe=timeframe)
    worker, store, calls = worker_and_store(tmp_path, channel, event, timeframe_policy=False)
    assert worker.deliver_once(event["bar_close_ms"] + 1000)
    assert not calls
    with store.connect() as db:
        query = "SELECT error FROM outbox" if channel == "telegram" else "SELECT error FROM bark_outbox"
        assert db.execute(query).fetchone()[0] == "before_timeframe_activation"


@pytest.mark.parametrize("channel", ["telegram", "bark"])
def test_expired_confirmation_cannot_be_made_fresh_by_recent_detection_time(tmp_path, channel):
    event = model_event(detected_at_ms=CONFIRM + FRESH_MS + 1000)
    worker, store, calls = worker_and_store(tmp_path, channel, event)
    assert worker.deliver_once(CONFIRM + FRESH_MS + 1000)
    assert not calls


@pytest.mark.parametrize("channel", ["telegram", "bark"])
@pytest.mark.parametrize("cutover", ["channel", "timeframe"])
@pytest.mark.parametrize("relative_cutover", [0, 1])
def test_later_confirmation_cannot_replay_arrow_before_channel_or_stream_enabled(
        tmp_path, channel, cutover, relative_cutover):
    event = model_event()
    activation = event["indicator"]["bar_close_ms"] + relative_cutover
    assert activation < event["bar_close_ms"]
    worker, store, calls = worker_and_store(tmp_path, channel, event)
    key = ("notification_timeframe:" + MODEL_PROTOCOL + ":" + event["timeframe"]
           if cutover == "timeframe" else
           ("notification_policy:" if channel == "telegram" else "notification_policy:bark:") + MODEL_PROTOCOL)
    # Simulate a manually requeued valid confirmation against a later cutover.
    store.set_meta(key, {"activated_ms": activation})
    assert worker.deliver_once(event["bar_close_ms"] + 1000)
    assert not calls
    expected = ("before_timeframe_activation" if cutover == "timeframe" else
                "before_notification_policy_activation" if channel == "telegram" else "before_bark_activation")
    with store.connect() as db:
        query = "SELECT error FROM outbox" if channel == "telegram" else "SELECT error FROM bark_outbox"
        assert db.execute(query).fetchone()[0] == expected
