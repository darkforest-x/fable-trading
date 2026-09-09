"""Regression scenarios for cache recovery, HTF availability and service health.

All prices, clocks and API replies are synthetic. No network call, real
Telegram credential loading, worker thread, or persistent runtime is used.
"""
from copy import deepcopy

import pytest
import requests

from yoyo.monitor import TIMEFRAMES, FRESH_MS, MODEL_PROTOCOL, MODEL_KIND, MODEL_SHA256, MODEL_MAX_WAIT
from yoyo.monitor import okx, service
from yoyo.monitor.okx import MarketError, OKX
from yoyo.monitor.service import Monitor
from yoyo.monitor.store import Store


SYMBOL = "TEST-USDT-SWAP"
INSTRUMENT = dict(instId=SYMBOL, settleCcy="USDT", tickSz="0.01")
NOW = 400 * TIMEFRAMES["1Dutc"] + 60_000


class NoTelegram:
    def __init__(self, store, *, enabled=False):
        self.store = store
        self.enabled = enabled
        self.creds = None
        assert enabled is False

    def status(self):
        return dict(configured=False, enabled=False)


class FakeMarket:
    """Always return the same confirmed bars, including after fault recovery."""

    def __init__(self):
        self.fail = set()
        self.offset_ms = 0
        self.requests = 0
        self.history = {}
        for timeframe, period in TIMEFRAMES.items():
            end = NOW // period * period
            self.history[timeframe] = [
                dict(t=end - (400 - i) * period, o=100., h=101., l=99., c=100., v=1.)
                for i in range(400)
            ]

    def clock(self):
        return NOW

    def candles(self, symbol, timeframe, previous=None):
        assert symbol == SYMBOL
        if timeframe in self.fail:
            raise MarketError("synthetic_endpoint_failure")
        return self.history[timeframe], 0


class FakeDetector:
    """Return a deterministic same-setup proof; never load real weights."""
    def __init__(self):
        self.calls = []
        self.fail_once = False

    def status(self):
        return {"ready": True}

    def predict(self, candles, symbol, timeframe, endpoint_ms):
        assert candles[-1]["t"] == endpoint_ms
        assert all(row["t"] <= endpoint_ms for row in candles)
        self.calls.append(endpoint_ms)
        if self.fail_once:
            self.fail_once = False
            raise ValueError("synthetic_model_failure")
        step = TIMEFRAMES[timeframe]
        return [dict(model_sha256=MODEL_SHA256, confidence=.65,
                     detection_id="synthetic-service-detection", input_pixel_sha256="0" * 64,
                     side="long" if candles[-1]["md"] > 0 else "short", structural_pass=True,
                     window_len=18, window_start_ms=endpoint_ms - 17 * step,
                     window_end_ms=endpoint_ms, core_start_ms=endpoint_ms - 5 * step,
                     core_end_ms=endpoint_ms - 2 * step, core_length_bars=4, post_bars=2)]


def configured_monitor(store, client, tg=None, bark=NOW - 120_000, *,
                       timeframe_policy=None):
    """Channel/stream cutovers precede registration; model processing stays explicit."""
    if tg is not None:
        # Historical Telegram metadata must not re-enable the disabled channel.
        store.activate_notification_policy(tg, protocol=MODEL_PROTOCOL)
    store.activate_bark_policy(bark, protocol=MODEL_PROTOCOL)
    policy = ({tf: NOW - 120_000 for tf in ("15m", "1H", "4H")}
              if timeframe_policy is None else timeframe_policy)
    for timeframe, activation in policy.items():
        if activation is not None:
            store.activate_timeframe_policy(timeframe, activation, protocol=MODEL_PROTOCOL)
    monitor = Monitor(store, client=client)
    monitor.bark.creds = "synthetic-device"
    monitor.bark_since = bark
    monitor.model_gate.detector = FakeDetector()
    return monitor


def process_model(monitor, timeframe):
    chart = monitor.chart(SYMBOL, timeframe)
    assert not chart["state"]["stale"]
    monitor.model_gate.process(SYMBOL, timeframe, chart["candles"])


