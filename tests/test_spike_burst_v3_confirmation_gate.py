"""Synthetic causal contracts for confirmation-earned V3 suppression.

No market files, outcome labels, fitted thresholds or historical scoring.
"""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_early_warning as ew
from yoyo.evaluation import spike_burst_v3_confirmation_gate as cg
from yoyo.evaluation import spike_burst_v3_reference_gate as rg


def frame(n=100):
    result = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n) + 400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., pastWidth=1., pastCrosses=3.,
        middle=100. + np.arange(n) * .01, md=1., sb=0., rv=1., expansion=1.),
        index=pd.date_range("2026-07-10T00:00Z", periods=n, freq="h"))
    result.attrs["minutes"] = 60
    return result


def launch(f, i, c=104., volume=600.):
    f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [100., c + .2, 99., c, volume]


def delayed_child_frame():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32):
        launch(f, i, 101.1, 100.)
    launch(f, 33, 104., 600.)
    return f


def test_weak_parent_expires_without_suppressing_later_strong_first_alert():
    f = frame()
    launch(f, 30, 101.1, 100.)
    launch(f, 42, 106.)
    launch(f, 54, 108.)
    base, old, result = ew.detect(f), rg.detect(f, .01), cg.detect(f, .01)
    assert np.flatnonzero(base.early).tolist() == [30, 42, 54]
    # A misses the strong bar42 while the weak reference still owns the trend;
    # it then arms at42, stops at43 and only accepts the later bar54 alert.
    assert np.flatnonzero(old.early).tolist() == [30, 54]
    assert np.flatnonzero(result.early).tolist() == [30, 42]
    assert not result.confirmed.iloc[30]
    assert result.reference_pending.iloc[30:34].all()
    assert not result.suppression_active.iloc[:42].any()
    assert result.reference_expired_unconfirmed.iloc[34]
    assert not result.reference_active.iloc[34]
    assert not result.reference_exit.iloc[34]
    assert np.isnan(result.reference_exit_price.iloc[34])
    assert result.confirmed.iloc[42] and result.suppression_acquired.iloc[42]
    assert result.holding_blocked.iloc[54]


def test_confirmation_acquires_rights_without_repricing_or_restarting_reference():
    f = delayed_child_frame()
    result = cg.detect(f, .01)
    assert result.early.iloc[30] and not result.confirmed.iloc[30]
    assert not result.suppression_active.iloc[30:33].any()
    assert result.confirmed.iloc[33] and result.confirm_age.iloc[33] == 3
    assert result.suppression_acquired.iloc[33]
    assert result.suppression_active.iloc[33:].all()
    assert result.reference_started.sum() == 1
    assert result.reference_entry.iloc[33] == result.reference_entry.iloc[30] == 101.1
    assert result.frozen_parent_high.iloc[33] == result.frozen_parent_high.iloc[30] == 101.
    for name in ("reference_initial_stop", "reference_risk"):
        assert result[name].iloc[33] == result[name].iloc[30]
    assert result.reference_active_at_confirmation.iloc[33]
    assert result.confirmed.sum() == result.suppression_acquired.sum() == 1


@pytest.mark.parametrize("age", [1, 2])
def test_intermediate_child_ages_acquire_once_at_actual_confirmation(age):
    f = delayed_child_frame()
    launch(f, 30 + age, 104., 600.)
    result = cg.detect(f, .01)
    assert not result.suppression_active.iloc[30:30 + age].any()
    assert result.confirm_age.iloc[30 + age] == age
    assert result.suppression_acquired.iloc[30 + age]
    assert result.suppression_acquired.sum() == 1


def test_same_bar_early_confirmation_has_one_reference_and_no_parent_bar_excursion():
    f = frame()
    launch(f, 30)
    f.loc[f.index[30], "high"] = 200.
    result = cg.detect(f, .01)
    assert result.early.iloc[30] and result.confirmed.iloc[30]
    assert result.confirm_age.iloc[30] == 0
    assert result.reference_started.sum() == 1
    assert result.suppression_acquired.iloc[30]
    assert not result.suppression_before.iloc[30]
    assert result.reference_current_r.iloc[30] == result.reference_peak_r.iloc[30] == 0.
    assert np.isnan(result.active_protection.iloc[30])


