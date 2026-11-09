import json
import sqlite3

import pytest

from yoyo.monitor.cloud_inverse_account import HOUR_MS, STEP_12H_MS, CloudInversePaper
from yoyo.monitor.cloud_inverse_feed import (
    RANKABLE_90D_MIN_CANDLES,
    CloudInverseFeed,
    inverse_regime,
    rankable_count_from_checkpoints,
)


def _payload(event_id, symbol, boundary, *, side=1, detected_offset=1_000, stop=90.0):
    return {
        "id": event_id,
        "symbol": symbol,
        "side": side,
        "signal_open_ms": boundary - STEP_12H_MS,
        "confirmed_ms": boundary,
        "detected_ms": boundary + detected_offset,
        "historical": False,
        "entry": 100.0,
        "stop": stop,
        "target": 120.0,
        "week": "week-1",
        "range_mode": "cloud",
    }


class FakePublicClient:
    def __init__(self, now_ms):
        self.now_ms = now_ms

    def clock(self):
        return self.now_ms


def test_rankable_count_uses_same_clock_checkpoint_and_182_bars():
    boundary = 100 * STEP_12H_MS
    symbols = [f"C{i}-USDT-SWAP" for i in range(10)]
    rows = [
        {"symbol": symbols[0], "boundary": boundary,
         "ema_state": {"time": boundary - STEP_12H_MS, "count": RANKABLE_90D_MIN_CANDLES}},
        {"symbol": symbols[1], "boundary": boundary,
         "ema_state": {"time": boundary - STEP_12H_MS, "count": RANKABLE_90D_MIN_CANDLES - 1}},
        {"symbol": symbols[2], "boundary": boundary,
         "ema_state": {"time": boundary - 2 * STEP_12H_MS, "count": 500}},
    ]
    completed, rankable = rankable_count_from_checkpoints(rows, symbols, boundary)
    assert completed == 3
    assert rankable == 1


def test_inverse_regime_requires_all_61_synchronized_closes():
    boundary = 120 * STEP_12H_MS
    eth = []
    btc = []
    for i in range(61):
        t = boundary - (60-i) * STEP_12H_MS
        btc.append({"t": t, "c": 100.0})
        eth.append({"t": t, "c": 100.0 if i < 60 else 105.0})
    assert inverse_regime(eth, btc, boundary) == pytest.approx(0.05)
    assert inverse_regime(eth[:-1], btc, boundary) is None


def test_complete_checkpoint_drains_last_source_rows_before_rank_order(tmp_path):
    boundary = 24 * STEP_12H_MS
    now = boundary + 30_000
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = runtime / "cloud-signals.sqlite3"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE signals(id TEXT PRIMARY KEY,confirmed_ms INTEGER,payload TEXT NOT NULL)")
    first = _payload("id-a", "AAA-USDT-SWAP", boundary)
    second = _payload("id-b", "BBB-USDT-SWAP", boundary, detected_offset=2_000)
    conn.execute("INSERT INTO signals VALUES(?,?,?)", ("id-a", boundary, json.dumps(first)))
    conn.commit()
    conn.close()

    class RacingFeed(CloudInverseFeed):
        def __init__(self):
            super().__init__(runtime, client=FakePublicClient(now), source_path=source,
                             clock=lambda: now)
            self.checks = 0

        def _checkpoint_state(self, boundary_ms, now_ms):
            self.checks += 1
            if self.checks == 2:
                writer = sqlite3.connect(source)
                writer.execute("INSERT INTO signals VALUES(?,?,?)",
                               ("id-b", boundary, json.dumps(second)))
                writer.commit()
                writer.close()
            self.market_state = {"source_boundary_ms": boundary, "source_complete": True}
            return {"boundary_ms": boundary, "source_boundary_ms": boundary,
                    "expected_symbols": 10, "completed_symbols": 10, "rankable_count": 10,
                    "rankable_symbols": {"AAA-USDT-SWAP", "BBB-USDT-SWAP"},
                    "source_complete": True}

        def _candidate_facts(self, candidate, now_ms):
            return -0.05, 0.2 if candidate["symbol"].startswith("BBB") else 0.4

        def _source_event_canonical_ids(self):
            return {"id-a", "id-b"}

        def public_ticker(self, symbol):
            return {"ts_ms": now - 1_000, "bid": 99.9, "ask": 100.0}

    account = CloudInversePaper(runtime, clock=lambda: boundary - 10_000)
    feed = RacingFeed()
    feed.queue_new_events(account, now)
    assert len(account.queued_candidates(boundary)) == 1

    result = feed.process_queued(account, now)
    assert result["batches_processed"] == 1
    assert result["opened"] == 2
    with account.db() as db:
        rows = db.execute("SELECT event_id,entry_order,status FROM source_events ORDER BY entry_order").fetchall()
    assert [(row["event_id"], row["entry_order"], row["status"]) for row in rows] == [
        ("id-b", 0, "opened"), ("id-a", 1, "opened")
    ]
    assert len(account.open_positions()) == 2


def test_empty_account_keeps_source_degraded_health_state(tmp_path):
    boundary = 12 * STEP_12H_MS
    now = boundary + 20 * 60_000
    source = tmp_path / "cloud-signals.sqlite3"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE signals(id TEXT PRIMARY KEY,confirmed_ms INTEGER,payload TEXT NOT NULL)")
    conn.execute("CREATE TABLE checkpoints(symbol TEXT,boundary INTEGER,ema_state TEXT)")
    conn.commit()
    conn.close()

    class IncompleteFeed(CloudInverseFeed):
        def _universe(self, now_ms):
            return ["AAA-USDT-SWAP"]

    feed = IncompleteFeed(tmp_path, client=FakePublicClient(now), source_path=source,
                          clock=lambda: now)
    account = CloudInversePaper(tmp_path, clock=lambda: boundary - 1)
    feed.queue_new_events(account, now)
    assert feed.health()["state"] == "degraded_source_scan_stalled"
    result = feed.process_queued(account, now)
    assert result["batches_processed"] == 0
    assert feed.health()["state"] == "degraded_source_scan_stalled"