@pytest.fixture(autouse=True)
def no_external_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("service regression test attempted a real network request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(service, "TelegramWorker", NoTelegram)


def market(store, timeframe):
    return next(row for row in store.list_markets()
                if row["symbol"] == SYMBOL and row["timeframe"] == timeframe)


@pytest.mark.parametrize("timeframe", ["15m", "1H", "4H"])
def test_fetch_failure_then_same_timestamp_recovery_restores_chart_and_market(tmp_path, timeframe):
    store = Store(tmp_path / "monitor.sqlite3")
    client = FakeMarket()
    monitor = Monitor(store, client=client)
    assert monitor.scan_symbol(INSTRUMENT) == []
    first = deepcopy(monitor.chart(SYMBOL, timeframe))
    assert first["state"]["ready"] and first["state"]["phase"] == "ready"
    assert first["state"]["stale"] is False

    client.fail.add(timeframe)
    assert monitor.scan_symbol(INSTRUMENT)
    failed = monitor.chart(SYMBOL, timeframe)
    assert failed["state"]["phase"] == "loading"
    assert failed["state"]["stale"] is True
    assert failed["state"]["error"] == "market_data_unavailable"
    assert market(store, timeframe)["error"] == "market_data_unavailable"
    # Price history remains reviewable, but it must not look healthy.
    assert failed["candles"] == first["candles"]

    client.fail.clear()
    assert monitor.scan_symbol(INSTRUMENT) == []
    recovered = monitor.chart(SYMBOL, timeframe)
    assert recovered["candles"][-1]["t"] == first["candles"][-1]["t"]
    for restored in (recovered["state"], market(store, timeframe)):
        assert restored["phase"] == "ready"
        assert restored["ready"] is True
        assert restored["stale"] is False
        assert not restored.get("error")


@pytest.mark.parametrize("timeframe,higher", [("15m", "1H"), ("1H", "4H"), ("4H", "1Dutc")])
def test_missing_htf_then_recovery_recomputes_same_local_candle(tmp_path, timeframe, higher):
    store = Store(tmp_path / "monitor.sqlite3")
    client = FakeMarket()
    client.fail.add(higher)
    monitor = Monitor(store, client=client)
    monitor.scan_symbol(INSTRUMENT)
    missing = deepcopy(monitor.chart(SYMBOL, timeframe))
    assert missing["state"]["htf_side"] == "unknown"
    assert missing["state"]["htf_known"] is False
    assert missing["state"]["htf_long_allowed"] is None
    assert missing["state"]["htf_short_allowed"] is None

    client.fail.clear()
    monitor.scan_symbol(INSTRUMENT)
    restored = monitor.chart(SYMBOL, timeframe)
    state = restored["state"]
    assert restored["candles"][-1]["t"] == missing["candles"][-1]["t"]
    assert state["htf_known"] is True and state["htf_side"] == "flat"
    assert state["htf_long_allowed"] is True and state["htf_short_allowed"] is True
    assert state["htf_bar_close_ms"] <= state["bar_open_ms"]
    # Losing that endpoint again must also remove the old permission.
    client.fail.add(higher)
    monitor.scan_symbol(INSTRUMENT)
    assert monitor.chart(SYMBOL, timeframe)["state"]["htf_side"] == "unknown"


def test_outbox_recovery_waits_until_start_and_precedes_worker_launch(tmp_path, monkeypatch):
    store = Store(tmp_path / "monitor.sqlite3")
    store.upsert_event(dict(protocol="test", symbol=SYMBOL, timeframe="1H", kind="entry",
                            side="long", price=100, bar_close_ms=NOW, detected_at_ms=NOW), notify=True)
    assert store.claim(NOW) is not None
    assert store.telegram_status().get("sending") == 1
    monitor = Monitor(store, client=FakeMarket())
    # Merely constructing a second server cannot mutate the active worker's row.
    assert store.telegram_status().get("sending") == 1
    assert store.telegram_status().get("unknown") == 0
    starts = []

    class InertThread:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]

        def start(self):
            starts.append((self.name, store.telegram_status()))

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(service.threading, "Thread", InertThread)
    monitor.start()
    assert len(starts) == 3
    assert {name for name, _ in starts} == {"impulse-scan", "impulse-model", "impulse-bark"}
    assert all(status.get("unknown") == 1 and status.get("sending", 0) == 0
               for _, status in starts)
    assert store.claim(NOW) is None  # An uncertain old send must not be resent.
    monitor.close()


