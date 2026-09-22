"""Focused contracts for the owner-requested 2026-09-22 bounded MA stops.

The test fixtures use signal-close SMA/ATR scalars and a confirmed higher
timeframe scalar already visible at chart open.  They never derive an MA from
future bars; the execution checks only verify the existing next-open and
close-confirmed 2R/trailing state machine around the new initial stop.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_ma_stop as ma_stop
from yoyo.evaluation import spike_v1_v8_be05 as engine


def _row(*, side: int = 1, entry: float = 100.0, stop: float = 98.0) -> dict[str, object]:
    """Build a source-like next-open row with an extra field for preservation."""
    risk = side * (entry - stop)
    return {
        "signal_i": 4,
        "signal_bar_open": pd.Timestamp("2026-01-01", tz="UTC"),
        "entry_i": 5,
        "entry_time": pd.Timestamp("2026-01-01 00:05", tz="UTC"),
        "side": side,
        "entry_price": entry,
        "initial_stop": stop,
        "initial_risk": risk,
        "initial_risk_frac": risk / entry,
        "protection": stop,
        "mfe_r": 0.0,
        "trail_armed": False,
        "custom": "preserve-me",
    }


def _context(*, gap_i: int | None = None, side: int = 1) -> base.StreamContext:
    """Make a minimal v1-common-long replay context with one frozen signal."""
    n = 11
    index = pd.date_range("2026-01-01", periods=n, freq="30min", tz="UTC")
    close = np.full(n, 100.0)
    bars = pd.DataFrame({
        "open": close.copy(), "high": close + 0.4, "low": close - 1.0,
        "close": close.copy(), "atr": np.ones(n),
        "s20": close.copy(), "e20": close.copy(), "md": np.ones(n), "sb": np.zeros(n),
        "ropeHigh": close + 1.0, "ropeLow": close - 1.0,
    }, index=index)
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    signals.loc[index[4], "long_signal" if side == 1 else "short_signal"] = True
    gap = pd.Series(False, index=index)
    if gap_i is not None:
        gap.iloc[gap_i] = True
    cache = {
        "bars": bars,
        "signals": signals.copy(),
        "v1_signals": signals.copy(),
        "data_gap": gap,
        "bb_ready": pd.Series(True, index=index),
        "bb": pd.DataFrame({"prior_squeeze_run3": True, "v7_ready": True}, index=index),
        "tick": 0.01,
    }
    ledger = pd.DataFrame({"signal_bar_open": [index[4]], "signal_i": [4]})
    return base.StreamContext(
        path=Path("."), key="synthetic_30m_ma_stop", receipt={"source_sha256": "synthetic", "cache_sha256": "synthetic"},
        cache=cache, signals_ledger=ledger, minutes=30,
        identity={"venue": "synthetic", "symbol": "SYN", "asset": "SYN", "timeframe_min": 30},
    )


def _transform_for_context(context: base.StreamContext, *, arm: str):
    """Return the parent runner's scalar-indexed callback shape."""
    frame = context.cache["bars"]
    sma120 = np.full(len(frame), 97.5)
    htf_sma60 = np.full(len(frame), 97.5)
    atr = frame["atr"].to_numpy(float)
    return lambda row, i, side: ma_stop.transform_initial(
        row, arm=arm, sma120=sma120[i], htf_sma60=htf_sma60[i], atr=atr[i], tick=float(context.cache["tick"])
    )


def test_baseline_is_an_exact_dict_copy_and_default_arm_is_unchanged() -> None:
    row = _row()
    out = ma_stop.transform_initial(row, arm="baseline", sma120=np.nan, htf_sma60=None, atr=-1, tick=.01)
    assert out == row and out is not row


@pytest.mark.parametrize(
    ("side", "original", "sma", "htf", "expected_stop"),
    [
        (1, 98.0, 97.5, 200.0, 97.3),
        (-1, 102.0, 1.0, 102.5, 102.7),
    ],
)
def test_mirrored_arms_widen_only_and_reset_risk(side, original, sma, htf, expected_stop) -> None:
    row = _row(side=side, stop=original)
    out = ma_stop.transform_initial(row, arm="sma120" if side == 1 else "htf_sma60",
                                     sma120=sma, htf_sma60=htf, atr=1.0, tick=.01)
    assert out is not None
    assert out["entry_price"] == row["entry_price"]
    assert out["initial_stop"] == pytest.approx(expected_stop)
    expected_risk = side * (float(row["entry_price"]) - expected_stop)
    assert out["initial_risk"] == pytest.approx(expected_risk)
    assert out["initial_risk_frac"] == pytest.approx(expected_risk / float(row["entry_price"]))
    assert out["protection"] == pytest.approx(expected_stop)
    assert out["custom"] == row["custom"]


@pytest.mark.parametrize(
    ("side", "original", "sma", "htf"),
    [(1, 98.0, 99.0, 97.5), (-1, 102.0, 100.0, 101.5)],
)
def test_only_widens_when_selected_ma_is_inside_original_stop(side, original, sma, htf) -> None:
    row = _row(side=side, stop=original)
    arm = "sma120" if side == 1 else "htf_sma60"
    out = ma_stop.transform_initial(row, arm=arm, sma120=sma, htf_sma60=htf, atr=1.0, tick=.01)
    assert out is not None
    assert out["initial_stop"] == row["initial_stop"]
    assert out["protection"] == row["protection"]


