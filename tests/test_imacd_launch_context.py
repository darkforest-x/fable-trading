"""Synthetic prior-box and independent higher-open-context contract tests."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation.imacd_launch_context import HIGHER, add_context
from yoyo.evaluation.imacd_launch_research import policy_masks


CONTEXT_COLUMNS = ["prior_box_high", "prior_box_low", "box_breakout", "breakout_score",
                   "htf_known", "htf_md", "htf_atr", "htf_close_ms", "htf_same_direction", "htf_score"]


def frames(index, side=1):
    index = pd.DatetimeIndex(index)
    bars = pd.DataFrame({"open": 100.0, "high": 110.0, "low": 90.0, "close": 100.0,
                         "volume": 1.0}, index=index)
    features = pd.DataFrame({"release_side": side, "atr": 2.0, "sentinel": np.arange(len(index))}, index=index)
    return bars, features


def higher_frames(period=60, n=350):
    bars, _ = frames(pd.date_range("1970-01-01", periods=n, freq=f"{period}min", tz="UTC"))
    features = pd.DataFrame({"md": np.arange(n, dtype=float)+1, "atr": 2.0}, index=bars.index)
    return bars, features


def empty_higher():
    bars, features = higher_frames(n=0)
    return bars, features


@pytest.mark.parametrize("side,close,broke,score", [(1, 110.0, False, 0.0), (1, 111.0, True, .5),
    (1, 109.0, False, -.5), (-1, 90.0, False, 0.0), (-1, 89.0, True, .5), (-1, 91.0, False, -.5)])
def test_strict_breakout_uses_previous_high_low_not_current_extreme(side, close, broke, score):
    bars, f = frames(pd.date_range("1970-01-01", periods=13, freq="15min", tz="UTC"), side)
    bars.loc[bars.index[-1], ["close", "high", "low"]] = [close, 999.0, .01]
    f.loc[f.index[-1], "atr"] = 1000.0  # Score must use prior ATR=2, not this candle.
    out = add_context(bars, f, *empty_higher(), 15)
    assert not out.box_breakout.iloc[:12].any()
    assert out.prior_box_high.iloc[:12].isna().all()
    assert out.prior_box_high.iloc[12] == 110.0
    assert out.prior_box_low.iloc[12] == 90.0
    assert out.box_breakout.iloc[12] == broke
    assert out.breakout_score.iloc[12] == score


@pytest.mark.parametrize("side", [1, -1])
def test_twelve_bar_left_boundary_is_inclusive_and_thirteenth_bar_excluded(side):
    bars, f = frames(pd.date_range("1970-01-01", periods=14, freq="15min", tz="UTC"), side)
    bars.loc[bars.index[0], ["high", "low"]] = [9999.0, .001]  # t-13, excluded.
    bars.loc[bars.index[1], ["high", "low"]] = [115.0, 85.0]  # t-12, included.
    bars.loc[bars.index[13], "close"] = 114.0 if side == 1 else 86.0
    out = add_context(bars, f, *empty_higher(), 15)
    assert out.prior_box_high.iloc[13] == 115.0
    assert out.prior_box_low.iloc[13] == 85.0
    assert not out.box_breakout.iloc[13]
    bars.loc[bars.index[1], ["high", "low"]] = [113.0, 87.0]
    assert add_context(bars, f, *empty_higher(), 15).box_breakout.iloc[13]


@pytest.mark.parametrize("side,column", [(1, "high"), (-1, "low")])
def test_incomplete_prior_box_fails_closed(side, column):
    bars, f = frames(pd.date_range("1970-01-01", periods=14, freq="15min", tz="UTC"), side)
    bars.loc[bars.index[1], column] = np.nan
    bars.loc[bars.index[-1], "close"] = 111 if side == 1 else 89
    assert not add_context(bars, f, *empty_higher(), 15).box_breakout.iloc[-1]


@pytest.mark.parametrize("minutes", [15, 60, 240])
def test_higher_candle_closed_at_local_close_is_not_available_at_local_open(minutes):
    higher_period = HIGHER[minutes]
    hb, hf = higher_frames(higher_period)
    higher_open = hb.index[342]
    index = pd.date_range(higher_open, periods=higher_period//minutes+1, freq=f"{minutes}min")
    bars, f = frames(index)
    out = add_context(bars, f, hb, hf, minutes)
    # Until the next higher boundary, only higher bar 341 has closed.
    assert out.htf_known.all()
    assert out.htf_md.iloc[:-1].eq(hf.md.iloc[341]).all()
    assert out.htf_md.iloc[-1] == hf.md.iloc[342]
    old_close_ms = int(higher_open.timestamp()*1000)
    assert out.htf_close_ms.iloc[:-1].eq(old_close_ms).all()
    assert out.htf_close_ms.iloc[-1] == old_close_ms + higher_period*60000
    # The penultimate local bar closes at the next higher boundary; it still
    # cannot use that higher candle because this contract snapshots local OPEN.
    assert out.htf_md.iloc[-2] != hf.md.iloc[342]


@pytest.mark.parametrize("minutes", [15, 60, 240])
def test_higher_warmup_requires_closed_index_340_and_includes_exact_boundary(minutes):
    higher_period = HIGHER[minutes]
    hb, hf = higher_frames(higher_period)
    index = pd.date_range(hb.index[340], periods=higher_period//minutes+1, freq=f"{minutes}min")
    bars, f = frames(index)
    out = add_context(bars, f, hb, hf, minutes)
    assert not out.htf_known.iloc[:-1].any()  # Latest closed index=339.
    assert out.htf_known.iloc[-1]  # Index340 closes exactly at local OPEN.
    assert out.htf_md.iloc[-1] == hf.md.iloc[340]
    assert out.htf_close_ms.iloc[:-1].isna().all()


@pytest.mark.parametrize("side,higher_md,same", [(1, 2.0, True), (1, -2.0, False), (1, 0.0, False),
    (-1, -2.0, True), (-1, 2.0, False), (-1, 0.0, False), (0, 2.0, False)])
def test_higher_direction_is_strict_and_mirrored(side, higher_md, same):
    hb, hf = higher_frames()
    hf.loc[hf.index[341], "md"] = higher_md
    bars, f = frames([hb.index[342]], side)
    point = add_context(bars, f, hb, hf, 15).iloc[0]
    assert point.htf_known
    assert point.htf_same_direction == same
    if side:
        assert point.htf_score == side*higher_md/2
    else:
        assert np.isnan(point.htf_score)
        assert not point.box_breakout


@pytest.mark.parametrize("column,value", [("md", np.nan), ("md", np.inf), ("atr", np.nan),
    ("atr", np.inf), ("atr", 0.0), ("atr", -1.0)])
def test_nonfinite_or_nonpositive_higher_context_fails_closed(column, value):
    hb, hf = higher_frames()
    hf.loc[hf.index[341], column] = value
    bars, f = frames([hb.index[342]])
    point = add_context(bars, f, hb, hf, 15).iloc[0]
    assert not point.htf_known
    assert not point.htf_same_direction
    assert np.isnan(point.htf_md) and np.isnan(point.htf_atr) and np.isnan(point.htf_close_ms)


def test_empty_not_yet_closed_and_stale_higher_history_fail_closed():
    hb, hf = higher_frames()
    for timestamp, high_bars, high_features in [
        (hb.index[342], *empty_higher()),
        (hb.index[0], hb, hf),
        (hb.index[-1] + pd.Timedelta(hours=2), hb, hf),
    ]:
        bars, f = frames([timestamp])
        point = add_context(bars, f, high_bars, high_features, 15).iloc[0]
        assert not point.htf_known and not point.htf_same_direction


def test_missing_expected_higher_candle_does_not_fall_back_to_older_context():
    hb, hf = higher_frames()
    timestamp = hb.index[345]
    dropped = hb.index[344]
    hb, hf = hb.drop(index=dropped), hf.drop(index=dropped)
    bars, f = frames([timestamp])
    point = add_context(bars, f, hb, hf, 15).iloc[0]
    assert not point.htf_known and np.isnan(point.htf_close_ms)


def test_current_forming_higher_and_future_local_changes_preserve_prefix():
    hb, hf = higher_frames()
    bars, f = frames(pd.date_range(hb.index[342], periods=20, freq="15min"))
    old = add_context(bars, f, hb, hf, 15)
    hf2, bars2, f2 = hf.copy(), bars.copy(), f.copy()
    hf2.loc[hf.index[342]:, ["md", "atr"]] = [-999, 900]
    bars2.loc[bars.index[4]:, ["close", "high", "low"]] = [500, 600, 400]
    f2.loc[f.index[4]:, "release_side"] = -1
    new = add_context(bars2, f2, hb, hf2, 15)
    assert_frame_equal(old.iloc[:4][CONTEXT_COLUMNS], new.iloc[:4][CONTEXT_COLUMNS])
    assert_frame_equal(old.iloc[:4], add_context(bars.iloc[:4], f.iloc[:4], hb.iloc[:342], hf.iloc[:342], 15))
    assert new.htf_md.iloc[4] != old.htf_md.iloc[4]


def test_input_frames_are_preserved():
    hb, hf = higher_frames()
    bars, f = frames(pd.date_range(hb.index[342], periods=20, freq="15min"))
    before = [x.copy(deep=True) for x in (bars, f, hb, hf)]
    out = add_context(bars, f, hb, hf, 15)
    for actual, expected in zip((bars, f, hb, hf), before):
        assert_frame_equal(actual, expected)
    assert_frame_equal(out[f.columns], f)


def test_two_candidate_policies_are_independent_and_h00_is_coverage_only():
    f = pd.DataFrame({"release_side": [1, -1, 1, 0], "box_breakout": [True, False, False, True],
                      "htf_known": [False, True, True, True], "htf_same_direction": [False, True, False, True]})
    masks = policy_masks(f)
    assert list(masks) == ["P00", "S01", "H00", "H01"]
    assert masks["P00"].tolist() == [True, True, True, False]
    assert masks["S01"].tolist() == [True, False, False, False]
    assert masks["H00"].tolist() == [False, True, True, False]
    assert masks["H01"].tolist() == [False, True, False, False]


def test_rejects_misaligned_feature_clocks_and_unsupported_period():
    hb, hf = higher_frames()
    bars, f = frames(pd.date_range(hb.index[342], periods=20, freq="15min"))
    with pytest.raises(ValueError, match="align"):
        add_context(bars, f.iloc[:-1], hb, hf, 15)
    with pytest.raises(ValueError, match="align"):
        add_context(bars, f, hb, hf.iloc[:-1], 15)
    with pytest.raises(ValueError, match="unsupported"):
        add_context(bars, f, hb, hf, 30)
