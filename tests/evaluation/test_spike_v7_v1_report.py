"""Accounting and identity checks for the V7 comparison postprocessor."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v7_v1_report import bools, event_metrics, exact_retention, fixed_sample, control_metrics, independent_account


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


def test_duplicate_markets_do_not_inflate_month_block_sign_flip_sample_size():
    pairs = pd.DataFrame({"variant": "v7_bb_long", "matched": True,
                          "signal_bar_open": pd.date_range("2025-01-01", periods=6, freq="MS", tz="UTC"),
                          "target_net_r": 2., "control_net_r": 1., "net_r_difference": 1.,
                          "net_return_difference": .02})
    one = control_metrics(pairs, ["variant"]).iloc[0]
    repeated = control_metrics(pd.concat([pairs]*20, ignore_index=True), ["variant"]).iloc[0]
    assert one.matched_months == repeated.matched_months == 6
    assert one.exploratory_month_block_sign_flip_p == repeated.exploratory_month_block_sign_flip_p


def test_independent_account_keeps_both_sides_and_ignores_censored_marks():
    trades = pd.DataFrame({"side":[1,-1,1], "net_return":[.1,-.2,4.], "censored":[False,False,True],
                           "entry_time":pd.date_range("2025-01-01",periods=3,tz="UTC")})
    result = independent_account(trades)
    assert result["net_return_closed_balance"] == pytest.approx(-.12)
    assert result["max_drawdown_closed_balance"] == pytest.approx(.2)
    assert result["unresolved"] == 1
    trades.loc[0,"net_return"] = -1.1
    result = independent_account(trades)
    assert result["insolvency_or_invalid_return_events"] == 1
    assert pd.isna(result["net_return_closed_balance"])


def test_zero_trade_archive_is_an_idle_account_without_execution_fields():
    result = independent_account(pd.DataFrame(columns=["variant", "censored", "net_r", "net_return"]))
    assert result["entries"] == result["closed"] == result["unresolved"] == 0
    assert result["net_return_closed_balance"] == result["max_drawdown_closed_balance"] == 0
    assert result["insolvency_or_invalid_return_events"] == 0
