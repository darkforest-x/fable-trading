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


def test_unselected_later_winner_is_only_descriptive():
    from yoyo.evaluation.spike_joint_btc_report import decision_flags
    got = decision_flags('same_sma60', 'h1_sma120', {'positive': True, 'significant': True})
    assert got['descriptive_conditions_met'] and not got['passed']
    assert decision_flags('h1_sma120', 'h1_sma120', {'positive': True})['passed']
    assert not decision_flags('h1_sma120', 'none', {'positive': True})['passed']


def test_report_rejects_same_count_but_wrong_window_or_control_time():
    from yoyo.evaluation.spike_joint_btc_report import validate_window, END
    frame = pd.DataFrame({'signal_close': [SPLIT], 'timeframe': ['15m'],
                          'control_signal_bar_open': [SPLIT]})
    validate_window(frame)
    frame.loc[0, 'signal_close'] = END
    with pytest.raises(ValueError, match='event outside'):
        validate_window(frame)
    frame.loc[0, 'signal_close'] = SPLIT
    frame.loc[0, 'control_signal_bar_open'] = END
    with pytest.raises(ValueError, match='control outside'):
        validate_window(frame)


def test_fixed_calendar_grid_retains_inactive_months():
    a = pd.DataFrame({'net_r': [1., -1.], 'month': ['01', '02'], 'week': ['w1', 'w2']})
    b = a.iloc[[0]]
    got = differences(a, b, 'net_r', months=['01', '02', '03'])
    assert got['months'] == 3
    assert got['empty_baseline_months'] == 1 and got['empty_filtered_months'] == 2
    assert got['valid_reps'] < 2000


def test_independent_parent_audit_handles_empty_legacy_control_schema(tmp_path, monkeypatch):
    import yoyo.evaluation.spike_joint_btc_report as report
    from yoyo.evaluation.spike_joint_btc_gate import _empty
    p = tmp_path / 'streams' / 'EMPTY'
    p.mkdir(parents=True)
    tables = _empty()
    pd.DataFrame(columns=['timeframe', 'signal_i', 'signal_bar_open', 'box_any_status']).to_csv(p / 'decisions.csv.gz', index=False)
    tables['trades'].to_csv(p / 'trades.csv.gz', index=False)
    pd.DataFrame(columns=['trade_key', 'arm', 'matched', 'reason']).to_csv(p / 'controls.csv.gz', index=False)
    monkeypatch.setattr(report, 'SOURCE', tmp_path)
    report.validate_parent_symbol('EMPTY', tmp_path, tables)


@pytest.mark.parametrize('empty_baseline', [False, True])
def test_entirely_empty_arm_retains_predeclared_month_grid(empty_baseline):
    one = pd.DataFrame({'net_r': [1.], 'month': ['01'], 'week': ['w1']})
    empty = one.iloc[:0]
    a, b = (empty, one) if empty_baseline else (one, empty)
    got = differences(a, b, 'net_r', months=['01', '02'])
    assert got['months'] == 2 and got['valid_reps'] == 0 and np.isnan(got['ci_low'])
    assert got['empty_baseline_months' if empty_baseline else 'empty_filtered_months'] == 2
