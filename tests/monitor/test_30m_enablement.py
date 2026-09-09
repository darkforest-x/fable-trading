"""Thirty-minute stream rollout: independent cutovers and public causal data.

Only synthetic candles, notification journals, and senders are used. No
production runtime, network, credentials, or inference is accessed.
"""
from types import SimpleNamespace

import pytest

from model_fixture import model_event
from test_service import FakeMarket, INSTRUMENT, NOW
from yoyo.monitor import (DIRECT_POLICY, DIRECT_TIMEFRAMES, MODEL_PROTOCOL,
                          MONITORED_TIMEFRAMES, HIGHER_TIMEFRAME, TIMEFRAMES, TV_INTERVALS)
from yoyo.monitor import service, tradingview, yolo_detector
from yoyo.monitor.notification_policy import delivery_error
from yoyo.monitor.okx import MarketError, OKX, parse_rows
from yoyo.monitor.store import Store


def test_thirty_minute_adapters_share_the_same_timeframe_contract():
    assert TIMEFRAMES["30m"] == 1_800_000
    assert HIGHER_TIMEFRAME["30m"] == "2H"  # Pine's <= 30 minute auto-HTF branch.
    assert MONITORED_TIMEFRAMES == DIRECT_TIMEFRAMES == ("5m", "15m", "30m", "1H", "4H", "1Dutc")
    assert set(yolo_detector.TIMEFRAME_MS) == set(TV_INTERVALS) == set(MONITORED_TIMEFRAMES)
    assert yolo_detector.TIMEFRAME_MS["30m"] == 1_800_000
    assert tradingview.INTERVALS["30m"] == TV_INTERVALS["30m"] == "30"


def raw_candle(opening, confirmed="1"):
    return [str(opening), "100", "101", "99", "100", "1", "1", "100", confirmed]


def test_thirty_minute_public_candles_require_confirmation_close_and_exchange_grid():
    rows = [raw_candle(0), raw_candle(1_800_000, "0"), raw_candle(3_600_000)]
    assert [row["t"] for row in parse_rows(rows, "30m", 3_600_000)] == [0]
    with pytest.raises(MarketError, match="alignment"):
        parse_rows([raw_candle(1)], "30m", 3_600_000)


def test_thirty_minute_fetch_uses_native_public_bar_and_skips_current_cached_close():
    client = OKX()
    client.clock = lambda: 3_600_001
    calls = []
    def get(path, params):
        calls.append((path, params))
        return [raw_candle(1_800_000), raw_candle(0)]
    client.get = get
    bars, gaps = client.candles("BTC-USDT-SWAP", "30m")
    assert calls == [("/api/v5/market/candles", {"instId": "BTC-USDT-SWAP", "bar": "30m", "limit": "300"})]
    assert gaps == 0 and [row["t"] for row in bars] == [0, 1_800_000]
    assert client.candles("BTC-USDT-SWAP", "30m", bars) == (bars, 0)
    assert len(calls) == 1


def test_thirty_minute_rollout_preserves_every_existing_stage_and_does_not_replay(tmp_path, monkeypatch):
    def worker(store):
        return SimpleNamespace(creds="synthetic-device", status=lambda: {})
    monkeypatch.setattr(service, "TelegramWorker", lambda store: SimpleNamespace(creds=None, status=lambda: {}))
    monkeypatch.setattr(service, "BarkWorker", worker)
    store = Store(tmp_path / "monitor.sqlite")
    existing = ("5m", "15m", "1H", "4H", "1Dutc")
    cutovers = {}
    for number, protocol in enumerate((DIRECT_POLICY, MODEL_PROTOCOL)):
        store.activate_bark_policy(NOW - 240_000 + number, protocol=protocol, retire_obsolete=False)
        for index, timeframe in enumerate(existing):
            cutover = NOW - 180_000 + number * 10 + index
            cutovers[protocol, timeframe] = cutover
            store.activate_timeframe_policy(timeframe, cutover, protocol=protocol)
        assert store.timeframe_activation("30m", protocol=protocol) is None
    with store.connect() as db:
        before_meta = {row["key"]: row["payload"] for row in db.execute("SELECT * FROM meta")}
    client = FakeMarket()
    client.synchronize = lambda: None
    client.instruments = lambda: [INSTRUMENT]
    for timeframe in MONITORED_TIMEFRAMES:
        client.history[timeframe][-1].update(o=120., h=121., l=119., c=120.)
    monitor = service.Monitor(store, client=client)
    monitor.scan()
    assert store.get_meta("scan")["completed"] == 6
    assert store.get_meta("scan")["errors"] == 0
    assert monitor.chart(INSTRUMENT["instId"], "30m")["state"]["higher_timeframe"] == "2H"
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        assert store.timeframe_activation("30m", protocol=protocol) == NOW
        assert store.get_meta("notification_policy:" + protocol) is None
    for (protocol, timeframe), cutover in cutovers.items():
        assert store.timeframe_activation(timeframe, protocol=protocol) == cutover
    with store.connect() as db:
        after_meta = {row["key"]: row["payload"] for row in db.execute("SELECT * FROM meta")}
    assert all(after_meta[key] == value for key, value in before_meta.items())
    assert store.telegram_status()["pending"] == 0
    assert store.bark_status()["pending"] == 3
    for timeframe in ("5m", "15m", "30m"):
        assert all(e["bark_notification_status"] == "history"
                   for e in store.list_events(timeframe=timeframe))
    old = next(e for e in store.list_events() if e["timeframe"] == "30m")
    assert old["bar_close_ms"] < NOW and old["bark_notification_status"] == "history"
    assert store.list_candidates(timeframe="30m") == []
    assert not monitor.record_arrow(old, [], NOW + 1)
    later = model_event(timeframe="30m", wait=0, close=old["bar_close_ms"] + TIMEFRAMES["30m"])
    for leg in (later["indicator"], later):
        assert delivery_error(store, leg, later["bar_close_ms"], "bark") == "bark_timeframe_muted_by_owner"
    assert monitor.record_arrow(later["indicator"], [], later["bar_close_ms"])
    assert store.upsert_event(later)
    displayed = store.list_events(timeframe="30m")
    assert {store.event_id(later["indicator"]), store.event_id(later)} <= {e["id"] for e in displayed}
    assert all(e["bark_notification_status"] == "history" for e in displayed)
    assert store.bark_status()["pending"] == 3
    # Restart preserves the permitted queues and never replays muted 30m history.
    restarted = service.Monitor(Store(store.path), client=client)
    restarted.scan()
    assert store.bark_status()["pending"] == 3
    assert store.telegram_status()["pending"] == 0
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        assert store.timeframe_activation("30m", protocol=protocol) == NOW
    for (protocol, timeframe), cutover in cutovers.items():
        assert store.timeframe_activation(timeframe, protocol=protocol) == cutover


