"""Synthetic causal quota, density support and original V3 child contracts.

The explicit feature fixtures isolate a registered state machine. They are not
market samples or recalibrations of the original moving-average mathematics.
"""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_early_warning as v3
from yoyo.evaluation import spike_burst_v3_density_rearm_gate as gate


def frame(n=100):
    result = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n) + 400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., width=1., flips=.25, pastWidth=1., pastCrosses=3.,
        middle=100. + np.arange(n) * .01, md=1., sb=0., rv=1., expansion=1.),
        index=pd.date_range("2026-07-10T00:00Z", periods=n, freq="h"))
    result.attrs["minutes"] = 60
    return result


def launch(f, i, close=104., volume=600.):
    f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [100., close + .2, 99., close, volume]


def non_dense(f, start, end):
    f.loc[f.index[start:end], "pastWidth"] = 4.


def test_bootstrap_preserves_first_original_parent_without_initial_density_gate():
    f = frame()
    non_dense(f, 0, len(f))
    launch(f, 30)
    base, result = v3.detect(f), gate.detect(f)
    assert base.early.iloc[30] and result.early.iloc[30]
    assert result.bootstrap_accepted.iloc[30]
    assert result.accept_origin.iloc[30] == "bootstrap"
    assert result.structure_bootstrap.iloc[30]
    assert result.quota_armed_before.iloc[30] and not result.quota_armed.iloc[30]
    assert not result.density_false_seen.iloc[30]
    assert result.structure_owner_i.iloc[30] == 30
    assert result.structure_high.iloc[30] == result.frozen_parent_high.iloc[30] == 101.
    assert result.structure_low.iloc[30] == 99.


def test_weak_first_parent_can_lock_later_stronger_candidate_without_new_density():
    f = frame()
    launch(f, 30, 101.1, 100.)
    launch(f, 44, 110.)
    result = gate.detect(f)
    assert result.early.iloc[30] and not result.confirmed.any()
    assert result.candidate_edge.iloc[44] and result.structure_blocked.iloc[44]
    assert result.reject_reason.iloc[44] == "await_density_false"
    assert not result.early.iloc[44]
    assert result.last_accepted_i.iloc[44] == 30


def test_continuously_dense_summaries_never_rearm_even_after_many_twelve_bar_windows():
    f = frame()
    for i in (30, 44, 60, 80):
        launch(f, i, 104. + i)
    result = gate.detect(f)
    assert np.flatnonzero(result.early).tolist() == [30]
    assert not result.rearmed.any()
    assert result.structure_id.iloc[80] == 1
    assert result.structure_high.iloc[80] == 101.


def test_restore_on_continuing_true_when_first_twelve_observations_are_disjoint():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    result = gate.detect(f)
    assert result.density_is_dense.iloc[36:44].all()
    assert not result.rearmed.iloc[36:43].any()
    assert not result.support_disjoint.iloc[42]  # Window begins at owner30.
    assert result.rearmed.iloc[43]  # Window31..42; True run began seven bars ago.
    assert result.rearm_window_start_i.iloc[43] == 31
    assert result.rearm_window_end_i.iloc[43] == 42
    assert result.rearm_owner_i.iloc[43] == 30
    assert result.rearm_false_i.iloc[43] == 31
    assert result.rearm_wait_bars.iloc[43] == 13
    assert result.quota_armed.iloc[43:].all()
    assert result.rearmed.sum() == 1


def test_accepted_bar_false_does_not_count_as_post_owner_false():
    f = frame()
    non_dense(f, 30, 31)
    launch(f, 30)
    launch(f, 44, 110.)
    result = gate.detect(f)
    assert result.density_known_false.iloc[30]
    assert not result.density_false_seen.iloc[30:].any()
    assert not result.rearmed.any() and not result.early.iloc[44]


def test_existing_density_threshold_equalities_are_preserved():
    f = frame()
    f[["pastWidth", "pastCrosses"]] = [3., 2.]
    launch(f, 30)
    non_dense(f, 31, 32)
    launch(f, 43, 110.)
    result = gate.detect(f)
    assert result.density_is_dense.iloc[30]
    assert result.rearmed.iloc[43] and result.early.iloc[43]


