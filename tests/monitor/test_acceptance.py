"""Read-only audit evidence from mocked APIs and temporary journals; no sends."""
from io import BytesIO
import json

import pytest

from model_fixture import model_event
from yoyo.monitor import (MODEL_PROTOCOL, MODEL_PROFILE_ID, MODEL_SHA256,
                          MONITORED_TIMEFRAMES)
from yoyo.monitor import acceptance
from yoyo.monitor.model_gate import pending_proof
from yoyo.monitor.store import Store


@pytest.fixture
def audit(tmp_path, monkeypatch):
    runtime = tmp_path / "Library/Application Support/Fable/ImpulseMonitor/monitor.sqlite3"
    store = Store(runtime)
    store.activate_notification_policy(0, protocol=MODEL_PROTOCOL)
    store.activate_bark_policy(0, protocol=MODEL_PROTOCOL)
    for timeframe in MONITORED_TIMEFRAMES:
        store.activate_timeframe_policy(timeframe, 0, protocol=MODEL_PROTOCOL)
    gate = dict(status="ready", loaded=True, queue_depth=0, processed_endpoints=2,
                last_checked_at_ms=1000, model_sha256=MODEL_SHA256, profile_id=MODEL_PROFILE_ID)
    status = dict(scan={"status": "idle"}, telegram={"configured": False}, runtime={"model_gate": gate})
    replies = {"/api/status": status, "/api/health": {"ok": True}, "/api/markets": {"total": 0, "items": []}}
    calls = []
    def urlopen(url, timeout):
        assert url.startswith("http://127.0.0.1:8766/")
        path = url.removeprefix("http://127.0.0.1:8766")
        assert timeout == 10 and path in replies
        calls.append(path)
        return BytesIO(json.dumps(replies[path]).encode())
    monkeypatch.setattr(acceptance.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(acceptance.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(acceptance.subprocess, "check_output", lambda *a, **k: "synthetic-commit\n")
    def collect():
        target = tmp_path / "evidence.json"
        acceptance.collect("synthetic-audit", target)
        assert calls[-3:] == ["/api/status", "/api/health", "/api/markets"]
        return json.loads(target.read_text())
    return store, gate, collect


def test_audit_current_confirmation_and_preserves_old_raw_receipts(audit):
    store, gate, collect = audit
    event = model_event()
    original = event["indicator"]
    store.upsert_event(original, notify=True, bark_notify=True)
    store.finish(store.event_id(original), "sent", message_id=71)
    store.finish_bark(store.event_id(original), "sent", server_timestamp=72)
    store.register_candidate(original, pending_proof(original))
    assert store.confirm_candidate(store.event_id(original), event, notify=True, bark_notify=True)
    result = collect()
    contract = result["signal_contract_audit"]
    assert contract["protocol"] == MODEL_PROTOCOL
    assert contract["signal_count"] == contract["current_outbox_count"] == 1
    assert contract["invalid_signal_ids"] == contract["invalid_outbox_ids"] == []
    assert result["bark_audit"]["current_outbox_count"] == 1
    assert result["model_audit"]["candidate_status_counts"] == {"confirmed": 1}
    assert result["model_audit"]["operational"]["ready_for_confirmation"] is True
    tg = {row["event_id"]: row for row in result["journal"]["telegram_receipts"]}
    bark = {row["event_id"]: row for row in result["journal"]["bark_receipts"]}
    assert tg[store.event_id(original)]["status"] == "sent"
    assert tg[store.event_id(original)]["message_id"] == 71
    assert bark[store.event_id(original)]["server_timestamp"] == 72
    assert tg[store.event_id(event)]["status"] == bark[store.event_id(event)]["status"] == "pending"


def test_distinct_original_arrows_at_same_confirmation_endpoint_are_not_duplicates(audit):
    store, gate, collect = audit
    first = model_event(wait=2)
    second = model_event(close=first["bar_close_ms"], wait=1, near_zero_bars=13)
    assert first["source_event_id"] != second["source_event_id"]
    for event in (first, second):
        store.upsert_event(event)
    result = collect()
    assert result["signal_contract_audit"]["signal_count"] == 2
    assert result["signal_contract_audit"]["invalid_signal_ids"] == []
    assert result["journal"]["duplicate_identity_groups"] == 0
    # Simulate a malformed legacy import with a second ID for the same source.
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM events WHERE id=?", (store.event_id(first),)).fetchone())
        payload = json.loads(row["payload"])
        payload["id"] = "duplicate-import"
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (
            "duplicate-import", row["symbol"], row["timeframe"], row["kind"], row["side"],
            row["close_ms"], row["detected_ms"], json.dumps(payload)))
    assert collect()["journal"]["duplicate_identity_groups"] == 1


@pytest.mark.parametrize("timeframe", MONITORED_TIMEFRAMES)
@pytest.mark.parametrize("scope", ["telegram", "bark", "timeframe"])
@pytest.mark.parametrize("missing", [False, True])
def test_all_streams_and_channels_audit_original_arrow_cutovers(audit, timeframe, scope, missing):
    store, gate, collect = audit
    event = model_event(timeframe=timeframe)
    store.upsert_event(event, notify=True, bark_notify=True)
    key = ("notification_timeframe:" + MODEL_PROTOCOL + ":" + timeframe if scope == "timeframe" else
           ("notification_policy:" if scope == "telegram" else "notification_policy:bark:") + MODEL_PROTOCOL)
    if missing:
        with store.connect() as db:
            db.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        activation = event["indicator"]["bar_close_ms"] + 1
        assert activation < event["bar_close_ms"]
        store.set_meta(key, {"activated_ms": activation})
    result = collect()
    expected = [store.event_id(event)]
    if scope == "timeframe":
        assert result["timeframe_audit"]["invalid_telegram_ids"] == expected
        assert result["timeframe_audit"]["invalid_bark_ids"] == expected
    else:
        audit_key = "signal_contract_audit" if scope == "telegram" else "bark_audit"
        assert result[audit_key]["pre_activation_outbox_ids"] == expected


def test_invalid_current_proof_is_reported_without_relabeling_raw_history(audit):
    store, gate, collect = audit
    event = model_event()
    event["model"]["side"] = "short"
    store.upsert_event(event, notify=True, bark_notify=True)
    store.upsert_event(event["indicator"])
    result = collect()
    assert result["signal_contract_audit"]["signal_count"] == 1
    expected = [store.event_id(event)]
    assert result["signal_contract_audit"]["invalid_signal_ids"] == expected
    assert result["signal_contract_audit"]["invalid_outbox_ids"] == expected
    assert result["bark_audit"]["invalid_outbox_ids"] == expected


@pytest.mark.parametrize("change", ["not_ready", "not_loaded", "wrong_weight", "wrong_profile", "candidate_error"])
def test_operational_readiness_requires_loaded_expected_profile_and_no_candidate_errors(audit, change):
    store, gate, collect = audit
    if change == "not_ready":
        gate["status"] = "loading"
    elif change == "not_loaded":
        gate["loaded"] = False
    elif change == "wrong_weight":
        gate["model_sha256"] = "unapproved"
    elif change == "wrong_profile":
        gate["profile_id"] = "unapproved"
    else:
        original = model_event()["indicator"]
        proof = pending_proof(original)
        store.register_candidate(original, proof)
        store.update_candidate(store.event_id(original), dict(proof, status="error"))
    assert collect()["model_audit"]["operational"]["ready_for_confirmation"] is False
