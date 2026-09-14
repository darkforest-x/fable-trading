"""Causal Pine Fanshen signals and one-entry, research-only exit replay.

``compute_signals`` is a literal closed-bar implementation of the owner-supplied
Pine formula: it needs ``high``, ``low``, and ``close``; it uses a five-bar
stochastic (%K is SMA3 of that stochastic), a further SMA3 %D, and WVF's
22/20/50 windows.  Every value at row ``i`` uses row ``i`` and earlier only.
The stochastic denominator is left as ``NaN`` when its five-bar high equals
its low, matching Pine arithmetic without an ``nz`` fill.  Pine's rolling
functions ignore missing observations: SMA and standard deviation wait for the
specified count of nonmissing values, while highest/lowest use available prior
values during warmup.  WVF's population standard deviation matches Pine
``ta.stdev``.

The replay deliberately consumes the frozen V8 entry construction from
``spike_recovery_exit``: five-bar initial stop, 0.2 ATR buffer, 2 ATR risk
floor, 2R/4ATR trail, and 20bp round-trip cost.  It never reads market data,
creates entries, or changes raw V6 reverse exits.  A Fanshen event observed on
a completed close may exit the opposite-side position only at its next observed
open.  Raw V6 reverse exits retain priority when both events share a close.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral
from typing import Literal, Optional

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_recovery_exit import (
    Prepared,
    _gap_censor,
    _round_frozen_trail,
    _stop_gap,
    _stop_hit,
)
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _close_trade
from yoyo.evaluation.spike_v7_fast import _initial_position_fast


_SIGNAL_COLUMNS = ("arrow_long", "arrow_short", "alert_long", "alert_short", "wvf_green")


@dataclass(frozen=True)
class PreparedSignals:
    """Immutable boolean Fanshen arrays aligned to a prepared OHLC frame.

    Callers replaying many independent entries should call ``prepare_signals``
    once.  The arrays are copied from the input frame, so later DataFrame
    mutation cannot alter an already-prepared study arm.
    """

    index: pd.Index
    arrow_long: np.ndarray
    arrow_short: np.ndarray
    alert_long: np.ndarray
    alert_short: np.ndarray
    wvf_green: np.ndarray

    def __post_init__(self) -> None:
        """Copy and freeze arrays, including callers that construct this class directly."""
        if any(len(getattr(self, name)) != len(self.index) for name in _SIGNAL_COLUMNS):
            raise ValueError("each prepared signal array must match its index")
        for name in _SIGNAL_COLUMNS:
            values = np.array(getattr(self, name), dtype=bool, copy=True)
            values.setflags(write=False)
            object.__setattr__(self, name, values)


def _bool_column(signals: pd.DataFrame, name: str) -> np.ndarray:
    """Coerce an explicitly supplied signal column without treating NaN as true."""
    return signals[name].fillna(False).astype(bool).to_numpy(copy=False)


def _pine_sma(series: pd.Series, length: int) -> pd.Series:
    """Match Pine ``ta.sma``: the latest ``length`` nonmissing observations."""
    values = series.dropna().rolling(length, min_periods=length).mean()
    return values.reindex(series.index).ffill()


def _pine_std(series: pd.Series, length: int) -> pd.Series:
    """Match Pine ``ta.stdev``: population std over ``length`` nonmissing values."""
    values = series.dropna().rolling(length, min_periods=length).std(ddof=0)
    return values.reindex(series.index).ffill()


def compute_signals(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute the supplied Pine K/D/WVF formula from closed OHLC bars only.

    Required columns are ``high``, ``low``, and ``close``.  ``stoch`` uses the
    current five-bar highest high and lowest low, each using its available
    closed-bar warmup history.  A zero high-low range remains ``NaN`` rather
    than being filled.  SMA/std windows count nonmissing observations, as Pine
    does.  Windows are 3 for K, 3 for D, 20 for WVF's band, and 50 for the WVF
    range high.  A crossover allows equality on the preceding bar (K[i-1] <=
    D[i-1]); a crossunder analogously allows K[i-1] >= D[i-1].  Thresholds are
    strict: K/D exactly 20 or 80 do not pass.
    """
    required = {"high", "low", "close"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame):
        raise ValueError("high, low, and close columns are required")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("frame index must be unique and chronological")
    high = pd.to_numeric(frame.high, errors="coerce")
    low = pd.to_numeric(frame.low, errors="coerce")
    close = pd.to_numeric(frame.close, errors="coerce")
    if ((low > high) & low.notna() & high.notna()).any():
        raise ValueError("low cannot exceed high")

    stoch_high = high.rolling(5, min_periods=1).max()
    stoch_low = low.rolling(5, min_periods=1).min()
    stoch_span = stoch_high - stoch_low
    stoch = ((close - stoch_low) / stoch_span * 100.0).where(stoch_span.ne(0))
    k = _pine_sma(stoch, 3)
    d = _pine_sma(k, 3)
    crossover = k.gt(d) & k.shift(1).le(d.shift(1))
    crossunder = k.lt(d) & k.shift(1).ge(d.shift(1))
    arrow_long = (crossover & k.lt(20) & d.lt(20)).fillna(False).astype(bool)
    arrow_short = (crossunder & k.gt(80) & d.gt(80)).fillna(False).astype(bool)

    highest_close = close.rolling(22, min_periods=1).max()
    wvf = ((highest_close - low) / highest_close * 100.0).where(highest_close.ne(0))
    wvf_mean = _pine_sma(wvf, 20)
    wvf_upper = wvf_mean + 2.0 * _pine_std(wvf, 20)
    range_high = wvf.rolling(50, min_periods=1).max() * 0.85
    wvf_green = (wvf.ge(wvf_upper) | wvf.ge(range_high)).fillna(False).astype(bool)

    return pd.DataFrame({
        "k": k, "d": d, "wvf": wvf, "wvf_green": wvf_green,
        "arrow_long": arrow_long, "arrow_short": arrow_short,
        "alert_long": (arrow_long & wvf_green).astype(bool),
        "alert_short": arrow_short,
    }, index=frame.index)