@pytest.mark.parametrize("level", [50., 200.])
def test_new_density_below_or_above_old_box_rearms_without_return_to_old_boundary(level):
    f = frame()
    launch(f, 30)
    f.loc[f.index[31:], ["open", "high", "low", "close", "s20", "e20"]] = [
        level, level + 1., level - 1., level, level - 1., level - .5]
    non_dense(f, 31, 49)
    f.loc[f.index[52], ["high", "close", "volume"]] = [level + 4.2, level + 4., 600.]
    result = gate.detect(f)
    assert result.rearmed.iloc[49]
    assert result.early.iloc[52]
    assert result.rearm_window_low.iloc[52] == level - 1.
    assert result.rearm_window_high.iloc[52] == level + 1.
    assert result.structure_owner_i.iloc[52] == 52
    assert result.structure_high.iloc[52] == level + 1.
    assert result.structure_low.iloc[52] == level - 1.


def test_owner_box_uses_acceptance_prior_range_not_stale_rearm_range():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    f.loc[f.index[43], "high"] = 200.
    launch(f, 44, 210.)
    result = gate.detect(f)
    assert result.rearmed.iloc[43] and result.rearm_window_high.iloc[44] == 101.
    assert result.early.iloc[44]
    assert result.structure_high.iloc[44] == result.frozen_parent_high.iloc[44] == 200.
    assert result.rearm_owner_i.iloc[44] == 30
    assert result.rearm_wait_bars.iloc[44] == 13
    assert result.structure_age_bars.iloc[44] == 0


def test_new_quota_never_queues_or_fabricates_a_consumed_raw_edge():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    for i in range(40, 49):
        launch(f, i, 200. + i)
    launch(f, 50, 400.)
    result = gate.detect(f)
    assert result.candidate_edge.iloc[40] and not result.early.iloc[40]
    assert result.rearmed.iloc[43] and result.quota_armed.iloc[43]
    assert not result.candidate_edge.iloc[41:49].any()
    assert not result.early.iloc[40:49].any()
    assert result.early.iloc[50]


def test_cooldown_block_does_not_spend_quota_or_change_accepted_clock():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    launch(f, 35, 106.)
    launch(f, 43, 110.)
    result = gate.detect(f)
    assert result.cooldown_blocked.iloc[35]
    assert not result.structure_blocked.iloc[35]
    assert result.reject_reason.iloc[35] == "cooldown"
    assert result.last_accepted_i.iloc[35] == 30
    assert result.early.iloc[43]  # Only8 bars after the rejected raw edge.


def test_structure_rejection_does_not_push_cooldown_and_is_not_a_parent():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    launch(f, 42, 108.)
    launch(f, 44, 112.)
    result = gate.detect(f)
    assert result.structure_blocked.iloc[42] and not result.cooldown_blocked.iloc[42]
    assert result.reject_reason.iloc[42] == "await_disjoint_support"
    assert np.isnan(result.parent_i.iloc[42])
    assert result.last_accepted_i.iloc[42] == 30
    assert result.rearmed.iloc[43] and result.early.iloc[44]


def test_rebuilt_accepted_cooldown_can_create_a_g_parent_absent_from_cached_v3():
    f = frame()
    launch(f, 30)
    launch(f, 44, 110.)
    non_dense(f, 45, 46)
    launch(f, 46, 114.)
    base, result = v3.detect(f), gate.detect(f)
    assert base.early.iloc[44] and result.structure_blocked.iloc[44]
    assert result.last_accepted_i.iloc[44] == 30
    assert result.rearmed.iloc[46] and result.early.iloc[46]
    assert not base.early.iloc[46] and base.cooldown_blocked.iloc[46]
    assert result.parent_i.iloc[46] == 46


