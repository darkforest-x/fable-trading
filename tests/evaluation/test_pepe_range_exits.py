"""Causal replay contracts and prior native TradingView baseline parity."""
import json
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.pepe_range_exits import prepare, replay, scalar, stop_events, entries, signals
from yoyo.evaluation.monday_range_entry_diagnostic import load


def context(bars, atr=None, hour=None):
    return dict(bars=np.ascontiguousarray(bars, dtype=float),
                atr=np.array(atr if atr is not None else [np.nan]*len(bars), dtype=float),
                hour=np.array(hour if hour is not None else [0]*len(bars), dtype=float))


@pytest.mark.parametrize("side", [1, -1])
def test_entry_bar_wick_does_not_stop_new_entry(side):
    ctx = context([[100, 200, 1, 100], [100, 100.5, 99.5, 100]])
    result = replay(ctx, [[0, side, 100, 100-side, 1]], tick=.01)[0]
    assert result[2] == 0 and result[1] == 100


def test_dual_touch_stop_first_but_opening_target_gap_is_first():
    ctx = context([[100, 100, 100, 100], [100, 103, 98, 102]])
    z = replay(ctx, [[0, 1, 100, 99, 1]], target=2, tick=.01)[0]
    assert tuple(z[[1, 2, 6]]) == (99, 1, 1)
    ctx["bars"][1, 0] = 103
    z = replay(ctx, [[0, 1, 100, 99, 1]], target=2, tick=.01)[0]
    assert tuple(z[[1, 2, 6]]) == (102, 4, 0)


def test_old_stop_wins_over_close_ratchet_and_hour_exit():
    ctx = context([[100, 100, 100, 100], [100, 106, 99.5, 105], [105, 110, 100, 109]],
                  [np.nan, 1, 1], [0, 0, -1])
    z = replay(ctx, [[0, 1, 100, 99, 2]], tick=.01)[0]
    assert z[1] == 101 and z[2] == 2 and z[3] == 6


def test_same_close_arming_allows_hour_reversal_exit():
    ctx = context([[100, 100, 100, 100], [100, 106, 99.5, 105]],
                  [np.nan, 1], [0, -1])
    z = replay(ctx, [[0, 1, 100, 99, 1]], tick=.01)[0]
    assert tuple(z[[1, 2, 4]]) == (105, 3, 1)


def test_unarmed_hour_reversal_does_not_exit():
    ctx = context([[100, 100, 100, 100], [100, 103, 99.5, 102], [102, 103, 101, 102]],
                  [np.nan, 1, np.nan], [0, -1, 0])
    z = replay(ctx, [[0, 1, 100, 99, 2]], tick=.01)[0]
    assert z[2] == 0 and z[4] == -1


def test_segment_right_edge_hides_future_outcomes():
    ctx = context([[100, 100, 100, 100], [100, 101, 99.5, 100.5], [100, 200, 1, 150]])
    z = replay(ctx, [[0, 1, 100, 99, 1]], target=20, tick=.01)[0]
    assert z[2] == 0 and z[1] == 100.5 and z[7] == 1


def test_gap_stop_uses_worse_open():
    ctx = context([[100, 100, 100, 100], [97, 100, 96, 99]])
    z = replay(ctx, [[0, 1, 100, 99, 1]], tick=.01)[0]
    assert z[1] == 97 and z[2] == 1


def test_tick_touch_is_not_lost_to_binary_float_representation():
    stop = 2847*1e-9
    ctx = context([[2.9e-6]*4, [2.88e-6, 2.89e-6, 2.847e-6, 2.85e-6]])
    z = replay(ctx, [[0, 1, 2.9e-6, stop, 1]])[0]
    assert z[2] == 1


def test_mathematical_k_80_is_not_strictly_overbought():
    # Registered OKX 15m Aug04 2026 12:00--16:15 UTC, in 1e-9 price units.
    bars = [[2908,2912,2905,2906],[2906,2907,2898,2902],
            [2902,2907,2900,2906],[2907,2917,2906,2911],
            [2911,2911,2899,2901],[2901,2901,2886,2890],
            [2889,2899,2888,2894],[2893,2907,2891,2904],
            [2904,2904,2882,2888],[2887,2888,2868,2883],
            [2882,2884,2865,2875],[2875,2889,2873,2885],
            [2884,2890,2882,2885],[2886,2895,2885,2893],
            [2893,2895,2877,2883],[2883,2908,2883,2907],
            [2907,2912,2902,2903],[2903,2906,2897,2901]]
    f = pd.DataFrame(np.array(bars)*1e-9, columns=["open","high","low","close"])
    assert signals(f).iloc[-1] == 0


def test_native_recent_ten_trades_match_existing_tradingview_evidence():
    five, quarter, hour = load("5m", 5), load("15m", 15), load("1H", 60)
    ctx = prepare(five, quarter, hour)
    refs = json.loads(open("output/pine/monday_range_v2_7_reference.json").read())["trades"]
    indices = np.flatnonzero(ctx["signal"])
    assert len(indices) == len(refs) == 10
    source = pd.DataFrame(dict(entry_i=indices, side=ctx["signal"][indices], end_i=len(five)-1))
    base, matrix = stop_events(ctx, source, 5, .05, 1e-9)
    got = replay(ctx, matrix)
    for row, record, result in zip(base.itertuples(), refs, got):
        assert five.index[row.entry_i].value//1_000_000 == record["entered"]
        assert row.side == record["side"]
        assert row.entry == pytest.approx(record["entry"], abs=1e-15)
        assert row.stop == pytest.approx(record["initial_stop"], abs=1e-15)
        assert result[1] == pytest.approx(record["exit_price"], abs=1e-15)
        assert result[2] == record["reason"]
        assert result[3] == pytest.approx(record["peak_r"], abs=1e-7)
        assert result[5] == pytest.approx(record["protection"], abs=1e-15)
        assert five.index[int(result[0])].value//1_000_000 == record["ended"]


def test_features_and_admission_prefix_invariance():
    five = load("5m", 5)
    full = prepare(five)
    prefix = prepare(five.iloc[:1501])
    for key in ("signal", "observed", "hour", "atr"):
        np.testing.assert_allclose(full[key][:1501], prefix[key], equal_nan=True)


@pytest.mark.parametrize("side", [1, -1])
def test_randomized_native_scalar_paths(side):
    rng = np.random.default_rng(620)
    close = 100 + np.cumsum(rng.normal(0, .5, 300))
    opening = np.r_[100, close[:-1]]
    bars = np.c_[opening, np.maximum(opening, close)+rng.random(300),
                 np.minimum(opening, close)-rng.random(300), close]
    ctx = context(bars, np.where(np.arange(300)%3 == 0, 1., np.nan),
                  np.where(np.arange(300)%12 == 0, -side, 0))
    for target, arm, multiple in [(2, 0, 0), (20, 0, 0), (0, 1, 2), (0, 5, 4)]:
        for i in range(0, 270, 19):
            event = [i, side, close[i], close[i]-side*2, 299]
            result = replay(ctx, [event], target, arm, multiple, .01)[0]
            np.testing.assert_allclose(result, scalar(ctx, event, target, arm, multiple, .01), atol=1e-10)
