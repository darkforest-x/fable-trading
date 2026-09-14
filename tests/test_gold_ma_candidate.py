"""Causality and non-vacuous display-state checks for the V2 candidate."""
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation.gold_ma_candidate import replay


def fixture():
    close = 100 + np.sin(np.arange(500) / 13) * 0.5
    opening = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open_time": pd.date_range("2025-01-01", periods=500, freq="min", tz="UTC"),
        "open": opening, "close": close, "high": np.maximum(opening, close)+0.1,
        "low": np.minimum(opening, close)-0.1})


def test_both_sides_and_later_confirmation_use_actual_bars():
    out = replay(fixture(), bar_minutes=1)
    for side in ("long", "short"):
        starts = np.flatnonzero(out[f"a_{side}_marker"])
        confirms = np.flatnonzero(out[f"a_{side}_confirmation"])
        assert len(starts) >= 2 and len(confirms) >= 1
        assert not set(starts) & set(confirms)
        assert all(any(1 <= end-start <= 5 for start in starts) for end in confirms)
        assert np.all(np.diff(starts) >= 6)


def test_marker_prefix_and_future_price_and_time_invariance():
    frame = fixture()
    out = replay(frame, bar_minutes=1)
    assert out.filter(regex="_marker$").iloc[:410].to_numpy().any()
    assert_frame_equal(replay(frame.iloc[:410], bar_minutes=1), out.iloc[:410])
    changed = frame.copy()
    changed.loc[410:, "open_time"] += pd.Timedelta(days=3)
    changed.loc[410:, ["open", "high", "low", "close"]] *= 10
    assert_frame_equal(replay(changed, bar_minutes=1).iloc[:410], out.iloc[:410])


def test_constant_bundle_cannot_supply_topological_evidence():
    frame = fixture()
    frame.loc[:, ["open", "high", "low", "close"]] = [100, 100.1, 99.9, 100]
    out = replay(frame, bar_minutes=1)
    assert not out.filter(regex="_marker$").to_numpy().any()
    assert not out.a_topology.iloc[360:].any()