def test_price_break_below_old_structure_does_not_itself_restore_quota():
    f = frame()
    launch(f, 30)
    f.loc[f.index[31:], ["open", "high", "low", "close", "s20", "e20"]] = [50., 51., 49., 50., 49., 49.5]
    f.loc[f.index[44], ["high", "close", "volume"]] = [54.2, 54., 600.]
    result = gate.detect(f)
    assert result.structure_low.iloc[44] == 99. and f.close.iloc[44] < 99.
    assert result.candidate_edge.iloc[44] and result.structure_blocked.iloc[44]
    assert not result.rearmed.any()


def test_same_bar_restoration_and_acceptance_preserve_both_owner_identities_and_origin():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    launch(f, 43, 110.)
    result = gate.detect(f)
    r = result.iloc[43]
    assert r.rearmed and r.early and not r.quota_armed_before
    assert r.quota_armed_for_edge and not r.quota_armed
    assert r.structure_owner_before_i == r.rearm_owner_i == 30
    assert r.structure_owner_i == r.parent_i == 43 and r.structure_id == 2
    assert r.accept_origin == "density_rearm" and not r.bootstrap_accepted
    assert r.rearm_false_i == 31 and r.rearm_wait_bars == 13
    assert not r.density_false_seen and np.isnan(r.first_false_i)


def test_armed_quota_persists_without_an_added_acceptance_density_gate():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    f.loc[f.index[44], "pastWidth"] = np.nan
    launch(f, 44, 110.)
    result = gate.detect(f)
    assert result.rearmed.iloc[43]
    assert not result.density_valid.iloc[44]
    assert result.early.iloc[44]  # Already restored quota; original raw rule passes.


@pytest.mark.parametrize("column", ["pastWidth", "pastCrosses"])
@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_unknown_summary_is_not_observation_of_leaving_density(column, invalid):
    f = frame()
    launch(f, 30)
    f.loc[f.index[32], column] = invalid
    launch(f, 44, 110.)
    result = gate.detect(f)
    assert not result.density_valid.iloc[32]
    assert not result.density_known_false.iloc[32]
    assert not result.density_false_seen.iloc[30:].any()
    assert not result.rearmed.any() and not result.early.iloc[44]


@pytest.mark.parametrize("column", ["width", "flips"])
@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_invalid_original_observation_invalidates_exactly_following_twelve_supports(column, invalid):
    f = frame()
    launch(f, 30)
    f.loc[f.index[31], column] = invalid
    non_dense(f, 32, 33)
    launch(f, 45, 110.)
    result = gate.detect(f)
    assert result.density_valid.iloc[31]  # Current raw observation is excluded.
    assert not result.density_valid.iloc[32:44].any()
    assert result.density_valid.iloc[44]
    assert not result.density_false_seen.iloc[30:].any()
    assert not result.rearmed.any() and not result.early.iloc[45]


def test_unknown_current_density_while_locked_has_explicit_rejection_reason():
    f = frame()
    launch(f, 30)
    launch(f, 44, 110.)
    f.loc[f.index[44], "pastCrosses"] = np.nan
    result = gate.detect(f)
    assert result.structure_blocked.iloc[44]
    assert result.reject_reason.iloc[44] == "density_unknown"


def test_unready_false_is_not_a_density_departure():
    f = frame()
    launch(f, 30)
    f.loc[f.index[32], "ready"] = False
    launch(f, 44, 110.)
    result = gate.detect(f)
    assert not result.density_valid.iloc[32] and not result.density_known_false.iloc[32]
    assert not result.rearmed.any() and not result.early.iloc[44]


def test_known_false_without_return_stays_locked_and_names_that_reason():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, len(f))
    launch(f, 44, 110.)
    result = gate.detect(f)
    assert result.density_false_seen.iloc[44]
    assert result.structure_blocked.iloc[44]
    assert result.reject_reason.iloc[44] == "await_density_reentry"


