"""Synthetic structural-box causality, lifecycle and unchanged V3 child tests."""
import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from yoyo.evaluation import spike_burst_early_warning as v3
from yoyo.evaluation import spike_burst_v3_structural as box


def frame(n=100):
    f = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n)+400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., pastWidth=1., pastCrosses=3.,
        middle=100.+np.arange(n)*.01, md=2., sb=1., rv=1., expansion=1.),
        index=pd.date_range('2020-01-01', periods=n, freq='h', tz='UTC'))
    f.attrs['minutes'] = 60
    return f


def quiet(f, start=10, end=30):
    f.loc[f.index[start:end], ['md', 'sb']] = [.05, .04]


def launch(f, i=30, price=104., volume=600.):
    f.loc[f.index[i], ['open', 'high', 'low', 'close', 'volume', 'md', 'sb']] = [100., price+.2, 99., price, volume, .3, .1]


def setup():
    f = frame()
    quiet(f)
    launch(f)
    return f


def test_complete_box_fires_once_actual_time_and_parent_quality():
    f = setup()
    s = box.detect(f)
    assert np.flatnonzero(s.early).tolist() == [30]
    assert np.flatnonzero(s.confirmed).tolist() == [30]
    assert s.setup_bars.iloc[30] == 20
    assert s.setup_start_i.iloc[30] == 10 and s.setup_last_near_i.iloc[30] == 29
    assert s.boxHigh.iloc[30] == s.frozen_parent_high.iloc[30] == 101
    assert s.setup_eligible.iloc[30] and s.setup_consumed.iloc[30]
    assert not s.setup_eligible.iloc[31] and s.confirm_age.iloc[30] == 0


def test_no_setup_means_ordinary_breakout_not_a_structural_signal():
    f = frame()
    launch(f)
    assert v3.detect(f).early.iloc[30]
    assert not box.detect(f).early.any()


@pytest.mark.parametrize('count,expected', [(11, False), (12, True)])
def test_exact_minimum_contiguous_near_bars(count, expected):
    f = frame()
    quiet(f, 30-count, 30)
    launch(f)
    assert bool(box.detect(f).early.iloc[30]) is expected


def test_both_md_and_signal_must_be_near_and_invalid_number_cannot_qualify():
    for col in ('md', 'sb'):
        f = setup()
        f.loc[f.index[20], col] = np.nan
        assert not box.detect(f).early.iloc[30]
        f.loc[f.index[20], col] = .101
        assert not box.detect(f).early.iloc[30]


def test_full_older_setup_high_not_just_rolling_twelve_high():
    f = setup()
    f.loc[f.index[10], 'high'] = 107.
    launch(f, 35, price=108.)
    s = box.detect(f)
    assert v3.detect(f).early.iloc[30]
    assert not s.early.iloc[30]
    assert s.early.iloc[35] and s.boxHigh.iloc[35] == 107.
    assert s.release_age.iloc[35] == 5


def test_twelfth_bar_freezes_band_but_later_near_bars_still_expand_box():
    f = setup()
    f.loc[f.index[21], 'atr'] = 100.
    f.loc[f.index[22], 'md'] = .2
    s = box.detect(f)
    assert s.quiet_count.iloc[21] == 12
    assert not s.near_zero.iloc[22] and s.near_band.iloc[22] == pytest.approx(.1)
    f = setup()
    f.loc[f.index[28], 'high'] = 107.
    launch(f, price=108.)
    s = box.detect(f)
    assert s.boxHigh.iloc[21] == 101 and s.boxHigh.iloc[29] == 107
    assert s.boxHigh.iloc[30] == 107 and s.early.iloc[30]


def test_release_bar_high_is_not_backfilled_into_frozen_box():
    f = setup()
    f.loc[f.index[30], 'high'] = 1000.
    s = box.detect(f)
    assert s.early.iloc[30] and s.frozen_parent_high.iloc[30] == 101.


