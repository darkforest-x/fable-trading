"""Focused tests for the trendline V2 port, its barriers and its controls.

The expensive mistakes this file is trying to make impossible:

  * a signal that reads a bar it could not have seen (rule 3),
  * a barrier replay that pays the good side of an ambiguous bar,
  * an accounting ledger where net R and net cash disagree,
  * a control draw that depends on how the loop was chunked.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.trendline_v2_signals import (
    TrendlineParams, confirmed_pivot_highs, detect, pine_atr,
)
from yoyo.evaluation.trendline_v2_strategy import (
    FEE_PER_SIDE, barrier_outcomes, causal_volatility_bucket, draw_control_indices,
    month_block_signflip, serial_path,
)


# --------------------------------------------------------------------------- ATR
def test_pine_atr_seeds_with_a_simple_mean_and_then_rolls():
    high = np.arange(1, 21, dtype=float) + 1.0
    low = np.arange(1, 21, dtype=float)
    close = low + 0.5
    atr = pine_atr(high, low, close, 14)
    assert np.all(np.isnan(atr[:13])), "no ATR before the window is full"
    tr = np.empty(20)
    tr[0] = high[0] - low[0]
    tr[1:] = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    assert atr[13] == pytest.approx(tr[:14].mean())
    assert atr[14] == pytest.approx(atr[13] + (tr[14] - atr[13]) / 14)


def test_pine_atr_is_causal():
    rng = np.random.default_rng(0)
    low = np.cumsum(rng.normal(size=200)) + 100
    high, close = low + 1.0, low + 0.5
    full = pine_atr(high, low, close, 14)
    cut = pine_atr(high[:120], low[:120], close[:120], 14)
    assert np.allclose(full[:120], cut, equal_nan=True)


# ------------------------------------------------------------------------- pivots
def test_pivot_is_reported_on_its_confirmation_bar_not_on_itself():
    high = np.array([1, 2, 3, 9, 3, 2, 1, 0.5, 0.4], dtype=float)
    pivot_of, ties = confirmed_pivot_highs(high, left=3, right=3)
    assert pivot_of[6] == 3, "bar 3 is the pivot, confirmed three bars later"
    assert pivot_of[3] == -1, "the pivot bar itself cannot confirm it"
    assert ties == 0


def test_a_tie_is_counted_and_not_admitted():
    high = np.array([1, 2, 9, 9, 3, 2, 1], dtype=float)
    pivot_of, ties = confirmed_pivot_highs(high, left=2, right=2)
    assert not (pivot_of >= 0).any(), "equal neighbours do not make a strict pivot"
    assert ties >= 1, "the ambiguity is reported rather than hidden"


# ------------------------------------------------------------------------- detect
def _descending_highs_then_breakout(n=400):
    """Two lower highs that a line can span, then a close that clears it."""
    close = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    for bar, peak in ((60, 130.0), (200, 118.0)):
        high[bar] = peak
        close[bar] = peak - 1
        low[bar] = peak - 2
    high[330:] = 125.0
    close[330:] = 124.0
    low[330:] = 120.0
    return high, low, close


def test_detect_draws_a_line_and_breaks_it_upward():
    high, low, close = _descending_highs_then_breakout()
    atr = np.full(len(close), 1.0)
    out = detect(high, low, close, atr, tick=0.01,
                 params=TrendlineParams(min_touches=2, lookback=600))
    assert out.lines, "a descending pair of pivots should produce a line"
    born = out.lines[0]["born_i"]
    assert born >= 200 + 8, "a line cannot exist before its right anchor is confirmed"
    assert out.break_event.any()
    assert np.flatnonzero(out.break_event)[0] >= 330, "the break follows the rally"


def test_detect_never_reads_a_future_bar():
    """Mutating everything after bar t must not move any signal at or before t."""
    high, low, close = _descending_highs_then_breakout()
    atr = pine_atr(high, low, close, 14)
    base = detect(high, low, close, atr, tick=0.01)
    cut = 350
    high2, low2, close2 = high.copy(), low.copy(), close.copy()
    high2[cut:] += 50.0
    low2[cut:] += 50.0
    close2[cut:] += 50.0
    atr2 = pine_atr(high2, low2, close2, 14)
    other = detect(high2, low2, close2, atr2, tick=0.01)
    assert np.array_equal(base.break_event[:cut], other.break_event[:cut])
    assert np.array_equal(base.born_event[:cut], other.born_event[:cut])


def _two_peaks(n, first, second):
    close = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    for bar, peak in ((60, first), (200, second)):
        high[bar] = peak
        close[bar] = peak - 1
        low[bar] = peak - 2
    return high, low, close


def test_expiry_retires_a_line_without_reporting_a_break():
    """An old line must leave quietly; "to expire" is not "to be broken".

    The slope here is shallow enough that the line is still far above price
    when it ages out, which is the only way to isolate expiry from the descent
    covered by the next test.
    """
    high, low, close = _two_peaks(1400, 130.0, 128.0)
    atr = np.full(1400, 1.0)
    out = detect(high, low, close, atr, tick=0.01, params=TrendlineParams(lookback=600))
    assert out.lines, "the line is drawn"
    assert not out.break_event.any(), "price never rose, so nothing was broken"
    assert not out.line_active[-1], "the line aged out instead of persisting forever"


def test_a_steep_line_descending_into_flat_price_still_fires_a_break():
    """The signal is "close above the line", not "price rallied".

    A line steep enough to reach a flat market produces a break with no upward
    move at all. This is the pasted indicator's own behaviour, not a porting
    artefact, and it is the reason the report treats the raw break count as a
    count of crossings rather than a count of breakouts.
    """
    high, low, close = _two_peaks(1400, 130.0, 118.0)
    atr = np.full(1400, 1.0)
    out = detect(high, low, close, atr, tick=0.01, params=TrendlineParams(lookback=600))
    fired = np.flatnonzero(out.break_event)
    assert len(fired) == 1
    assert close[fired[0]] == pytest.approx(100.0), "price is exactly where it started"


# ------------------------------------------------------------------------ barriers
def _flat_bars(n=50, price=100.0):
    opens = np.full(n, price)
    high = np.full(n, price)
    low = np.full(n, price)
    close = np.full(n, price)
    return opens, high, low, close


def test_target_and_stop_and_time_each_produce_their_own_exit():
    opens, high, low, close = _flat_bars(60)
    high[5] = 110.0          # target for the first signal
    low[8] = 90.0            # stop, later, so the target wins
    out = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                           sl_mult=1.0, tp_mult=2.0, max_hold=20, path="adverse_first")
    assert out["reason"][0] == "target"
    assert out["exit_price"][0] == pytest.approx(104.0)

    opens, high, low, close = _flat_bars(60)
    low[3] = 90.0
    out = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                           sl_mult=1.0, tp_mult=2.0, max_hold=20, path="adverse_first")
    assert out["reason"][0] == "stop"
    assert out["exit_price"][0] == pytest.approx(98.0)

    opens, high, low, close = _flat_bars(60)
    out = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                           sl_mult=1.0, tp_mult=2.0, max_hold=20, path="adverse_first")
    assert out["reason"][0] == "time"
    assert out["exit_i"][0] == 20, "entry bar 1 plus twenty held bars"


def test_a_bar_touching_both_barriers_is_a_loss_under_adverse_first():
    opens, high, low, close = _flat_bars(60)
    high[4], low[4] = 110.0, 90.0
    bad = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                           sl_mult=1.0, tp_mult=2.0, max_hold=20, path="adverse_first")
    good = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                            sl_mult=1.0, tp_mult=2.0, max_hold=20, path="favorable_first")
    assert bad["reason"][0] == "stop" and good["reason"][0] == "target"
    assert bad["net_r"][0] < 0 < good["net_r"][0]


def test_a_gap_through_the_stop_fills_at_the_gap_not_at_the_stop():
    opens, high, low, close = _flat_bars(60)
    opens[4] = high[4] = 80.0
    low[4] = close[4] = 79.0
    out = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                           sl_mult=1.0, tp_mult=2.0, max_hold=20, path="adverse_first")
    assert out["exit_price"][0] == pytest.approx(80.0), "worse than the 98.0 stop"


def test_the_cash_and_R_ledgers_agree_and_the_round_trip_is_two_tenths_of_a_percent():
    opens, high, low, close = _flat_bars(60)
    high[5] = 110.0
    out = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                           sl_mult=1.0, tp_mult=2.0, max_hold=20, path="adverse_first")
    assert out["net_pnl"][0] == pytest.approx(out["gross_pnl"][0] - out["fees"][0])
    assert out["net_r"][0] == pytest.approx(out["net_pnl"][0] / out["risk"][0])
    assert out["fees"][0] == pytest.approx((out["entry"][0] + out["exit_price"][0]) * FEE_PER_SIDE)
    assert FEE_PER_SIDE * 2 == pytest.approx(0.002)


def test_entry_is_the_next_bar_open_never_the_signal_close():
    opens, high, low, close = _flat_bars(60)
    opens[1] = 101.0
    out = barrier_outcomes(opens, high, low, close, np.array([0]), np.array([2.0]),
                           sl_mult=1.0, tp_mult=2.0, max_hold=20, path="adverse_first")
    assert out["entry_i"][0] == 1 and out["entry"][0] == pytest.approx(101.0)


# ------------------------------------------------------------------------ controls
def _pool(n=600):
    months = np.array([f"2024-{1 + (i * 12) // n:02d}" for i in range(n)])
    return np.arange(n), months, np.tile([0, 1, 2], n // 3)


def test_controls_share_the_month_and_the_bucket_of_their_signal():
    pool_i, pool_month, pool_bucket = _pool()
    signal_i = np.array([30, 300])
    times = pd.DatetimeIndex(["2024-01-05T00:00:00Z", "2024-07-05T00:00:00Z"])
    owners, controls, unmatched = draw_control_indices(
        "BTC", times, signal_i, pool_i, pool_month, pool_bucket,
        pool_month[signal_i], pool_bucket[signal_i], 5, "seed")
    assert unmatched == 0 and len(controls) == 10
    for owner, control in zip(owners, controls):
        assert pool_month[control] == pool_month[owner]
        assert pool_bucket[control] == pool_bucket[owner]
        assert control != owner, "a signal is not its own control"


def test_the_draw_depends_on_the_signal_not_on_the_iteration_order():
    pool_i, pool_month, pool_bucket = _pool()
    times = pd.DatetimeIndex(["2024-01-05T00:00:00Z", "2024-07-05T00:00:00Z"])
    both = draw_control_indices("BTC", times, np.array([30, 300]), pool_i, pool_month,
                                pool_bucket, pool_month[[30, 300]], pool_bucket[[30, 300]],
                                5, "seed")
    alone = draw_control_indices("BTC", times[1:], np.array([300]), pool_i, pool_month,
                                 pool_bucket, pool_month[[300]], pool_bucket[[300]], 5, "seed")
    assert np.array_equal(both[1][5:], alone[1])


def test_a_stratum_too_small_drops_the_signal_instead_of_widening():
    pool_i = np.array([0, 1])
    pool_month = np.array(["2024-01", "2024-01"])
    pool_bucket = np.array([0, 0])
    owners, controls, unmatched = draw_control_indices(
        "BTC", pd.DatetimeIndex(["2024-01-05T00:00:00Z"]), np.array([0]),
        pool_i, pool_month, pool_bucket, np.array(["2024-01"]), np.array([0]), 5, "seed")
    assert unmatched == 1 and not len(owners) and not len(controls)


# ------------------------------------------------------------------- volatility
def test_the_volatility_bucket_never_looks_at_its_own_bar_or_later():
    rng = np.random.default_rng(3)
    close = pd.Series(np.cumsum(rng.normal(size=400)) + 500)
    frame = pd.DataFrame({"high": close + 1, "low": close - 1, "close": close})
    bucket, defined = causal_volatility_bucket(frame)
    later = frame.copy()
    later.loc[300:, ["high", "low", "close"]] += 100
    bucket2, defined2 = causal_volatility_bucket(later)
    assert np.array_equal(bucket[:300], bucket2[:300])
    assert np.array_equal(defined[:300], defined2[:300])
    assert not defined[:133].any(), "no bucket before SMA14 and 120 prior values exist"


# ------------------------------------------------------------------ permutation
def test_month_blocks_flip_together_and_the_test_is_one_sided():
    month = np.array(["2024-01"] * 10 + ["2024-02"] * 10)
    excess = np.r_[np.full(10, 1.0), np.full(10, 1.0)]
    result = month_block_signflip(month, excess, seed=1)
    assert result["months"] == 2
    # Two blocks, four sign assignments, only (+,+) reaches the observed sum.
    assert result["p"] == pytest.approx(0.25)


def test_one_dominant_month_cannot_reach_significance_alone():
    month = np.array([f"2024-{m:02d}" for m in range(1, 7)])
    excess = np.array([10.0, -0.1, -0.1, -0.1, -0.1, -0.1])
    result = month_block_signflip(month, excess, seed=1)
    assert result["p"] > 0.01, "one month is one observation, not six"


# ---------------------------------------------------------------------- serial
def test_serial_skips_a_signal_that_opens_before_the_previous_exit():
    signal_i = np.array([0, 5, 30])
    exit_i = np.array([20, 25, 40])
    net_r = np.array([1.0, -1.0, 2.0])
    out = serial_path(signal_i, exit_i, net_r, net_r * 100)
    assert out["n"] == 2 and out["skipped"] == 1
    assert out["net_r"] == pytest.approx(3.0)


def test_serial_reports_the_drawdown_of_the_path_it_actually_took():
    signal_i = np.array([0, 10, 20])
    exit_i = np.array([5, 15, 25])
    net_r = np.array([-2.0, -1.0, 1.0])
    out = serial_path(signal_i, exit_i, net_r, net_r * 100)
    assert out["max_drawdown_r"] == pytest.approx(3.0)
    assert out["max_loss_streak"] == 2