def test_confirmation_on_age_four_is_too_late_and_cannot_resurrect_expired_parent():
    f = delayed_child_frame()
    launch(f, 33, 101.1, 100.)
    launch(f, 34, 104., 600.)
    result = cg.detect(f, .01)
    assert result.confirmation_window_expired.iloc[34]
    assert result.reference_expired_unconfirmed.iloc[34]
    assert np.isnan(result.parent_i.iloc[34])
    assert not result.confirmed.any()
    assert not result.suppression_active.any()


def test_child_after_stop_is_historical_upgrade_not_suppression_or_reentry():
    f = delayed_child_frame()
    f.loc[f.index[31], "low"] = 90.
    result = cg.detect(f, .01)
    assert result.reference_exit.iloc[31]
    assert not result.suppression_exit.iloc[31]
    assert result.confirmed.iloc[33] and result.confirm_age.iloc[33] == 3
    assert not result.reference_active_at_confirmation.iloc[33]
    assert not result.suppression_acquired.any()
    assert not result.suppression_active.any()
    assert result.reference_started.sum() == 1
    assert result.reference_entry.iloc[33] == 101.1


def test_stop_and_child_same_bar_checks_old_protection_before_upgrade():
    f = delayed_child_frame()
    f.loc[f.index[33], "low"] = 90.
    result = cg.detect(f, .01)
    assert result.reference_exit.iloc[33] and result.confirmed.iloc[33]
    assert not result.reference_active_at_confirmation.iloc[33]
    assert not result.suppression_acquired.any()
    assert result.reference_peak_r.iloc[33] == result.reference_peak_r.iloc[32]


def test_original_cooldown_still_blocks_strong_alert_before_twelve_bars():
    f = frame()
    launch(f, 30, 101.1, 100.)
    launch(f, 36, 106.)
    launch(f, 42, 108.)
    result = cg.detect(f, .01)
    assert result.reference_expired_unconfirmed.iloc[34]
    assert result.cooldown_blocked.iloc[36] and not result.early.iloc[36]
    assert not result.holding_blocked.iloc[36]
    assert result.early.iloc[42]
    assert np.flatnonzero(result.early).tolist() == [30, 42]


def test_confirmed_stop_bar_cannot_restart_and_blocked_edge_does_not_extend_cooldown():
    f = frame()
    launch(f, 30)
    launch(f, 42, 120.)
    f.loc[f.index[42], "low"] = 90.
    launch(f, 43, 121.)
    launch(f, 45, 125.)
    result = cg.detect(f, .01)
    assert result.suppression_exit.iloc[42] and result.holding_blocked.iloc[42]
    assert not result.early.iloc[42]
    assert not result.early.iloc[43]  # A blocked full-condition edge is consumed.
    assert result.early.iloc[45]  # Accepted-only cooldown remains anchored at 30.
    assert result.reference_peak_r.iloc[42] == result.reference_peak_r.iloc[41]
    assert result.reference_exit_price.iloc[42] == result.active_protection.iloc[42]


def test_invalid_reference_does_not_remove_early_child_or_cooldown():
    f = frame()
    launch(f, 30)
    f.loc[f.index[30], "atr"] = 100.
    launch(f, 35, 106.)
    launch(f, 45, 108.)
    result = cg.detect(f, .01)
    assert result.early.iloc[30] and not result.reference_started.iloc[30]
    assert result.confirmed.iloc[30]
    assert not result.suppression_acquired.iloc[30]
    assert np.isnan(result.reference_entry.iloc[30])
    assert result.cooldown_blocked.iloc[35] and not result.early.iloc[35]
    assert result.early.iloc[45]


