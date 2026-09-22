"""Regression checks for unequal denominators and time-safe gate selection."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_joint_btc_report import (
    SPLIT, attribution, differences, drawdown, holm, period_rows, select_earlier, sign_flip,
)


def test_early_selection_cannot_use_cross_split_outcomes():
    t = pd.DataFrame({"status": ["closed"] * 3,
        "signal_close": [SPLIT - pd.Timedelta(days=1), SPLIT - pd.Timedelta(days=2), SPLIT],
        "exit_time": [SPLIT, SPLIT - pd.Timedelta(days=1), SPLIT + pd.Timedelta(days=1)]})
    assert period_rows(t, "earlier").index.tolist() == [1]
    assert period_rows(t, "later").index.tolist() == [2]


def test_same_outcomes_different_counts_do_not_mean_same_quality():
    a = pd.DataFrame({"net_r": [-1., -1., 3., -1.], "month": ["01", "01", "02", "02"], "week": ["w1", "w1", "w2", "w2"]})
    b = a.iloc[[2]].copy()
    d = differences(a, b, "net_r")
    assert d["delta_mean"] == 3.
    assert d["delta_sum"] == 3.
    assert d["valid_reps"] < 2000  # A draw can contain only months without B.


def test_month_bootstrap_zero_effect_when_every_event_duplicated():
    a = pd.DataFrame({"net_r": [-1., 3.], "month": ["01", "02"], "week": ["w1", "w2"]})
    b = pd.concat([a, a], ignore_index=True)
    d = differences(a, b, "net_r")
    assert d["delta_mean"] == d["ci_low"] == d["ci_high"] == 0.
    assert d["p"] == 1.


def test_sign_flip_exact_resolution_and_missing_block():
    assert sign_flip(np.ones(3)) == .125
    assert np.isnan(sign_flip([1]))
    assert sign_flip([0, 0, 0]) == 1.


def test_holm_preserves_monotonicity_and_missing_family_members():
    assert np.allclose(holm([.01, .04, .03], family=3), [.03, .06, .06])
    assert holm([.01, np.nan], family=24)[0] == .24
    with pytest.raises(ValueError):
        holm([.01, .02], family=1)


def test_exit_time_drawdown_coalesces_simultaneous_exits():
    t = pd.DataFrame({"exit_time": ["t1", "t1", "t2"], "net_r": [4., -3., -2.]})
    assert drawdown(t) == 2.
    assert drawdown(t.iloc[::-1]) == 2.


def test_attribution_accounts_for_lost_and_added_entries_and_rejects_exit_drift():
    a = pd.DataFrame({"trade_key": ["a", "b"], "net_r": [-1., 12.], "net_bp": [-10., 120.]})
    b = pd.DataFrame({"trade_key": ["b", "c"], "net_r": [12., -2.], "net_bp": [120., -20.]})
    d = attribution(a, b)
    assert d["retained_gt10r"] == 1 and d["avoided_loss_r"] == 1.
    assert d["added"] == 1 and d["net_delta_r"] == -1.
    b.loc[0, "net_r"] = 13.
    with pytest.raises(ValueError):
        attribution(a, b)


def test_selection_ignores_later_results_and_rejects_negative_bp():
    rows = []
    for ex in ("price", "rsi7"):
        for period in ("earlier", "later"):
            for tf in ("15m", "1h", "pooled"):
                for gate, r, bp in (("h1_sma120", .2, 3.), ("same_sma60", .1, 4.)):
                    rows.append(dict(exit_rule=ex, period=period, timeframe=tf, gate=gate, closed=300,
                                     mean_net_r=r if period == "earlier" else 100. * (gate == "same_sma60"), mean_net_bp=bp))
    t = pd.DataFrame(rows)
    assert {r["selected_gate"] for r in select_earlier(t)} == {"h1_sma120"}
    t.loc[t.gate.eq("h1_sma120"), "mean_net_bp"] = -1.
    assert {r["selected_gate"] for r in select_earlier(t)} == {"none"}
