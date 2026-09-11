"""Focused contract checks for the immutable 133-record review-data builder."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v1_review_data as review


def test_select_records_rejects_selection_count_drift(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.csv.gz"
    fields = ["event_id", "venue", "symbol", "timeframe_min", "direction", "signal_close_time"]
    with gzip.open(ledger, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"event_id": "one", "venue": "okx", "symbol": "A-USDT-SWAP", "timeframe_min": 30, "direction": "long", "signal_close_time": "2026-08-27T00:00:00Z"})
    monkeypatch.setattr(review, "LEDGER_SHA256", hashlib.sha256(ledger.read_bytes()).hexdigest())
    monkeypatch.setattr(review, "_load_live_records", lambda _: [])
    with pytest.raises(review.ReviewDataError, match="frozen_selection_drift"):
        review.select_records(ledger, tmp_path / "no.db")


def test_chart_features_use_full_segment_before_display_slice():
    index = pd.date_range("2026-01-01", periods=500, freq="30min", tz="UTC")
    close = np.arange(500, dtype=float) / 10 + 10
    source = pd.DataFrame({"open": close - .1, "high": close + .2, "low": close - .3, "close": close, "volume": 100.}, index=index)
    target = index[400]
    record = review.ReviewRecord("id", "live_journal", "A-USDT-SWAP", 30, int((target + pd.Timedelta(minutes=30)).value // 1_000_000), {"price": float(close[400]), "initial_stop": 1.})
    chart = review._chart(record, source, {"type": "test"})
    expected = review.features(source).loc[target, "s120"]
    signal = chart["candles"][chart["signal_candle_index"]]
    assert chart["coverage"]["display_before_bars"] == 100
    assert signal["sma120"] == pytest.approx(float(expected))
    assert chart["future_context"] == review.FUTURE_CONTEXT
    assert chart["entry"] is None and chart["exit"] is None
    assert chart["initial_stop"] == 1.
    assert "active_stop" not in signal


def test_covered_ohlc_requires_replay_evidence_hash(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    path = source_root / "data" / "normalized" / "okx" / "A-USDT-SWAP_30m.csv.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(b"time,open,high,low,close,volume\n", mtime=0))
    monkeypatch.setattr(review, "SOURCE_ROOT", source_root)
    record = review.ReviewRecord("ledger", "covered_ledger", "A-USDT-SWAP", 30, 0, {})
    with pytest.raises(review.ReviewDataError, match="frozen_ohlc_hash_mismatch"):
        review._source_ohlcv(tmp_path, tmp_path / "monitor.sqlite3", record, {
            "ledger_sha256": review.LEDGER_SHA256,
            "frozen_ohlc_sha256": "wrong",
            "frozen_ohlc_timeframe_min": 30,
        })


def test_live_checkpoint_is_frozen_once_and_not_re_read(tmp_path, monkeypatch):
    record = review.ReviewRecord("id", "live_journal", "A-USDT-SWAP", 30, 10, {})
    candles = [{"t": 0, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 3}]
    monkeypatch.setattr(review, "_read_checkpoint", lambda *_: candles)
    first, provenance = review._live_ohlcv(tmp_path, tmp_path / "monitor.sqlite3", record)
    monkeypatch.setattr(review, "_read_checkpoint", lambda *_: pytest.fail("checkpoint must not be reread"))
    second, _ = review._live_ohlcv(tmp_path, tmp_path / "monitor.sqlite3", record)
    assert first.equals(second)
    assert provenance["type"] == "frozen_live_checkpoint"
