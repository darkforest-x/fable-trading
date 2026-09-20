"""Frozen projection-version snapshots for the SPIKE joint ledger."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from yoyo.monitor import spike_lines_api as api
from yoyo.monitor.spike_lines_worker import LEGACY_PERFORMANCE_BASIS, LinesBook

RSI_BASIS = "v11_2_box_joint_rsi7_same_tf_streak_next_open_v1_net_cost"
RSI_V2_BASIS = "v11_2_box_joint_rsi7_since_entry_same_tf_streak_next_open_v2_net_cost"


def _joint(number: int, *, side: str = "long", performance: dict | None = None) -> dict:
    return {
        "id": f"{number:024d}", "kind": "joint", "symbol": f"S{number}-USDT-SWAP", "timeframe": "15m",
        "bar_open_ms": number * 1_000, "bar_close_ms": number * 1_000 + 900,
        "detected_at_ms": number * 1_000 + 950, "side": side,
        "performance": performance or {"status": "profit", "exit_r": float(number), "current_r": float(number),
                                         "basis": LEGACY_PERFORMANCE_BASIS},
    }


def _book(tmp_path) -> tuple[LinesBook, object]:
    path = tmp_path / "spike.sqlite3"
    return LinesBook(path), path


def test_policy_switch_archives_all_existing_joints_before_refresh_including_old_rows(tmp_path):
    book, path = _book(tmp_path)
    old = _joint(1)
    aged = _joint(2, performance={"status": "loss", "exit_r": -1.0, "current_r": -1.0,
                                   "basis": LEGACY_PERFORMANCE_BASIS})
    book.insert([old, aged])

    policy = book.initialize_performance_policy(RSI_BASIS, changed_at_ms=55_000)
    assert policy == {"version": RSI_BASIS, "changed_at_ms": 55_000,
                      "baseline_version": LEGACY_PERFORMANCE_BASIS, "baseline_snapshot_ms": 55_000}

    baseline = api.ledger(path, kind="joint", now_ms=999_999, performance_version="baseline")
    current = api.ledger(path, kind="joint", now_ms=999_999)
    assert baseline["basis"] == LEGACY_PERFORMANCE_BASIS
    assert baseline["as_of_ms"] == baseline["performance_snapshot_ms"] == 55_000
    assert {row["id"] for row in baseline["items"]} == {old["id"], aged["id"]}
    assert baseline["stats"]["realized_r"] == pytest.approx(0.0)
    assert baseline["available_performance_versions"][-1]["value"] == "baseline"
    assert current["stats"] == baseline["stats"]
    assert current["available_performance_versions"][-1]["value"] == "baseline"
    assert current["available_performance_versions"][0]["label"] == "旧全局计数 · 当前回算"


def test_v2_switch_retains_initial_baseline_and_freezes_v1_as_previous(tmp_path):
    book, path = _book(tmp_path)
    original = _joint(1)
    book.insert([original])
    book.initialize_performance_policy(RSI_BASIS, changed_at_ms=100)
    book.refresh_performance(original["symbol"], original["timeframe"], {
        original["id"]: {"status": "profit", "exit_r": 4.0, "current_r": 4.0, "basis": RSI_BASIS},
    })
    v1_later = _joint(2, performance={"status": "loss", "exit_r": -3.0, "current_r": -3.0, "basis": RSI_BASIS})
    book.insert([v1_later])

    policy = book.initialize_performance_policy(RSI_V2_BASIS, changed_at_ms=200)
    assert policy == {"version": RSI_V2_BASIS, "changed_at_ms": 200,
                      "baseline_version": LEGACY_PERFORMANCE_BASIS, "baseline_snapshot_ms": 100,
                      "previous_version": RSI_BASIS, "previous_snapshot_ms": 200}
    book.refresh_performance(original["symbol"], original["timeframe"], {
        original["id"]: {"status": "loss", "exit_r": -5.0, "current_r": -5.0, "basis": RSI_V2_BASIS},
    })
    book.refresh_performance(v1_later["symbol"], v1_later["timeframe"], {
        v1_later["id"]: {"status": "profit", "exit_r": 6.0, "current_r": 6.0, "basis": RSI_V2_BASIS},
    })
    v2_later = _joint(3, performance={"status": "profit", "exit_r": 9.0, "current_r": 9.0, "basis": RSI_V2_BASIS})
    book.insert([v2_later])

    baseline = api.ledger(path, kind="joint", now_ms=1_000, performance_version="baseline")
    previous = api.ledger(path, kind="joint", now_ms=1_000, performance_version="previous")
    current = api.ledger(path, kind="joint", now_ms=1_000)
    assert {row["id"] for row in baseline["items"]} == {original["id"]}
    assert baseline["performance_snapshot_ms"] == baseline["as_of_ms"] == 100
    assert {row["id"] for row in previous["items"]} == {original["id"], v1_later["id"]}
    assert previous["performance_snapshot_ms"] == previous["as_of_ms"] == 200
    assert previous["items"][0]["is_fresh"] is False
    assert {row["id"] for row in current["items"]} == {original["id"], v1_later["id"], v2_later["id"]}
    assert current["basis"] == RSI_V2_BASIS
    assert current["available_performance_versions"] == [
        {"value": "current", "label": "开仓后RSI第7个 · 当前回算"},
        {"value": "baseline", "label": "原价格退出 · 初始快照"},
        {"value": "previous", "label": "旧全局计数 · 修正前快照"},
    ]
    assert LinesBook(path).initialize_performance_policy(RSI_V2_BASIS, changed_at_ms=999) == policy
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM performance_versions WHERE version=? AND saved_ms=?",
                          (RSI_BASIS, 200)).fetchone()[0] == 2


def test_refresh_and_later_insert_cannot_overwrite_or_expand_frozen_baseline(tmp_path):
    book, path = _book(tmp_path)
    old = _joint(1)
    aged = _joint(3, performance={"status": "profit", "exit_r": 1.0, "current_r": 1.0,
                                  "basis": LEGACY_PERFORMANCE_BASIS})
    book.insert([old, aged])
    book.initialize_performance_policy(RSI_BASIS, changed_at_ms=100)

    book.refresh_performance(old["symbol"], old["timeframe"], {
        old["id"]: {"status": "loss", "exit_r": -3.0, "current_r": -3.0, "basis": RSI_BASIS},
    })
    later = _joint(2, performance={"status": "profit", "exit_r": 8.0, "current_r": 8.0, "basis": RSI_BASIS})
    book.insert([later])

    baseline = api.ledger(path, kind="joint", now_ms=10_000, performance_version="baseline")
    current = api.ledger(path, kind="joint", now_ms=10_000)
    assert {row["id"] for row in baseline["items"]} == {old["id"], aged["id"]}
    assert next(row for row in baseline["items"] if row["id"] == old["id"])["performance"]["exit_r"] == pytest.approx(1.0)
    assert current["stats"]["realized_r"] == pytest.approx(6.0)
    assert set(current["projection_versions"]) == {LEGACY_PERFORMANCE_BASIS, RSI_BASIS}
    assert current["projection_version_counts"][RSI_BASIS] == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM performance_versions").fetchone()[0] == 2


def test_policy_restart_is_idempotent_and_keeps_first_detection_and_snapshot(tmp_path):
    book, path = _book(tmp_path)
    old = _joint(1)
    book.insert([old])
    first = book.initialize_performance_policy(RSI_BASIS, changed_at_ms=100)
    restarted = LinesBook(path)
    assert restarted.initialize_performance_policy(RSI_BASIS, changed_at_ms=999) == first
    assert api.events(path, kind="joint", now_ms=2_000)[0]["detected_at_ms"] == old["detected_at_ms"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM performance_versions").fetchone()[0] == 1
        assert db.execute("SELECT saved_ms FROM performance_versions").fetchone()[0] == 100


def test_cross_version_refresh_without_a_policy_refuses_to_rewrite_or_archive(tmp_path):
    book, path = _book(tmp_path)
    old = _joint(1)
    book.insert([old])
    with pytest.raises(RuntimeError, match="cross-version"):
        book.refresh_performance(old["symbol"], old["timeframe"], {
            old["id"]: {"status": "loss", "exit_r": -9.0, "current_r": -9.0, "basis": RSI_BASIS},
        })
    assert api.events(path, kind="joint", now_ms=2_000)[0]["performance"] == old["performance"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM performance_versions").fetchone()[0] == 0


def test_baseline_preserves_side_and_frozen_stats_as_of_switch(tmp_path):
    book, path = _book(tmp_path)
    book.insert([_joint(1, side="long"), _joint(2, side="short", performance={
        "status": "loss", "exit_r": -2.0, "current_r": -2.0, "basis": LEGACY_PERFORMANCE_BASIS})])
    book.initialize_performance_policy(RSI_BASIS, changed_at_ms=50)
    baseline = api.ledger(path, kind="joint", now_ms=500, performance_version="baseline")
    assert baseline["as_of_ms"] == 50 and baseline["stats"]["short"] == 1
    assert baseline["stats"]["realized_r"] == pytest.approx(-1.0)
    short = next(row for row in baseline["items"] if row["side"] == "short")
    assert short["is_fresh"] is False


@pytest.mark.parametrize("period", ["today", "week"])
def test_baseline_calendar_filters_use_the_frozen_snapshot_clock(tmp_path, period):
    book, path = _book(tmp_path)
    # Sunday 23:00 in Shanghai; the current clock is Monday in the next week.
    close = int(datetime(2026, 9, 20, 15, tzinfo=timezone.utc).timestamp() * 1_000)
    event = _joint(1)
    event.update(bar_open_ms=close - 900, bar_close_ms=close, detected_at_ms=close + 1)
    book.insert([event])
    book.initialize_performance_policy(RSI_BASIS, changed_at_ms=close + 100)
    later = int(datetime(2026, 9, 21, 16, tzinfo=timezone.utc).timestamp() * 1_000)
    baseline = api.ledger(path, kind="joint", now_ms=later, period=period, performance_version="baseline")
    current = api.ledger(path, kind="joint", now_ms=later, period=period)
    assert baseline["total"] == 1 and baseline["as_of_ms"] == close + 100
    assert current["total"] == 0


def test_legacy_database_without_policy_or_archive_stays_honest(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE events (id TEXT PRIMARY KEY, kind TEXT, symbol TEXT, timeframe TEXT,
                bar_open_ms INTEGER, bar_close_ms INTEGER, detected_at_ms INTEGER, payload TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY, payload TEXT);
        """)
        event = _joint(1)
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (
            event["id"], event["kind"], event["symbol"], event["timeframe"], event["bar_open_ms"],
            event["bar_close_ms"], event["detected_at_ms"], __import__("json").dumps(event)))
    current = api.ledger(path, kind="joint", now_ms=2_000)
    baseline = api.ledger(path, kind="joint", now_ms=2_000, performance_version="baseline")
    assert current["basis"] == LEGACY_PERFORMANCE_BASIS
    assert current["available_performance_versions"] == [{"value": "current", "label": "原退出 · 当前回算"}]
    assert baseline["items"] == [] and baseline["performance_snapshot_ms"] is None


def test_ledger_route_rejects_unknown_projection_version(tmp_path):
    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app

    client = TestClient(create_app(runtime=tmp_path, start_monitor=False))
    assert client.get("/api/lines/ledger", params={"performance_version": "tomorrow"}).status_code == 400