def withdrawn_ten_minute_event(monkeypatch, *, symbol="LEGACY-USDT-SWAP"):
    """Construct an aligned unsupported-period proof without enabling a stream."""
    with monkeypatch.context() as temporary:
        temporary.setitem(TIMEFRAMES, "10m", 600_000)
        return model_event(timeframe="10m", wait=0, close=NOW - 60_000,
                           original_changes={"symbol": symbol})


def test_withdrawal_retires_only_pending_bark_and_model_work_and_is_idempotent(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite")
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        store.set_meta("notification_timeframe:" + protocol + ":10m", {"activated_ms": 12345})
        store.activate_bark_policy(12345, protocol=protocol, retire_obsolete=False)
    ids = {}
    originals = {}
    for index, status in enumerate(("pending", "sent", "unknown", "sending")):
        event = withdrawn_ten_minute_event(monkeypatch, symbol=f"OLD{index}-USDT-SWAP")
        original = event["indicator"]
        assert store.upsert_event(original, notify=True, bark_notify=True)
        assert store.upsert_event(event, notify=True, bark_notify=True)
        ids[status] = [store.event_id(original), store.event_id(event)]
        for eid in ids[status]:
            if status != "pending":
                store.finish_bark(eid, status, server_timestamp=99 if status == "sent" else None)
        proof = dict(event["model"], status="pending")
        assert store.register_candidate(original, proof)
        originals[status] = store.event_id(original)
        if status in ("sent", "unknown"):
            # Existing terminal model proofs must also remain immutable.
            store.update_candidate(store.event_id(original), event["model"])
        elif status == "sending":
            store.update_candidate(store.event_id(original), dict(proof, status="error", reason="inference_error"))
    active = model_event(timeframe="1H", wait=0, close=NOW - 60_000)
    store.upsert_event(active["indicator"], bark_notify=True)
    store.register_candidate(active["indicator"], dict(active["model"], status="pending"))
    with store.connect() as db:
        saved_meta = {r["key"]: r["payload"] for r in db.execute("SELECT * FROM meta")}
        saved_tg = [tuple(r) for r in db.execute("SELECT * FROM outbox ORDER BY event_id")]
        saved_terminals = {r["event_id"]: tuple(r) for r in db.execute("SELECT * FROM bark_outbox WHERE status IN ('sent','unknown')")}
        saved_events = [tuple(r) for r in db.execute("SELECT * FROM events ORDER BY id")]
    # A process-interrupted send stays uncertain; never resend it after withdrawal.
    store.recover_outbox()
    assert store.retire_disabled_timeframes() == {"bark_pending": 2, "model_candidates": 2}
    assert store.retire_disabled_timeframes() == {"bark_pending": 0, "model_candidates": 0}
    with store.connect() as db:
        assert saved_meta == {r["key"]: r["payload"] for r in db.execute("SELECT * FROM meta")}
        assert saved_tg == [tuple(r) for r in db.execute("SELECT * FROM outbox ORDER BY event_id")]
        assert saved_events == [tuple(r) for r in db.execute("SELECT * FROM events ORDER BY id")]
        for eid, previous in saved_terminals.items():
            assert tuple(db.execute("SELECT * FROM bark_outbox WHERE event_id=?", (eid,)).fetchone()) == previous
        for eid in ids["pending"]:
            row = db.execute("SELECT status,error,attempts FROM bark_outbox WHERE event_id=?", (eid,)).fetchone()
            assert tuple(row) == ("skipped", "timeframe_disabled_by_owner", 0)
        for eid in ids["sending"]:
            row = db.execute("SELECT status,error FROM bark_outbox WHERE event_id=?", (eid,)).fetchone()
            assert tuple(row) == ("unknown", "process_interrupted_during_delivery")
    legacy = {e["id"]: e for e in store.list_candidates(timeframe="10m")}
    for state in ("pending", "sending"):
        assert legacy[originals[state]]["model"]["status"] == "disabled"
        assert legacy[originals[state]]["model"]["reason"] == "timeframe_disabled_by_owner"
    for state in ("sent", "unknown"):
        assert legacy[originals[state]]["model"]["status"] == "confirmed"
    assert store.list_candidates(timeframe="10m", pending_only=True) == []
    assert len(store.list_candidates(timeframe="1H", pending_only=True)) == 1
    assert store.bark_status()["pending"] == 1


def test_sender_rechecks_withdrawn_raw_and_yolo_even_without_startup_migration(tmp_path, monkeypatch):
    from yoyo.monitor.bark import BarkWorker
    store = Store(tmp_path / "monitor.sqlite")
    event = withdrawn_ten_minute_event(monkeypatch)
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        store.activate_bark_policy(0, protocol=protocol, retire_obsolete=False)
        store.set_meta("notification_timeframe:" + protocol + ":10m", {"activated_ms": 0})
    for leg in (event["indicator"], event):
        assert delivery_error(store, leg, NOW, "bark") == "timeframe_disabled_by_owner"
        store.upsert_event(leg, bark_notify=True)
    def forbidden(*args, **kwargs):
        pytest.fail("withdrawn ten-minute event reached network sender")
    worker = BarkWorker(store, creds="synthetic-device", sender=forbidden)
    assert worker.deliver_once(NOW)
    assert worker.deliver_once(NOW)
    assert not worker.deliver_once(NOW)
    with store.connect() as db:
        assert [tuple(r) for r in db.execute("SELECT status,error FROM bark_outbox")] == [
            ("skipped", "timeframe_disabled_by_owner"), ("skipped", "timeframe_disabled_by_owner")]


def test_withdrawn_period_never_enters_model_or_current_market_grid(tmp_path, monkeypatch):
    import threading
    from yoyo.monitor.model_gate import ModelGate
    store = Store(tmp_path / "monitor.sqlite")
    event = withdrawn_ten_minute_event(monkeypatch)
    raw = event["indicator"]
    # Simulate an old database candidate before the startup cleanup.
    store.register_candidate(raw, dict(event["model"], status="pending"))
    gate = ModelGate(store, lambda: NOW, threading.Event(), detector=SimpleNamespace(
        predict=lambda *a: pytest.fail("withdrawn period reached inference")))
    assert gate.register(raw) is False
    gate.submit(raw["symbol"], "10m", [])
    assert not gate._queue
    gate.process(raw["symbol"], "10m", [])  # Does not index the removed duration.
    for timeframe in ("10m", "30m"):
        store.upsert_market(dict(symbol=raw["symbol"], timeframe=timeframe, active=True,
                                 phase="ready", bar_close_ms=NOW-60_000))
    monkeypatch.setattr(service, "TelegramWorker", lambda store: SimpleNamespace(status=lambda: {}))
    monkeypatch.setattr(service, "BarkWorker", lambda store: SimpleNamespace(creds=None, status=lambda: {}))
    monitor = service.Monitor(store, client=FakeMarket())
    assert [r["timeframe"] for r in monitor.markets()] == ["30m"]
    assert {r["timeframe"] for r in store.list_markets()} == {"10m", "30m"}


def test_startup_withdraws_before_any_delivery_or_model_worker_starts(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite")
    event = withdrawn_ten_minute_event(monkeypatch)
    store.upsert_event(event["indicator"], bark_notify=True)
    store.register_candidate(event["indicator"], dict(event["model"], status="pending"))
    monkeypatch.setattr(service, "TelegramWorker", lambda store: SimpleNamespace(status=lambda: {}))
    monkeypatch.setattr(service, "BarkWorker", lambda store: SimpleNamespace(creds=None, status=lambda: {}))
    observed = []
    def thread(*, target, name, daemon):
        def start():
            assert store.bark_status()["pending"] == 0
            assert store.list_candidates(timeframe="10m")[0]["model"]["status"] == "disabled"
            observed.append(name)
        return SimpleNamespace(start=start, join=lambda timeout: None)
    monkeypatch.setattr(service.threading, "Thread", thread)
    monitor = service.Monitor(store, client=FakeMarket())
    monitor.start()
    assert observed == ["impulse-scan", "impulse-model", "impulse-bark"]
