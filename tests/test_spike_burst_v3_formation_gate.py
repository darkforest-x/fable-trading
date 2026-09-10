"""Synthetic historical-only formation, edge clocks and V3 child contracts."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_early_warning as ew
from yoyo.evaluation import spike_burst_v3_formation_gate as fg


def frame(n=100):
    result = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n) + 400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., pastWidth=1., pastCrosses=3.,
        middle=100. + np.arange(n) * .01, md=1., sb=0., rv=1., expansion=1.),
        index=pd.date_range("2026-07-10T00:00Z", periods=n, freq="h"))
    result.attrs["minutes"] = 60
    return result


def launch(f, i, close=104., volume=600.):
    f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [100., close + .2, 99., close, volume]


def set_width(f, start, end, width):
    """All other MAs are 99..100; widths >=1 set the exact maximum."""
    f.loc[f.index[start:end], "s120"] = 99. + np.asarray(width)


def test_exact_prior_six_by_six_medians_exclude_current_width():
    f = frame()
    set_width(f, 18, 24, [8., 2., 10., 4., 12., 6.])
    set_width(f, 24, 30, [1., 2., 3., 4., 5., 6.])
    set_width(f, 30, 31, 1000.)
    result = fg.detect(f)
    assert result.formation_old_median.iloc[30] == 7.
    assert result.formation_recent_median.iloc[30] == 3.5
    assert result.formation_width.iloc[30] == 1000.
    assert result.formed.iloc[30]


def test_comparison_is_median_not_mean_or_a_minimum_width_proxy():
    f = frame()
    set_width(f, 18, 24, [1., 1., 1., 1., 1., 100.])
    set_width(f, 24, 30, 2.)
    result = fg.detect(f)
    assert result.formation_old_median.iloc[30] == 1.
    assert result.formation_recent_median.iloc[30] == 2.
    assert not result.formed.iloc[30]  # Older mean exceeds2, but its median does not.


def test_horizontal_width_passes_and_flat_case_matches_original_v3_events():
    f = frame()
    for i in (30, 42, 54):
        launch(f, i, 104. + i / 10)
    base, result = ew.detect(f), fg.detect(f)
    assert not result.formation_valid.iloc[:12].any()
    assert result.formation_valid.iloc[12:].all()
    assert result.formed.iloc[12:].all()
    pd.testing.assert_frame_equal(result[base.columns], base)


def test_widening_blocks_early_and_does_not_create_an_orphan_child():
    f = frame()
    set_width(f, 24, 30, 4.)
    launch(f, 30)
    result = fg.detect(f)
    assert result.candidate_edge.iloc[30] and result.formation_valid.iloc[30]
    assert not result.formed.iloc[30]
    assert result.formation_blocked.iloc[30]
    assert not result.early.any() and not result.confirmed.any()
    assert np.isnan(result.parent_i.iloc[30])
    assert not result.cooldown_blocked.iloc[30]


def test_formation_rejection_does_not_spend_accepted_cooldown():
    f = frame()
    set_width(f, 24, 30, 4.)
    launch(f, 30)
    launch(f, 36, 108.)
    result = fg.detect(f)
    assert result.formation_blocked.iloc[30]
    assert result.early.iloc[36]  # Six bars since rejected edge, no accepted parent yet.
    assert result.parent_i.iloc[36] == 36


def test_gate_recovery_does_not_queue_a_consumed_raw_edge():
    f = frame()
    set_width(f, 24, 30, 4.)
    for i in range(30, 38):
        launch(f, i, 110. + i)
    launch(f, 40, 160.)
    result = fg.detect(f)
    assert result.formation_blocked.iloc[30]
    assert result.formed.iloc[36]
    assert not result.candidate_edge.iloc[36]
    assert not result.early.iloc[30:38].any()
    assert result.early.iloc[40]


def test_rejected_edge_after_prior_parent_does_not_push_its_cooldown_forward():
    f = frame()
    launch(f, 30)
    set_width(f, 36, 42, 4.)
    launch(f, 42, 106.)
    launch(f, 48, 110.)
    result = fg.detect(f)
    assert result.early.iloc[30]
    assert result.formation_blocked.iloc[42]
    assert result.early.iloc[48]  # Accepted clock is still30, not rejected42.


def test_cooldown_rejection_remains_separate_from_formation_rejection():
    f = frame()
    launch(f, 30)
    launch(f, 35, 106.)
    launch(f, 42, 110.)
    result = fg.detect(f)
    assert result.cooldown_blocked.iloc[35]
    assert not result.formation_blocked.iloc[35]
    assert np.flatnonzero(result.early).tolist() == [30, 42]


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("ma", fg.MA_COLUMNS)
def test_each_missing_ma_invalidates_next_twelve_windows_without_skipna(ma, invalid):
    f = frame()
    f.loc[f.index[30], ma] = invalid
    result = fg.detect(f)
    assert result.formed.iloc[30]  # Current width is excluded by the registered formula.
    assert np.isnan(result.formation_width.iloc[30])
    assert not result.formation_valid.iloc[31:43].any()
    assert not result.formed.iloc[31:43].any()
    assert result.formation_valid.iloc[43] and result.formed.iloc[43]


def test_current_invalid_slow_ma_does_not_add_an_unregistered_current_gate():
    f = frame()
    launch(f, 30)
    f.loc[f.index[30], "s120"] = np.nan
    result = fg.detect(f)
    assert result.formed.iloc[30] and result.early.iloc[30]


@pytest.mark.parametrize("atr", [1e-12, 1e12, np.nan, np.inf, 0.])
def test_current_atr_does_not_normalize_or_invalidate_prior_absolute_width_gate(atr):
    f = frame()
    set_width(f, 18, 24, 4.)
    launch(f, 30)
    base = fg.detect(f)
    f.loc[f.index[30], "atr"] = atr
    result = fg.detect(f)
    columns = ["formation_width", "formation_old_median", "formation_recent_median",
        "formation_valid", "formed"]
    pd.testing.assert_frame_equal(result[columns], base[columns])
    assert result.early.iloc[30] == base.early.iloc[30]


def test_historical_atr_cannot_reverse_absolute_width_formation():
    f = frame()
    set_width(f, 18, 24, 4.)
    set_width(f, 24, 30, 2.)
    base = fg.detect(f)
    f.loc[f.index[18:24], "atr"] = 100.
    f.loc[f.index[24:30], "atr"] = .001
    result = fg.detect(f)
    assert result.formed.iloc[30]  # ATR-normalized width would reverse this result.
    pd.testing.assert_frame_equal(result.filter(regex="^formation_|^formed$"),
        base.filter(regex="^formation_|^formed$"))


def test_ready_is_still_required_even_if_formation_passes():
    f = frame()
    launch(f, 30)
    f.loc[f.index[30], "ready"] = False
    result = fg.detect(f)
    assert result.formed.iloc[30] and not result.early.iloc[30]


def test_gap_requires_twelve_contiguous_prior_widths_through_current_time():
    f = frame().drop(frame().index[31])
    for i in range(31, 46):
        launch(f, i, 110. + i)
    result = fg.detect(f)
    assert not result.formation_valid.iloc[31:43].any()
    assert result.formation_valid.iloc[43]
    assert result.early.iloc[43]
    assert f.index[43] - f.index[31] == pd.Timedelta(hours=12)


def test_gap_clears_accepted_parent_and_cannot_confirm_across_missing_hour():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    f = f.drop(f.index[31])
    launch(f, 31, 104.)
    result = fg.detect(f)
    assert result.early.iloc[30]
    assert not result.confirmed.iloc[31]
    assert np.isnan(result.parent_i.iloc[31])


def test_child_does_not_reapply_formation_or_reset_parent_time():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32):
        launch(f, i, 101.1, 100.)
    launch(f, 33, 104.)
    set_width(f, 30, 34, 10.)
    result = fg.detect(f)
    assert result.early.iloc[30] and result.formed.iloc[30]
    assert not result.formed.iloc[33]
    assert result.confirmed.iloc[33] and result.confirm_age.iloc[33] == 3
    assert result.parent_i.iloc[33] == 30
    assert result.frozen_parent_high.iloc[33] == 101.
    assert result.confirmed.sum() == 1
    assert result.early.sum() == 1


def test_child_quality_after_age_four_cannot_reopen_expired_parent():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32, 33):
        launch(f, i, 101.1, 100.)
    launch(f, 34, 104.)
    result = fg.detect(f)
    assert result.early.iloc[30]
    assert not result.confirmed.any()
    assert np.isnan(result.parent_i.iloc[34])


def test_full_condition_edge_uses_fast_ma_and_no_reference_occupancy():
    f = frame()
    for i in range(30, 34):
        launch(f, i, 104. + i)
    f.loc[f.index[30], "s20"] = 200.
    f["trend_side"] = 1
    result = fg.detect(f)
    assert not result.candidate_edge.iloc[30]
    assert result.early.iloc[31]  # Raw full condition turns true here, not at30.
    assert not result.early.iloc[32]
    assert not any("reference" in c or "suppression" in c for c in result.columns)


def test_prefix_future_mutation_and_input_immutability():
    f = frame()
    for i in (30, 42, 54, 75):
        launch(f, i, 104. + i / 10)
    set_width(f, 36, 42, 4.)
    f.loc[f.index[57], "s120"] = np.nan
    f = f.drop(f.index[66])
    before = f.copy(deep=True)
    full = fg.detect(f)
    for length in (12, 13, 29, 31, 35, 43, 55, 59, 68, 80, 98):
        pd.testing.assert_frame_equal(fg.detect(f.iloc[:length]), full.iloc[:length])
    mutated = f.copy()
    mutated.loc[mutated.index[80:], list(fg.MA_COLUMNS)] = 1000.
    mutated.loc[mutated.index[80:], ["high", "close", "volume"]] = [2000., 1999., 1e9]
    pd.testing.assert_frame_equal(fg.detect(mutated).iloc[:80], full.iloc[:80])
    pd.testing.assert_frame_equal(f, before)


def test_requires_all_original_six_ma_columns():
    with pytest.raises(ValueError, match="six MA columns: e120"):
        fg.detect(frame().drop(columns="e120"))