@pytest.mark.parametrize("confirmed", [False, True])
def test_gap_censors_reference_and_resets_pending_and_suppression(confirmed):
    f = frame()
    launch(f, 30, 104. if confirmed else 101.1, 600. if confirmed else 100.)
    f = f.drop(f.index[32])
    launch(f, 55, 106.)
    result = cg.detect(f, .01)
    assert result.reference_gap_censored.iloc[32]
    assert bool(result.suppression_gap_censored.iloc[32]) == confirmed
    assert not result.reference_active.iloc[32]
    assert not result.reference_exit.iloc[32]
    assert np.isnan(result.parent_i.iloc[32])
    assert np.isnan(result.reference_entry.iloc[32])
    assert result.early.iloc[55]


def test_confirmed_parent_window_expiry_does_not_cancel_earned_suppression():
    f = frame()
    launch(f, 30)
    result = cg.detect(f, .01)
    assert result.confirmation_window_expired.iloc[34]
    assert not result.reference_expired_unconfirmed.iloc[34]
    assert result.reference_active.iloc[34:].all()
    assert result.suppression_active.iloc[34:].all()
    assert not result.reference_exit.any()  # No invented period-end liquidation.


def test_if_every_parent_confirms_immediately_same_events_and_reference_as_a():
    f = frame()
    for i in (30, 42, 54, 70):
        launch(f, i, 104. + i / 10)
    old, result = rg.detect(f, .01), cg.detect(f, .01)
    common = ["early", "confirmed", "parent_i", "frozen_parent_high", "confirm_age",
        "candidate_edge", "cooldown_blocked", "holding_blocked", "reference_active",
        "reference_started", "reference_entry", "reference_initial_stop", "reference_risk",
        "active_protection", "reference_protection", "reference_peak_r", "reference_current_r",
        "reference_exit", "reference_exit_price", "reference_armed"]
    pd.testing.assert_frame_equal(result[common], old[common])


def test_if_no_child_confirms_early_events_match_original_v3():
    f = frame()
    f["pastCrosses"] = 0.
    for i in (30, 42, 54, 70):
        launch(f, i, 104. + i / 10)
    base, result = ew.detect(f), cg.detect(f, .01)
    assert not result.confirmed.any()
    assert not result.suppression_active.any()
    pd.testing.assert_frame_equal(result[["early", "confirmed", "parent_i", "confirm_age"]],
        base[["early", "confirmed", "parent_i", "confirm_age"]])


def test_new_parent_replaces_only_expired_provisional_reference_at_new_entry():
    f = frame()
    launch(f, 30, 101.1, 100.)
    launch(f, 42, 106.)
    result = cg.detect(f, .01)
    assert result.reference_entry.iloc[30] == 101.1
    assert result.reference_entry.iloc[42] == 106.
    assert result.reference_owner_i.iloc[42] == result.parent_i.iloc[42] == 42
    assert result.reference_started.sum() == 2
    assert result.reference_risk.iloc[42] == (
        result.reference_entry.iloc[42] - result.reference_initial_stop.iloc[42])


def test_prefix_and_future_mutation_leave_every_past_column_unchanged():
    f = delayed_child_frame()
    launch(f, 42, 120.)
    f.loc[f.index[42], "low"] = 90.
    launch(f, 56, 130., 100.)
    launch(f, 70, 135.)
    result = cg.detect(f, .01)
    for n in (29, 31, 33, 34, 35, 43, 57, 61, 71, 99):
        pd.testing.assert_frame_equal(cg.detect(f.iloc[:n], .01), result.iloc[:n])
    mutated = f.copy()
    mutated.iloc[75:, mutated.columns.get_loc("low")] = 1.
    mutated.iloc[75:, mutated.columns.get_loc("close")] = 1000.
    mutated.iloc[75:, mutated.columns.get_loc("volume")] = 1e9
    pd.testing.assert_frame_equal(cg.detect(mutated, .01).iloc[:75], result.iloc[:75])


@pytest.mark.parametrize("tick", [0., -1., np.nan, np.inf])
def test_rejects_invalid_tick(tick):
    with pytest.raises(ValueError, match="Positive finite exchange tick"):
        cg.detect(frame(), tick)
