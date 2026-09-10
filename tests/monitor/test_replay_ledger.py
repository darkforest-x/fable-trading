"""Covered-ledger links retain replay delivery isolation and outcome boundaries."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path

from yoyo.evaluation.spike_v1_twoyear_allmarkets import METHOD_VERSION, PINE_SHA, SOURCE_SHA256
from yoyo.monitor.replay_import import normalize_row
from yoyo.monitor.replay_ledger import (freeze_ledger_snapshot, invalidate_replay_links, link_replay_events,
                                        reconcile_replay_events, write_link_receipt)
from yoyo.monitor.store import Store

START = 1_800_000_000_000
STEP = 60 * 60 * 1000


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(text)


def _ledger_row(open_ms: int, *, censored: bool = False) -> dict[str, str]:
    return {
        "event_id": f"ledger-{open_ms}", "venue": "binance", "symbol": "PEPEUSDT", "timeframe_min": "60",
        "direction": "long", "signal_bar_open": str(open_ms), "signal_close_time": str(open_ms + STEP),
        "entry_time": str(open_ms + STEP), "entry_price": "110", "exit_time": str(open_ms + 2 * STEP),
        "exit_price": "120", "exit_reason": "target", "net_r": "0.5", "net_return": "0.1", "fees_return": "0.002",
        "censored": str(censored),
    }


def _setup(tmp_path: Path) -> tuple[Store, Path, Path, Path]:
    store = Store(tmp_path / "monitor.sqlite3")
    for offset, censored in ((0, False), (STEP, True)):
        row = _ledger_row(START + offset, censored=censored)
        event = normalize_row({**row, "signal_close": "100", "reference_signal_risk": "10"}, import_id="test")
        assert store.upsert_event(event, notify=False, bark_notify=False)
    ledger = tmp_path / "covered.csv.gz"
    rows = [_ledger_row(START), _ledger_row(START + STEP, censored=True)]
    with gzip.open(ledger, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    manifest = tmp_path / "coverage_progress.json"
    manifest.write_text(json.dumps({"method_version": METHOD_VERSION, "source_sha256": SOURCE_SHA256,
                                    "pine_sha256": PINE_SHA}), encoding="utf-8")
    root = tmp_path / "normalized"
    _write(root / "binance" / "PEPEUSDT_30m.csv.gz", "time,open,high,low,close,volume\n")
    return store, ledger, manifest, root


def test_replay_ledger_link_keeps_both_ids_evidence_and_delivery_isolation(tmp_path):
    store, ledger, manifest, root = _setup(tmp_path)
    result = link_replay_events(store, ledger_path=ledger, manifest_path=manifest, ohlc_root=root)
    assert result == {"replay_events": 2, "matched_realized": 1, "matched_censored": 1,
                      "unmatched": 0, "source_mismatch": 0, "ohlc_missing": 0, "linked": 2, "reconciled": 2}
    events = {event["bar_open_ms"]: event for event in store.list_events(limit=10, source="replay", confirmation="raw")}
    realized, censored = events[START], events[START + STEP]
    link = realized["covered_ledger"]
    assert link["monitor_event_id"] == realized["id"]
    assert link["ledger_event_id"] == f"ledger-{START}"
    assert link["match_key"] == {"venue": "binance", "symbol": "PEPEUSDT", "timeframe_min": 60,
                                 "signal_bar_open_ms": START}
    assert link["evidence"]["source_sha256"] == SOURCE_SHA256
    assert link["evidence"]["pine_sha256"] == PINE_SHA
    assert link["evidence"]["ledger_sha256"] == hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert link["outcome"] == {"status": "realized", "entry_time_ms": START + STEP, "entry_price": 110.0,
                               "exit_time_ms": START + 2 * STEP, "exit_price": 120.0, "exit_reason": "target",
                               "net_r": 0.5, "net_return": 0.1, "fees_return": 0.002}
    assert realized["performance_status"] == "covered_linked_realized_unverified"
    assert censored["performance_status"] == "covered_linked_censored_unverified"
    assert censored["covered_ledger"]["outcome"] == {"status": "censored"}
    assert store.bark_status()["pending"] == 0 and store.candidate_counts() == {}
    receipt = tmp_path / "link_receipt.csv.gz"
    assert write_link_receipt(store, receipt) == 2
    with gzip.open(receipt, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {(row["monitor_event_id"], row["ledger_event_id"], row["link_status"]) for row in rows} == {
        (realized["id"], f"ledger-{START}", "realized"),
        (censored["id"], f"ledger-{START + STEP}", "censored"),
    }
    assert all(row["frozen_ohlc_sha256"] for row in rows)


def test_replay_ledger_refuses_source_mismatch_without_writing(tmp_path):
    store, ledger, manifest, root = _setup(tmp_path)
    event = store.list_events(limit=1, source="replay", confirmation="raw")[0]
    assert store.update_event_payload(event["id"], {"source_sha256": "wrong"})
    result = link_replay_events(store, ledger_path=ledger, manifest_path=manifest, ohlc_root=root)
    assert result["linked"] == 1 and result["source_mismatch"] == 1
    updated = store.get_event(event["id"])
    assert updated["performance_status"] == "unverified"
    assert updated["covered_ledger"]["link_status"] == "source_mismatch"
    assert updated["covered_ledger"]["reason"] == "replay_source_sha256_mismatch"
    assert "outcome" not in updated["covered_ledger"]


def test_reconcile_freezes_bytes_and_keeps_stale_audit_without_display_outcome(tmp_path):
    store, ledger, manifest, root = _setup(tmp_path)
    assert link_replay_events(store, ledger_path=ledger, manifest_path=manifest, ohlc_root=root)["linked"] == 2
    old = store.list_events(limit=1, source="replay", confirmation="raw")[0]
    old_hash = old["covered_ledger"]["evidence"]["ledger_sha256"]
    old_status = old["performance_status"]
    snapshots = tmp_path / "immutable"
    result = reconcile_replay_events(store, ledger_path=ledger, manifest_path=manifest,
                                     snapshot_dir=snapshots, ohlc_root=root,
                                     stale_reason="mutable_ledger_artifact_replaced")
    assert result == {"replay_events": 2, "matched_realized": 1, "matched_censored": 1,
                      "unmatched": 0, "source_mismatch": 0, "ohlc_missing": 0,
                      "linked": 2, "reconciled": 2, "invalidated": 2}
    copied_ledger, copied_manifest = freeze_ledger_snapshot(
        ledger_path=ledger, manifest_path=manifest, snapshot_dir=snapshots)
    assert copied_ledger.read_bytes() == ledger.read_bytes()
    assert copied_manifest.read_bytes() == manifest.read_bytes()
    refreshed = store.get_event(old["id"])
    assert refreshed["covered_ledger"]["evidence"]["ledger_file"] == str(copied_ledger)
    stale = refreshed["covered_ledger"]["superseded_stale_evidence"]
    assert stale["reason"] == "mutable_ledger_artifact_replaced"
    assert stale["previous_performance_status"] == old_status
    assert stale["previous_link"]["evidence"]["ledger_sha256"] == old_hash


def test_invalidation_hides_stale_outcome_until_a_new_snapshot_is_linked(tmp_path):
    store, ledger, manifest, root = _setup(tmp_path)
    assert link_replay_events(store, ledger_path=ledger, manifest_path=manifest, ohlc_root=root)["linked"] == 2
    assert invalidate_replay_links(store, reason="ledger_bytes_missing") == 2
    for event in store.list_events(limit=10, source="replay", confirmation="raw"):
        assert event["performance_status"] == "stale_evidence_unverified"
        link = event["covered_ledger"]
        assert link["link_status"] == "stale_evidence"
        assert "outcome" not in link
        assert link["previous_link"]["outcome"]["status"] in {"realized", "censored"}


def test_replay_ledger_reconciles_every_cursor_page(tmp_path):
    store, ledger, manifest, root = _setup(tmp_path)
    extra = _ledger_row(START + 2 * STEP)
    event = normalize_row({**extra, "signal_close": "100", "reference_signal_risk": "10"}, import_id="test")
    assert store.upsert_event(event, notify=False, bark_notify=False)
    rows = [_ledger_row(START), _ledger_row(START + STEP, censored=True), extra]
    with gzip.open(ledger, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    result = link_replay_events(store, ledger_path=ledger, manifest_path=manifest, ohlc_root=root, page_size=2)
    assert result["replay_events"] == result["linked"] == 3
    receipt = tmp_path / "all-pages.csv.gz"
    assert write_link_receipt(store, receipt) == 3


def test_replay_ledger_marks_missing_frozen_ohlc_without_borrowing_an_outcome(tmp_path):
    store, ledger, manifest, root = _setup(tmp_path)
    for path in root.rglob("*.csv.gz"):
        path.unlink()
    result = link_replay_events(store, ledger_path=ledger, manifest_path=manifest, ohlc_root=root)
    assert result["replay_events"] == 2 and result["linked"] == 0 and result["reconciled"] == 2 and result["ohlc_missing"] == 2
    for event in store.list_events(limit=10, source="replay", confirmation="raw"):
        assert event["performance_status"] == "unverified"
        assert event["covered_ledger"]["link_status"] == "ohlc_missing"
        assert event["covered_ledger"]["reason"] == "frozen_ohlc_missing"
        assert "outcome" not in event["covered_ledger"]
    receipt = tmp_path / "missing.csv.gz"
    assert write_link_receipt(store, receipt) == 2
    with gzip.open(receipt, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["link_status"] for row in rows} == {"ohlc_missing"}
    assert {row["link_reason"] for row in rows} == {"frozen_ohlc_missing"}
