"""Synthetic contracts for the pre-registered V6 / original-WVF study."""
import pandas as pd
import pytest

from yoyo.evaluation.spike_v6_wvf_study import (
    ExecutionSpec, WvfSpec, aggregate_complete, make_signal_ledger, simulate_v6_variant, summarize_account,
    wvf_features, wvf_long_reclaim,
)


def market(n=90):
    f = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 2.0},
                     index=pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC"))
    f.attrs["minutes"] = 60
    return f


def test_wvf_uses_population_std_and_ph_is_max_multiplier_not_quantile():
    f = market()
    f.loc[f.index[20:70], "close"] = list(range(100, 150))
    f.loc[f.index[20:70], "high"] = f.loc[f.index[20:70], "close"] + 1
    f.loc[f.index[70], ["close", "high", "low"]] = [130, 131, 120]
    out = wvf_features(f)
    i = 70
    values = out.wvf.iloc[i-19:i+1]
    assert out.wvf_std20_ddof0.iloc[i] == pytest.approx(values.std(ddof=0))
    assert out.wvf_range_high.iloc[i] == pytest.approx(out.wvf_rolling_max50.iloc[i] * .85)
    assert WvfSpec().ph == .85


def test_wvf_gap_resets_rolling_history_and_extreme_cannot_cross_it():
    f = market()
    f.loc[f.index[55], "low"] = 60
    gap = pd.Series(False, index=f.index); gap.iloc[60] = True
    out = wvf_features(f, data_gap=gap)
    assert out.segment_id.iloc[60] != out.segment_id.iloc[59]
    assert out.wvf.iloc[60:81].isna().all()
    reclaim = wvf_long_reclaim(f, out, window=12)
    assert reclaim.wvf_last_extreme_i.iloc[60:72].isna().all()


def test_reclaim_requires_prior_not_current_extreme_unbroken_low_and_high_reclaim():
    f = market(10)
    feature = pd.DataFrame({"segment_id": 0, "wvf_extreme": False}, index=f.index)
    feature.loc[f.index[3], "wvf_extreme"] = True
    f.loc[f.index[3], ["high", "low"]] = [105, 95]
    f.loc[f.index[6], "close"] = 106
    out = wvf_long_reclaim(f, feature, window=12)
    assert out.wvf_admitted.iloc[6] and out.wvf_last_extreme_i.iloc[6] == 3
    f.loc[f.index[5], "close"] = 94
    assert wvf_long_reclaim(f, feature, window=12).wvf_filter_reason.iloc[6] == "extreme_low_close_broken"
    f.loc[f.index[5], "close"] = 100; f.loc[f.index[6], "close"] = 105
    assert wvf_long_reclaim(f, feature, window=12).wvf_filter_reason.iloc[6] == "extreme_high_not_reclaimed"
    feature.loc[f.index[6], "wvf_extreme"] = True
    assert wvf_long_reclaim(f, feature, window=12).wvf_filter_reason.iloc[6] == "current_wvf_extreme"


def test_window_24_is_independent_sensitivity_not_an_implicit_parameter_grid():
    f = market(30)
    feature = pd.DataFrame({"segment_id": 0, "wvf_extreme": False}, index=f.index)
    feature.loc[f.index[5], "wvf_extreme"] = True
    f.loc[f.index[5], ["high", "low"]] = [101, 90]
    f.loc[f.index[20], "close"] = 102
    assert not wvf_long_reclaim(f, feature, window=12).wvf_admitted.iloc[20]
    assert wvf_long_reclaim(f, feature, window=24).wvf_admitted.iloc[20]
    with pytest.raises(ValueError, match="pre-registered"):
        wvf_long_reclaim(f, feature, window=13)


def test_signal_ledger_keeps_unfiltered_shorts_and_all_raw_events():
    f = market(5)
    signals = pd.DataFrame({"long_signal": [False, True, False, False, False],
                            "short_signal": [False, False, False, True, False]}, index=f.index)
    reclaim = pd.DataFrame({"wvf_admitted": [False] * 5, "wvf_filter_reason": ["no_prior_extreme"] * 5,
                            "wvf_current_extreme": [False] * 5, "wvf_last_extreme_i": [float("nan")] * 5}, index=f.index)
    ledger = make_signal_ledger(signals, reclaim, variant="B12", minutes=60)
    assert ledger.side.tolist() == [1, -1]
    assert ledger.admitted_for_entry.tolist() == [False, True]
    assert ledger.signal_confirm_time.tolist() == [f.index[1] + pd.Timedelta(hours=1), f.index[3] + pd.Timedelta(hours=1)]


def test_entry_bar_stop_wins_before_high_and_signal_is_next_open():
    f = market(8)
    signals = pd.DataFrame({"long_signal": [False, False, False, False, True, False, False, False],
                            "short_signal": False}, index=f.index)
    f.loc[f.index[5], ["open", "high", "low", "close"]] = [100, 200, 95, 150]
    _, trades = simulate_v6_variant(f, signals, admission=pd.Series(True, index=f.index), variant="A",
                                    spec=ExecutionSpec(tick=.1))
    row = trades.iloc[0]
    assert row.entry_time == f.index[5] and row.exit_time == f.index[5]
    assert row.exit_reason == "initial_stop" and row.mfe_r == 0.0


