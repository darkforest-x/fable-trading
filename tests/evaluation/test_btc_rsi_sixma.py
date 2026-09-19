"""Timing and fill contracts for the frozen BTC RSI/6MA offline replay."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.btc_rsi_sixma import COST, prepare, simulate, trace_trade


def _frame(count: int = 72) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=count, freq="5min", tz="UTC")
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}, index=index)


def _ctx(count: int = 72):
    ctx = prepare(_frame(count))
    # The indicator's native diamonds are deliberately not part of execution
    # unit tests.  Each test supplies a one-hour event and exercises the fixed
    # timing/fill contract after prepare has verified the 5m grid.
    ctx["diamond_side"][:] = 0
    ctx["diamond_stop"][:] = np.nan
    ctx["sixma_long"][:] = False
    ctx["sixma_short"][:] = False
    return ctx


def _diamond(ctx, hour_i: int, side: int, stop: float) -> int:
    ctx["diamond_side"][hour_i] = side
    ctx["diamond_stop"][hour_i] = stop
    return int(ctx["diamond_frame_i"][hour_i])


def _window(ctx):
    return ctx["frame"].index[0], ctx["frame"].index[-1] + pd.Timedelta(minutes=5)


def test_htf_diamond_is_visible_only_at_hour_close_then_enters_next_open() -> None:
    ctx = _ctx()
    # Hour 0 is rows 0..11, so the low that defines this diamond's stop is 90.
    ctx["frame"].iloc[:12, ctx["frame"].columns.get_loc("low")] = 90.0
    ctx["low"][:12] = 90.0
    first = _diamond(ctx, 0, 1, 90.0)
    assert first == 12
    ctx["sixma_long"][first] = True
    ctx["high"][first + 1] = 131.0
    ctx["frame"].iloc[first + 1, ctx["frame"].columns.get_loc("high")] = 131.0

    start, end = _window(ctx)
    result = simulate(ctx, start, end)
    event, trade = result["events"].iloc[0], result["trades"].iloc[0]
    assert event.hourly_close_time == ctx["frame"].index[12]
    assert event.confirm_bar_open_time == ctx["frame"].index[12]
    assert event.confirm_time == ctx["frame"].index[13]
    assert trade.entry_i == 13 and trade.entry_time == ctx["frame"].index[13]
    assert trade.stop_price == 90.0 and trade.target_price == 130.0


def test_confirmation_bar_stop_touch_cancels_pending_original_hourly_stop() -> None:
    ctx = _ctx()
    first = _diamond(ctx, 0, 1, 95.0)
    ctx["sixma_long"][first] = True
    ctx["low"][first] = 95.0
    ctx["frame"].iloc[first, ctx["frame"].columns.get_loc("low")] = 95.0
    result = simulate(ctx, *_window(ctx))
    assert result["trades"].empty
    assert result["events"].iloc[0].status == "cancelled_stop"


def test_trace_stop_first_for_dual_touch_and_gap_and_entry_cost() -> None:
    ctx = _ctx()
    entry_i = 13
    ctx["high"][entry_i] = 115.0; ctx["low"][entry_i] = 95.0
    ctx["frame"].iloc[entry_i, ctx["frame"].columns.get_loc("high")] = 115.0
    ctx["frame"].iloc[entry_i, ctx["frame"].columns.get_loc("low")] = 95.0
    dual = trace_trade(ctx, entry_i, 1, 95.0)
    assert (dual["reason"], dual["exit_price"], dual["dual_touch"]) == ("stop", 95.0, True)

    ctx = _ctx()
    ctx["open"][14] = 94.0
    ctx["frame"].iloc[14, ctx["frame"].columns.get_loc("open")] = 94.0
    gap = trace_trade(ctx, 13, 1, 95.0)
    assert (gap["reason"], gap["exit_i"], gap["exit_price"]) == ("stop_gap", 14, 94.0)

    ctx = _ctx()
    ctx["high"][13] = 115.0
    ctx["frame"].iloc[13, ctx["frame"].columns.get_loc("high")] = 115.0
    target = trace_trade(ctx, 13, 1, 95.0)
    assert target["gross_return"] == pytest.approx(.15)
    assert target["net_return"] == pytest.approx(.15 - COST)
    assert target["gross_r"] == pytest.approx(3.0)
    assert target["net_r"] == pytest.approx((.15 - COST) / .05)

    ctx = _ctx()
    ctx["low"][13] = 85.0
    ctx["frame"].iloc[13, ctx["frame"].columns.get_loc("low")] = 85.0
    short = trace_trade(ctx, 13, -1, 105.0)
    assert (short["reason"], short["exit_price"], short["gross_r"]) == ("target", 85.0, 3.0)
    ctx["open"][13] = 105.0
    ctx["frame"].iloc[13, ctx["frame"].columns.get_loc("open")] = 105.0
    assert trace_trade(ctx, 13, -1, 105.0)["reason"] == "invalid_entry_gap"


def test_sixma_uses_close_mas_and_is_strict_about_open_and_close() -> None:
    frame = _frame(144)
    close = np.arange(len(frame), dtype=float) + 100.0
    frame["close"] = close
    frame["open"] = close + 1.0
    frame["high"] = close + 2.0
    frame["low"] = close - 1.0
    prepared = prepare(frame)
    assert prepared["sixma_long"][-1]
    # The same close-derived MAs are unchanged by an OHLC high mutation.
    high_changed = frame.copy(); high_changed["high"] += 500.0
    np.testing.assert_allclose(prepared["ma"]["sma20"], prepare(high_changed)["ma"]["sma20"], equal_nan=True)
    equal = frame.copy()
    equal.iloc[-1, equal.columns.get_loc("open")] = prepared["ma"]["sma20"][-1]
    equal.iloc[-1, equal.columns.get_loc("low")] = prepared["ma"]["sma20"][-1] - 1.0
    assert not prepare(equal)["sixma_long"][-1]


def test_serial_position_skips_later_diamond_and_direct_has_no_confirmation_gate() -> None:
    ctx = _ctx()
    first, second = _diamond(ctx, 0, 1, 95.0), _diamond(ctx, 1, -1, 105.0)
    ctx["sixma_long"][first] = True
    # Keep the first position alive across the second diamond, then hit 3R.
    ctx["high"][31] = 115.0
    ctx["frame"].iloc[31, ctx["frame"].columns.get_loc("high")] = 115.0
    serial = simulate(ctx, *_window(ctx))
    assert len(serial["trades"]) == 1
    assert serial["events"].iloc[1].status == "occupied_skip"

    direct_ctx = _ctx()
    first = _diamond(direct_ctx, 0, 1, 95.0)
    assert first == 12 and not direct_ctx["sixma_long"][first]
    direct_ctx["high"][13] = 115.0
    direct_ctx["frame"].iloc[13, direct_ctx["frame"].columns.get_loc("high")] = 115.0
    direct = simulate(direct_ctx, *_window(direct_ctx), arm="direct")
    assert direct["trades"].iloc[0].entry_i == 12


def test_new_strong_diamond_replaces_an_unconfirmed_pending_event() -> None:
    ctx = _ctx()
    first, second = _diamond(ctx, 0, 1, 95.0), _diamond(ctx, 1, -1, 105.0)
    assert (first, second) == (12, 24)
    ctx["sixma_short"][second] = True
    ctx["low"][second + 1] = 85.0
    ctx["frame"].iloc[second + 1, ctx["frame"].columns.get_loc("low")] = 85.0
    result = simulate(ctx, *_window(ctx))
    assert result["events"].iloc[0].status == "replaced"
    assert result["trades"].iloc[0].side == -1


def test_terminal_position_stays_open_in_separate_table() -> None:
    ctx = _ctx()
    first = _diamond(ctx, 0, 1, 95.0)
    ctx["sixma_long"][first] = True
    result = simulate(ctx, *_window(ctx))
    assert result["trades"].empty and len(result["open_positions"]) == 1
    assert result["open_positions"].iloc[0].reason == "open"


def test_prepare_is_prefix_causal_and_refuses_gapped_input() -> None:
    frame = _frame(8664)
    clean = prepare(frame)
    changed = frame.copy()
    cutoff = 8604  # exact 1h boundary: no mutated source hourly candle before it
    changed.iloc[cutoff:, changed.columns.get_loc("high")] += 100
    changed.iloc[cutoff:, changed.columns.get_loc("close")] += 50
    later = prepare(changed)
    np.testing.assert_allclose(clean["volatility"][:cutoff], later["volatility"][:cutoff], equal_nan=True)
    np.testing.assert_array_equal(clean["vol_bucket"][:cutoff], later["vol_bucket"][:cutoff])
    for name in clean["ma"]:
        np.testing.assert_allclose(clean["ma"][name][:cutoff], later["ma"][name][:cutoff], equal_nan=True)
    hour_cutoff = cutoff // 12
    np.testing.assert_allclose(clean["rsi"][:hour_cutoff], later["rsi"][:hour_cutoff], equal_nan=True)
    np.testing.assert_allclose(clean["sar"][:hour_cutoff], later["sar"][:hour_cutoff], equal_nan=True)
    with pytest.raises(ValueError, match="continuous"):
        prepare(frame.drop(frame.index[20]))
