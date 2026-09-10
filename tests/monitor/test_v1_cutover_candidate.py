"""Forward-only V1 raw-to-YOLO candidate boundary checks."""
from __future__ import annotations

import threading

from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256
from yoyo.monitor import DIRECT_POLICY, SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor import v1_worker
from yoyo.monitor.model_gate import ModelGate, pending_proof
from yoyo.monitor.notification_policy import arm_v1_bark
from yoyo.monitor.store import Store


SYMBOL = "TEST-USDT-SWAP"
TIMEFRAME = "4H"
STEP = TIMEFRAMES[TIMEFRAME]
NOW = 1_000 * STEP


def raw(close: int) -> dict:
    return {"protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": "live", "confirmation": "raw",
            "venue": "okx", "symbol": SYMBOL, "timeframe": TIMEFRAME, "timeframe_min": 240,
            "direction": "long", "side": "long", "bar_open_ms": close - STEP, "bar_close_ms": close,
            "signal_close_time": close, "is_closed": True, "confirmed": True, "ready": True,
            "price": 100.0, "risk": 2.0, "initial_stop": 98.0, "source_sha256": SOURCE_SHA256,
            "entry_reference": "next_open", "executable_entry_time": None, "detected_at_ms": close}


class ScannerClient:
    def __init__(self, close: int):
        self.close = close

    def synchronize(self):
        return 0

    def clock(self):
        return NOW

    def instruments(self):
        return [{"instId": SYMBOL, "tickSz": "0.01"}]

    def candles(self, symbol, timeframe, previous=None, limit=720):
        step = TIMEFRAMES[timeframe]
        close = self.close if timeframe == TIMEFRAME else NOW
        return [{"t": close - step, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 1.0}], 0


def run_scanner(tmp_path, monkeypatch, event, *, already_armed_at=None):
    store = Store(tmp_path / "monitor.sqlite3")
    if already_armed_at is not None:
        arm_v1_bark(store, already_armed_at)
    monkeypatch.setattr(v1_worker, "OKX", lambda: ScannerClient(event["bar_close_ms"]))
    monkeypatch.setattr(v1_worker, "analyze", lambda candles, higher, timeframe, *, tick, chart_limit:
                        {"events": [dict(event)] if timeframe == TIMEFRAME else [], "chart": [],
                         "state": {"phase": "ready", "ready": True}})
    scanner = v1_worker.V1Scanner(str(store.path))
    scanner.scan_once()
    return store


def test_pre_cutover_raw_within_nine_bars_is_history_not_a_yolo_candidate(tmp_path, monkeypatch):
    event = raw(NOW - STEP)  # 4H old is inside legacy nine-bar allowance.
    store = run_scanner(tmp_path, monkeypatch, event)
    assert len(store.list_events(kind=SIGNAL_KIND)) == 1
    assert store.candidate_counts() == {}
    assert store.bark_status()["pending"] == 0


def test_fresh_post_cutover_raw_registers_the_separate_yolo_candidate(tmp_path, monkeypatch):
    event = raw(NOW)
    store = run_scanner(tmp_path, monkeypatch, event, already_armed_at=0)
    candidates = store.list_candidates()
    assert len(candidates) == 1 and candidates[0]["id"] == store.event_id(event)
    assert candidates[0]["model"]["status"] == "pending"
    assert store.bark_status()["pending"] == 1


def test_persisted_pre_cutover_candidate_is_disabled_before_detector_or_confirmation(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    event = raw(NOW - STEP)
    arm_v1_bark(store, NOW)
    assert store.register_candidate(event, pending_proof(event))

    class Detector:
        def __init__(self): self.calls = 0
        def status(self): return {"ready": True}
        def predict(self, *args):
            self.calls += 1
            raise AssertionError("pre-cutover candidate must not reach detector")

    detector = Detector()
    gate = ModelGate(store, lambda: NOW, threading.Event(), detector)
    gate.process(SYMBOL, TIMEFRAME, [{"t": event["bar_open_ms"], "c": 100.0}])
    candidate = store.list_candidates()[0]
    assert candidate["model"]["status"] == "disabled"
    assert candidate["model"]["reason"] == "raw_not_eligible:before_bark_activation"
    assert detector.calls == 0
    assert store.list_events(kind="yolo_confirmed") == []
    assert store.bark_status()["pending"] == 0
