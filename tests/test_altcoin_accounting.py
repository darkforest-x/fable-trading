"""Synthetic causality and fee checks for altcoin event/sleeve accounting."""

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.altcoin_accounting import compound_portfolio, evaluate_events


def sample(rows, *, freq="1h", atr=1.0, md=1.0, sma60=90.0):
    bars = pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                        index=pd.date_range("2025-01-01", periods=len(rows), freq=freq, tz="UTC"))
    features = pd.DataFrame({"atr": atr, "md": md, "sma60": sma60}, index=bars.index)
    return bars, features


def event(bars, features, *, signal=0, side=1, **kwargs):
    return evaluate_events(bars, features, [signal], [side], first_i=0, last_i=len(bars) - 1, **kwargs).iloc[0]


def test_signal_extreme_is_not_credited_and_samebar_stop_wins_tp():
    bars, f = sample([[100, 500, 50, 100], [100, 110, 97, 105], [105, 106, 104, 105]])
    row = event(bars, f, exit_rule="fixed3r")
    assert row.valid and row.entry_i == 1 and row.entry_price == 100
    assert row.exit_reason == "initial_stop" and row.exit_price == 98
    assert row.mfe_return == 0 and row.mae_r == pytest.approx(1)
    assert row.net_return == pytest.approx(-0.022)
    assert row.net_bp == pytest.approx(-220) and row.gross_bp == pytest.approx(-200)
    assert row.reason == "initial_stop"


def test_short_stop_before_samebar_target():
    bars, f = sample([[100, 101, 99, 100], [100, 103, 90, 95]])
    row = event(bars, f, side=-1, exit_rule="fixed3r")
    assert row.exit_price == 102 and row.gross_r == pytest.approx(-1)
    assert row.mfe_r == 0


def test_gap_stop_uses_worse_open_and_no_later_extremes():
    bars, f = sample([[100, 101, 99, 100], [100, 102, 99, 101], [95, 150, 90, 100]])
    row = event(bars, f)
    assert row.exit_reason == "stop_gap" and row.exit_at_open
    assert row.exit_price == 95 and row.gross_r == pytest.approx(-2.5)
    assert row.mfe_r == pytest.approx(1) and row.mae_r == pytest.approx(2.5)


def test_target_gap_conservatively_receives_only_target():
    bars, f = sample([[100, 101, 99, 100], [100, 105, 99, 104], [110, 115, 109, 114]])
    row = event(bars, f, exit_rule="fixed3r")
    assert row.exit_reason == "take_profit_gap" and row.exit_price == 106
    assert row.exit_at_open and row.mfe_r == pytest.approx(3)


@pytest.mark.parametrize("rule", ["md", "sma60"])
def test_close_exit_is_next_open_and_can_use_complete_decision_bar(rule):
    bars, f = sample([[100, 101, 99, 100], [100, 105, 99, 102], [104, 130, 103, 120]])
    f.loc[f.index[1], "md"] = 0
    f.loc[f.index[1], "sma60"] = 103
    row = event(bars, f, exit_rule=rule)
    assert row.exit_i == 2 and row.exit_price == 104 and row.exit_at_open
    assert row.mfe_return == pytest.approx(0.05)
    assert row.hold_seconds == 3600


def test_chandelier_raised_from_high_cannot_stop_same_bar():
    bars, f = sample([[100, 101, 99, 100], [100, 110, 99, 109], [108, 109, 106, 108]])
    row = event(bars, f, exit_rule="chandelier")
    assert row.exit_i == 2 and row.exit_price == 107
    assert row.exit_reason == "chandelier_stop"
    assert row.mfe_return == pytest.approx(0.1)


def test_chandelier_gap_if_new_stop_is_above_previous_close():
    bars, f = sample([[100, 101, 99, 100], [100, 110, 99, 104], [104, 111, 102, 110]])
    row = event(bars, f, exit_rule="chandelier")
    assert row.exit_i == 2 and row.exit_price == 104 and row.exit_reason == "chandelier_gap"


def test_chandelier_short_is_mirrored_and_ratchets_only_after_close():
    bars, f = sample([[100, 101, 99, 100], [100, 101, 90, 91], [92, 94, 91, 93]])
    row = event(bars, f, side=-1, exit_rule="chandelier")
    assert row.exit_i == 2 and row.exit_price == 93 and row.gross_return == pytest.approx(0.07)