def test_monitor_defaults_disable_telegram_without_loading_credentials(tmp_path, monkeypatch):
    from yoyo.monitor import telegram

    monkeypatch.setattr(service, "TelegramWorker", telegram.TelegramWorker)
    monkeypatch.setattr(telegram, "credentials", lambda: pytest.fail("disabled Telegram loaded credentials"))
    monitor = Monitor(Store(tmp_path / "monitor.sqlite3"), client=FakeMarket())
    assert monitor.telegram.enabled is False
    assert monitor.telegram.creds is None
    assert monitor.telegram.status()["enabled"] is False
    assert monitor.model_gate.telegram_enabled is False


def test_start_retires_only_pending_telegram_and_preserves_bark_and_history(tmp_path, monkeypatch):
    from model_fixture import model_event

    store = Store(tmp_path / "monitor.sqlite3")
    events = {}
    for offset, status in enumerate(("pending", "sent", "unknown")):
        event = model_event(wait=0, close=(30 + offset) * TIMEFRAMES["1H"])
        events[status] = store.event_id(event)
        store.upsert_event(event, notify=True, bark_notify=True)
        if status != "pending":
            store.finish(events[status], status, message_id=42 if status == "sent" else None,
                         error="historic_uncertainty" if status == "unknown" else None)
    with store.connect() as db:
        before = {r["event_id"]: dict(r) for r in db.execute("SELECT * FROM outbox")}
        bark_before = [dict(r) for r in db.execute("SELECT * FROM bark_outbox ORDER BY event_id")]
    starts = []
    class InertThread:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]
        def start(self):
            starts.append((self.name, store.telegram_status()["pending"]))
        def join(self, timeout=None):
            pass
    monkeypatch.setattr(service.threading, "Thread", InertThread)
    monitor = Monitor(store, client=FakeMarket())
    assert store.telegram_status()["pending"] == 1  # Construction is read-only.
    monitor.start()
    assert {name for name, _ in starts} == {"impulse-scan", "impulse-model", "impulse-bark"}
    assert all(pending == 0 for _, pending in starts)
    with store.connect() as db:
        after = {r["event_id"]: dict(r) for r in db.execute("SELECT * FROM outbox")}
        bark_after = [dict(r) for r in db.execute("SELECT * FROM bark_outbox ORDER BY event_id")]
    assert after[events["pending"]]["status"] == "skipped"
    for state in ("sent", "unknown"):
        assert after[events[state]] == before[events[state]]
    assert bark_after == bark_before
    monitor.close()


@pytest.mark.parametrize("status,errors,age_ms,ready,ok", [
    ("degraded", 2, 1_000, False, False),
    ("degraded", 1, 1_000, True, False),
    ("idle", 0, 1_000, True, True),
    ("error", 0, 1_000, False, False),
    ("idle", 0, 20 * 60_000, False, False),
])
def test_health_distinguishes_all_failed_partial_and_current_scan(tmp_path, monkeypatch,
                                                                status, errors, age_ms, ready, ok):
    from yoyo.monitor.server import create_app

    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    scan = dict(status=status, total=2, errors=errors, finished_at_ms=NOW - age_ms)
    monkeypatch.setattr(monitor, "status", lambda: dict(now_ms=NOW, scan=dict(scan),
                       runtime={"model_gate": {"status": "ready"}}))
    # Invoke the installed route itself; no optional HTTP test-client dependency.
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/healthz")
    result = endpoint()
    assert result["service_alive"] is True
    assert result["market_ready"] is ready
    assert result["ok"] is ok


@pytest.mark.parametrize("model_status", ["loading", "error"])
def test_health_is_not_ok_until_model_gate_is_ready(tmp_path, monkeypatch, model_status):
    from yoyo.monitor.server import create_app
    app = create_app(runtime=tmp_path, start_monitor=False)
    monkeypatch.setattr(app.state.monitor, "status", lambda: dict(
        now_ms=NOW, scan=dict(status="idle", total=3, errors=0, finished_at_ms=NOW - 1000),
        runtime={"model_gate": {"status": model_status}}))
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/healthz")
    result = endpoint()
    assert result["service_alive"] is True and result["market_ready"] is True
    assert result["ok"] is False


def test_rejected_clock_offset_does_not_replace_last_trusted_clock(monkeypatch):
    client = OKX()
    client.offset_ms = 37
    monkeypatch.setattr(okx.time, "time", lambda: 1000.)
    monkeypatch.setattr(client, "get", lambda *args, **kwargs: [{"ts": "1120001"}])
    with pytest.raises(MarketError, match="local_clock_out_of_sync"):
        client.synchronize()
    assert client.offset_ms == 37
    assert client.clock() == 1_000_037


