"""Causal boundary contracts for the V8 four-hour failure exit."""
from __future__ import annotations

import pandas as pd

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
