"""Causal SPIKE V1 raw + YOLO-extra contract checks with synthetic candles."""
from __future__ import annotations

import threading

import pytest

from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256
from yoyo.monitor import (BARK_TIMEFRAMES, MODEL_KIND, MODEL_PROTOCOL, MODEL_SHA256,
                          SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES)
from yoyo.monitor.model_gate import ModelGate
from yoyo.monitor.notification_policy import arm_v1_bark, delivery_error
from yoyo.monitor.policy import is_model_signal
from yoyo.monitor.store import Store


class Detector:
    def __init__(self, proposal):
        self.proposal = proposal
        self.calls = []

    def status(self):
        return {"ready": True}

    def predict(self, candles, symbol, timeframe, endpoint_ms):
        assert candles[-1]["t"] == endpoint_ms
        assert all(name in candles[-1] for name in ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120"))
        self.calls.append(endpoint_ms)
        return [self.proposal]


@pytest.mark.parametrize("timeframe", BARK_TIMEFRAMES)
def test_raw_v1_remains_independent_while_yolo_adds_a_separate_bark_leg(tmp_path, timeframe):
    step, p = TIMEFRAMES[timeframe], 40 * TIMEFRAMES[timeframe]
    close = p + step
    raw = {"protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": "live", "confirmation": "raw",
           "venue": "okx", "symbol": "TEST-USDT-SWAP", "timeframe": timeframe,
           "timeframe_min": step // 60_000, "direction": "long", "side": "long",
           "bar_open_ms": p, "bar_close_ms": close, "signal_close_time": close, "is_closed": True,
           "price": 100.0, "risk": 2.0, "initial_stop": 98.0, "source_sha256": SOURCE_SHA256,
           "entry_reference": "next_open", "executable_entry_time": None, "detected_at_ms": close,
           "confirmed": True, "ready": True}
    proposal = {"model_sha256": MODEL_SHA256, "confidence": .65, "detection_id": "synthetic-v1-extra",
                "input_pixel_sha256": "b" * 64, "side": "long", "structural_pass": True,
                "window_len": 18, "window_start_ms": p - 17 * step, "window_end_ms": p,
                "core_start_ms": p - 5 * step, "core_end_ms": p - 2 * step,
                "core_length_bars": 4, "post_bars": 2}
    candles = [{"t": t, "o": 99.0, "h": 101.0, "l": 98.0, "c": 100.0, "v": 20.0,
                # A zero MD does not revoke the raw V1 launch or block its YOLO-extra check.
                "md": 0.0, "sma20": 100.0, "ema20": 100.0, "sma60": 100.0,
                "ema60": 100.0, "sma120": 100.0, "ema120": 100.0}
               for t in range(p - 20 * step, p + step, step)]
    store = Store(tmp_path / "monitor.sqlite3")
    store.activate_bark_policy(0, protocol=MODEL_PROTOCOL)
    store.activate_timeframe_policy(timeframe, 0, protocol=MODEL_PROTOCOL)
    detector = Detector(proposal)
    gate = ModelGate(store, lambda: close + 1_000, threading.Event(), detector)
    assert gate.register(raw)
    assert store.list_events(kind=SIGNAL_KIND)[0]["bark_notification_status"] == "history"
    gate.process(raw["symbol"], timeframe, candles)
    yolo = store.list_events(kind=MODEL_KIND, protocol=MODEL_PROTOCOL)
    assert len(yolo) == 1 and is_model_signal(yolo[0])
    assert yolo[0]["source"] == "live" and yolo[0]["confirmation"] == "yolo"
    assert yolo[0]["indicator"]["id"] == store.event_id(raw)
    assert store.bark_status()["pending"] == 1
    assert detector.calls == [p]


def test_new_v1_bark_cutover_keeps_old_raw_and_replay_out_of_every_outbox(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    step, close = TIMEFRAMES["1H"], 90 * TIMEFRAMES["1H"]
    def event(source, at):
        return {"protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": source, "confirmation": "raw",
                "venue": "okx", "symbol": "TEST-USDT-SWAP", "timeframe": "1H", "timeframe_min": 60,
                "direction": "long", "side": "long", "bar_open_ms": at - step, "bar_close_ms": at,
                "signal_close_time": at, "is_closed": True, "price": 100., "risk": 2., "initial_stop": 98.,
                "source_sha256": SOURCE_SHA256, "entry_reference": "next_open", "executable_entry_time": None,
                "detected_at_ms": at, "confirmed": True, "ready": True}
    old = event("live", close)
    assert store.upsert_event(old, bark_notify=False)
    receipt = arm_v1_bark(store, close + 1_000)
    assert receipt["telegram"] == "disabled"
    assert store.bark_status()["pending"] == 0 and store.telegram_status()["pending"] == 0
    replay = event("replay", close + 2 * step)
    assert delivery_error(store, replay, replay["bar_close_ms"] + 1, "bark") == "not_model_confirmed_signal"
    assert store.upsert_event(replay, bark_notify=False)
    fresh = event("live", close + 2 * step)
    assert delivery_error(store, fresh, fresh["bar_close_ms"] + 1, "bark") is None
    assert store.upsert_event(fresh, bark_notify=True)
    assert store.bark_status()["pending"] == 1 and store.telegram_status()["pending"] == 0
