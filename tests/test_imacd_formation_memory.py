"""Synthetic window, state-preservation, and causality checks; no market data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation.imacd_formation_memory import MEMORY_COLUMNS, add_formation_memory


def feature_frame(widths) -> pd.DataFrame:
    widths = np.asarray(widths, dtype=float)
    return pd.DataFrame({
        "rope_low": np.full(len(widths), 100.0),
        "rope_high": 100.0 + widths,
        "release_side": np.zeros(len(widths), dtype=np.int8),
        "keep_proximity": np.ones(len(widths), dtype=bool),
    })


def test_exact_adjacent_windows_and_first_complete_row():
    widths = np.arange(1.0, 161.0)
    out = add_formation_memory(feature_frame(widths))
    assert not out.keep_memory.iloc[:132].any()
    assert out.formation_memory_background_width.iloc[:132].isna().all()
    assert out.formation_memory_recent_width.iloc[:12].isna().all()
    for t in (132, 133, 159):
        recent = np.median(widths[t - 12:t])
        background = np.median(widths[t - 132:t - 12])
        assert out.formation_memory_recent_width.iloc[t] == recent
        assert out.formation_memory_background_width.iloc[t] == background
        assert out.formation_memory_ratio.iloc[t] == recent / background
        assert out.keep_memory.iloc[t] == (recent <= background)


def test_window_boundary_belongs_to_background_not_recent():
    # At t=132, index 119 (t-13) is the last background bar and index 120
    # (t-12) is the first recent bar. Central order statistics expose a shift.
    widths = np.r_[np.arange(1.0, 121.0), np.arange(1001.0, 1013.0), 9000.0]
    out = add_formation_memory(feature_frame(widths)).iloc[132]
    assert out.formation_memory_background_width == 60.5
    assert out.formation_memory_recent_width == 1006.5


def test_current_candle_and_future_cannot_rewrite_decision_features():
    f = feature_frame(5 + np.sin(np.arange(200) / 7))
    original = add_formation_memory(f)
    modified = f.copy(deep=True)
    modified.loc[150:, ["rope_high", "rope_low", "release_side", "keep_proximity"]] = [
        9000.0, 1.0, -1, False,
    ]
    changed = add_formation_memory(modified)
    assert_frame_equal(original.loc[:150, MEMORY_COLUMNS], changed.loc[:150, MEMORY_COLUMNS])
    assert_frame_equal(original.iloc[:151], add_formation_memory(f.iloc[:151]))
    assert changed.formation_memory_recent_width.iloc[163] != original.formation_memory_recent_width.iloc[163]


def test_memory_keeps_already_narrow_shape_despite_focus_local_expansion():
    # Compression preceded the final focus segment. Its first six widths are
    # 1.0 and its final six are 1.2, so the old segment-local test would reject.
    widths = np.r_[np.full(90, 10.0), np.full(36, 1.0), np.full(6, 1.2), 99.0]
    assert np.median(widths[126:132]) > np.median(widths[114:120])
    point = add_formation_memory(feature_frame(widths)).iloc[132]
    assert point.formation_memory_background_width == 10.0
    assert point.formation_memory_recent_width == pytest.approx(1.1)
    assert point.formation_memory_ratio == pytest.approx(.11)
    assert point.keep_memory


def test_rising_width_rejects_and_equal_width_passes():
    rising = add_formation_memory(feature_frame(np.r_[np.full(120, 1.0), np.full(12, 2.0), 0.0]))
    assert rising.formation_memory_ratio.iloc[132] == 2.0
    assert not rising.keep_memory.iloc[132]
    plateau = add_formation_memory(feature_frame(np.full(133, 100.0)))
    assert plateau.formation_memory_ratio.iloc[132] == 1.0
    assert plateau.keep_memory.iloc[132]


@pytest.mark.parametrize("background,recent,ratio,keep", [
    (0.0, 0.0, 1.0, True),
    (0.0, 1.0, np.inf, False),
    (1.0, 0.0, 0.0, True),
])
def test_zero_width_semantics(background, recent, ratio, keep):
    f = feature_frame(np.r_[np.full(120, background), np.full(12, recent), 50.0])
    point = add_formation_memory(f).iloc[132]
    assert point.formation_memory_ratio == ratio
    assert point.keep_memory == keep


@pytest.mark.parametrize("bad_index", [0, 119, 120, 131])
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, 99.0])
def test_every_historical_bar_must_have_finite_nonnegative_width(bad_index, value):
    f = feature_frame(np.ones(133))
    f.loc[bad_index, "rope_high"] = value  # 99.0 is below rope_low=100.0.
    point = add_formation_memory(f).iloc[132]
    assert not point.keep_memory
    assert np.isnan(point.formation_memory_ratio)


def test_missing_data_recovers_only_after_it_leaves_both_windows():
    f = feature_frame(np.ones(280))
    f.loc[100, "rope_low"] = np.nan
    out = add_formation_memory(f)
    assert not out.keep_memory.iloc[132:233].any()
    assert out.keep_memory.iloc[233]


def test_invalid_current_bounds_do_not_invalidate_complete_prior_windows():
    f = feature_frame(np.ones(133))
    f.loc[132, ["rope_high", "rope_low"]] = np.nan
    point = add_formation_memory(f).iloc[132]
    assert point.keep_memory
    assert point.formation_memory_ratio == 1.0


def test_does_not_mutate_input_or_gate_on_release_and_proximity():
    f = feature_frame(np.ones(134))
    f.loc[132, ["release_side", "keep_proximity"]] = [-1, False]
    before = f.copy(deep=True)
    out = add_formation_memory(f)
    assert_frame_equal(f, before)
    assert_frame_equal(out[f.columns], before)
    assert out.keep_memory.iloc[132] and out.keep_memory.iloc[133]
    out.loc[0, "rope_high"] = -10.0
    assert_frame_equal(f, before)


def test_minimal_nullable_input_and_empty_frame():
    f = pd.DataFrame({"rope_high": pd.array([1.0] * 133, dtype="Float64"),
                      "rope_low": pd.array([0.0] * 133, dtype="Float64")})
    assert add_formation_memory(f).keep_memory.iloc[132]
    f.loc[0, "rope_high"] = pd.NA
    assert not add_formation_memory(f).keep_memory.iloc[132]
    out = add_formation_memory(f.iloc[:0])
    assert out.empty
    assert list(out.columns) == list(f.columns) + list(MEMORY_COLUMNS)
    assert out.keep_memory.dtype == bool


@pytest.mark.parametrize("f,message", [
    (None, "DataFrame"),
    (pd.DataFrame({"rope_high": [1.0]}), "rope_low"),
    (pd.DataFrame([[1.0, 0.0, 0.0]], columns=["rope_high", "rope_low", "rope_low"]), "unique"),
    (pd.DataFrame({"rope_high": [1.0, 1.0], "rope_low": [0.0, 0.0]}, index=[1, 0]), "chronological"),
    (pd.DataFrame({"rope_high": [1.0, 1.0], "rope_low": [0.0, 0.0]}, index=[0, 0]), "unique"),
    (pd.DataFrame({"rope_high": ["bad"], "rope_low": [0.0]}), "numeric"),
])
def test_invalid_input_contract(f, message):
    with pytest.raises(ValueError, match=message):
        add_formation_memory(f)
