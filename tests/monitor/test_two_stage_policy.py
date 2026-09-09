"""Synthetic cutover/API/scan-path tests; no market inference or live delivery."""
from types import SimpleNamespace

import pytest

from model_fixture import model_event
from yoyo.monitor import DIRECT_POLICY, DIRECT_TIMEFRAMES, FRESH_MS, MODEL_KIND, MODEL_PROTOCOL, SIGNAL_KIND, TIMEFRAMES
from yoyo.monitor import service, snapshot
from yoyo.monitor.notification_policy import delivery_error, is_direct_start
from yoyo.monitor.store import Store


def activate(store, since):
    for protocol in (MODEL_PROTOCOL, DIRECT_POLICY):
        store.activate_notification_policy(since, protocol=protocol, retire_obsolete=False)
        store.activate_bark_policy(since, protocol=protocol, retire_obsolete=False)
        for tf in (DIRECT_TIMEFRAMES if protocol == DIRECT_POLICY else ("15m", "1H", "4H")):
            store.activate_timeframe_policy(tf, since, protocol=protocol)


@pytest.mark.parametrize("timeframe", ["1H", "4H"])
@pytest.mark.parametrize("channel", ["telegram", "bark"])
def test_new_cutover_and_freshness_boundaries(tmp_path, timeframe, channel):
    e = model_event(timeframe=timeframe, wait=0)["indicator"]
    store = Store(tmp_path / "test.db")
    # The original pre-YOLO notification policy cannot enable this new stage.
    store.activate_notification_policy(0)
    store.activate_bark_policy(0)
    assert delivery_error(store, e, e["bar_close_ms"], channel) is not None
    activate(store, e["bar_close_ms"] - 1)
    assert delivery_error(store, e, e["bar_close_ms"], channel) is None
    assert delivery_error(store, e, e["bar_close_ms"] + FRESH_MS, channel) is None
    assert delivery_error(store, e, e["bar_close_ms"] + FRESH_MS + 1, channel) == "signal_expired"
    assert delivery_error(store, e, e["bar_close_ms"] - 1, channel) == "signal_expired"
    prefix = "notification_policy:" + ("bark:" if channel == "bark" else "")
    store.set_meta(prefix + DIRECT_POLICY, {"activated_ms": e["bar_close_ms"]})
    assert delivery_error(store, e, e["bar_close_ms"], channel) is not None


@pytest.mark.parametrize("changes", [
    {"price": 0}, {"price": float("nan")}, {"price": True}, {"symbol": "TEST\n-SWAP"},
    {"bar_close_ms": True}, {"bar_open_ms": -1}, {"focus_start_ms": 0},
    {"confirmed": False}, {"tv_marker_visible": False}, {"previous_md": .2},
    {"md": .1}, {"near_zero_bars": 11}, {"timeframe": "15m"},
])
def test_direct_stage_rejects_incomplete_or_wrong_arrow(changes):
    e = model_event(wait=0)["indicator"]
    assert is_direct_start(e)
    assert not is_direct_start(dict(e, **changes))


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    def worker(store):
        return SimpleNamespace(creds=("synthetic", "synthetic"), status=lambda: {})
    monkeypatch.setattr(service, "TelegramWorker", worker)
    monkeypatch.setattr(service, "BarkWorker", worker)
    store = Store(tmp_path / "test.db")
    return service.Monitor(store, client=SimpleNamespace(clock=lambda: 0))


@pytest.mark.parametrize("timeframe", ["15m", "1H", "4H"])
def test_scan_records_direct_before_registration_with_causal_snapshot(monitor, monkeypatch, timeframe):
    e = model_event(timeframe=timeframe, wait=0)["indicator"]
    activate(monitor.store, e["bar_close_ms"] - 1)
    captures = []
    def render(event, chart):
        captures.append(event)
        return b"\x89PNG\r\n\x1a\nsynthetic"
    monkeypatch.setattr(snapshot, "render_signal", render)
    assert monitor.record_arrow(e, [], e["bar_close_ms"] + 1)
    # register_candidate's second upsert must not swallow or duplicate the first leg.
    monitor.model_gate.register(e)
    assert not monitor.record_arrow(e, [], e["bar_close_ms"] + 2)
    expected = int(timeframe in DIRECT_TIMEFRAMES)
    assert len(captures) == expected
    assert monitor.store.telegram_status()["pending"] == expected
    assert monitor.store.bark_status()["pending"] == expected


def test_render_failure_does_not_consume_bark_or_direct_text(monitor, monkeypatch):
    e = model_event(wait=0)["indicator"]
    activate(monitor.store, e["bar_close_ms"] - 1)
    monkeypatch.setattr(snapshot, "render_signal", lambda *args: (_ for _ in ()).throw(ValueError("synthetic")))
    monitor.record_arrow(e, [], e["bar_close_ms"] + 1)
    assert monitor.store.notification_media_status() == {"snapshots": 0, "render_fallbacks": 1}
    assert monitor.store.bark_status()["pending"] == 1


def test_snapshot_import_failure_still_queues_both_channels(monitor, monkeypatch):
    import sys
    e = model_event(wait=0)["indicator"]
    activate(monitor.store, e["bar_close_ms"] - 1)
    monkeypatch.setitem(sys.modules, "yoyo.monitor.snapshot", None)
    monitor.record_arrow(e, [], e["bar_close_ms"] + 1)
    assert monitor.store.telegram_status()["pending"] == monitor.store.bark_status()["pending"] == 1
    assert monitor.store.notification_media_status()["render_fallbacks"] == 1