def prepare_signals(signals: pd.DataFrame) -> PreparedSignals:
    """Copy computed Fanshen booleans once for repeated causal entry replays."""
    if not isinstance(signals, pd.DataFrame) or not set(_SIGNAL_COLUMNS).issubset(signals):
        raise ValueError("signals need arrow_long, arrow_short, alert_long, alert_short, and wvf_green")
    if not signals.index.is_unique or not signals.index.is_monotonic_increasing:
        raise ValueError("signals index must be unique and chronological")
    return PreparedSignals(signals.index.copy(), *(_bool_column(signals, name) for name in _SIGNAL_COLUMNS))


def prepare(frame: pd.DataFrame, raw_signals: pd.DataFrame) -> Prepared:
    """Prepare frozen V8 OHLC/raw-V6 inputs, avoiding duplicate engine copies."""
    from yoyo.evaluation.spike_recovery_exit import prepare as prepare_recovery

    return prepare_recovery(frame, raw_signals)


def _valid_end(end_i: Optional[int], size: int) -> int:
    if end_i is None:
        return size
    if isinstance(end_i, (bool, np.bool_)) or not isinstance(end_i, Integral) or not 0 <= int(end_i) <= size:
        raise ValueError("end_i must be an exclusive frame bound")
    return int(end_i)


def _validated_signals(signals: pd.DataFrame | PreparedSignals, index: pd.Index) -> PreparedSignals:
    result = prepare_signals(signals) if isinstance(signals, pd.DataFrame) else signals
    if not isinstance(result, PreparedSignals):
        raise TypeError("signals must be a DataFrame or PreparedSignals")
    if not result.index.equals(index):
        raise ValueError("Fanshen signals must align to prepared OHLC bars")
    return result


