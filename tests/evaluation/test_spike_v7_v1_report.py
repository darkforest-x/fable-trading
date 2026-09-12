"""Accounting and identity checks for the V7 comparison postprocessor."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v7_v1_report import bools, event_metrics, exact_retention, fixed_sample, control_metrics


def test_censored_rows_never_become_wins_and_no_fake_portfolio():
    trades = pd.DataFrame({"censored": ["False", "False", "True"],
                           "net_return": [.1, -.05, 99], "net_r": [2, -1, 900]})
    result = event_metrics(trades)
    assert result["closed"] == 2
    assert result["censored"] == 1
    assert result["win_rate"] == .5
    assert result["event_pf"] == 2
    assert result["sum_net_r"] == 1
    assert not any("drawdown" in key or "equity" in key for key in result)


def test_control_sampling_does_not_depend_on_outcomes():
    trades = pd.DataFrame({"variant": ["v7_bb_long"] * 40, "side": [1] * 40,
                           "censored": False, "signal_bar_open": pd.date_range("2025-01-01", periods=40, freq="h"),
                           "net_r": range(40)})
    chosen = fixed_sample(trades, 16).signal_bar_open.tolist()
    trades["net_r"] = trades.net_r * -100
    assert fixed_sample(trades.sample(frac=1, random_state=7), 16).signal_bar_open.tolist() == chosen
    assert len(chosen) == 16


def test_tail_retention_requires_the_actual_same_entry():
    base = dict(venue="okx", symbol="TEST", timeframe_min=60, segment=0, side=1, censored=False)
    trades = pd.DataFrame([
        {**base, "variant": "v6_unfiltered_long", "signal_bar_open": "2025-01-01", "net_r": 12},
        {**base, "variant": "v6_unfiltered_long", "signal_bar_open": "2025-01-02", "net_r": 11},
        {**base, "variant": "v7_bb_long", "signal_bar_open": "2025-01-02", "net_r": 11},
        {**base, "variant": "v7_bb_long", "signal_bar_open": "2025-01-03", "net_r": 20},
    ])
    rows = exact_retention(trades)
    result = rows.loc[rows.baseline.eq("v6_unfiltered_long")].iloc[0]
    assert result.baseline_net_ge_10r == 2
    assert result.same_entry_tails_retained == 1
    assert result.same_entry_tails_missed == 1


def test_unknown_boolean_fails_closed():
    with pytest.raises(ValueError, match="unrecognized"):
        bools(pd.Series(["maybe"]))


def test_all_unresolved_controls_are_reported_without_fabricating_returns():
    pairs = pd.DataFrame({"variant": ["v7_bb_long"], "matched": [False], "reason": ["control_unresolved"]})
    result = control_metrics(pairs, ["variant"]).iloc[0]
    assert result.sampled_targets == 1
    assert result.matched_targets == 0
    assert pd.isna(result.control_mean_net_r)
