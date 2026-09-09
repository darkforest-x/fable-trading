"""Synthetic-only causal and cash-accounting tests for trend research."""

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from yoyo.evaluation.altseason_trend_engine import (
    BAR,
    EVENT_COLUMNS,
    build_features,
    evaluate_events,
    portfolio_from_events,
)


def synthetic_bars(n=1700, start="2020-01-01", scale=1.0):
    t = np.arange(n, dtype=float)
    close = 100 * np.exp(0.0005*t + 0.025*np.sin(t/21) + 0.008*np.sin(t/5))
    opening = np.r_[close[0], close[:-1]] if n else close
    return pd.DataFrame({
        "open": opening*scale,
        "high": np.maximum(opening, close)*1.003*scale,
        "low": np.minimum(opening, close)*0.997*scale,
        "close": close*scale,
        "volume": np.full(n, 1000.0),
    }, index=pd.date_range(start, periods=n, freq="4h", tz="UTC"))


def flat_bars(n=20):
    frame = synthetic_bars(n)
    frame[["open", "close"]] = 100.0
    frame["high"] = 101.0
    frame["low"] = 99.0
    return frame


def manual_features(bars, atr=1.0, forecast=1.0, prior_low=90.0):
    return pd.DataFrame({
        "atr": atr, "ready": True, "prior_low10": prior_low,
        "ewmac_forecast": forecast,
    }, index=bars.index)


def test_appending_future_cannot_change_any_past_feature():
    bars = synthetic_bars(1900)
    before = build_features(bars.iloc[:1663])
    after = build_features(bars)
    assert_frame_equal(before, after.iloc[:1663], check_exact=True)


def test_daily_close_not_visible_until_all_six_bars_close():
    bars = synthetic_bars(1626)
    original = build_features(bars)
    altered = bars.copy()
    # Alter only the final day's last bar, keeping all prior input unchanged.
    altered.iloc[-1, altered.columns.get_indexer(["close", "high"])] = [300.0, 301.0]
    changed = build_features(altered)
    assert_frame_equal(original.iloc[:-1], changed.iloc[:-1], check_exact=True)
    for row in range(1620, 1625):
        assert original.iloc[row].daily_source_close_time == bars.index[1620]
    assert original.iloc[-1].daily_source_close_time == bars.index[-1] + BAR
    assert original.iloc[-1].ewmac_forecast != changed.iloc[-1].ewmac_forecast
    available = original.daily_source_close_time.dropna()
    assert (available.array <= (available.index + BAR).array).all()
    assert (available.dt.hour == 0).all()


def test_partial_first_day_is_omitted_from_warmup():
    bars = synthetic_bars(256*6 + 5, start="2020-01-01 04:00")
    features = build_features(bars)
    assert pd.isna(features.iloc[3].daily_count)
    assert pd.isna(features.iloc[4].daily_count)
    assert features.iloc[10].daily_count == 1
    assert not features.iloc[:-1].ready.any()
    assert features.iloc[-1].daily_count == 256
    assert features.iloc[-1].ready


def test_shared_256_day_warmup_and_zero_volatility_rejection():
    features = build_features(synthetic_bars(1600))
    assert not features.iloc[:1535].ready.any()
    assert features.iloc[1535].ready
    flat = build_features(flat_bars(1600))
    assert not flat.ready.any()
    assert flat.ewmac_forecast.isna().all()


def test_atr_explicit_seed_then_wilder_recursion_and_prior_channels():
    bars = synthetic_bars(30)
    features = build_features(bars)
    tr = pd.concat([
        bars.high-bars.low,
        (bars.high-bars.close.shift()).abs(),
        (bars.low-bars.close.shift()).abs(),
    ], axis=1).max(axis=1)
    assert features.atr.iloc[:13].isna().all()
    assert features.atr.iloc[13] == pytest.approx(tr.iloc[:14].mean())
    assert features.atr.iloc[14] == pytest.approx((13*features.atr.iloc[13]+tr.iloc[14])/14)
    assert features.prior_high20.iloc[20] == bars.high.iloc[:20].max()
    assert features.prior_low10.iloc[20] == bars.low.iloc[10:20].min()


