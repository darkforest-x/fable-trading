"""Causal contracts for the V9 long running-high trailing sensitivity."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_high_r_v1 as high_r
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v9_full_replay import prepare_v9, random_controls


def fixture(*, seed: int = 4, side: int = 1) -> base.StreamContext:
    """Return one small V9-ready cache with an authenticated-style ledger."""
    index = pd.date_range("2025-01-04", periods=28, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .5, len(index)))
    bars = pd.DataFrame({"open": np.r_[100., close[:-1]], "close": close, "atr": 1., "rv": 50.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ready": True,
                         "ropeHigh": close, "ropeLow": close}, index=index)
    bars["high"] = bars[["open", "close"]].max(axis=1) + .35
    bars["low"] = bars[["open", "close"]].min(axis=1) - .35
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    for j, i in enumerate(range(5, 23, 4)):
        signals.loc[index[i], "long_signal" if (j + (side < 0)) % 2 == 0 else "short_signal"] = True
    ledger = pd.DataFrame({"signal_bar_open": index[np.flatnonzero(signals.any(axis=1))],
                           "signal_i": np.flatnonzero(signals.any(axis=1))})
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(),
             "bb": pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index),
             "bb_ready": pd.Series(True, index=index), "data_gap": pd.Series(False, index=index), "tick": .01}
    return base.StreamContext(Path("."), "synthetic", {}, cache, ledger, 60,
                              {"asset": "ETH", "symbol": "ETHUSDC", "venue": "synthetic", "timeframe_min": 60})


def prepared(context: base.StreamContext):
    """Make the exact V9 admission-prepared engine input used by the study."""
    return prepare_v9(engine.prepare_arm(context, arm="v8"))[0]


def assert_valid_ohlc(context: base.StreamContext) -> None:
    """Keep synthetic paths executable rather than relying on impossible bars."""
    bars = context.cache["bars"]
    assert (bars.high >= bars[["open", "close"]].max(axis=1)).all()
    assert (bars.low <= bars[["open", "close"]].min(axis=1)).all()


def single_long() -> tuple[base.StreamContext, object]:
    """Build a long whose pre-arm high exceeds its eventual close arm level."""
    context = fixture()
    bars, signals = context.cache["bars"], context.cache["signals"]
    signals.loc[:, :] = False
    signals.iloc[4, signals.columns.get_loc("long_signal")] = True
    context.cache["v1_signals"].loc[:, :] = signals
    context = replace(context, signals_ledger=pd.DataFrame({"signal_bar_open": [bars.index[4]], "signal_i": [4]}))
    for i in range(len(bars)):
        bars.iloc[i, bars.columns.get_indexer(["open", "high", "low", "close", "atr", "rv"])] = [100., 100.5, 99.5, 100., 1., 50.]
    # Entry is index 5 and its frozen 5-bar low gives an initial stop of 98.
    bars.iloc[5, bars.columns.get_indexer(["open", "high", "low", "close"])] = [100., 101., 99., 100.5]
    bars.iloc[6, bars.columns.get_indexer(["open", "high", "low", "close"])] = [100.5, 106., 100., 101.]
    bars.iloc[7, bars.columns.get_indexer(["open", "high", "low", "close"])] = [103., 104.2, 103., 104.]
    bars.iloc[8, bars.columns.get_indexer(["open", "high", "low", "close"])] = [104., 104., 101., 102.]
    for i in range(9, len(bars)):
        bars.iloc[i, bars.columns.get_indexer(["open", "high", "low", "close"])] = [103., 103.5, 102.5, 103.]
    return context, prepared(context)


@pytest.mark.parametrize("seed", range(6))
def test_baseline_is_exact_parent_v9_replay_for_randomized_fixtures(seed: int) -> None:
    context, value = fixture(seed=seed), None
    value = prepared(context)
    expected = engine.replay_serial(context, arm="v8", enable_be=False, prepared=value)
    actual = high_r.replay_serial(value, peak_long=False)
    for got, want in zip(actual, expected):
        pd.testing.assert_frame_equal(got, want)


def test_long_running_peak_changes_only_the_anchor_and_emits_next_bar_evidence() -> None:
    context, value = single_long()
    assert_valid_ohlc(context)
    peak, _, events = high_r.replay_serial(value)
    baseline, _, _ = high_r.replay_serial(value, peak_long=False)
    trade = peak.loc[~peak.censored].iloc[0]
    assert (trade.exit_i, trade.exit_price, trade.exit_reason) == (8, pytest.approx(102.), "trailing_stop")
    assert baseline.iloc[0].censored
    event = events.iloc[0]
    assert (event.highest_known, event.anchor, event.protection_after) == pytest.approx((106., 106., 102.))
    assert event.available_at == context.cache["bars"].index[8]
    assert event.effective_next_bar


def test_current_low_is_checked_before_a_new_peak_protection_can_apply() -> None:
    context, value = single_long()
    bars = context.cache["bars"]
    bars.iloc[7, bars.columns.get_indexer(["high", "low", "close"])] = [106., 97., 104.]
    assert_valid_ohlc(context)
    value = prepared(context)
    trades, _, events = high_r.replay_serial(value)
    trade = trades.loc[~trades.censored].iloc[0]
    assert (trade.exit_i, trade.exit_price, trade.exit_reason) == (7, pytest.approx(98.), "initial_stop")
    assert events.empty


def test_close_not_high_arms_the_frozen_two_r_trail() -> None:
    context, value = single_long()
    bars = context.cache["bars"]
    bars.iloc[7, bars.columns.get_indexer(["high", "low", "close"])] = [110., 100., 103.99]
    assert_valid_ohlc(context)
    value = prepared(context)
    row = high_r.replay_serial(value)[0].iloc[0]
    fixed = high_r.replay_fixed_entry(value, row)
    assert fixed["censored"] and fixed["protection"] == pytest.approx(98.)


def test_peak_candidate_uses_tick_floor_and_worse_next_open_gap_price() -> None:
    context, value = single_long()
    bars = context.cache["bars"]
    bars.iloc[6, bars.columns.get_indexer(["high", "low", "close"])] = [106.08, 100., 101.]
    bars.iloc[7, bars.columns.get_indexer(["high", "low", "close"])] = [104.2, 103., 104.]
    bars.iloc[8, bars.columns.get_indexer(["open", "high", "low", "close"])] = [101., 102., 100.5, 101.]
    assert_valid_ohlc(context)
    value = replace(prepared(context), spec=replace(prepared(context).spec, tick=.05))
    trade = high_r.replay_serial(value)[0].loc[lambda table: ~table.censored].iloc[0]
    assert (trade.exit_price, trade.exit_reason) == (pytest.approx(101.), "trailing_stop_gap")


def test_short_fixed_path_is_unchanged() -> None:
    context = fixture(side=-1)
    value = prepared(context)
    serial = high_r.replay_serial(value, peak_long=False)[0]
    short = serial.loc[serial.side.eq(-1)].iloc[0]
    expected = high_r.replay_fixed_entry(value, short, peak_long=False)
    actual = high_r.replay_fixed_entry(value, short, peak_long=True)
    for key in engine.KEY:
        assert actual[key] == pytest.approx(expected[key], nan_ok=True) if isinstance(actual[key], float) else actual[key] == expected[key]


def test_fixed_and_serial_agree_for_shared_peak_entries_and_prefix_is_invariant() -> None:
    context, value = single_long()
    serial = high_r.replay_serial(value)[0]
    trade = serial.iloc[0]
    fixed = high_r.replay_fixed_entry(value, trade)
    for key in engine.KEY:
        assert fixed[key] == pytest.approx(trade[key], nan_ok=True) if isinstance(fixed[key], float) else fixed[key] == trade[key]
    future_cache = {key: (item.copy(deep=True) if isinstance(item, (pd.DataFrame, pd.Series)) else item)
                    for key, item in context.cache.items()}
    future = replace(context, cache=future_cache)
    future.cache["bars"].iloc[9:, future.cache["bars"].columns.get_indexer(["open", "high", "low", "close"])] = [1000., 1002., 999., 1001.]
    assert_valid_ohlc(future)
    future_trades, _, future_events = high_r.replay_serial(prepared(future))
    prefix_trade = future_trades.iloc[0]
    for key in engine.KEY:
        assert prefix_trade[key] == pytest.approx(trade[key], nan_ok=True) if isinstance(prefix_trade[key], float) else prefix_trade[key] == trade[key]
    pd.testing.assert_frame_equal(high_r.replay_serial(value)[2], future_events)


def test_peak_policy_equals_parent_when_high_equals_close_on_a_valid_long_path() -> None:
    context, value = single_long()
    bars = context.cache["bars"]
    bars.iloc[5, bars.columns.get_indexer(["open", "high", "low", "close"])] = [100., 100.5, 99., 100.5]
    bars.iloc[6, bars.columns.get_indexer(["open", "high", "low", "close"])] = [100.5, 101., 100., 101.]
    bars.iloc[7, bars.columns.get_indexer(["open", "high", "low", "close"])] = [101., 104., 101., 104.]
    bars.iloc[8, bars.columns.get_indexer(["open", "high", "low", "close"])] = [104., 104., 101., 102.]
    assert_valid_ohlc(context)
    value = prepared(context)
    peak, _, _ = high_r.replay_serial(value)
    baseline, _, _ = high_r.replay_serial(value, peak_long=False)
    pd.testing.assert_frame_equal(peak[engine.KEY], baseline[engine.KEY], check_dtype=False)


def test_source_cache_is_not_mutated_and_baseline_control_selection_is_identical() -> None:
    context = fixture()
    before = {key: item.copy(deep=True) for key, item in context.cache.items() if isinstance(item, (pd.DataFrame, pd.Series))}
    value = prepared(context)
    targets = high_r.replay_serial(value, peak_long=False)[0].assign(arm="v9")
    high_r.replay_serial(value, peak_long=True)
    expected = random_controls(value, targets)
    actual = high_r.matched_controls(value, targets, peak_long=False)
    pd.testing.assert_frame_equal(actual, expected)
    for key, original in before.items():
        pd.testing.assert_frame_equal(context.cache[key], original) if isinstance(original, pd.DataFrame) else pd.testing.assert_series_equal(context.cache[key], original)
