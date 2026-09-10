"""Synthetic next-open fills, stop precedence, cutoff and ratchet invariants."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_burst_execution import simulate_trade


def market(n=10, minutes=60):
    frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., atr=5.),
        index=pd.date_range("2026-01-01", periods=n, freq=f"{minutes}min", tz="UTC"))
    frame.attrs["minutes"] = minutes
    return frame


def sim(frame, i=4, tick=.1, end=None):
    end = end if end is not None else frame.index[-1]+pd.Timedelta(minutes=frame.attrs["minutes"])
    return simulate_trade(frame, i, tick, end)


def candle(frame, i, o, h, l, c, atr=None):
    frame.loc[frame.index[i], ["open", "high", "low", "close"]] = [o, h, l, c]
    if atr is not None:
        frame.loc[frame.index[i], "atr"] = atr


def test_initial_stop_uses_five_signal_lows_buffer_and_outward_tick():
    f = market()
    f.loc[f.index[0], "low"] = 1.  # Outside signal5's last-five window.
    f.loc[f.index[1], "low"] = 88.13
    out = sim(f, i=5, tick=.25)
    assert out["valid"] and out["initial_stop"] == 87.0
    assert out["initial_risk"] == 13.0


def test_atr_floor_signal_close_stop_is_frozen_but_fill_risk_is_actual_open():
    f = market()
    f.loc[f.index[5]:, ["open", "high", "low", "close", "atr"]] = [110., 114., 109., 112., .1]
    out = sim(f)
    assert out["entry_price"] == 110. and out["initial_stop"] == 90.
    assert out["initial_risk"] == 20.
    assert out["initial_risk_frac"] == pytest.approx(20/110)
    assert out["net_return"] == pytest.approx(112/110-1-.002)
    assert out["net_r"] == pytest.approx((2-.22)/20)
    assert not out["trail_armed"]


def test_entry_bar_stop_precedes_high_and_does_not_grow_peak():
    f = market()
    candle(f, 5, 100, 180, 89, 150)
    out = sim(f)
    assert out["exit_price"] == 90.
    assert out["exit_reason"] == "initial_stop"
    assert out["peak_r"] == 0.
    assert out["exit_timing"] == "intrabar_unknown"
    assert out["exit_time_lower"] == f.index[5]
    assert out["exit_time"] == f.index[6] > out["entry_time"]
    assert out["hold_bars"] == 1
    assert out["net_r"] == pytest.approx(-1.02)


def test_gap_fills_worse_observed_open_and_ignores_later_high():
    f = market()
    candle(f, 5, 100, 105, 99, 104)
    candle(f, 6, 85, 200, 80, 150)
    out = sim(f)
    assert out["exit_price"] == 85.
    assert out["exit_reason"] == "initial_stop_gap"
    assert out["exit_timing"] == "open"
    assert out["exit_time"] == f.index[6]
    assert out["peak_r"] == .5 and out["hold_bars"] == 1


def test_prices_after_natural_exit_cannot_change_or_invalidate_its_result():
    f = market()
    candle(f, 5, 100, 105, 99, 104)
    candle(f, 6, 85, 200, 80, 150)
    out = sim(f)
    f.loc[f.index[7]:, ["open", "high", "low", "close", "atr"]] = np.nan
    assert sim(f) == out


def test_high_alone_does_not_arm_and_there_is_no_fixed_profit_target():
    f = market(7)
    candle(f, 5, 100, 200, 99, 110, 1)
    candle(f, 6, 110, 115, 91, 110, 1)
    out = sim(f)
    assert out["censored"] and out["exit_reason"] == "boundary_mark"
    assert out["peak_r"] == 10 and not out["trail_armed"]
    assert out["final_protection"] == 90.


def test_trail_only_activates_next_bar_and_stop_bar_high_never_counts():
    f = market()
    candle(f, 5, 100, 131, 95, 130, 2)
    candle(f, 6, 130, 200, 121, 125, 2)
    out = sim(f)
    assert out["exit_i"] == 6  # Same launchbar low95 was NOT tested against new122 stop.
    assert out["exit_price"] == 122.
    assert out["exit_reason"] == "trailing_stop"
    assert out["peak_r"] == pytest.approx(3.1)
    assert out["trail_armed"]


def test_optional_path_is_from_same_loop_and_uses_previously_active_stop():
    f = market()
    candle(f, 5, 100, 131, 95, 130, 2)
    candle(f, 6, 130, 200, 121, 125, 2)
    plain = sim(f)
    detailed = simulate_trade(f, 4, .1, f.index[-1]+pd.Timedelta(hours=1), include_path=True)
    path = detailed.pop("path")
    assert detailed == plain and "path" not in plain
    assert path.index.tolist() == [f.index[5], f.index[6]]
    assert path.active_stop.tolist() == [90., 122.]
    assert path.entry.tolist() == [True, False] and path.exit.tolist() == [False, True]
    assert path.exit_price.iloc[-1] == 122.


def test_armed_state_persists_below_activation_and_protection_never_falls():
    f = market()
    candle(f, 5, 100, 121, 99, 120, 5)  # Arms; next stop100.
    candle(f, 6, 120, 121, 105, 110, 1)  # Below2R but remains armed; next106.
    candle(f, 7, 110, 112, 107, 111, 10)  # ATR jump cannot lower106.
    candle(f, 8, 111, 160, 105, 108, 1)
    out = sim(f)
    assert out["exit_i"] == 8 and out["exit_price"] == 106.
    assert out["peak_r"] == pytest.approx(2.1)


def test_trailing_gap_fills_open_and_current_atr_cannot_rescue_stop():
    f = market()
    candle(f, 5, 100, 131, 99, 130, 2)
    candle(f, 6, 115, 200, 110, 150, 100)
    out = sim(f)
    assert out["exit_price"] == 115 and out["exit_reason"] == "trailing_stop_gap"
    assert out["exit_time"] == f.index[6] and out["peak_r"] == pytest.approx(3.1)


@pytest.mark.parametrize("atr", [0, -1, np.nan, np.inf])
def test_unknown_atr_during_hold_preserves_previous_protection(atr):
    f = market()
    candle(f, 5, 100, 131, 99, 130, 2)
    candle(f, 6, 130, 140, 125, 139, atr)
    candle(f, 7, 139, 141, 121, 125, 2)
    out = sim(f)
    assert out["exit_price"] == 122 and out["exit_i"] == 7


@pytest.mark.parametrize("minutes", [60, 240])
def test_cutoff_marks_last_complete_close_and_future_candles_cannot_change_it(minutes):
    f = market(minutes=minutes)
    end = f.index[7]
    candle(f, 5, 100, 106, 99, 105)
    candle(f, 6, 105, 108, 104, 107)
    out = sim(f, end=end)
    assert out["exit_price"] == 107 and out["exit_time"] == end
    assert out["censored"] and not out["natural_exit"]
    assert out["exit_i"] == 6 and out["hold_bars"] == 2
    changed = f.copy()
    changed.loc[changed.index[7]:, ["open", "high", "low", "close", "atr"]] = np.nan
    assert sim(changed, end=end) == out
    assert sim(f.iloc[:7].copy(), end=end) == out


def test_no_entry_at_cutoff_and_decision_after_cutoff_retained():
    f = market()
    assert sim(f, i=4, end=f.index[5])["invalid_reason"] == "no_entry_bar"
    assert sim(f, i=5, end=f.index[5])["invalid_reason"] == "decision_after_cutoff"
    assert sim(f, i=9)["invalid_reason"] == "no_entry_bar"


@pytest.mark.parametrize("tick", [0, -1, np.nan, np.inf, None])
def test_invalid_tick_fails_closed(tick):
    assert sim(market(), tick=tick)["invalid_reason"] == "invalid_tick"


@pytest.mark.parametrize("atr", [0, -1, np.nan, np.inf])
def test_invalid_signal_atr_fails_closed(atr):
    f = market(); f.loc[f.index[4], "atr"] = atr
    assert sim(f)["invalid_reason"] == "invalid_signal_atr"


def test_impossible_stop_and_entry_below_frozen_stop_fail_closed():
    f = market(); f.loc[f.index[4], "atr"] = 100
    assert sim(f)["invalid_reason"] == "invalid_initial_stop"
    f = market(); candle(f, 5, 85, 88, 80, 86)
    out = sim(f)
    assert not out["valid"] and out["invalid_reason"] == "entry_at_or_below_stop"


def test_only_previous_five_lows_affect_initial_stop():
    f = market()
    candle(f, 4, 100, 110, 82.37, 100, 5)
    candle(f, 5, 100, 102, 85, 101)
    out = sim(f, tick=.25)
    assert out["initial_stop"] == 81.25
    f.loc[f.index[6], "low"] = 60
    assert sim(f, tick=.25)["initial_stop"] == out["initial_stop"]


def test_input_frame_is_not_mutated_and_millisecond_clock_equivalent():
    f = market(); original = f.copy(deep=True)
    out = sim(f)
    pd.testing.assert_frame_equal(f, original)
    f.index = f.index.as_unit("ms")
    assert sim(f) == out


def test_invalid_clock_schema_and_ohlc_rejected():
    f = market()
    with pytest.raises(ValueError, match="continuous"):
        sim(f.drop(f.index[2]))
    with pytest.raises(ValueError, match="aligned UTC"):
        sim(f, end=pd.Timestamp("2026-01-02"))
    with pytest.raises(ValueError, match="UTC index"):
        sim(f.set_axis(f.index.tz_convert("Asia/Shanghai")))
    with pytest.raises(ValueError, match="OHLC"):
        bad = f.copy(); bad.loc[bad.index[6], "low"] = 101; sim(bad)
    with pytest.raises(ValueError, match="integer"):
        sim(f, i=True)
