"""Focused math and receipt-contract tests for the V8 aggregate report."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v8_report import (
    EXPECTED_ADMISSIONS,
    _feature_analysis,
    _matched_controls,
    _retention_summary,
    _trade_summary,
    cluster_effect,
)


def test_asset_cluster_effect_is_not_changed_by_cross_venue_duplication():
    values = pd.DataFrame({"asset": ["A", "B", "C"], "delta": [.06, .02, -.01]})
    first = cluster_effect(values, draws=200)
    duplicated = cluster_effect(pd.concat([values, values], ignore_index=True), draws=200)
    assert first["assets"] == 3
    assert first["delta"] == pytest.approx(duplicated["delta"])


def test_trade_summary_excludes_censored_from_pf_and_realized_tail():
    trades = pd.DataFrame({"arm": ["v7"] * 3, "period": ["validation"] * 3, "timeframe_min": [60] * 3,
                           "censored": [False, False, True], "net_return": [.1, -.05, .8],
                           "net_r": [11., -1., 40.], "mfe_r": [12., 11., 50.]})
    result = _trade_summary(trades, ["arm", "period", "timeframe_min"]).iloc[0]
    assert result.trade_rows == 3 and result.closed == 2 and result.censored == 1
    assert result.realized_10r == 1 and result.mfe_10r == 2
    assert result.pf == pytest.approx(2.0)


def test_retention_distinguishes_realized_and_mfe_tails():
    retention = pd.DataFrame({"period": ["development"] * 3, "timeframe_min": [30] * 3,
                              "net_r": [11., 1., 2.], "mfe_r": [11., 12., 1.],
                              "net_return": [.1, -.1, -.1], "exact_retained": [True, False, False]})
    row = _retention_summary(retention).iloc[0]
    assert row.baseline_realized_10r == 1 and row.retained_realized_10r == 1
    assert row.baseline_mfe_10r == 2 and row.retained_mfe_10r == 1
    assert row.removed_losers == 2


def test_feature_analysis_uses_explicit_boolean_gate_and_keeps_periods_separate():
    f = pd.DataFrame({
        "period": ["development", "validation"], "timeframe_min": [30, 30],
        "gate_not_overheated3": [True, False], "executed": [True, True], "closed": [True, True],
        "realized_10r": [True, False], "mfe_10r": [True, False], "net_positive": [True, False],
        "failure_reason": ["realized_ge10r", "initial_stop_later"],
        "rope_distance_atr": [1., 4.], "efficiency3": [.4, .5], "current_volume_ratio": [1., 2.],
        "current_tr_expansion": [1., 2.], "bb_width_ratio_p10": [1., 2.], "cost_share_of_close_r": [.1, .2],
    })
    feature, failures, development = _feature_analysis(f)
    assert feature.admissions.sum() == 2
    assert failures.filtered_admissions.sum() == 1
    assert development.period.eq("development").all()


def test_filtered_nonexecuted_admission_is_not_counted_as_a_removed_losing_trade():
    f = pd.DataFrame({
        "period": ["validation", "validation"], "timeframe_min": [60, 60],
        "gate_not_overheated3": [False, False], "executed": [False, True], "closed": [False, True],
        "realized_10r": [False, False], "mfe_10r": [False, False], "net_positive": [False, False],
        "failure_reason": ["not_executed_occupied", "initial_stop_later"],
        "rope_distance_atr": [4., 5.], "efficiency3": [.4, .5], "current_volume_ratio": [1., 2.],
        "current_tr_expansion": [1., 2.], "bb_width_ratio_p10": [1., 2.], "cost_share_of_close_r": [.1, .2],
    })
    _, failures, _ = _feature_analysis(f)
    occupied = failures.loc[failures.failure_reason.eq("not_executed_occupied")].iloc[0]
    stopped = failures.loc[failures.failure_reason.eq("initial_stop_later")].iloc[0]
    assert occupied.filtered_admissions == 1 and occupied.removed_nonpositive == 0
    assert stopped.removed_nonpositive == 1


def test_matched_controls_remain_separate_by_timeframe():
    controls = pd.DataFrame({"arm": ["v8", "v8"], "period": ["validation", "validation"],
                             "timeframe_min": [30, 60], "asset": ["A", "B"], "matched": [True, True],
                             "net_return_difference": [.01, .02]})
    summary = _matched_controls(controls)
    assert set(summary.timeframe_min) == {30, 60}


def test_admission_contract_is_documented_as_distinct_from_actual_trades():
    assert EXPECTED_ADMISSIONS == 132593
