"""Synthetic V9 cutover, notification and public API regression checks.

No exchange candles, historical results, device keys or actual pushes are used.
The destructive reset is exercised only against pytest's temporary databases.
"""
from copy import deepcopy
import json

import pytest

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES, MODEL_PROTOCOL
from yoyo.monitor.bark import BarkWorker, message
from yoyo.monitor.model_gate import pending_proof
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.policy import is_tv_start
from yoyo.monitor.store import Store


STEP = TIMEFRAMES["15m"]
NOW = 2_000_000 * STEP


def event(close=NOW, side="long"):
    return {"protocol": SIGNAL_PROTOCOL, "strategy_version": "spike-v9-entry-bundle-20260915-v1",
            "v9_admitted": True, "kind": SIGNAL_KIND, "source": "live", "confirmation": "raw",
            "symbol": "ETH-USDT-SWAP", "venue": "okx", "timeframe": "15m", "timeframe_min": 15,
            "side": side, "direction": side, "bar_close_ms": close, "bar_open_ms": close - STEP,
            "signal_close_time": close, "is_closed": True, "confirmed": True, "ready": True,
            "price": 100., "risk": 3., "initial_stop": 97. if side == "long" else 103.,
            "detected_at_ms": close, "source_sha256": "a" * 64}


def test_reset_clears_all_old_signals_and_preserves_only_raw_warmup(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    old = event(NOW - STEP)
    old.update(protocol="spike-burst-v1-monitor-v1", kind="spike_burst_v1")
    store.upsert_event(old, notify=True, bark_notify=True)
    store.register_candidate(old, pending_proof(old))
    store.save_candle_checkpoint(old["symbol"], "15m", [{"t": NOW - STEP}])
    store.upsert_market({"symbol": old["symbol"], "timeframe": "15m", "events": [old]})
    store.set_meta("v1:last_closed:ETH-USDT-SWAP:15m", NOW)
    receipt = store.reset_for_v9(NOW)
    assert receipt["removed"]["events"] == 1
    assert receipt["removed"]["bark_outbox"] == 1
    assert store.event_count() == 0
    assert store.candidate_counts() == {}
    assert store.list_markets() == []
    assert store.list_market_summaries() == []
    assert store.load_candle_checkpoint(old["symbol"], "15m") == [{"t": NOW - STEP}]
    assert store.get_meta("v1:last_closed:ETH-USDT-SWAP:15m") is None
    assert store.reset_for_v9(NOW + STEP) == receipt


@pytest.mark.parametrize("change", [{}, {"bar_close_ms": NOW - STEP},
    {"protocol": "spike-burst-v1-monitor-v1", "bar_close_ms": NOW + STEP},
    {"source": "replay", "bar_close_ms": NOW + STEP}])
def test_cold_history_cannot_reappear_or_enqueue_after_reset(tmp_path, change):
    store = Store(tmp_path / "monitor.sqlite3")
    store.reset_for_v9(NOW)
    old = dict(event(), **change)
    assert not store.upsert_event(old, bark_notify=True)
    assert not store.register_candidate(old, pending_proof(old))
    assert store.event_count() == 0
    assert store.bark_status()["pending"] == 0


@pytest.mark.parametrize("side", ["long", "short"])
def test_new_v9_both_sides_notify_once_through_mock_sender(tmp_path, side):
    store = Store(tmp_path / "monitor.sqlite3")
    store.reset_for_v9(NOW - 1)
    row = event(side=side)
    assert is_tv_start(row)
    assert delivery_error(store, row, NOW, "bark") is None
    assert store.upsert_event(row, bark_notify=True)
    assert not store.upsert_event(row, bark_notify=True)
    calls = []

    class Response:
        status_code = 200
        def json(self): return {"code": 200, "timestamp": NOW // 1000}

    def sender(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return Response()

    worker = BarkWorker(store, creds="synthetic-key", sender=sender)
    assert worker.deliver_once(NOW)
    assert not worker.deliver_once(NOW)
    assert len(calls) == 1
    assert calls[0][1]["title"].startswith("V9 · ")
    assert calls[0][1]["group"] == "SPIKE V9"
    assert store.bark_status()["sent"] == 1
    # A later service start/reset request must not clear the new event again.
    store.reset_for_v9(NOW + STEP)
    assert store.event_count() == 1


def test_v1_or_unadmitted_rows_never_pass_v9_delivery(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    store.reset_for_v9(NOW - 1)
    for update in ({"protocol": "spike-burst-v1-monitor-v1"},
                   {"v9_admitted": False}, {"strategy_version": "v8"},
                   {"direction": "short"}):
        row = dict(event(), **update)
        assert not is_tv_start(row)
        assert delivery_error(store, row, NOW, "bark") is not None
    assert delivery_error(store, event(), NOW + 30 * 60_000 + 1, "bark") == "signal_expired"


def test_model_origin_before_reset_cannot_reappear(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    store.reset_for_v9(NOW)
    row = dict(event(NOW + STEP), kind="yolo_confirmed", protocol=MODEL_PROTOCOL,
               confirmation="yolo", indicator=event(), source_event_id="old")
    assert not store.upsert_event(row, bark_notify=True)
    assert store.event_count() == 0


def test_api_and_ledger_only_show_post_reset_v9(tmp_path):
    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app
    app = create_app(tmp_path, start_monitor=False)
    store = app.state.monitor.store
    app.state.monitor.client.clock = lambda: NOW
    store.upsert_event(event(NOW - STEP))
    store.reset_for_v9(NOW - 1)
    store.upsert_event(event(side="short"))
    with TestClient(app) as client:
        rows = client.get("/api/signals?confirmation=raw").json()["items"]
        assert len(rows) == 1 and rows[0]["side"] == "short"
        assert rows[0]["protocol"] == SIGNAL_PROTOCOL
        assert rows[0]["v9_admitted"] is True
        for source in ("warmup", "replay"):
            assert client.get("/api/signals?confirmation=raw&source=" + source).json()["items"] == []
        book = client.get("/api/signals?view=ledger&confirmation=raw").json()
        assert book["total"] == 1 and book["items"][0]["protocol"] == SIGNAL_PROTOCOL
        assert "SPIKE V9" in client.get("/").text


def test_short_yolo_uses_original_side_and_v9_identity(tmp_path):
    from yoyo.monitor import MODEL_SHA256
    from yoyo.monitor.model_gate import confirmation
    from yoyo.monitor.policy import is_model_signal
    original = event(NOW - 2 * STEP, side="short")
    original["id"] = Store.event_id(original)
    end = NOW - STEP
    proposal = {"structural_pass": True, "side": "short", "model_sha256": MODEL_SHA256,
                "confidence": .6, "detection_id": "synthetic", "input_pixel_sha256": "b" * 64,
                "core_start_ms": end - 6 * STEP, "core_end_ms": end - 3 * STEP,
                "window_start_ms": end - 17 * STEP, "window_end_ms": end,
                "window_len": 18, "core_length_bars": 4, "post_bars": 3}
    derived = confirmation(original, proposal, {"t": end, "c": 98.}, NOW)
    assert derived["direction"] == derived["side"] == "short"
    assert is_model_signal(derived)
    store = Store(tmp_path / "monitor.sqlite3")
    store.reset_for_v9(NOW - 3 * STEP)
    assert delivery_error(store, derived, NOW, "bark") is None


def test_scanner_restarts_never_restore_old_events_or_queue_twice(tmp_path, monkeypatch):
    from yoyo.monitor import v9_worker
    store = Store(tmp_path / "monitor.sqlite3")
    store.reset_for_v9(NOW - 1)
    calls = []

    class Client:
        def synchronize(self): pass
        def clock(self): return NOW
        def instruments(self): return [{"instId": "ETH-USDT-SWAP", "tickSz": ".01", "uly": "ETH-USDT"}]
        def candles(self, symbol, timeframe, previous=None, limit=720):
            step = TIMEFRAMES[timeframe]
            return [{"t": NOW - step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 1.}], 0

    def analyze(candles, higher, timeframe, *, tick, base_asset, chart_limit):
        assert base_asset == "ETH"
        calls.append(timeframe)
        return {"state": {"phase": "ready", "ready": True, "timeframe": timeframe}, "chart": candles,
                "events": [event(NOW - STEP), event(side="short")] if timeframe == "15m" else []}

    monkeypatch.setattr(v9_worker, "OKX", Client)
    monkeypatch.setattr(v9_worker, "analyze", analyze)
    v9_worker.V9Scanner(str(store.path)).scan_once()
    assert store.event_count() == 1
    assert store.bark_status()["pending"] == 1
    assert len(store.list_candidates()) == 1
    assert len(store.get_market("ETH-USDT-SWAP", "15m")["events"]) == 1
    calls.clear()
    v9_worker.V9Scanner(str(store.path)).scan_once()
    assert calls == []
    assert store.event_count() == 1 and store.bark_status()["pending"] == 1


def test_okx_underlying_metadata_cannot_confuse_base_and_quote():
    from yoyo.monitor.v9_worker import instrument_base
    assert instrument_base({"uly": "ETH-USDC", "baseCcy": ""}) == "ETH"
    assert instrument_base({"uly": "USDC-USDT", "baseCcy": ""}) == "USDC"
    assert instrument_base({"instId": "USDC-USDT-SWAP"}) is None
    assert instrument_base({"uly": "ETH-USDT", "baseCcy": "BTC"}) is None


def test_migration_refuses_a_running_service_lock(tmp_path, monkeypatch):
    import fcntl
    from yoyo.monitor import v9_migration
    Store(tmp_path / "monitor.sqlite3")
    with (tmp_path / "service.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            v9_migration.migrate(tmp_path)
    assert not (tmp_path / "backups").exists()


def test_migration_backup_and_reset_are_private_and_idempotent(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path
    from yoyo.monitor import v9_migration
    store = Store(tmp_path / "monitor.sqlite3")
    store.upsert_event(event(NOW - STEP))

    class Clock:
        def synchronize(self): pass
        def clock(self): return NOW

    monkeypatch.setattr(v9_migration, "OKX", Clock)
    receipt = v9_migration.migrate(tmp_path)
    backup = Path(receipt["backup"])
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(str(backup)) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert store.event_count() == 0
    store.upsert_event(event(NOW + STEP))
    assert v9_migration.migrate(tmp_path)["activated_ms"] == NOW
    assert store.event_count() == 1
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