def test_unfiltered_opposite_signal_exits_filtered_variant_at_next_open():
    f = market(9)
    signals = pd.DataFrame({"long_signal": [False, False, False, False, True, False, False, False, False],
                            "short_signal": [False, False, False, False, False, False, True, False, False]}, index=f.index)
    f.loc[f.index[7], "open"] = 111.0
    admission = pd.Series(False, index=f.index); admission.iloc[4] = True  # opposite short does not receive a new entry
    _, trades = simulate_v6_variant(f, signals, admission=admission, variant="B12", spec=ExecutionSpec(tick=.1))
    assert len(trades) == 1
    assert trades.iloc[0].exit_reason == "opposite_v6_next_open"
    assert trades.iloc[0].exit_time == f.index[7] and trades.iloc[0].exit_price == 111.0


def test_stop_precedes_same_close_opposite_signal_and_future_mutation_changes_nothing_before_prefix():
    f = market(10)
    signals = pd.DataFrame({"long_signal": [False, False, False, False, True, False, False, False, False, False],
                            "short_signal": [False, False, False, False, False, False, True, False, False, False]}, index=f.index)
    f.loc[f.index[5], ["open", "high", "low", "close"]] = [100, 200, 90, 95]
    _, first = simulate_v6_variant(f, signals, admission=pd.Series(True, index=f.index), variant="A", spec=ExecutionSpec(tick=.1))
    changed = f.copy(); changed.loc[changed.index[6]:, ["open", "high", "low", "close"]] = [200, 201, 199, 200]
    _, second = simulate_v6_variant(changed, signals, admission=pd.Series(True, index=f.index), variant="A", spec=ExecutionSpec(tick=.1))
    assert first.iloc[0].exit_reason == "initial_stop" and first.iloc[0].exit_time == f.index[5]
    pd.testing.assert_series_equal(first.iloc[0], second.iloc[0])


def test_gap_censors_open_position_and_same_side_signal_cannot_reenter_after_stop():
    f = market(10)
    signals = pd.DataFrame({"long_signal": [False, False, False, False, True, True, False, False, False, False],
                            "short_signal": False}, index=f.index)
    # The first position is stopped on bar3.  Its same-side confirmation on that
    # close must not schedule an immediate replacement at bar4.
    f.loc[f.index[5], ["open", "high", "low", "close"]] = [100, 110, 90, 95]
    _, stopped = simulate_v6_variant(f, signals, admission=pd.Series(True, index=f.index), variant="A", spec=ExecutionSpec(tick=.1))
    assert len(stopped) == 1 and stopped.iloc[0].exit_reason == "initial_stop"
    signals.loc[:, "long_signal"] = False; signals.loc[f.index[4], "long_signal"] = True
    f.loc[f.index[5], ["open", "high", "low", "close"]] = [100, 101, 99, 100]
    gap = pd.Series(False, index=f.index); gap.iloc[6] = True
    _, censored = simulate_v6_variant(f, signals, admission=pd.Series(True, index=f.index), variant="A", data_gap=gap, spec=ExecutionSpec(tick=.1))
    assert censored.iloc[0].exit_reason == "data_gap_censored" and bool(censored.iloc[0].censored)


def test_open_stop_beats_pending_reverse_and_reverse_entry_can_follow():
    f = market(10)
    signals = pd.DataFrame({"long_signal": [False, False, False, False, True, False, False, False, False, False],
                            "short_signal": [False, False, False, False, False, False, True, False, False, False]}, index=f.index)
    # Bar7 is the next open after the short signal and gaps through the long stop.
    f.loc[f.index[7], ["open", "high", "low", "close"]] = [89, 100, 80, 90]
    _, trades = simulate_v6_variant(f, signals, admission=pd.Series(True, index=f.index), variant="A", spec=ExecutionSpec(tick=.1))
    assert trades.iloc[0].exit_reason == "initial_stop_gap"
    assert trades.iloc[1].side == -1 and trades.iloc[1].entry_time == f.index[7]


def test_account_drawdown_includes_initial_cash_and_aggregation_keeps_only_full_bars():
    assert summarize_account(pd.DataFrame()).get("max_drawdown_closed_trade") == 0.0
    f = market(9)
    f.index = pd.date_range("2025-01-01", periods=9, freq="30min", tz="UTC")
    f["volume"] = 1.0; f["quote_volume"] = 100.0
    partial = aggregate_complete(f, 240)
    assert len(partial) == 1 and partial.open.iloc[0] == 100.0


def test_empty_signal_ledger_keeps_schema_for_runner_fold_filtering():
    f = market(6)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=f.index)
    ledger = make_signal_ledger(signals, None, variant="A", minutes=60)
    assert ledger.empty and {"signal_confirm_time", "admitted_for_entry", "side"}.issubset(ledger)