def test_accepted_clock_offset_uses_request_midpoint(monkeypatch):
    client = OKX()
    clock = iter([1000., 1000.2])
    monkeypatch.setattr(okx.time, "time", lambda: next(clock))
    monkeypatch.setattr(client, "get", lambda *args, **kwargs: [{"ts": "1000600"}])
    assert client.synchronize() == 500
    assert client.offset_ms == 500


def test_scan_registers_visible_release_without_notifying_or_filtering_density(tmp_path):
    from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.history['1H'][-1].update(o=120., h=121., l=119., c=120.)
    monitor = configured_monitor(store, client)
    assert monitor.scan_symbol(INSTRUMENT) == []
    events = store.list_events()
    canonical = [e for e in events if e['kind'] == SIGNAL_KIND]
    assert len(canonical) == 1
    signal = canonical[0]
    assert signal['source_kind'] == 'release' and signal['tv_marker_visible']
    assert abs(signal['previous_md']) <= signal['focus_band'] < signal['md']
    assert signal['dense'] is False and signal['near_zero_bars'] >= 12
    assert signal['notification_status'] == 'history'
    assert store.candidate_counts()['pending'] == 1
    assert store.event_count(kind=MODEL_KIND) == 0
    assert store.telegram_media_status() == dict(snapshots=0, render_fallbacks=0)
    assert all(e['notification_status'] == 'history' for e in events if e['kind'] != SIGNAL_KIND)
    assert store.telegram_status(protocol=SIGNAL_PROTOCOL)['pending'] == 0
    # A repeated scan does not create a second identity or notification.
    assert monitor.scan_symbol(INSTRUMENT) == []
    assert store.telegram_status(protocol=SIGNAL_PROTOCOL)['pending'] == 0


def test_policy_activation_uses_exchange_clock_before_delivery(tmp_path, monkeypatch):
    client = FakeMarket()
    monkeypatch.setattr(service, 'now_ms', lambda: NOW - 120_000)
    client.synchronize = lambda: None
    client.instruments = lambda: []
    store = Store(tmp_path / 'monitor.sqlite3')
    monitor = Monitor(store, client=client)
    assert not monitor.notification_ready.is_set()
    monitor.bark.creds = 'synthetic-device-key'
    monitor.scan()
    assert monitor.notification_ready.is_set()
    assert store.get_meta('notification_policy:' + MODEL_PROTOCOL) is None
    assert monitor.bark_since == NOW
    assert store.get_meta('notification_policy:bark:' + MODEL_PROTOCOL)['activated_ms'] == NOW
    # The latest already-closed bar is older than activation, even if the Mac
    # started timestamp was still before that bar's close.
    client.history['1H'][-1].update(o=120., h=121., l=119., c=120.)
    monitor.scan_symbol(INSTRUMENT)
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == 0


def test_raw_zero_departure_inside_focus_band_does_not_notify(tmp_path):
    from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.history['1H'][-1].update(o=113., h=114., l=112., c=113.)
    monitor = configured_monitor(store, client)
    assert monitor.scan_symbol(INSTRUMENT) == []
    events = store.list_events()
    assert any(e['kind'] == 'zero_breakout' for e in events)
    assert not any(e['kind'] == SIGNAL_KIND for e in events)
    assert store.telegram_status(protocol=SIGNAL_PROTOCOL)['pending'] == 0


def test_bark_new_channel_does_not_replay_pre_activation_signal(tmp_path):
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.history['1H'][-1].update(o=120., h=121., l=119., c=120.)
    monitor = configured_monitor(store, client, bark=NOW)
    monitor.scan_symbol(INSTRUMENT)
    process_model(monitor, '1H')
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == 0


def test_eligible_model_confirmation_enters_bark_once_with_telegram_disabled(tmp_path):
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.history['1H'][-1].update(o=120., h=121., l=119., c=120.)
    monitor = configured_monitor(store, client)
    monitor.scan_symbol(INSTRUMENT)
    process_model(monitor, '1H')
    monitor.scan_symbol(INSTRUMENT)
    process_model(monitor, '1H')
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == 1


