"""V12.8 producer cutover: retain history, isolate versions, never replay pushes."""
import json

from yoyo.monitor import (DIRECT_POLICY, MODEL_PROTOCOL, SIGNAL_PROTOCOL, SIGNAL_KIND, STRATEGY_VERSION,
                          LEGACY_SIGNAL_PROTOCOL, LEGACY_SIGNAL_KIND, TIMEFRAMES)
from yoyo.monitor.notification_policy import arm_v9_bark, arm_v9_telegram, delivery_error
from yoyo.monitor.store import Store
from yoyo.monitor.version_upgrade import record_upgrade
from yoyo.monitor.signal_analytics import ledger

STEP = TIMEFRAMES["15m"]
NOW = 2_000_000 * STEP


def event(close=NOW, *, legacy=False):
    return {"protocol": LEGACY_SIGNAL_PROTOCOL if legacy else SIGNAL_PROTOCOL,
            "kind": LEGACY_SIGNAL_KIND if legacy else SIGNAL_KIND,
            "strategy_version": "spike-v9-entry-bundle-20260915-v1" if legacy else STRATEGY_VERSION,
            "v9_admitted": True, "v128_admitted": not legacy,
            "source": "live", "confirmation": "raw", "symbol": "NEIRO-USDT-SWAP",
            "venue": "okx", "timeframe": "15m", "timeframe_min": 15,
            "side": "short", "direction": "short", "bar_close_ms": close,
            "bar_open_ms": close - STEP, "detected_at_ms": close + 1,
            "price": .00009625, "risk": .000002, "initial_stop": .00009825,
            "is_closed": True, "confirmed": True, "ready": True,
            "source_sha256": "a" * 64, "performance": {"status": "unknown"}}


