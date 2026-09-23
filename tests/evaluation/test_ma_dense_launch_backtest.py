"""Contract tests for the dense-launch BTC/ETH backtest scanner."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import ma_dense_launch_backtest as bt


def _bars(n: int = 60) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    close = np.linspace(100.0, 101.0, n)
    return pd.DataFrame({"open": close, "high": close + .5, "low": close - .5,
                         "close": close, "volume": 1.0}, index=index)


def test_aggregate_keeps_only_complete_buckets():
    base = _bars(12)
    base = base.drop(base.index[4])
    bars = bt.aggregate(base, 3)
    assert list(bars.index.minute) == [0, 6, 9]
    assert bars.iloc[0].volume == 3.0 and bars.iloc[0].high == base.high.iloc[:3].max()


def test_outcome_prefers_the_stop_inside_one_bar():
    frame = _bars(10).copy()
    frame.iloc[2, frame.columns.get_loc("low")] = 90.0
    frame.iloc[2, frame.columns.get_loc("high")] = 110.0
    resolved = bt.outcome(frame, 1, 1.0, 100.0, 95.0, 105.0, 5, .002)
    assert resolved["result"] == "SL" and resolved["exit_i"] == 2
    assert resolved["exit_price"] == pytest.approx(95.0)


def test_outcome_times_out_at_the_horizon_close():
    frame = _bars(20)
    resolved = bt.outcome(frame, 1, 1.0, 100.0, 50.0, 150.0, 4, .002)
    assert resolved["result"] == "TIMEOUT" and resolved["exit_i"] == 4
    assert resolved["exit_price"] == pytest.approx(float(frame.close.iloc[4]))


def test_trade_applies_cost_and_risk_units():
    frame = _bars(30).copy()
    frame.iloc[6, frame.columns.get_loc("high")] = 130.0
    trade = bt._trade(frame, 4, 1.0, float(frame.open.iloc[5]) * 0.99, 10, .002, 3.0)
    assert trade["result"] == "TP"
    assert trade["net_bp"] == pytest.approx(trade["gross_bp"] - 20.0, abs=1e-6)
    assert trade["net_r"] == pytest.approx(trade["gross_r"] - .002 / trade["risk_frac"], abs=1e-9)


def test_coarse_gate_rejects_a_flat_tape():
    frame = bt.rules.add_features(_bars(200))
    assert len(bt.coarse_candidates(frame, 1.0)) == 0
    assert len(bt.coarse_candidates(frame, -1.0)) == 0


def test_dedupe_keeps_the_best_quality_per_window():
    base = pd.Timestamp("2026-01-01T00:00Z")
    rows = [{"direction": "LONG", "quality_score": .4, "core_end_time": base, "signal_close": base},
            {"direction": "LONG", "quality_score": .6, "core_end_time": base + pd.Timedelta(hours=1),
             "signal_close": base + pd.Timedelta(hours=1)},
            {"direction": "LONG", "quality_score": .5, "core_end_time": base + pd.Timedelta(hours=9),
             "signal_close": base + pd.Timedelta(hours=9)}]
    kept = bt._deduplicate(rows, 240)
    assert [r["quality_score"] for r in kept] == [.6, .5]


def test_config_matches_the_plan():
    cfg = json.loads(bt.CONFIG.read_text())
    assert cfg["timeframes_min"] == [1, 3, 5, 15, 30, 60]
    assert cfg["r_multiple"] == 3.0 and cfg["horizon_hours"] == 12.0
    assert cfg["round_trip_cost"] == 0.002 and cfg["dedupe_minutes"] == 240
    assert cfg["window_start"].startswith("2024-09-23") and cfg["window_end"].startswith("2026-09-22")
    assert Path(cfg["reference_pack"]).exists()
