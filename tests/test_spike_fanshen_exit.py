"""Focused causal contracts for the research-only Fanshen exit module."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_fanshen_exit as fanshen
from yoyo.evaluation import spike_recovery_exit as recovery


def _indicator_frame(stoch_values: list[float]) -> pd.DataFrame:
    """Use constant five-bar extrema, making close map directly to stochastic."""
    index = pd.date_range("2026-01-01", periods=len(stoch_values), freq="h", tz="UTC")
    return pd.DataFrame({"high": 101.0, "low": 1.0, "close": np.asarray(stoch_values) + 1.0}, index=index)


def _replay_inputs(side: int = 1, n: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0}, index=index)
    bars.attrs["minutes"] = 60
    raw = pd.DataFrame({"long_signal": False, "short_signal": False, "_data_gap": False}, index=index)
    raw.iloc[4, raw.columns.get_loc("long_signal" if side == 1 else "short_signal")] = True
    return bars, raw


def _signals(index: pd.Index, *, arrow_long: int | None = None, arrow_short: int | None = None,
             alert_long: int | None = None, alert_short: int | None = None) -> pd.DataFrame:
    out = pd.DataFrame(False, index=index, columns=["arrow_long", "arrow_short", "alert_long", "alert_short", "wvf_green"])
    for name, position in (("arrow_long", arrow_long), ("arrow_short", arrow_short),
                           ("alert_long", alert_long), ("alert_short", alert_short)):
        if position is not None:
            out.iloc[position, out.columns.get_loc(name)] = True
    return out


def test_crossover_uses_prior_equality_but_strict_20_80_thresholds() -> None:
    # With a constant prior K=D, Pine crossover/crossunder permit equality on
    # the preceding bar.  The current threshold comparisons stay strict.
    long_ok = fanshen.compute_signals(_indicator_frame([10.0] * 12 + [10.0, 10.0, 25.0]))
    long_at_20 = fanshen.compute_signals(_indicator_frame([10.0] * 12 + [10.0, 10.0, 40.0]))
    short_ok = fanshen.compute_signals(_indicator_frame([90.0] * 12 + [90.0, 90.0, 75.0]))
    short_at_80 = fanshen.compute_signals(_indicator_frame([90.0] * 12 + [90.0, 90.0, 60.0]))
    assert long_ok.arrow_long.iloc[-1] and long_ok.k.iloc[-2] == pytest.approx(long_ok.d.iloc[-2])
    assert long_at_20.k.iloc[-1] == pytest.approx(20.0) and not long_at_20.arrow_long.iloc[-1]
    assert short_ok.arrow_short.iloc[-1] and short_ok.k.iloc[-2] == pytest.approx(short_ok.d.iloc[-2])
    assert short_at_80.k.iloc[-1] == pytest.approx(80.0) and not short_at_80.arrow_short.iloc[-1]


def test_signal_prefix_is_immune_to_later_bars_and_wvf_filters_only_long_alert() -> None:
    values = np.linspace(20.0, 80.0, 90)
    clean = _indicator_frame(values.tolist())
    poisoned = clean.copy()
    poisoned.iloc[60:, poisoned.columns.get_loc("close")] = 1.0
    poisoned.iloc[60:, poisoned.columns.get_loc("low")] = 0.001
    got, changed = fanshen.compute_signals(clean), fanshen.compute_signals(poisoned)
    pd.testing.assert_frame_equal(got.iloc[:60], changed.iloc[:60])
    assert got.alert_long.equals(got.arrow_long & got.wvf_green)
    assert got.alert_short.equals(got.arrow_short)
    cached = fanshen.prepare_signals(got)
    assert not cached.arrow_long.flags.writeable
    with pytest.raises(ValueError):
        cached.arrow_long[0] = True


def test_flat_stochastic_range_stays_nan_and_cannot_create_arrows() -> None:
    frame = pd.DataFrame({"high": [10.0] * 12, "low": [10.0] * 12, "close": [10.0] * 12})
    out = fanshen.compute_signals(frame)
    assert out.k.isna().all() and out.d.isna().all()
    assert not out.arrow_long.any() and not out.arrow_short.any()


def test_pine_windows_skip_missing_values_and_recover_after_a_flat_span() -> None:
    # At i=5 the current stochastic is 60.  Pine SMA3 skips the NaN at i=4,
    # averaging the three latest valid values (30, 40, 60), not the last three
    # calendar rows.  Constant extrema on rows 0..4 then prove that highest /
    # lowest use available warmup history once a non-flat bar arrives.
    missing = _indicator_frame([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0])
    missing.iloc[4, missing.columns.get_loc("close")] = np.nan
    out = fanshen.compute_signals(missing)
    assert out.k.iloc[5] == pytest.approx((30.0 + 40.0 + 60.0) / 3.0)

    flat_then_live = pd.DataFrame({
        "high": [10.0] * 5 + [101.0] * 3,
        "low": [10.0] * 5 + [1.0] * 3,
        "close": [10.0] * 5 + [11.0, 21.0, 31.0],
    })
    recovered = fanshen.compute_signals(flat_then_live)
    assert recovered.k.iloc[7] == pytest.approx(20.0)


def test_fanshen_is_close_confirmed_and_fills_at_next_open() -> None:
    bars, raw = _replay_inputs()
    bars.iloc[7, bars.columns.get_loc("open")] = 104.25
    prepared = fanshen.prepare(bars, raw)
    signals = _signals(bars.index, arrow_short=6)
    trade = fanshen.replay_fanshen_entry(prepared, signals, 4, profitable_only=False)
    assert trade is not None
    assert trade["exit_i"] == 7 and trade["exit_price"] == 104.25
    assert trade["exit_reason"] == "fanshen_arrows_next_open" and trade["exit_at_open"]
    assert trade["fanshen_pending_signal_i"] == 6


def test_stop_wins_before_same_close_fanshen_and_gap_stop_wins_before_pending_exit() -> None:
    bars, raw = _replay_inputs()
    bars.iloc[6] = [100.0, 101.0, 90.0, 100.0, 1.0]
    prepared = fanshen.prepare(bars, raw)
    stopped = fanshen.replay_fanshen_entry(prepared, _signals(bars.index, arrow_short=6), 4, profitable_only=False)
    assert stopped is not None and stopped["exit_reason"] == "initial_stop" and stopped["exit_i"] == 6

    bars, raw = _replay_inputs()
    bars.iloc[7] = [90.0, 91.0, 89.0, 90.0, 1.0]
    gap = fanshen.replay_fanshen_entry(fanshen.prepare(bars, raw), _signals(bars.index, arrow_short=6), 4,
                                       profitable_only=False)
    assert gap is not None and gap["exit_reason"] == "initial_stop_gap" and gap["exit_i"] == 7


def test_raw_v6_reverse_has_priority_over_same_close_fanshen_event() -> None:
    bars, raw = _replay_inputs()
    raw.iloc[6, raw.columns.get_loc("short_signal")] = True
    bars.iloc[7, bars.columns.get_loc("open")] = 103.0
    trade = fanshen.replay_fanshen_entry(fanshen.prepare(bars, raw), _signals(bars.index, arrow_short=6), 4,
                                         profitable_only=False)
    assert trade is not None and trade["exit_reason"] == "opposite_v6_next_open"
    assert not trade["fanshen_exit"]


def test_short_alert_is_unfiltered_and_profitable_only_uses_signal_close_net_r() -> None:
    bars, raw = _replay_inputs()
    bars.iloc[7, bars.columns.get_loc("open")] = 103.0
    signals = _signals(bars.index, arrow_short=6, alert_short=6)
    prepared = fanshen.prepare(bars, raw)
    # The long position's short alert needs no WVF-green field.  It is rejected
    # only because the signal close would be negative after the 20bp cost.
    rejected = fanshen.replay_fanshen_entry(prepared, signals, 4, trigger="alerts", profitable_only=True)
    accepted = fanshen.replay_fanshen_entry(prepared, signals, 4, trigger="alerts", profitable_only=False)
    assert rejected is not None and rejected["exit_reason"] == "boundary_mark"
    assert accepted is not None and accepted["exit_reason"] == "fanshen_alerts_next_open"


def test_replace_trail_removes_only_frozen_2r_4atr_trail() -> None:
    bars, raw = _replay_inputs(n=8)
    bars.iloc[5] = [100.0, 104.5, 100.0, 104.0, 1.0]  # arm 2R close trail for bar 6.
    bars.iloc[6] = [104.0, 104.2, 99.0, 100.0, 1.0]
    prepared, signals = fanshen.prepare(bars, raw), _signals(bars.index)
    augmented = fanshen.replay_fanshen_entry(prepared, signals, 4, exit_mode="augment")
    replaced = fanshen.replay_fanshen_entry(prepared, signals, 4, exit_mode="replace_trail")
    assert augmented is not None and augmented["exit_reason"] == "trailing_stop"
    assert replaced is not None and replaced["exit_reason"] == "boundary_mark"
    assert replaced["initial_stop"] == augmented["initial_stop"] and replaced["trail_replaced"]


def test_no_fanshen_augment_matches_frozen_original_replay() -> None:
    bars, raw = _replay_inputs()
    bars.iloc[5] = [100.0, 104.5, 100.0, 104.0, 1.0]
    bars.iloc[6] = [104.0, 104.2, 99.0, 100.0, 1.0]
    prepared = fanshen.prepare(bars, raw)
    got = fanshen.replay_fanshen_entry(prepared, _signals(bars.index), 4, exit_mode="augment")
    want = recovery.replay_entry(prepared, 4, take_profit_r=None)
    assert got is not None and want is not None
    for key in ("signal_i", "entry_i", "side", "exit_i", "exit_reason", "entry_price", "exit_price",
                "initial_stop", "initial_risk", "gross_return", "net_return", "gross_r", "net_r", "censored"):
        if isinstance(got[key], float) and math.isnan(got[key]):
            assert math.isnan(want[key])
        elif isinstance(got[key], float):
            assert got[key] == pytest.approx(want[key])
        else:
            assert got[key] == want[key]