def test_price_scale_invariance_features_and_cash_portfolio():
    bars = synthetic_bars(1700)
    features = build_features(bars)
    scaled = bars.copy()
    scaled[["open", "high", "low", "close"]] *= 17.5
    scaled_features = build_features(scaled)
    for column in ("ewmac_forecast", "vol_bucket", "ready", "donchian_signal", "ewmac_signal"):
        assert_series_equal(features[column], scaled_features[column], check_exact=False, rtol=1e-9, atol=1e-9)
    events = evaluate_events(bars, features, [1550, 1575, 1600], 1535, 1699, "E")
    scaled_events = evaluate_events(scaled, scaled_features, [1550, 1575, 1600], 1535, 1699, "E")
    for column in ("gross_return", "net_return", "initial_risk_frac", "exit_i", "censored"):
        assert_series_equal(events[column], scaled_events[column], check_exact=False, rtol=1e-9, atol=1e-9)
    curve, _ = portfolio_from_events(bars, events, 1535, 1699)
    scaled_curve, _ = portfolio_from_events(scaled, scaled_events, 1535, 1699)
    assert_frame_equal(curve.drop(columns="size"), scaled_curve.drop(columns="size"), check_exact=False, rtol=1e-9, atol=1e-9)


def test_entry_uses_next_open_and_cannot_see_signal_bar_range():
    bars = flat_bars()
    bars.loc[bars.index[2], ["high", "low"]] = [500.0, 1.0]
    bars.loc[bars.index[3], ["open", "high"]] = [102.0, 103.0]
    event = evaluate_events(bars, manual_features(bars), [2], 0, 10, "D").iloc[0]
    assert event.entry_i == 3
    assert event.entry_price == 102.0
    assert event.signal_time == event.entry_time
    assert event.mfe_return < 1.0


def test_gap_stop_fills_worse_open_and_beats_pending_rule_exit():
    bars = flat_bars()
    bars.loc[bars.index[3], ["open", "high", "low", "close"]] = [92.0, 94.0, 91.0, 93.0]
    event = evaluate_events(bars, manual_features(bars, atr=2, forecast=0), [1], 0, 10, "E").iloc[0]
    assert event.entry_i == 2
    assert event.exit_i == 3
    assert event.stop_price == 96
    assert event.exit_price == 92
    assert event.exit_at_open
    assert event.exit_reason == "stop_gap"
    assert event.net_return == pytest.approx(-0.082)


def test_intrabar_stop_beats_close_rule_and_excludes_unknown_post_stop_high():
    bars = flat_bars()
    bars.loc[bars.index[2], ["low", "high"]] = [95.0, 130.0]
    event = evaluate_events(bars, manual_features(bars, atr=2, forecast=0), [1], 0, 10, "E").iloc[0]
    assert event.exit_i == event.entry_i == 2
    assert event.exit_price == 96
    assert event.exit_reason == "initial_stop"
    assert not event.exit_at_open
    assert event.natural_exit and not event.censored
    assert event.mfe_return == 0
    assert event.mae_return == pytest.approx(-0.04)


def test_d_e_does_not_filter_entry_when_ewmac_is_nonpositive():
    bars = flat_bars()
    features = manual_features(bars, forecast=-1)
    event = evaluate_events(bars, features, [1], 0, 10, "D_E").iloc[0]
    assert event.valid
    assert event.entry_i == 2 and event.exit_i == 3
    assert event.exit_reason == "ewmac_exit"
    assert event.hold_bars == 1 and event.hold_hours == 4


def test_donchian_close_exit_waits_until_next_open_and_ignores_exit_bar_range():
    bars = flat_bars()
    bars.loc[bars.index[2], ["close", "low"]] = [99.0, 98.5]
    bars.loc[bars.index[3], ["high", "low"]] = [200.0, 50.0]
    event = evaluate_events(bars, manual_features(bars, atr=2, prior_low=99.5), [1], 0, 10, "D").iloc[0]
    assert event.exit_i == 3 and event.exit_at_open
    assert event.exit_reason == "donchian_exit"
    assert event.exit_price == 100
    assert event.mfe_return == pytest.approx(0.01)
    assert event.mae_return == pytest.approx(-0.015)


def test_fold_boundary_censored_and_last_signal_not_filled():
    bars = flat_bars()
    events = evaluate_events(bars, manual_features(bars), [1, 10, 0], 1, 10, "D")
    assert events.iloc[0].censored and not events.iloc[0].natural_exit
    assert events.iloc[0].exit_reason == "fold_boundary"
    assert events.iloc[0].exit_i == 10
    assert not events.iloc[1].valid
    assert events.iloc[1].exit_reason == "no_next_open_in_fold"
    assert not events.iloc[2].valid
    assert events.iloc[2].exit_reason == "signal_outside_fold"


def test_no_administrative_30_day_holding_cap():
    bars = flat_bars(400)
    event = evaluate_events(bars, manual_features(bars), [1], 0, 399, "D").iloc[0]
    assert event.exit_i == 399
    assert event.hold_hours > 30*24
    assert event.censored


