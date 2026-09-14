"""Causal boundary contracts for the V8 four-hour failure exit."""
from __future__ import annotations

import pandas as pd
import numpy as np
from pathlib import Path

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as common
from yoyo.evaluation import spike_v8_four_hour_exit as study
from yoyo.evaluation.spike_v8_four_hour_exit import _check_four_hour


def _position(entry: str, *, side: int = 1, mfe: float = .5) -> dict[str, object]:
    return {"entry_time": pd.Timestamp(entry, tz="UTC"), "entry_price": 100., "initial_risk": 10., "side": side,
            "mfe_r": mfe, "four_hour_checked": False, "four_hour_triggered": False,
            "four_hour_check_bar_open": pd.NaT, "four_hour_mfe_r": float("nan"), "four_hour_close_gross_r": float("nan")}


def test_30m_check_is_first_completed_bar_at_exactly_four_hours_and_only_once() -> None:
    pos = _position("2025-01-01T00:00:00Z")
    assert not _check_four_hour(pos, stamp=pd.Timestamp("2025-01-01T03:00:00Z"), minutes=30, high=105., low=99., close=100.)
    assert _check_four_hour(pos, stamp=pd.Timestamp("2025-01-01T03:30:00Z"), minutes=30, high=105., low=99., close=100.)
    assert pos["four_hour_checked"] and pos["four_hour_triggered"]
    assert not _check_four_hour(pos, stamp=pd.Timestamp("2025-01-01T04:00:00Z"), minutes=30, high=105., low=99., close=100.)


def test_four_hour_bar_can_be_checked_on_its_entry_bar_close() -> None:
    pos = _position("2025-01-01T00:00:00Z", mfe=.9)
    assert _check_four_hour(pos, stamp=pd.Timestamp("2025-01-01T00:00:00Z"), minutes=240, high=109., low=99., close=100.)


def test_one_r_mfe_or_positive_close_blocks_the_fixed_exit_after_a_real_check() -> None:
    pos = _position("2025-01-01T00:00:00Z", mfe=1.)
    assert not _check_four_hour(pos, stamp=pd.Timestamp("2025-01-01T03:30:00Z"), minutes=30, high=110., low=99., close=100.)
    assert pos["four_hour_checked"] and not pos["four_hour_triggered"]
    pos = _position("2025-01-01T00:00:00Z", mfe=.5)
    assert not _check_four_hour(pos, stamp=pd.Timestamp("2025-01-01T03:30:00Z"), minutes=30, high=105., low=99., close=100.01)
    assert pos["four_hour_close_gross_r"] > 0


def _prepared(rows: list[tuple[float, float, float, float]], *, opposite_at: int | None = None) -> common.PreparedArm:
    # V6's authentic initial-stop constructor needs its frozen 20-bar lookback.
    rows = [(100., 101., 99., 100.)] * 20 + rows
    index = pd.date_range("2025-01-01", periods=len(rows), freq="4h", tz="UTC")
    bars = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index).assign(atr=1.)
    context = base.StreamContext(Path("."), "synthetic_240m", {}, {"bars": bars, "tick": .01}, pd.DataFrame(), 240,
                                 {"venue": "synthetic", "symbol": "SYN", "asset": "SYN", "timeframe_min": 240})
    raw = np.zeros(len(bars), dtype=int); raw[20] = 1
    if opposite_at is not None: raw[20 + opposite_at] = -1
    arrays = [bars[name].to_numpy(float) for name in ("open", "high", "low", "close", "atr")]
    return common.PreparedArm(context, "v8", "v7_both", bars, np.zeros(len(bars), dtype=bool), np.ones(len(bars), dtype=bool), raw,
                              *arrays, {}, base.ExecutionSpec(tick=.01))


def _replay(monkeypatch, prepared: common.PreparedArm) -> pd.DataFrame:
    monkeypatch.setattr(study.common, "prepare_arm", lambda context, arm: prepared)
    return study.replay_serial(prepared.context, candidate=True)[0]


def test_same_four_hour_bar_stop_wins_even_when_its_high_reaches_one_r(monkeypatch) -> None:
    trades = _replay(monkeypatch, _prepared([(100, 100, 99, 100), (100, 200, 0, 100), (100, 100, 99, 100)]))
    row = trades.iloc[0]
    assert row.exit_reason == "initial_stop"
    assert not row.four_hour_checked


def test_planned_four_hour_exit_yields_to_next_open_gap_stop(monkeypatch) -> None:
    trades = _replay(monkeypatch, _prepared([(100, 100, 99, 100), (100, 101, 99, 99), (95, 96, 94, 95)]))
    row = trades.iloc[0]
    assert row.four_hour_triggered
    assert row.exit_reason == "initial_stop_gap"


def test_raw_opposite_exit_precedes_same_open_four_hour_exit(monkeypatch) -> None:
    trades = _replay(monkeypatch, _prepared([(100, 100, 99, 100), (100, 101, 99, 99), (100, 101, 99, 100)], opposite_at=1))
    row = trades.iloc[0]
    assert row.four_hour_triggered
    assert row.exit_reason == "opposite_v6_next_open"


def test_trigger_on_last_completed_bar_is_censored_without_inventing_next_open(monkeypatch) -> None:
    trades = _replay(monkeypatch, _prepared([(100, 100, 99, 100), (100, 101, 99, 99)]))
    row = trades.iloc[0]
    assert row.four_hour_triggered and row.censored
    assert row.exit_reason == "boundary_mark"