def test_boundary_is_censored_and_time_limit_is_contractual_exit():
    bars, f = sample([[100, 101, 99, 100], [100, 104, 99, 102], [102, 105, 101, 104]])
    boundary = event(bars, f)
    assert boundary.censored and not boundary.natural_exit and boundary.exit_reason == "boundary_mark"
    assert boundary.mfe_return == pytest.approx(0.05)
    expiry = event(bars, f, max_hold_days=1 / 24)
    assert expiry.exit_i == 1 and expiry.exit_reason == "max_hold" and not expiry.censored


def test_invalid_risk_and_no_entry_are_explicit_rows():
    bars, f = sample([[100, 101, 99, 100], [100, 101, 99, 100]], atr=50)
    row = event(bars, f)
    assert not row.valid and row.invalid_reason == "invalid_initial_risk"
    row = event(bars, f, signal=1)
    assert not row.valid and row.invalid_reason == "no_entry_bar"


def test_unreachable_short_target_does_not_change_candidate_eligibility():
    bars, f = sample([[100, 101, 99, 100], [100, 101, 60, 70]], atr=20)
    row = event(bars, f, side=-1, exit_rule="fixed3r")
    assert row.valid and row.target_unreachable and row.target_price == -20
    assert row.reason == "boundary_mark" and row.exit_price == 70


def test_gap_during_holding_invalidates_candidate_without_filling_bars():
    bars, f = sample([[100, 101, 99, 100]] * 5)
    bars, f = bars.drop(bars.index[2]), f.drop(f.index[2])
    row = event(bars, f)
    assert not row.valid and row.invalid_reason == "gap_during_holding"


def test_future_append_cannot_change_naturally_closed_event():
    bars, f = sample([[100, 101, 99, 100], [100, 107, 99, 105], [105, 106, 104, 105], [105, 999, 1, 500]])
    short = event(bars.iloc[:3], f.iloc[:3], exit_rule="fixed3r")
    full = event(bars, f, exit_rule="fixed3r")
    pd.testing.assert_series_equal(short, full)


def test_price_scaling_preserves_returns_and_r():
    bars, f = sample([[100, 101, 99, 100], [100, 110, 99, 109], [108, 109, 106, 108]])
    original = event(bars, f, exit_rule="chandelier")
    scaled = f.copy()
    scaled[["atr", "md", "sma60"]] *= 0.001
    other = event(bars * 0.001, scaled, exit_rule="chandelier")
    assert original.exit_i == other.exit_i
    for name in ["gross_return", "net_return", "mfe_r", "mae_r", "gross_r"]:
        assert other[name] == pytest.approx(original[name])


def test_portfolio_entry_fee_mtm_exit_fee_and_compounding():
    bars, f = sample([[100, 101, 99, 100], [100, 105, 99, 104], [104, 107, 103, 106], [100, 107, 99, 106]])
    events = evaluate_events(bars, f, [0, 2], [1, 1], first_i=0, last_i=3, exit_rule="fixed3r")
    equity, selected, d = compound_portfolio(bars, events, 0, 3)
    assert equity.iloc[1] == pytest.approx(1.039)
    assert equity.iloc[2] == pytest.approx(1.058)
    assert equity.iloc[3] == pytest.approx(1.058 ** 2)
    assert len(selected) == 2
    assert d["total_fees"] == pytest.approx(0.002 + 0.002 * 1.058)
    assert selected.iloc[1].portfolio_entry_notional == pytest.approx(1.058)


def test_samebar_intrabar_exit_prevents_second_entry():
    bars, f = sample([[100, 101, 99, 100], [100, 105, 99, 104], [104, 107, 103, 106]])
    events = evaluate_events(bars, f, [0, 1], [1, 1], first_i=0, last_i=2, exit_rule="fixed3r")
    _, selected, d = compound_portfolio(bars, events, 0, 2)
    assert len(selected) == 1 and d["skipped_overlap"] == 1