def test_flat_trade_fees_reconcile_to_real_cash():
    bars = flat_bars()
    events = evaluate_events(bars, manual_features(bars, forecast=0), [1], 0, 10, "E")
    assert events.iloc[0].gross_return == 0
    assert events.iloc[0].net_return == -0.002
    curve, ledger = portfolio_from_events(bars, events, 0, 10)
    trade = ledger.iloc[0]
    assert trade.accepted
    assert trade.allocated_fraction == 0.5
    assert trade.entry_notional == 0.5
    assert trade["size"] == 0.005
    assert trade.entry_fee == trade.exit_fee == 0.0005
    assert trade.pnl == pytest.approx(-0.001)
    assert curve.equity.iloc[-1] == pytest.approx(0.999)
    assert curve.equity.iloc[-1] == pytest.approx(1+ledger.pnl.sum())
    assert curve.loc[bars.index[2], "cash"] == pytest.approx(0.4995)


def test_cash_cap_includes_entry_fee_and_quantity_stays_fixed():
    bars = flat_bars()
    bars["low"] = 99.999
    bars["high"] = 100.001
    bars.loc[bars.index[4], ["high", "close"]] = [200.0, 200.0]
    events = evaluate_events(bars, manual_features(bars, atr=0.01), [1], 0, 10, "D")
    curve, ledger = portfolio_from_events(bars, events, 0, 10)
    assert ledger.iloc[0].allocated_fraction == pytest.approx(1/1.001)
    assert curve.cash.min() >= 0
    assert curve.exposure.max() <= 1
    held = curve[curve["size"] > 0]
    assert held["size"].nunique() == 1
    assert held.equity.max() > 1.9
    assert curve.equity.iloc[-1] == pytest.approx(1+ledger.pnl.sum())


def test_overlap_and_same_open_reentry_rejected_but_next_bar_allowed():
    bars = flat_bars()
    features = manual_features(bars, forecast=0)
    events = evaluate_events(bars, features, [1, 1, 2, 3], 0, 10, "E")
    curve, ledger = portfolio_from_events(bars, events, 0, 10)
    assert ledger.accepted.tolist() == [True, False, False, True]
    assert ledger.rejection_reason.iloc[1:3].eq("overlapping_position_or_same_exit_bar").all()
    assert curve.equity.iloc[-1] == pytest.approx(0.999**2)
    assert curve.equity.iloc[-1] == pytest.approx(1+ledger.pnl.sum())


def test_empty_events_and_empty_bars_are_valid_no_trade_results():
    bars = flat_bars()
    events = evaluate_events(bars, manual_features(bars), [], 0, 10, "D")
    assert events.empty and list(events.columns) == EVENT_COLUMNS
    curve, ledger = portfolio_from_events(bars, events, 0, 10)
    assert curve.equity.eq(1).all() and curve.exposure.eq(0).all()
    assert ledger.empty
    empty = synthetic_bars(0)
    features = build_features(empty)
    assert features.empty
    empty_events = evaluate_events(empty, features, [], 0, -1, "D")
    empty_curve, empty_ledger = portfolio_from_events(empty, empty_events, 0, -1)
    assert empty_curve.empty and empty_ledger.empty


def test_gaps_and_non_utc_input_fail_closed():
    bars = synthetic_bars(30)
    with pytest.raises(ValueError, match="continuous"):
        build_features(bars.drop(bars.index[10]))
    wrong_zone = bars.copy()
    wrong_zone.index = wrong_zone.index.tz_convert("Asia/Shanghai")
    with pytest.raises(ValueError, match="timezone must be UTC"):
        build_features(wrong_zone)


def test_event_study_future_suffix_cannot_change_natural_exit():
    bars = flat_bars(30)
    short = evaluate_events(bars.iloc[:15], manual_features(bars.iloc[:15], forecast=0), [1], 0, 14, "E")
    long = evaluate_events(bars, manual_features(bars, forecast=0), [1], 0, 29, "E")
    assert_frame_equal(short, long)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_datetime_storage_resolution_does_not_change_features_or_events(unit):
    bars = synthetic_bars(1700)
    baseline = build_features(bars)
    changed = bars.copy()
    changed.index = changed.index.as_unit(unit)
    features = build_features(changed)
    features.index = features.index.as_unit("ns")
    assert_frame_equal(features, baseline)
    events = evaluate_events(bars, baseline, [1550], 1535, 1699, "D")
    precision_events = evaluate_events(changed, build_features(changed), [1550], 1535, 1699, "D")
    assert_frame_equal(events, precision_events)
