"""Focused API and replay-import contract checks using synthetic ledger rows."""
from __future__ import annotations

import threading

import pytest

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES
from yoyo.monitor import server as server_module
from yoyo.monitor.replay_import import import_rows
from yoyo.monitor.server import create_app
from yoyo.monitor.store import Store


NOW = 1_800_000_000_000


def raw(*, source: str, close: int = NOW - 1_000) -> dict:
    step = TIMEFRAMES["1H"]
    return {
        "protocol": SIGNAL_PROTOCOL, "kind": SIGNAL_KIND, "source": source,
        "confirmation": "raw", "venue": "okx", "symbol": "PEPE-USDT-SWAP",
        "timeframe": "1H", "timeframe_min": 60, "direction": "long", "side": "long",
        "bar_open_ms": close - step, "bar_close_ms": close, "signal_close_time": close,
        "is_closed": True, "price": 0.000002714, "risk": 0.000000094,
        "initial_stop": 0.000002620, "source_sha256": "a" * 64,
        "entry_reference": "next_open", "executable_entry_time": None,
        "detected_at_ms": close,
    }


def endpoint(app):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/signals")


def test_confirmation_filters_select_raw_kind_and_keep_live_replay_separate(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    live, replay = raw(source="live"), raw(source="replay")
    assert store.upsert_event(live, bark_notify=False)
    assert store.upsert_event(replay, bark_notify=False)
    assert store.event_id(live) != store.event_id(replay)
    monkeypatch.setattr(app.state.monitor.client, "clock", lambda: NOW)
    get = endpoint(app)
    live_result = get(limit=20, symbol=None, timeframe=None, kind=None, side=None,
                      source="live", confirmation="raw")
    replay_result = get(limit=20, symbol=None, timeframe=None, kind=None, side=None,
                        source="replay", confirmation="raw")
    assert [row["source"] for row in live_result["items"]] == ["live"]
    assert [row["source"] for row in replay_result["items"]] == ["replay"]
    assert live_result["items"][0]["is_fresh"] is True
    assert replay_result["items"][0]["is_fresh"] is False
    assert store.bark_status()["pending"] == 0 and store.candidate_counts() == {}


def test_replay_import_keeps_only_signal_fields_and_never_creates_delivery(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    row = {
        "venue": "binance", "symbol": "PEPEUSDT", "timeframe_min": "60", "direction": "long",
        "signal_bar_open": str(NOW - TIMEFRAMES["1H"]), "signal_close_time": str(NOW),
        "signal_close": "0.000002714", "reference_signal_risk": "0.000000094",
        "entry_time": str(NOW + TIMEFRAMES["1H"]),
        "net_return": "999", "exit_reason": "untrusted outcome",
    }
    assert import_rows(store, [row], import_id="synthetic-ledger") == {"inserted": 1, "already_present": 0, "outside_v1_contract": 0}
    assert import_rows(store, [row], import_id="synthetic-ledger") == {"inserted": 0, "already_present": 1, "outside_v1_contract": 0}
    event = store.list_events()[0]
    assert event["source"] == "replay" and event["confirmation"] == "raw"
    assert event["replay_unverified"] is True and event["performance_status"] == "unverified"
    assert event["execution_clock_status"] == "backtest_unverified"
    assert "net_return" not in event and "exit_reason" not in event
    assert store.bark_status()["pending"] == 0 and store.candidate_counts() == {}


def test_replay_import_fails_before_a_bad_row_can_be_notified(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    with pytest.raises(ValueError, match="long-only"):
        import_rows(store, [{"venue": "okx", "symbol": "BTC-USDT-SWAP", "timeframe_min": "60",
                             "direction": "short", "signal_bar_open": "0", "signal_close_time": "3600000",
                             "signal_close": "1", "reference_signal_risk": ".1"}], import_id="bad")
    assert store.list_events() == []


def test_replay_import_skips_daily_rows_outside_the_v1_monitor_contract(tmp_path):
    store = Store(tmp_path / "monitor.sqlite3")
    result = import_rows(store, [{"venue": "okx", "symbol": "BTC-USDT-SWAP", "timeframe_min": "1440",
                                  "direction": "long", "signal_bar_open": "0", "signal_close_time": "86400000",
                                  "signal_close": "1", "reference_signal_risk": ".1"}], import_id="daily")
    assert result == {"inserted": 0, "already_present": 0, "outside_v1_contract": 1}
    assert store.list_events() == []


def test_replay_signal_cursor_pages_stably_past_the_first_limit(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    for index in range(3):
        event = raw(source="replay", close=NOW - index * TIMEFRAMES["1H"])
        assert store.upsert_event(event, notify=False, bark_notify=False)
    monkeypatch.setattr(app.state.monitor.client, "clock", lambda: NOW)
    get = endpoint(app)
    first = get(limit=2, symbol=None, timeframe=None, kind=None, side=None,
                source="replay", confirmation="raw")
    assert len(first["items"]) == 2 and first["next_cursor"]
    cursor = first["next_cursor"]
    second = get(limit=2, symbol=None, timeframe=None, kind=None, side=None,
                 source="replay", confirmation="raw",
                 before_close_ms=cursor["close_ms"], before_id=cursor["event_id"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert {row["id"] for row in first["items"]}.isdisjoint({row["id"] for row in second["items"]})
    with pytest.raises(Exception, match="cursor requires both"):
        get(limit=2, symbol=None, timeframe=None, kind=None, side=None,
            source="replay", confirmation="raw", before_close_ms=cursor["close_ms"])


def test_signal_trace_has_phase_counts_without_request_identifiers(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    assert store.upsert_event(raw(source="replay"), notify=False, bark_notify=False)
    monkeypatch.setattr(app.state.monitor.client, "clock", lambda: NOW)
    markers = []
    monkeypatch.setattr(server_module, "DISPATCH_TRACE", True)
    monkeypatch.setattr(server_module, "dispatch_trace", lambda marker, **_: markers.append(marker))

    result = endpoint(app)(limit=1, symbol="PEPE-USDT-SWAP", timeframe="1H", kind=None, side=None,
                           source="replay", confirmation="raw")

    assert len(result["items"]) == 1
    assert markers == [
        "handler:/api/signals", "signals:sqlite", "signals:decode", "signals:rows=1",
        "signals:freshness", "signals:return=1",
    ]
    assert all("PEPE" not in marker and "replay" not in marker for marker in markers)