def test_open_exit_allows_new_candidate_at_same_open():
    bars, f = sample([[100, 101, 99, 100], [100, 105, 99, 102], [104, 107, 103, 106]])
    f.loc[f.index[1], "md"] = 0
    events = evaluate_events(bars, f, [0, 1], [1, 1], first_i=0, last_i=2)
    _, selected, d = compound_portfolio(bars, events, 0, 2)
    assert len(selected) == 2 and d["skipped_overlap"] == 0
    assert selected.iloc[1].portfolio_entry_equity == pytest.approx(1.038)


def test_negative_equity_after_short_gap_cannot_reenter():
    bars, f = sample([[100, 101, 99, 100], [100, 101, 99, 100], [250, 251, 249, 250], [250, 251, 249, 250]])
    events = evaluate_events(bars, f, [0, 1, 2], [-1, 1, 1], first_i=0, last_i=3)
    equity, selected, d = compound_portfolio(bars, events, 0, 3)
    assert len(selected) == 1 and d["skipped_nonpositive_equity"] == 2
    assert equity.iloc[-1] == pytest.approx(-0.502)


def test_zero_trades_keep_initial_equity_and_fee_drawdown_is_visible():
    bars, f = sample([[100, 101, 99, 100]] * 3)
    empty = evaluate_events(bars, f, [], [], first_i=0, last_i=2)
    eq, selected, d = compound_portfolio(bars, empty, 0, 2)
    assert list(eq) == [1, 1, 1] and selected.empty and d["max_drawdown"] == 0
    flat = evaluate_events(bars, f, [0], [1], first_i=0, last_i=2)
    eq, _, d = compound_portfolio(bars, flat, 0, 2)
    assert list(eq) == pytest.approx([1, 0.999, 0.998])
    assert d["max_drawdown"] == pytest.approx(0.002)


def test_schema_timezone_alignment_and_portfolio_return_tampering_reject():
    bars, f = sample([[100, 101, 99, 100]] * 3)
    naive = bars.copy()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        event(naive, f)
    with pytest.raises(ValueError, match="identical"):
        event(bars, f.iloc[:-1])
    events = evaluate_events(bars, f, [0], [1], first_i=0, last_i=2)
    events.loc[0, "net_return"] = 5
    with pytest.raises(ValueError, match="fees"):
        compound_portfolio(bars, events, 0, 2)


@pytest.mark.parametrize("freq, seconds", [("15min", 900), ("1h", 3600), ("4h", 14400)])
def test_supported_periods_have_explicit_close_timestamps(freq, seconds):
    bars, f = sample([[100, 101, 99, 100]] * 3, freq=freq)
    row = event(bars, f)
    assert row.period_seconds == seconds
    assert row.decision_time == bars.index[1]
    assert row.exit_time == bars.index[-1] + pd.Timedelta(seconds=seconds)


@pytest.mark.parametrize("unit", ["ms", "us"])
@pytest.mark.parametrize("rule", ["fixed3r", "md", "chandelier", "sma60"])
def test_index_resolution_does_not_change_outcomes_or_portfolio(unit, rule):
    bars, features = sample([[100, 101, 99, 100], [100, 110, 99, 109],
                             [108, 109, 106, 108], [108, 109, 107, 108]])
    features.loc[features.index[1], "md"] = 0
    features.loc[features.index[1], "sma60"] = 110
    expected = evaluate_events(bars, features, [0], [1], first_i=0, last_i=3, exit_rule=rule)
    expected_equity, expected_selected, expected_diag = compound_portfolio(bars, expected, 0, 3)
    changed_bars, changed_features = bars.copy(), features.copy()
    changed_bars.index = changed_bars.index.as_unit(unit)
    changed_features.index = changed_features.index.as_unit(unit)
    actual = evaluate_events(changed_bars, changed_features, [0], [1], first_i=0, last_i=3, exit_rule=rule)
    equity, selected, diag = compound_portfolio(changed_bars, actual, 0, 3)
    pd.testing.assert_frame_equal(actual, expected)
    pd.testing.assert_series_equal(equity, expected_equity)
    pd.testing.assert_frame_equal(selected, expected_selected)
    pd.testing.assert_series_equal(diag.pop("adverse_equity"), expected_diag.pop("adverse_equity"))
    assert diag == expected_diag