def test_bark_confirmation_keeps_earlier_signal_without_snapshot_dependency(tmp_path, monkeypatch):
    import sys
    store = Store(tmp_path / 'monitor.sqlite3')
    store.activate_timeframe_policy('15m', NOW - 120_000, protocol=MODEL_PROTOCOL)
    client = FakeMarket()
    client.history['15m'][-1].update(o=120., h=121., l=119., c=120.)
    target = client.history['15m'][-1]['t']
    client.history['15m'].append(dict(client.history['15m'][-1], t=target + 900_000))
    client.clock = lambda: NOW + 900_000
    monitor = configured_monitor(store, client)
    monkeypatch.setitem(sys.modules, 'yoyo.monitor.snapshot', None)
    assert monitor.scan_symbol(INSTRUMENT) == []
    process_model(monitor, '15m')
    assert monitor.scan_symbol(INSTRUMENT) == []
    process_model(monitor, '15m')
    assert store.claim(client.clock()) is None
    assert store.bark_status()['pending'] == 1
    row = store.claim_bark(client.clock())
    assert row['event']['bar_open_ms'] == target
    assert row['event']['indicator']['bar_open_ms'] == target
    assert store.telegram_media_status() == dict(snapshots=0, render_fallbacks=0)


def test_disabled_telegram_never_renders_confirmation_snapshot(tmp_path, monkeypatch):
    from yoyo.monitor import snapshot
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.history['1H'][-1].update(o=120., h=121., l=119., c=120.)
    monitor = configured_monitor(store, client)

    def broken(*args):
        pytest.fail('disabled Telegram must not render a snapshot')

    monkeypatch.setattr(snapshot, 'render_signal', broken)
    assert monitor.scan_symbol(INSTRUMENT) == []
    process_model(monitor, '1H')
    assert store.claim(NOW) is None
    assert store.bark_status()['pending'] == 1
    row = store.claim_bark(NOW)
    assert row['event']['price'] == 120.
    assert store.telegram_media_status() == dict(snapshots=0, render_fallbacks=0)


def test_later_telegram_cutover_does_not_block_eligible_bark_signal(tmp_path):
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.history['1H'][-1].update(o=120., h=121., l=119., c=120.)
    monitor = configured_monitor(store, client, tg=NOW)
    monitor.scan_symbol(INSTRUMENT)
    process_model(monitor, '1H')
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == 1


@pytest.mark.parametrize('activation,expected', [(None, 0), (NOW, 0), (NOW - 60_000, 0), (NOW - 120_000, 1)])
def test_15m_notification_requires_its_own_forward_cutover(tmp_path, activation, expected):
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.history['15m'][-1].update(o=120., h=121., l=119., c=120.)
    if activation is not None:
        store.activate_timeframe_policy('15m', activation, protocol=MODEL_PROTOCOL)
    monitor = configured_monitor(store, client, timeframe_policy={"15m": activation})
    assert monitor.scan_symbol(INSTRUMENT) == []
    process_model(monitor, '15m')
    assert monitor.chart(SYMBOL, '15m')['state']['higher_timeframe'] == '1H'
    assert any(e['kind'] == 'tv_start' and e['timeframe'] == '15m' for e in store.list_events())
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == expected
    monitor.scan_symbol(INSTRUMENT)
    process_model(monitor, '15m')
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == expected


def test_scan_covers_three_periods_and_persists_cutover_before_workers(tmp_path):
    store = Store(tmp_path / 'monitor.sqlite3')
    client = FakeMarket()
    client.synchronize = lambda: None
    client.instruments = lambda: [INSTRUMENT]
    monitor = Monitor(store, client=client)
    monitor.scan()
    status = monitor.status()
    assert status['scan']['completed'] == status['scan']['total'] == 3
    assert status['scan']['errors'] == 0
    assert status['runtime']['timeframes'] == ['15m', '1H', '4H']
    assert status['runtime']['timeframe_notification_since_ms'] == {'15m': NOW, '1H': NOW, '4H': NOW}
    assert monitor.notification_ready.is_set()
    # Restart retains first activation, without resetting the old channels.
    assert Store(store.path).activate_timeframe_policy('15m', NOW + 900_000, protocol=MODEL_PROTOCOL) == NOW


