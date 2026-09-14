"""Causal contracts for the read-only default V1 MA-drift replay."""

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from yoyo.evaluation.ma_drift_v1_reference import WARMUP_BARS, replay


def drift_fixture(count: int = 420) -> pd.DataFrame:
    """One closed-bar bearish drift: PineTS baseline expects 403 then 409."""
    close = np.full(count, 100.0)
    close[398:404] = [100.02, 100.01, 99.95, 99.90, 99.85, 99.80]
    close[404:409] = [99.78, 99.77, 99.76, 99.75, 99.74]
    close[409] = 99.30
    open_ = np.r_[100.0, close[:-1]]
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01T00:00Z", periods=count, freq="h"),
            "open": open_,
            "high": np.maximum(open_, close) + 0.30,
            "low": np.minimum(open_, close) - 0.30,
            "close": close,
        }
    )


def test_positive_drift_matches_prior_pine_ts_fixture_and_exposes_gates() -> None:
    before = drift_fixture()
    source = before.copy(deep=True)
    state = replay(source, bar_minutes=60)
    assert_frame_equal(source, before)
    assert np.flatnonzero(state.warning.to_numpy()).tolist() == [403]
    assert np.flatnonzero(state.confirmation.to_numpy()).tolist() == [409]
    assert state.setup.iloc[403] and state.pending.iloc[403]
    assert state.frozen_low.iloc[403] == 99.50
    assert state.breakout.iloc[409] and not state.pending.iloc[409]
    required = {
        "s20", "e20", "s60", "e60", "s120", "e120", "atr", "ready",
        "recent_compact", "fast_down", "descending_blocks", "net_close_down",
        "mostly_under_rope", "contained_drift", "close_under_rope",
        "close_near_rope", "setup", "warning", "confirmation",
    }
    assert required.issubset(state.columns)


def test_flat_series_never_sets_up_or_confirms() -> None:
    source = drift_fixture()
    source.loc[:, ["open", "high", "low", "close"]] = [100.0, 100.3, 99.7, 100.0]
    state = replay(source, bar_minutes=60)
    assert state.ready.iloc[WARMUP_BARS - 1]
    assert not state.setup.any()
    assert not state.warning.any()
    assert not state.confirmation.any()


def test_prefix_and_future_mutation_do_not_change_completed_outputs() -> None:
    source = drift_fixture()
    full = replay(source, bar_minutes=60)
    for end in (360, 404, 410):
        prefix = replay(source.iloc[:end].copy(), bar_minutes=60)
        assert_frame_equal(prefix, full.iloc[:end], check_dtype=False)
    changed = source.copy(deep=True)
    changed.loc[410:, ["open", "high", "low", "close"]] = [150.0, 151.0, 149.0, 150.0]
    replayed = replay(changed, bar_minutes=60)
    assert_frame_equal(replayed.iloc[:410], full.iloc[:410], check_dtype=False)
    future_gap = source.copy(deep=True)
    future_gap.loc[415:, "open_time"] += pd.Timedelta(days=1)
    with_gap = replay(future_gap, bar_minutes=60)
    assert_frame_equal(with_gap.iloc[:415], full.iloc[:415], check_dtype=False)


def test_missing_interval_clears_the_pending_path_and_restarts_warmup() -> None:
    source = drift_fixture().drop(index=350).reset_index(drop=True)
    state = replay(source, bar_minutes=60)
    gap_i = 350
    assert state.data_gap.iloc[gap_i]
    assert state.segment_bars.iloc[gap_i] == 1
    assert not state.ready.iloc[gap_i:].any()
    assert not state.warning.any()
    assert not state.confirmation.any()
