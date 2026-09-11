"""Focused API and replay-import contract checks using synthetic ledger rows."""
from __future__ import annotations

import threading

import pytest

from yoyo.monitor import MODEL_KIND, MODEL_PROTOCOL, SIGNAL_KIND, SIGNAL_PROTOCOL, TIMEFRAMES
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


def yolo(*, close: int = NOW - 1_000) -> dict:
    indicator = raw(source="live", close=close - TIMEFRAMES["1H"])
    return {
        "protocol": MODEL_PROTOCOL, "kind": MODEL_KIND, "source": "live", "confirmation": "yolo",
        "venue": indicator["venue"], "symbol": indicator["symbol"], "timeframe": "1H", "timeframe_min": 60,
        "direction": "long", "side": "long", "bar_open_ms": close - TIMEFRAMES["1H"],
        "bar_close_ms": close, "price": 0.000002800, "is_closed": True, "risk": indicator["risk"],
        "source_sha256": indicator["source_sha256"], "detected_at_ms": close,
        "indicator": indicator,
        "model": {"status": "confirmed", "confidence": 0.73, "wait_bars": 2, "max_wait_bars": 9,
                  "core_start_ms": close - 5 * TIMEFRAMES["1H"], "core_end_ms": close - 3 * TIMEFRAMES["1H"],
                  "window_end_ms": close - TIMEFRAMES["1H"], "last_checked_close_ms": close,
                  "expires_at_ms": close + 7 * TIMEFRAMES["1H"]},
    }


def test_confirmation_filters_select_raw_kind_and_keep_live_replay_separate(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    store.set_meta("notification_policy:v1_bark_arm", {"activated_ms": NOW - 10 * TIMEFRAMES["1H"]})
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
    assert [row["id"] for row in first["items"] + second["items"]] == [
        store.event_id(raw(source="replay", close=NOW - index * TIMEFRAMES["1H"])) for index in range(3)]
    with pytest.raises(Exception, match="cursor requires both"):
        get(limit=2, symbol=None, timeframe=None, kind=None, side=None,
            source="replay", confirmation="raw", before_close_ms=cursor["close_ms"])


def test_signal_summary_keeps_live_channel_receipts_and_yolo_card_contract(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    store.set_meta("notification_policy:v1_bark_arm", {"activated_ms": NOW - 10 * TIMEFRAMES["1H"]})
    sent = raw(source="live", close=NOW - 1_000)
    failed = raw(source="live", close=NOW - TIMEFRAMES["1H"])
    assert store.upsert_event(sent, notify=True, bark_notify=True)
    assert store.upsert_event(failed, notify=True, bark_notify=True)
    sent_tg = store.claim(NOW)
    sent_bark = store.claim_bark(NOW)
    failed_tg = store.claim(NOW)
    failed_bark = store.claim_bark(NOW)
    store.finish(sent_tg["event_id"], "sent", message_id=7)
    store.finish_bark(sent_bark["event_id"], "sent", server_timestamp=NOW)
    store.finish(failed_tg["event_id"], "failed", error="synthetic")
    store.finish_bark(failed_bark["event_id"], "failed", error="synthetic")
    confirmed = yolo(close=NOW - 2 * TIMEFRAMES["1H"])
    assert store.upsert_event(confirmed, notify=False, bark_notify=False)
    monkeypatch.setattr(app.state.monitor.client, "clock", lambda: NOW)
    get = endpoint(app)

    raw_rows = get(limit=10, symbol=None, timeframe=None, kind=None, side=None,
                   source="live", confirmation="raw")["items"]
    receipt_rows = {row["id"]: row for row in raw_rows}
    assert receipt_rows[sent_tg["event_id"]]["notification_status"] == "sent"
    assert receipt_rows[sent_bark["event_id"]]["bark_notification_status"] == "sent"
    assert receipt_rows[failed_tg["event_id"]]["notification_status"] == "failed"
    assert receipt_rows[failed_bark["event_id"]]["bark_notification_status"] == "failed"
    assert all(row["kind"] == SIGNAL_KIND and row["protocol"] == SIGNAL_PROTOCOL for row in raw_rows)
    raw_summary = receipt_rows[sent_tg["event_id"]]
    assert raw_summary["initial_stop"] == sent["initial_stop"]
    assert store.get_event(sent_tg["event_id"])["initial_stop"] == sent["initial_stop"]

    result = get(limit=10, symbol=None, timeframe=None, kind=None, side=None,
                 source="live", confirmation="yolo")["items"]
    assert len(result) == 1
    row = result[0]
    assert row["kind"] == MODEL_KIND and row["protocol"] == MODEL_PROTOCOL
    assert row["model"] == {key: confirmed["model"][key] for key in
                            ("status", "confidence", "wait_bars", "max_wait_bars", "core_start_ms",
                             "core_end_ms", "window_end_ms", "last_checked_close_ms", "expires_at_ms")}
    assert row["indicator"] == {key: confirmed["indicator"][key] for key in
                                 ("protocol", "kind", "source", "confirmation", "timeframe", "timeframe_min",
                                  "venue", "symbol", "direction", "side", "bar_open_ms", "bar_close_ms",
                                  "price", "risk", "initial_stop")}


def test_signal_summary_projects_replay_outcomes_but_get_event_keeps_evidence(tmp_path, monkeypatch):
    app = create_app(runtime=tmp_path, start_monitor=False)
    store = app.state.monitor.store
    realized = raw(source="replay", close=NOW - TIMEFRAMES["1H"])
    censored = raw(source="replay", close=NOW - 2 * TIMEFRAMES["1H"])
    realized.update(performance_status="covered_linked_realized_unverified", covered_ledger={
        "link_status": "realized", "evidence": {"ledger_file": "/immutable/ledger.csv.gz", "ledger_sha256": "a" * 64},
        "outcome": {"status": "realized", "entry_time_ms": NOW, "exit_time_ms": NOW + 1,
                    "exit_reason": "protective_stop", "net_r": -1.02, "net_return": -0.01},
    })
    censored.update(performance_status="covered_linked_censored_unverified", covered_ledger={
        "link_status": "censored", "evidence": {"ledger_file": "/immutable/ledger.csv.gz", "ledger_sha256": "b" * 64},
        "outcome": {"status": "censored"},
    })
    assert store.upsert_event(realized, notify=False, bark_notify=False)
    assert store.upsert_event(censored, notify=False, bark_notify=False)
    monkeypatch.setattr(app.state.monitor.client, "clock", lambda: NOW)
    rows = endpoint(app)(limit=10, symbol=None, timeframe=None, kind=None, side=None,
                         source="replay", confirmation="raw")["items"]
    by_id = {row["id"]: row for row in rows}
    summary = by_id[store.event_id(realized)]
    assert summary["covered_ledger"] == {"link_status": "realized", "outcome": {
        "status": "realized", "exit_reason": "protective_stop", "exit_time_ms": NOW + 1, "net_r": -1.02}}
    assert "evidence" not in summary["covered_ledger"]
    assert by_id[store.event_id(censored)]["covered_ledger"] == {"link_status": "censored", "outcome": {"status": "censored"}}
    assert store.get_event(store.event_id(realized))["covered_ledger"]["evidence"]["ledger_sha256"] == "a" * 64
    assert store.bark_status()["pending"] == 0 and store.candidate_counts() == {}


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