@pytest.mark.parametrize('age,expected', [(0, True), (5, True), (6, False)])
def test_release_opportunity_age_boundaries(age, expected):
    f = frame()
    quiet(f)
    launch(f, 30+age)
    s = box.detect(f)
    assert bool(s.early.iloc[30+age]) is expected
    assert bool(s.setup_expired.iloc[36])
    assert not s.setup_eligible.iloc[36:].any()


def test_same_trend_new_rolling_high_does_not_rearm_but_new_full_setup_does():
    f = setup()
    launch(f, 42, price=106.)
    s = box.detect(f)
    assert v3.detect(f).early.iloc[42]
    assert not s.early.iloc[42]
    quiet(f, 45, 57)
    launch(f, 57, price=108.)
    s = box.detect(f)
    assert s.early.iloc[57] and s.setup_id.iloc[57] != s.setup_id.iloc[30]
    assert s.setup_bars.iloc[57] == 12


def test_short_return_to_near_invalidates_old_unconsumed_box():
    f = frame()
    quiet(f)
    quiet(f, 32, 33)
    launch(f, 34)
    s = box.detect(f)
    assert s.setup_eligible.iloc[30]
    assert not s.early.iloc[34] and pd.isna(s.setup_id.iloc[34])


def test_child_age_three_frozen_box_and_no_backdating():
    f = setup()
    launch(f, 30, price=101.1, volume=100.)
    for i in (31, 32):
        launch(f, i, price=101.1, volume=100.)
    launch(f, 33, price=104., volume=600.)
    f.loc[f.index[30], 'high'] = 110.
    s = box.detect(f)
    assert s.early.iloc[30] and not s.confirmed.iloc[30:33].any()
    assert s.confirmed.iloc[33] and s.confirm_age.iloc[33] == 3
    assert s.parent_i.iloc[33] == 30 and s.frozen_parent_high.iloc[33] == 101.
    assert not s.confirmed.iloc[34:].any()


def test_child_age_four_expires_even_if_quality_arrives_late():
    f = setup()
    for i in (30, 31, 32, 33):
        launch(f, i, price=101.1, volume=100.)
    launch(f, 34)
    s = box.detect(f)
    assert s.early.iloc[30] and not s.confirmed.any()


def test_not_ready_clears_structure_but_preserves_existing_parent_age():
    f = setup()
    launch(f, 30, price=101.1, volume=100.)
    f.loc[f.index[31], 'ready'] = False
    launch(f, 32, price=104., volume=600.)
    s = box.detect(f)
    assert pd.isna(s.setup_id.iloc[31]) and not s.early.iloc[32]
    assert s.confirmed.iloc[32] and s.confirm_age.iloc[32] == 2


def test_gap_resets_box_and_child_and_cannot_bridge_near_sequence():
    f = setup().drop(frame().index[25])
    s = box.detect(f)
    assert s.gap_reset.iloc[25] and not s.early.any()
    f = setup()
    launch(f, 30, price=101.1, volume=100.)
    f = f.drop(f.index[31])
    launch(f, 31, price=104., volume=600.)
    s = box.detect(f)
    assert s.gap_reset.iloc[31] and not s.confirmed.any()


@pytest.mark.parametrize('end', [21, 22, 30, 31, 34, 37, 60, 99])
def test_prefix_invariance_and_input_unchanged(end):
    f = setup()
    quiet(f, 45, 57)
    launch(f, 57, price=108.)
    saved = f.copy(deep=True)
    whole = box.detect(f)
    assert_frame_equal(box.detect(f.iloc[:end]), whole.iloc[:end])
    assert_frame_equal(f, saved)


def test_original_v3_columns_are_available_and_diagnostic_raw_condition_retained():
    f = setup()
    original = v3.detect(f)
    changed = box.detect(f)
    assert set(original.columns).issubset(changed.columns)
    assert changed.v3_early_condition.equals(original.early_condition.rename('v3_early_condition'))


def test_missing_release_observation_cannot_leave_a_qualifying_old_box():
    f = setup()
    f.loc[f.index[30], 'md'] = np.nan
    launch(f, 31, price=104.)
    s = box.detect(f)
    assert not s.early.any() and pd.isna(s.setup_id.iloc[30])
