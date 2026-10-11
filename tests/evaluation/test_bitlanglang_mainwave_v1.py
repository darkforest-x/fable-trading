"""Mechanics of exp-bitlanglang-mainwave-20261011-v1."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import bitlanglang_mainwave_v1 as bm

DAY = bm.DAY_MS


def test_config_matches_the_builder_constants():
    cfg = json.loads(bm.CONFIG.read_text())
    assert cfg["rule"]["round_trip_cost"] == bm.COST == 0.002
    assert cfg["universe_size"] == 100 and cfg["select_before"].startswith("2025-01-01")
    assert (bm.BOX_DAYS, bm.BOX_MAX_ATR, bm.VOL_DAYS, bm.VOL_MULT) == (14, 4.0, 7, 3.0)
    assert (bm.PULLBACK, bm.SETUP_DAYS, bm.MAX_ATTEMPTS, bm.STOP_PAD, bm.MIN_PLAN_R) == (0.03, 10, 3, 0.0075, 3.0)
    assert (bm.LOW_LOOKBACK, bm.HOLD_BARS, bm.TRIGGER_EXPIRY) == (48, 3, 48)
    assert bm.MAX_HOLD == 30 * 288 and bm.N_CONTROLS == 20


def bars_from(lows):
    """5m bars around given lows: open/close inside, high = low + 1 unless overridden later."""
    l = np.asarray(lows, float)
    o = l + 0.5
    c = l + 0.4
    h = l + 1.0
    return o, h, l, c


def test_turn_signal_needs_three_holding_bars_then_a_bullish_close_above_the_prior_high():
    lows = [100.0] * 50 + [90.0, 91, 91, 91, 91, 91, 91]
    o, h, l, c = bars_from(lows)
    # bar 54 is bearish; bar 55 bullish but closes below bar 54's high; bar 56 triggers
    o[55], c[55] = 91.2, 91.9   # high of 54 is 92
    o[56], c[56], h[56] = 91.2, 92.5, 92.6
    o = np.append(o, 92.5); h = np.append(h, 93); l = np.append(l, 92); c = np.append(c, 92.8)
    sig = bm.turn_signals(o, h, l, c)
    row = sig.loc[sig.low_i == 50]
    assert len(row) == 1 and int(row.trig.iat[0]) == 56 and row.low.iat[0] == 90.0


def test_turn_signal_is_cancelled_by_a_lower_low_and_by_an_early_bullish_bar():
    lows = [100.0] * 50 + [90.0, 91, 89.5, 91, 91, 91, 91, 91]
    o, h, l, c = bars_from(lows)
    o[57], c[57], h[57] = 91.2, 92.5, 92.6
    o = np.append(o, 92.5); h = np.append(h, 93); l = np.append(l, 92); c = np.append(c, 92.8)
    sig = bm.turn_signals(o, h, l, c)
    assert 50 not in set(sig.low_i)            # 89.5 broke it inside the 3 holding bars
    assert set(sig.low_i) == {52} and int(sig.trig.iat[0]) == 57
    # a bullish breakout bar inside the 3 holding bars does not count
    lows = [100.0] * 50 + [90.0, 91, 91, 91, 91]
    o, h, l, c = bars_from(lows)
    o[51], c[51], h[51] = 91.0, 93.0, 93.5
    o = np.append(o, [91.0, 92.0, 94.0]); h = np.append(h, [92.0, 94.0, 95.0])
    l = np.append(l, [91.0, 91.5, 93.5]); c = np.append(c, [91.5, 93.9, 94.5])  # last bar = entry bar
    sig = bm.turn_signals(o, h, l, c)
    assert int(sig.loc[sig.low_i == 50].trig.iat[0]) == 56


def sim(o, h, l, c, stop, tp1, tp2):
    return bm.simulate(np.array(o, float), np.array(h, float), np.array(l, float), np.array(c, float),
                       0, stop, tp1, tp2, cap=10)


def test_exit_paths_half_at_tp1_then_break_even():
    # entry 100, stop 98 (risk 2), tp1 104, tp2 110
    stop = sim([100, 101], [101, 102], [99, 97], [100, 97.5], 98, 104, 110)
    assert stop["kind"] == "stop" and stop["gross_r"] == pytest.approx(-1.0)
    assert stop["net_r"] == pytest.approx(-1.0 - 0.002 / 0.02)
    same_bar = sim([100, 100], [101, 105], [99, 97], [100, 100], 98, 104, 110)
    assert same_bar["kind"] == "stop"          # stop first when both print in one bar
    be = sim([100, 101, 103, 101], [101, 104.5, 104, 102], [99, 100.5, 101, 99.5], [100.5, 103, 102, 100], 98, 104, 110)
    assert be["kind"] == "tp1_be" and be["gross_r"] == pytest.approx(0.5 * 4 / 2)
    both = sim([100, 101, 105, 109], [101, 104.5, 108, 111], [99, 100.5, 104, 108], [100.5, 104, 107, 110], 98, 104, 110)
    assert both["kind"] == "tp2" and both["tp2_hit"] and both["gross_r"] == pytest.approx(0.5 * 2 + 0.5 * 5)
    gap = sim([100, 96], [101, 97], [99, 95], [99, 96], 98, 104, 110)
    assert gap["gross_r"] == pytest.approx(-2.0)  # filled at the 96 open
    timeout = sim([100] * 12, [101] * 12, [99] * 12, [100.6] * 12, 98, 104, 110)
    assert timeout["kind"] == "timeout" and timeout["bars"] == 10 and timeout["gross"] == pytest.approx(0.006)


def test_targets_use_pivots_first_then_round_numbers():
    assert bm.targets(np.array([99.0, 105.0, 105.5, 130.0]), 100.0) == (105.0, 130.0)
    assert bm.targets(np.array([105.0]), 100.0) == (105.0, 200.0)
    assert bm.targets(np.array([]), 0.0276) == pytest.approx((0.03, 0.04))
    assert bm.round_levels(36231.0, 37500.0, 2) == [40000.0, 50000.0]


def daily_frame(n=40, seed=0):
    rng = np.random.default_rng(seed)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    d = pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3, "close": close,
                      "volume": np.full(n, 1000.0), "qv": close * 1000, "frozen": 0.0},
                     index=np.arange(n) * DAY)
    return d


def test_daily_box_and_breakout_do_not_look_ahead():
    d = daily_frame()
    d.loc[30 * DAY, ["close", "high", "volume"]] = [d.high.iloc[16:30].max() + 0.5, d.high.iloc[16:30].max() + 0.6, 5000.0]
    f = bm.daily_features(d)
    assert f.at[30 * DAY, "breakout"]
    later = d.copy()
    later.loc[31 * DAY:, ["high", "low", "close", "volume"]] *= 3
    g = bm.daily_features(later)
    cols = ["top", "bottom", "height_atr", "vol_ratio", "breakout"]
    pd.testing.assert_frame_equal(f.loc[:30 * DAY, cols], g.loc[:30 * DAY, cols])


def test_resistance_uses_only_pivots_confirmed_by_the_breakout_day():
    d = daily_frame(60)
    d.loc[20 * DAY, "high"] = 20.0   # confirmed pivot
    d.loc[47 * DAY, "high"] = 30.0   # pivot whose right side ends after day 50
    f = bm.daily_features(d)
    levels = bm.resistance_levels(f, 50 * DAY)
    assert 20.0 in levels and 30.0 not in levels
    assert 30.0 in bm.resistance_levels(f, 52 * DAY)