def test_chart_api_accepts_15m_and_rejects_unmonitored_period(tmp_path):
    from fastapi import HTTPException
    from yoyo.monitor.server import create_app
    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    monitor.client = FakeMarket()
    monitor.scan_symbol(INSTRUMENT)
    endpoint = next(r.endpoint for r in app.routes if getattr(r, 'path', None) == '/api/chart')
    assert endpoint(SYMBOL, '15m')['timeframe'] == '15m'
    with pytest.raises(HTTPException) as exc:
        endpoint(SYMBOL, '5m')
    assert exc.value.status_code == 400


@pytest.mark.parametrize('recovery_delay,expected', [(16 * 60_000, 1), (31 * 60_000, 0)])
def test_15m_stale_market_does_not_consume_fresh_signal_before_recovery(tmp_path, recovery_delay, expected):
    store = Store(tmp_path / 'monitor.sqlite3')
    store.activate_timeframe_policy('15m', NOW - 120_000, protocol=MODEL_PROTOCOL)
    client = FakeMarket()
    client.history['15m'][-1].update(o=120., h=121., l=119., c=120.)
    client.clock = lambda: NOW + 15 * 60_000
    monitor = configured_monitor(store, client)
    monitor.scan_symbol(INSTRUMENT)
    assert monitor.chart(SYMBOL, '15m')['state']['stale']
    assert store.telegram_status()['pending'] == store.bark_status()['pending'] == 0
    # A confirmed old arrow may be retained as raw history, but no inference
    # or notification is consumed while the latest market candle is missing.
    assert all(e['notification_status'] == 'history' for e in store.list_events())
    assert monitor.model_gate.detector.calls == []
    client.clock = lambda: NOW + recovery_delay
    while client.history['15m'][-1]['t'] + 900_000 < client.clock() // 900_000 * 900_000:
        client.history['15m'].append(dict(client.history['15m'][-1], t=client.history['15m'][-1]['t'] + 900_000))
    monitor.scan_symbol(INSTRUMENT)
    assert not monitor.chart(SYMBOL, '15m')['state']['stale']
    process_model(monitor, '15m')
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == expected
    monitor.scan_symbol(INSTRUMENT)
    process_model(monitor, '15m')
    assert store.telegram_status()['pending'] == 0
    assert store.bark_status()['pending'] == expected


def test_cached_same_bar_resubmits_pending_model_after_inference_failure(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    client = FakeMarket()
    client.history["1H"][-1].update(o=120., h=121., l=119., c=120.)
    monitor = configured_monitor(store, client)
    monitor.scan_symbol(INSTRUMENT)
    key = (SYMBOL, "1H")
    assert key in monitor.model_gate._queue
    monitor.model_gate._queue.clear()
    detector = monitor.model_gate.detector
    detector.fail_once = True
    process_model(monitor, "1H")
    assert store.candidate_counts()["error"] == 1
    assert store.telegram_status()["pending"] == 0
    cached = deepcopy(monitor.chart(*key))
    monitor.scan_symbol(INSTRUMENT)
    assert monitor.chart(*key)["candles"] == cached["candles"]
    assert key in monitor.model_gate._queue
    process_model(monitor, "1H")
    assert detector.calls == [client.history["1H"][-1]["t"]] * 2
    assert store.candidate_counts()["confirmed"] == 1
    assert store.telegram_status()["pending"] == 0
    assert store.bark_status()["pending"] == 1


@pytest.mark.parametrize("lag_ms,eligible", [(MODEL_MAX_WAIT * TIMEFRAMES["1H"] + FRESH_MS, True),
                                           (MODEL_MAX_WAIT * TIMEFRAMES["1H"] + FRESH_MS + 1, False)])
def test_archival_arrow_outside_confirmation_budget_is_not_registered(tmp_path, lag_ms, eligible):
    store = Store(tmp_path / "monitor.sqlite3")
    client = FakeMarket()
    client.history["1H"][-1].update(o=120., h=121., l=119., c=120.)
    original_close = client.history["1H"][-1]["t"] + TIMEFRAMES["1H"]
    client.clock = lambda: original_close + lag_ms
    monitor = configured_monitor(store, client)
    monitor.scan_symbol(INSTRUMENT)
    assert any(event["kind"] == "tv_start" for event in store.list_events())
    candidates = store.list_candidates(symbol=SYMBOL, timeframe="1H")
    assert len(candidates) == int(eligible)
    assert monitor.model_gate.detector.calls == []
    assert store.telegram_status()["pending"] == store.bark_status()["pending"] == 0