def test_both_uses_lower_long_support_and_higher_short_resistance() -> None:
    long = ma_stop.transform_initial(_row(side=1), arm="both", sma120=97.5, htf_sma60=96.5, atr=1., tick=.01)
    short = ma_stop.transform_initial(_row(side=-1, stop=102.), arm="both", sma120=103.5, htf_sma60=104.5, atr=1., tick=.01)
    assert long is not None and short is not None
    assert long["initial_stop"] == pytest.approx(96.3)
    assert short["initial_stop"] == pytest.approx(104.7)


def test_outward_rounding_and_noop_tick_preserve_grid_value() -> None:
    long = ma_stop.transform_initial(_row(), arm="sma120", sma120=97.876, htf_sma60=None, atr=1., tick=.01)
    short = ma_stop.transform_initial(_row(side=-1, stop=102.), arm="htf_sma60", sma120=None, htf_sma60=102.124, atr=1., tick=.01)
    noop = ma_stop.transform_initial(_row(entry=.30, stop=.29), arm="sma120", sma120=.50, htf_sma60=None, atr=1., tick=.01)
    assert long is not None and short is not None and noop is not None
    assert long["initial_stop"] == pytest.approx(97.67)
    assert short["initial_stop"] == pytest.approx(102.33)
    assert noop["initial_stop"] == .29


@pytest.mark.parametrize(
    "kwargs",
    [
        {"arm": "unknown", "sma120": 97.0, "htf_sma60": 97.0, "atr": 1., "tick": .01},
        {"arm": "sma120", "sma120": None, "htf_sma60": 97.0, "atr": 1., "tick": .01},
        {"arm": "sma120", "sma120": -97.0, "htf_sma60": 97.0, "atr": 1., "tick": .01},
        {"arm": "sma120", "sma120": np.nan, "htf_sma60": 97.0, "atr": 1., "tick": .01},
        {"arm": "sma120", "sma120": 97.0, "htf_sma60": 97.0, "atr": -1., "tick": .01},
    ],
)
def test_unknown_or_invalid_features_fail_closed(kwargs) -> None:
    if kwargs["arm"] == "unknown":
        with pytest.raises(ValueError):
            ma_stop.transform_initial(_row(), **kwargs)
    else:
        assert ma_stop.transform_initial(_row(), **kwargs) is None


def test_default_and_explicit_baseline_callback_have_serial_parity() -> None:
    context = _context()
    prepared = engine.prepare_arm(context, arm="v1_common_execution_long")
    expected = engine.replay_serial(context, arm="v1_common_execution_long", enable_be=False, prepared=prepared)
    actual = engine.replay_serial(
        context, arm="v1_common_execution_long", enable_be=False, prepared=prepared,
        initial_transform=_transform_for_context(context, arm="baseline"),
    )
    for left, right in zip(expected, actual):
        pdt.assert_frame_equal(left, right, check_dtype=False)


def test_transformed_initial_stop_is_active_before_trail_and_fixed_entry_matches() -> None:
    context = _context()
    # Original stop is 98.00; transformed SMA stop is 97.30 and is touched
    # on the entry bar before any close-confirmed trail update can run.
    context.cache["bars"].iloc[5, context.cache["bars"].columns.get_loc("low")] = 97.2
    prepared = engine.prepare_arm(context, arm="v1_common_execution_long")
    trades, _, _ = engine.replay_serial(
        context, arm="v1_common_execution_long", enable_be=False, prepared=prepared,
        initial_transform=_transform_for_context(context, arm="sma120"),
    )
    trade = trades.iloc[0]
    assert (trade.exit_reason, trade.exit_i, trade.exit_price) == ("initial_stop", 5, pytest.approx(97.3))
    assert trade.mfe_r == pytest.approx(0.0)
    fixed = engine.replay_fixed_entry(context, trade, arm="v1_common_execution_long", enable_be=False, prepared=prepared)
    assert (fixed["exit_reason"], fixed["exit_i"], fixed["exit_price"]) == ("initial_stop", 5, pytest.approx(97.3))


def test_changed_initial_risk_delays_two_r_arm_and_gap_status_survives() -> None:
    context = _context()
    bars = context.cache["bars"]
    bars.iloc[5] = [100., 104.5, 99., 104.5, 1., 100., 100., 1., 0., 100., 100.]
    bars.iloc[6] = [100., 100.1, 99.5, 99.5, 1., 100., 100., 1., 0., 100., 100.]
    prepared = engine.prepare_arm(context, arm="v1_common_execution_long")
    baseline, _, _ = engine.replay_serial(context, arm="v1_common_execution_long", enable_be=False, prepared=prepared)
    widened, _, _ = engine.replay_serial(
        context, arm="v1_common_execution_long", enable_be=False, prepared=prepared,
        initial_transform=_transform_for_context(context, arm="sma120"),
    )
    assert baseline.iloc[0].exit_reason == "trailing_stop_gap"
    assert widened.iloc[0].censored
    assert widened.iloc[0].initial_risk > baseline.iloc[0].initial_risk

    gap_context = _context(gap_i=6)
    gap_prepared = engine.prepare_arm(gap_context, arm="v1_common_execution_long")
    gap_trades, _, _ = engine.replay_serial(
        gap_context, arm="v1_common_execution_long", enable_be=False, prepared=gap_prepared,
        initial_transform=_transform_for_context(gap_context, arm="sma120"),
    )
    assert (bool(gap_trades.iloc[0].censored), gap_trades.iloc[0].exit_reason) == (True, "data_gap_censored")
