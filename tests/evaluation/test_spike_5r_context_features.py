"""Causality and coverage tests for the 5R context descriptors."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_5r_context_features import FEATURE_COLUMNS, VALUE_COLUMNS, build_context_features


def _frame(rows: int = 520, *, offset: float = 0.0) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=rows, freq="15min", tz="UTC")
    close = 100.0 + offset + np.arange(rows) * .05
    out = pd.DataFrame({"open": close - .1, "high": close + .3, "low": close - .3, "close": close}, index=index)
    for position, name in enumerate(("s20", "e20", "s60", "e60", "s120", "e120")):
        out[name] = close - .1 - position * .01
    return out


def test_whole_stream_context_is_invariant_to_future_mutation() -> None:
    symbol, btc = _frame(), _frame(offset=10)
    signal_i = 480
    before = build_context_features(symbol, 15, btc)
    changed, changed_btc = symbol.copy(), btc.copy()
    changed.iloc[signal_i + 1:, :] *= 100
    changed_btc.iloc[signal_i + 1:, :] *= .01
    after = build_context_features(changed, 15, changed_btc)
    pd.testing.assert_series_equal(before.iloc[signal_i], after.iloc[signal_i], check_names=False)
    assert list(before.columns) == list(FEATURE_COLUMNS)


def test_incomplete_four_hour_group_is_unknown_and_cannot_use_older_bucket() -> None:
    symbol = _frame()
    signal_i = 480  # signal closes 120:15, so the 116:00--120:00 bucket must be available.
    signal_time = symbol.index[signal_i]
    missing_component = symbol.index[470]
    gapped = symbol.drop(index=missing_component)
    row = build_context_features(gapped, 15).loc[signal_time]
    assert not row.symbol_completed4h_close_sma20_fraction_known
    assert np.isnan(row.symbol_completed4h_close_sma20_fraction)


def test_missing_or_stale_btc_native_candle_is_unknown_never_forward_filled() -> None:
    symbol, btc = _frame(), _frame(offset=5)
    signal_i = 480
    signal_time = symbol.index[signal_i]
    # The signal closes at 120:15. Removing BTC's 120:00--120:15 candle must
    # not permit the prior BTC close to stand in for this clock.
    gapped_btc = btc.drop(index=signal_time)
    row = build_context_features(symbol, 15, gapped_btc).loc[signal_time]
    assert not row.btc_trailing24h_return_known
    assert np.isnan(row.btc_trailing24h_return)
    assert not row.btc_completed4h_close_sma20_fraction_known
    assert np.isnan(row.btc_completed4h_close_sma20_fraction)
    assert not row.symbol_relative_strength24h_known


def test_morphology_uses_prior_compact_history_and_completed_failed_break() -> None:
    frame = _frame()
    # Keep the six-MA span compact for a long run, then create an upbreak at
    # j and a below-frozen-high confirmation at j+1.  The feature at j+1 may
    # count it because both completed bars are then known.
    frame.loc[:, ["s20", "e20", "s60", "e60", "s120", "e120"]] = 100.0
    frame.loc[:, "e120"] = 100.1
    j = 450
    prior_high = float(frame.high.iloc[j - 20:j].max())
    frame.iloc[j, frame.columns.get_loc("close")] = prior_high + 2
    frame.iloc[j, frame.columns.get_loc("high")] = prior_high + 2.2
    frame.iloc[j, frame.columns.get_loc("open")] = prior_high + 1.8
    frame.iloc[j, frame.columns.get_loc("low")] = prior_high + 1.6
    frame.iloc[j + 1, frame.columns.get_loc("close")] = prior_high - .1
    frame.iloc[j + 1, frame.columns.get_loc("high")] = prior_high + .1
    frame.iloc[j + 1, frame.columns.get_loc("open")] = prior_high
    frame.iloc[j + 1, frame.columns.get_loc("low")] = prior_high - .2
    row = build_context_features(frame, 15).iloc[j + 1]
    assert row.six_ma_span_to_prior96_median_known
    assert row.prior_compact_span_duration_known
    assert row.prior_compact_span_duration > 0
    assert row.prior32_failed_upbreak_count_known
    assert row.prior32_failed_upbreak_count >= 1


def test_current_gap_invalidates_prior_duration_and_all_unknown_values_are_nan() -> None:
    frame = _frame()
    signal_i = 480
    # A bad current candle must reset the span run.  The prior compact state is
    # not allowed to survive this current, unavailable signal observation.
    frame.iloc[signal_i, frame.columns.get_loc("low")] = frame.close.iloc[signal_i] + 1
    row = build_context_features(frame, 15).iloc[signal_i]
    assert not row.six_ma_span_price_known
    assert np.isnan(row.six_ma_span_price)
    assert not row.prior_compact_span_duration_known
    assert np.isnan(row.prior_compact_span_duration)
    for name in VALUE_COLUMNS:
        if not row[f"{name}_known"]:
            assert np.isnan(row[name]), name


def test_zero_span_never_divides_by_zero_or_claims_ratio_coverage() -> None:
    frame = _frame()
    frame.loc[:, ["s20", "e20", "s60", "e60", "s120", "e120"]] = 100.0
    row = build_context_features(frame, 15).iloc[480]
    assert row.six_ma_span_price_known
    assert row.six_ma_span_price == 0
    assert not row.six_ma_span_to_prior96_median_known
    assert np.isnan(row.six_ma_span_to_prior96_median)
    assert not row.span_expansion_last4_vs_previous16_known
    assert np.isnan(row.span_expansion_last4_vs_previous16)