def test_stale_scan_and_existing_history_never_replay(monitor):
    e = model_event(wait=0)["indicator"]
    activate(monitor.store, e["bar_close_ms"] - 1)
    monitor.record_arrow(e, [], e["bar_close_ms"] + 1, stale=True)
    monitor.record_arrow(e, [], e["bar_close_ms"] + 2)
    assert monitor.store.telegram_status()["pending"] == monitor.store.bark_status()["pending"] == 0


def test_additive_restart_keeps_both_pending_stages_and_cutovers(tmp_path):
    store = Store(tmp_path / "test.db")
    e = model_event(wait=0)
    cutover = e["bar_close_ms"] - 1
    activate(store, cutover)
    for event in (e["indicator"], e):
        store.upsert_event(event, notify=True, bark_notify=True)
    activate(Store(store.path), e["bar_close_ms"] + 1000)
    assert store.telegram_status()["pending"] == store.bark_status()["pending"] == 2
    assert store.timeframe_activation("1H", protocol=DIRECT_POLICY) == cutover
    assert store.notification_status("telegram")["pending"] == 2


def test_direct_api_is_forward_only_keeps_model_default_and_filters_before_limit(tmp_path, monkeypatch):
    from yoyo.monitor.server import create_app
    monkeypatch.setattr(service, "TelegramWorker", lambda store: SimpleNamespace(creds=None))
    monkeypatch.setattr(service, "BarkWorker", lambda store: SimpleNamespace(creds=None))
    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    store = monitor.store
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/signals")
    def get(kind=MODEL_KIND, **filters):
        return endpoint(limit=1, symbol=None, timeframe=filters.get("timeframe"), kind=kind, side=None)
    event = model_event(wait=0)
    old = model_event(wait=0, close=event["bar_close_ms"] - TIMEFRAMES["1H"])["indicator"]
    store.upsert_event(old, notify=True)
    store.finish(store.event_id(old), "sent", message_id=1)
    assert get(SIGNAL_KIND)["items"] == []
    activate(store, event["bar_close_ms"] - 1)
    store.upsert_event(event["indicator"], notify=True, bark_notify=True)
    store.upsert_event(event)
    # A newer 15m arrow cannot hide a valid 1H start behind the requested limit.
    store.upsert_event(model_event(timeframe="15m", wait=0, close=event["bar_close_ms"] + TIMEFRAMES["15m"])["indicator"])
    monitor.client = SimpleNamespace(clock=lambda: event["bar_close_ms"] + 1000)
    result = get(SIGNAL_KIND)
    assert result["total"] == len(result["items"]) == 1
    assert result["items"][0]["notification_status"] == "pending"
    assert result["items"][0]["is_fresh"] is True
    assert get(SIGNAL_KIND, timeframe="15m")["items"] == []
    assert get()["items"][0]["kind"] == MODEL_KIND
    assert store.notification_status("telegram").get("sent", 0) == 0
    assert store.telegram_status()["sent"] == 1


@pytest.mark.parametrize("channel", ["telegram", "bark"])
@pytest.mark.parametrize("first_state", ["pending", "sending", "sent", "failed", "unknown", "skipped", "expired"])
def test_confirmation_respects_first_leg_order_without_blocking_on_terminal_receipts(tmp_path, channel, first_state):
    store = Store(tmp_path / "test.db")
    event = model_event(wait=0 if first_state != "expired" else 2)
    original = event["indicator"]
    activate(store, original["bar_close_ms"] - 1)
    for e in (original, event):
        store.upsert_event(e, notify=True, bark_notify=True)
    finish = store.finish if channel == "telegram" else store.finish_bark
    claim = store.claim if channel == "telegram" else store.claim_bark
    now = event["bar_close_ms"] + 1000
    finish(store.event_id(original), "pending" if first_state == "expired" else first_state, due_ms=now + 60_000)
    row = claim(now)
    if first_state in ("sending", "pending"):
        assert row is None
        finish(store.event_id(original), "sent")
        row = claim(now)
    assert row["event"]["kind"] == MODEL_KIND


def test_full_scan_model_unavailable_still_queues_direct_and_restart_preserves_both_legs(monitor, monkeypatch):
    from test_service import FakeMarket, NOW, INSTRUMENT
    client = FakeMarket()
    client.synchronize = lambda: None
    client.instruments = lambda: [INSTRUMENT]
    for tf in ("15m", "1H", "4H"):
        client.history[tf][-1].update(o=120., h=121., l=119., c=120.)
    monitor.client = client
    activate(monitor.store, NOW - 120_000)
    monkeypatch.setattr(snapshot, "render_signal", lambda *args: b"\x89PNG\r\n\x1a\nsynthetic")
    monitor.model_gate.detector = SimpleNamespace(predict=lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable")))
    monitor.scan()
    assert monitor.notification_ready.is_set()
    assert monitor.store.telegram_status()["pending"] == monitor.store.bark_status()["pending"] == 2
    assert {e["timeframe"] for e in monitor.store.list_events(direct_only=True)} == {"1H", "4H"}
    assert monitor.store.event_count(MODEL_KIND, MODEL_PROTOCOL) == 0
    # Both delivery legs survive startup policy activation, without new warmup sends.
    confirmation = model_event(close=NOW - 60_000, wait=0)
    monitor.store.upsert_event(confirmation, notify=True, bark_notify=True)
    restarted = service.Monitor(monitor.store, client=client)
    restarted.scan()
    assert monitor.store.telegram_status()["pending"] == monitor.store.bark_status()["pending"] == 3
    assert monitor.store.timeframe_activation("1H", protocol=DIRECT_POLICY) == NOW - 120_000