def test_telegram_arm_is_forward_only_idempotent_and_independent_of_bark(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    store.reset_for_v9(NOW)
    bark_before = store.get_meta("notification_policy:bark:" + DIRECT_POLICY)
    store.set_meta("notification_policy:" + DIRECT_POLICY, {"activated_ms": NOW - 10 * STEP})
    store.set_meta("notification_policy:" + MODEL_PROTOCOL, {"activated_ms": NOW - 10 * STEP})
    historical = event(NOW + STEP)
    store.upsert_event(historical)

    receipt = arm_v9_telegram(store, NOW + STEP + 1)
    assert receipt["activated_ms"] == NOW + STEP + 1
    assert arm_v9_telegram(store, NOW + 2 * STEP) == receipt
    assert store.get_meta("notification_policy:" + DIRECT_POLICY)["activated_ms"] == NOW + STEP + 1
    assert store.get_meta("notification_policy:" + MODEL_PROTOCOL)["activated_ms"] == NOW + STEP + 1
    assert store.get_meta("notification_policy:bark:" + DIRECT_POLICY) == bark_before
    assert store.telegram_status()["pending"] == 0
    assert delivery_error(store, historical, NOW + STEP + 2, "telegram") == "before_notification_policy_activation"

    current = event(NOW + 2 * STEP)
    assert delivery_error(store, current, NOW + 2 * STEP + 1, "telegram") is None
    store.upsert_event(current, notify=True)
    assert store.telegram_status()["pending"] == 1


def test_telegram_formats_raw_v128_direction_and_version(tmp_path):
    from yoyo.monitor.telegram import TelegramWorker, message

    store = Store(tmp_path / "monitor.sqlite3")
    arm_v9_telegram(store, NOW - 1)
    short = event(NOW, legacy=False)
    short["side"] = short["direction"] = "short"
    store.upsert_event(short, notify=True)
    calls = []

    class Accepted:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    worker = TelegramWorker(store, ("synthetic-token", "synthetic-chat"),
                            lambda *args, **kwargs: calls.append((args, kwargs)) or Accepted(), enabled=True)
    assert worker.deliver_once(NOW + 1)
    assert len(calls) == 1
    text = calls[0][1]["json"]["text"]
    assert "V12.8 后台信号" in text and "空头" in text
    assert "V12.8 后台信号" in message(short)


def test_upgrade_preserves_event_bytes_and_receipts_and_has_one_cutover(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    old = event(legacy=True)
    store.upsert_event(old, bark_notify=True)
    old_id = store.event_id(old)
    with store.connect() as db:
        before = db.execute("SELECT payload FROM events WHERE id=?", (old_id,)).fetchone()[0]
        db.execute("UPDATE bark_outbox SET status='sent' WHERE event_id=?", (old_id,))
    arm_v9_bark(store, NOW + 100)
    receipt = record_upgrade(store, NOW + 100)
    assert record_upgrade(store, NOW + STEP) == receipt
    assert arm_v9_bark(store, NOW + STEP)["activated_ms"] == NOW + 100
    with store.connect() as db:
        assert db.execute("SELECT payload FROM events WHERE id=?", (old_id,)).fetchone()[0] == before
        assert db.execute("SELECT status FROM bark_outbox WHERE event_id=?", (old_id,)).fetchone()[0] == "sent"
    assert delivery_error(store, old, NOW + 101, "bark") is not None
    assert delivery_error(store, event(), NOW + 101, "bark") is not None
    new = event(NOW + STEP)
    assert delivery_error(store, new, NOW + STEP + 1, "bark") is None
    assert store.event_id(event()) != old_id


def test_ledger_separates_recomputed_warmup_live_and_legacy_before_stats(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    arm_v9_bark(store, NOW + 100)
    for row in (event(legacy=True), event(), event(NOW + STEP)):
        store.upsert_event(row)
    for scope, protocol, close in (("legacy", LEGACY_SIGNAL_PROTOCOL, NOW),
                                    ("warmup", SIGNAL_PROTOCOL, NOW),
                                    ("live", SIGNAL_PROTOCOL, NOW + STEP)):
        result = ledger(store, now=NOW + STEP + 1, source=scope)
        assert result["stats"]["total"] == 1
        assert result["items"][0]["protocol"] == protocol
        assert result["items"][0]["bar_close_ms"] == close
        assert result["items"][0]["is_fresh"] == (scope == "live")


def test_api_defaults_exclude_legacy_and_exposes_archive(tmp_path):
    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app
    app = create_app(tmp_path, start_monitor=False)
    store = app.state.monitor.store
    app.state.monitor.client.clock = lambda: NOW + STEP + 1
    arm_v9_bark(store, NOW - 1)
    store.upsert_event(event(legacy=True))
    store.upsert_event(event())
    with TestClient(app) as client:
        for route in ("/api/signals", "/api/signals?confirmation=raw", "/api/signals?view=ledger"):
            response = client.get(route)
            assert response.status_code == 200
            assert [r["protocol"] for r in response.json()["items"]] == [SIGNAL_PROTOCOL]
        archive = client.get("/api/signals?view=ledger&source=legacy").json()
        assert [r["protocol"] for r in archive["items"]] == [LEGACY_SIGNAL_PROTOCOL]


def test_worker_rebuild_has_h1_input_and_no_historical_push(tmp_path, monkeypatch):
    from yoyo.monitor import v9_worker
    store = Store(tmp_path / "monitor.sqlite3")
    store.set_meta("migration:spike-v9-reset-v1", {"activated_ms": NOW - 10 * STEP})
    clock = [NOW + 100]
    calls = []

    class Client:
        def synchronize(self): pass
        def clock(self): return clock[0]
        def instruments(self): return [{"instId": "NEIRO-USDT-SWAP", "tickSz": ".00000001", "uly": "NEIRO-USDT"}]
        def candles(self, symbol, timeframe, previous=None, limit=720):
            step = TIMEFRAMES[timeframe]
            end = clock[0] // step * step
            return [{"t": end - step, "o": 100., "h": 101., "l": 99., "c": 100., "v": 1.}], 0

    def analyze(candles, higher, timeframe, **kwargs):
        calls.append(timeframe)
        if timeframe == "15m":
            assert higher and higher[-1]["t"] == clock[0] // TIMEFRAMES["1H"] * TIMEFRAMES["1H"] - TIMEFRAMES["1H"]
        return {"state": {"protocol": SIGNAL_PROTOCOL, "phase": "ready", "ready": True, "timeframe": timeframe},
                "chart": candles, "events": [event(candles[-1]["t"] + STEP)] if timeframe == "15m" else []}

    monkeypatch.setattr(v9_worker, "OKX", Client)
    monkeypatch.setattr(v9_worker, "analyze", analyze)
    v9_worker.V9Scanner(str(store.path)).scan_once()
    assert calls.index("1H") < calls.index("15m")
    assert store.event_count() == 1
    assert store.bark_status()["pending"] == 0
    assert store.telegram_status()["pending"] == 0
    calls.clear()
    v9_worker.V9Scanner(str(store.path)).scan_once()
    assert calls == []
    clock[0] += STEP
    v9_worker.V9Scanner(str(store.path)).scan_once()
    assert store.event_count() == 2
    assert store.bark_status()["pending"] == 1
    assert store.telegram_status()["pending"] == 1
    v9_worker.V9Scanner(str(store.path)).scan_once()
    assert store.bark_status()["pending"] == 1
    assert store.telegram_status()["pending"] == 1


def test_lines_refresh_and_queries_never_relabel_old_positions(tmp_path):
    from yoyo.monitor.spike_lines_worker import LinesBook
    from yoyo.monitor.spike_lines_api import ledger as lines_ledger, events as line_events
    from yoyo.monitor.joint_notifications import JOINT_PROTOCOL
    book = LinesBook(tmp_path / "lines.sqlite3")
    old = {"id": "old", "kind": "joint", "symbol": "NEIRO-USDT-SWAP", "timeframe": "15m",
           "bar_open_ms": NOW - STEP, "bar_close_ms": NOW, "detected_at_ms": NOW + 1,
           "protocol": "spike-v11-2-lines-monitor-v1", "close": 1., "side": "long",
           "performance": {"status": "active", "basis": "old", "current_r": 5.}}
    new = dict(old, id="new", protocol=JOINT_PROTOCOL,
               performance={"status": "active", "basis": "new", "current_r": 2.})
    book.insert([old, new])
    book.set_meta("performance_policy", {"version": "new", "changed_at_ms": NOW + 100})
    book.set_meta("activation:v128", {"activated_ms": NOW - 1})
    book.refresh_performance(old["symbol"], "15m", {"new": {"status": "active", "basis": "new", "current_r": 3.}}, protocol=JOINT_PROTOCOL)
    with book.connect() as db:
        saved = json.loads(db.execute("SELECT payload FROM events WHERE id='old'").fetchone()[0])
    assert saved == old
    assert [e["id"] for e in line_events(book.path, kind="joint", now_ms=NOW + 1)] == ["new"]
    current = lines_ledger(book.path, kind="joint", now_ms=NOW + 100)
    legacy = lines_ledger(book.path, kind="joint", now_ms=NOW + 100, performance_version="legacy")
    assert current["stats"]["floating_r"] == 3.
    assert legacy["stats"]["floating_r"] == 5.
    assert legacy["items"][0]["is_fresh"] is False