def test_gap_resets_bootstrap_and_never_carries_old_parent_or_density_support():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    f = f.drop(f.index[31])
    launch(f, 43, 108.)
    result = gate.detect(f)
    assert result.gap_reset.iloc[31] and result.bootstrap_quota.iloc[31]
    assert np.isnan(result.parent_i.iloc[31]) and np.isnan(result.structure_owner_i.iloc[31])
    assert not result.density_support_complete.iloc[31:43].any()
    assert result.density_valid.iloc[43]
    assert result.early.iloc[43] and result.bootstrap_accepted.iloc[43]
    assert result.structure_id.iloc[43] == 2
    assert not result.confirmed.iloc[31:43].any()


@pytest.mark.parametrize("age", [0, 1, 2, 3])
def test_child_stays_at_original_age_with_quota_consumed_and_without_regating(age):
    f = frame()
    if age == 0:
        launch(f, 30)
    else:
        launch(f, 30, 101.1, 100.)
        f.loc[f.index[30], "high"] = 110.
        for i in range(31, 30 + age):
            launch(f, i, 101.1, 100.)
        non_dense(f, 31, 35)
        launch(f, 30 + age, 104.)
    base, result = v3.detect(f), gate.detect(f)
    i = 30 + age
    assert base.confirmed.iloc[i] and result.confirmed.iloc[i]
    assert result.confirm_age.iloc[i] == age and result.parent_i.iloc[i] == 30
    assert result.frozen_parent_high.iloc[i] == 101.
    assert not result.quota_armed.iloc[i]
    assert np.flatnonzero(result.confirmed).tolist() == [i]
    assert np.flatnonzero(result.early).tolist() == [30]


def test_late_quality_cannot_revive_expired_parent_or_invent_a_child():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32, 33):
        launch(f, i, 101.1, 100.)
    launch(f, 34, 104.)
    result = gate.detect(f)
    assert result.early.iloc[30]
    assert not result.confirmed.any()
    assert np.isnan(result.parent_i.iloc[34])
    assert result.structure_owner_i.iloc[34] == 30  # Structural state outlives only the child window.


def test_market_volume_and_current_atr_do_not_add_density_validity_filters():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    base = gate.detect(f)
    f.loc[f.index[31:50], ["volume", "atr"]] = [np.nan, np.nan]
    result = gate.detect(f)
    columns = ["density_valid", "density_is_dense", "density_known_false", "rearmed", "rearm_i"]
    pd.testing.assert_frame_equal(result[columns], base[columns])


def test_original_fields_are_unchanged_and_input_is_not_mutated():
    f = frame()
    launch(f, 30)
    non_dense(f, 31, 36)
    original = f.copy(deep=True)
    expected = v3.early_fields(f)
    result = gate.detect(f)
    pd.testing.assert_frame_equal(result[expected.columns], expected, check_flags=False)
    pd.testing.assert_frame_equal(f, original)
    assert not any("reference" in c or "risk" in c or "future" in c for c in result.columns)


def test_prefixes_and_future_edits_cannot_change_historical_quota_or_parent_events():
    f = frame()
    for i, price in ((30, 104.), (43, 110.), (70, 120.), (95, 130.)):
        launch(f, i, price)
    non_dense(f, 31, 36)
    non_dense(f, 44, 51)
    f.loc[f.index[58], "width"] = np.nan
    f = f.drop(f.index[80])
    result = gate.detect(f)
    for length in (1, 12, 13, 30, 31, 32, 42, 43, 44, 57, 60, 71, 82, 99):
        pd.testing.assert_frame_equal(gate.detect(f.iloc[:length]), result.iloc[:length])
    later = f.copy(deep=True)
    later.loc[later.index[85:], ["close", "high", "volume", "pastWidth", "pastCrosses"]] = [999., 1000., 1e8, 100., 0.]
    pd.testing.assert_frame_equal(gate.detect(later).iloc[:85], result.iloc[:85])


@pytest.mark.parametrize("column", ["width", "flips"])
def test_original_density_observation_columns_are_required(column):
    with pytest.raises(ValueError, match="Density rearm requires original feature columns: " + column):
        gate.detect(frame().drop(columns=column))