def replay_fanshen_entry(
    prepared: Prepared,
    signals: pd.DataFrame | PreparedSignals,
    signal_i: int,
    *,
    trigger: Literal["arrows", "alerts"] = "arrows",
    exit_mode: Literal["augment", "replace_trail"] = "augment",
    profitable_only: bool = True,
    side_override: Optional[int] = None,
    tick: float = .01,
    end_i: Optional[int] = None,
) -> Optional[dict[str, object]]:
    """Replay one frozen V8 entry with optional causal opposite Fanshen exits.

    ``trigger='arrows'`` uses the plotted K/D arrows; ``'alerts'`` uses the
    alert stream, where only long arrows receive the WVF-green filter.  Thus a
    long position responds to a short arrow/alert and a short position responds
    to a long arrow/alert.  ``augment`` retains the original 2R/4ATR close
    trail.  ``replace_trail`` removes that trail but retains the frozen initial
    stop and every raw V6 opposite-side exit.  With ``profitable_only``, the
    extra exit is scheduled only if its *signal close* has positive net R after
    the frozen 20bp cost; it never tests the next opening price.

    Pending exits execute at the next observed open.  Existing stop protection
    wins on an adverse opening gap, then a raw V6 reverse wins over a concurrent
    Fanshen exit.  An intrabar stop wins before the close can schedule either
    next-open exit.  ``end_i`` is exclusive and never reads a future bar.
    """
    if not isinstance(prepared, Prepared):
        raise TypeError("prepared must come from prepare()")
    if trigger not in {"arrows", "alerts"}:
        raise ValueError("trigger must be arrows or alerts")
    if exit_mode not in {"augment", "replace_trail"}:
        raise ValueError("exit_mode must be augment or replace_trail")
    if not isinstance(profitable_only, (bool, np.bool_)):
        raise ValueError("profitable_only must be boolean")
    if not math.isfinite(float(tick)) or float(tick) <= 0:
        raise ValueError("tick must be positive and finite")
    size = len(prepared.index)
    if isinstance(signal_i, (bool, np.bool_)) or not isinstance(signal_i, Integral) or not 0 <= int(signal_i) < size:
        raise ValueError("signal_i must be a valid frame position")
    signal_i, end, tick = int(signal_i), _valid_end(end_i, size), float(tick)
    prepared_signals = _validated_signals(signals, prepared.index)
    if side_override is not None:
        if isinstance(side_override, (bool, np.bool_)) or not isinstance(side_override, Integral) or int(side_override) not in (-1, 1):
            raise ValueError("side_override must be +1, -1, or None")
        side_override = int(side_override)
    raw_signal_side = int(prepared.raw_side[signal_i])
    if (side_override is None and raw_signal_side == 0) or signal_i + 1 >= end:
        return None

    side = raw_signal_side if side_override is None else side_override
    spec = ExecutionSpec(tick=tick)
    position = _initial_position_fast(prepared.index, prepared.open, prepared.high, prepared.low,
                                      prepared.close, prepared.atr, prepared.gap, signal_i, side, spec)
    if position is None:
        return None
    entry, risk = float(position["entry_price"]), float(position["initial_risk"])
    initial_stop = float(position["initial_stop"])
    cost_r = spec.round_trip_cost / float(position["initial_risk_frac"])
    choice_long = prepared_signals.arrow_long if trigger == "arrows" else prepared_signals.alert_long
    choice_short = prepared_signals.arrow_short if trigger == "arrows" else prepared_signals.alert_short
    pending_raw_reverse = False
    pending_fanshen = False
    pending_fanshen_i: Optional[int] = None
    pending_fanshen_net_r = math.nan

    def decorate(trade: dict[str, object], *, exit_at_open: bool) -> dict[str, object]:
        trade.update(
            exit_at_open=exit_at_open,
            final_protection=float(position["protection"]),
            trigger=trigger,
            exit_mode=exit_mode,
            profitable_only=bool(profitable_only),
            fanshen_pending_signal_i=pending_fanshen_i,
            fanshen_pending_signal_time=(pd.NaT if pending_fanshen_i is None else prepared.index[pending_fanshen_i]),
            fanshen_signal_close_net_r=pending_fanshen_net_r,
            fanshen_exit=trade["exit_reason"] == f"fanshen_{trigger}_next_open",
            entry_side_source="side_override" if side_override is not None else "raw_v6_signal",
            side_override=side_override,
            trail_replaced=exit_mode == "replace_trail",
            cost_r=cost_r,
        )
        return trade

    def finish(price: float, reason: str, i: int, *, open_exit: bool, censored: bool = False) -> dict[str, object]:
        trade = _close_trade(position, exit_i=i, exit_time=prepared.index[i], exit_price=float(price), reason=reason, spec=spec)
        if censored:
            trade["censored"] = True
            trade["exit_time_precision"] = "last_complete_close"
        return decorate(trade, exit_at_open=open_exit)

    for i in range(int(position["entry_i"]), end):
        if bool(prepared.gap[i]):
            return decorate(_gap_censor(position, exit_i=i, exit_time=prepared.index[i]), exit_at_open=False)
        opening, high, low, close, atr = (float(values[i]) for values in
                                          (prepared.open, prepared.high, prepared.low, prepared.close, prepared.atr))
        protection = float(position["protection"])
        if pending_raw_reverse or pending_fanshen:
            if _stop_gap(side, opening, protection):
                reason = "trailing_stop_gap" if protection != initial_stop else "initial_stop_gap"
            elif pending_raw_reverse:
                reason = "opposite_v6_next_open"
            else:
                reason = f"fanshen_{trigger}_next_open"
            return finish(opening, reason, i, open_exit=True)

        if _stop_hit(side, high, low, protection):
            price = min(opening, protection) if side == 1 else max(opening, protection)
            reason = "trailing_stop" if protection != initial_stop else "initial_stop"
            if _stop_gap(side, opening, protection):
                reason += "_gap"
            return finish(price, reason, i, open_exit=reason.endswith("_gap"))

        favorable = high if side == 1 else low
        position["mfe_r"] = max(float(position["mfe_r"]), side * (favorable - entry) / risk)
        if exit_mode == "augment":
            close_r = side * (close - entry) / risk
            position["trail_armed"] = bool(position["trail_armed"]) or close_r >= spec.arm_r
            if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
                candidate = _round_frozen_trail(close - side * spec.trail_atr * atr, side=side, tick=tick)
                old = float(position["protection"])
                position["protection"] = max(old, candidate) if side == 1 else min(old, candidate)

        pending_raw_reverse = int(prepared.raw_side[i]) == -side
        opposite_fanshen = bool(choice_short[i]) if side == 1 else bool(choice_long[i])
        close_net_r = side * (close - entry) / risk - cost_r
        pending_fanshen = opposite_fanshen and (not profitable_only or close_net_r > 0.0)
        pending_fanshen_i = i if pending_fanshen else None
        pending_fanshen_net_r = close_net_r if pending_fanshen else math.nan

    last = end - 1
    return finish(float(prepared.close[last]), "boundary_mark", last, open_exit=False, censored=True)
