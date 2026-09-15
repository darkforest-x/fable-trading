"""Parameterized, bounded BB × Stoch replay for offline parameter searches.

The default :class:`ParamSpec` is the frozen v2 formula in
``bb_stoch_replay``: BB(200, population stdev, 2.0), Stoch(5, 3, 3), a 3%
initial stop, and 20/80 arrow thresholds.  Feature windows use only closed
OHLC rows available at their decision bar.  In particular, each resting BB
touch target is solved from the preceding ``bb_length - 1`` closes and is
rounded to the fixed 0.01 tick before the next bar can trade it.

``prepare`` validates and copies feature arrays once.  ``replay_entry`` then
uses only those arrays and an exclusive bound; it neither reads market data nor
constructs a future decision.  The event accounting deliberately matches the
frozen one-ETH, 10 bp executed-notional-fee replay, including the documented
OHLC approximations around a newly marketable break-even stop.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral, Real
from typing import Literal

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_fanshen_exit import _pine_sma


_OHLC = ("open", "high", "low", "close")
_TICK = 0.01
_FEE_RATE = 0.001


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or int(value) < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


@dataclass(frozen=True)
class ParamSpec:
    """Causal BB/Stoch search parameters; defaults reproduce frozen v2.

    ``overbought`` is derived as ``100 - oversold`` so symmetric thresholds
    remain a single search degree of freedom.  The current Stoch uses the full
    closed-bar sequence, matching the source formula even when a BB history is
    reset by a declared data gap; this deliberately leaves scope selection
    available as a future explicit parameter rather than changing defaults.
    """

    bb_length: int = 200
    bb_mult: float = 2.0
    stop_fraction: float = 0.03
    stoch_length: int = 5
    k_smooth: int = 3
    d_smooth: int = 3
    oversold: float = 20.0

    def __post_init__(self) -> None:
        bb_length = _positive_int(self.bb_length, "bb_length")
        stoch_length = _positive_int(self.stoch_length, "stoch_length")
        k_smooth = _positive_int(self.k_smooth, "k_smooth")
        d_smooth = _positive_int(self.d_smooth, "d_smooth")
        bb_mult = _finite_float(self.bb_mult, "bb_mult")
        stop_fraction = _finite_float(self.stop_fraction, "stop_fraction")
        oversold = _finite_float(self.oversold, "oversold")
        if bb_length < 3:
            raise ValueError("bb_length must be at least 3")
        if bb_mult <= 0:
            raise ValueError("bb_mult must be positive")
        if bb_length <= 1 + bb_mult * bb_mult:
            raise ValueError("bb_length must exceed 1 + bb_mult squared")
        if not 0 < stop_fraction < 1:
            raise ValueError("stop_fraction must be between zero and one")
        if not 0 < oversold < 50:
            raise ValueError("oversold must be strictly between zero and 50")
        object.__setattr__(self, "bb_length", bb_length)
        object.__setattr__(self, "bb_mult", bb_mult)
        object.__setattr__(self, "stop_fraction", stop_fraction)
        object.__setattr__(self, "stoch_length", stoch_length)
        object.__setattr__(self, "k_smooth", k_smooth)
        object.__setattr__(self, "d_smooth", d_smooth)
        object.__setattr__(self, "oversold", oversold)

    @property
    def overbought(self) -> float:
        """Symmetric strict short-arrow threshold."""
        return 100.0 - self.oversold


@dataclass(frozen=True)
class Prepared:
    """Validated, immutable feature arrays for repeated single-entry replays."""

    index: pd.Index
    spec: ParamSpec
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    signals: np.ndarray
    target_upper: np.ndarray
    target_lower: np.ndarray
    gaps: np.ndarray


def _require_spec(spec: ParamSpec) -> ParamSpec:
    if not isinstance(spec, ParamSpec):
        raise TypeError("spec must be a ParamSpec")
    return spec


def _valid_frame(frame: pd.DataFrame, *, need_features: bool = False) -> None:
    """Validate chronological closed bars without fetching or filling data."""
    needed = set(_OHLC) | ({"signal", "target_upper", "target_lower"} if need_features else set())
    if not isinstance(frame, pd.DataFrame) or not needed.issubset(frame):
        raise ValueError(f"frame needs {', '.join(sorted(needed))}")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("frame index must be unique and chronological")
    values = frame.loc[:, _OHLC].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(values.to_numpy(float)).all():
        raise ValueError("OHLC values must be finite")
    if (values.low.gt(values.high) | values.open.lt(values.low) | values.open.gt(values.high)
            | values.close.lt(values.low) | values.close.gt(values.high)).any():
        raise ValueError("OHLC bounds are invalid")


def _gaps(index: pd.Index, supplied: pd.Series | None = None) -> pd.Series:
    """Return explicit or timestamp-derived discontinuities; five minutes is fixed."""
    if supplied is not None:
        result = pd.Series(supplied, index=index).fillna(True).astype(bool)
    elif isinstance(index, pd.DatetimeIndex):
        result = index.to_series().diff().ne(pd.Timedelta(minutes=5))
        result.iloc[0] = False
    else:
        result = pd.Series(False, index=index)
    return result.astype(bool)


def _stoch_arrows(frame: pd.DataFrame, spec: ParamSpec) -> pd.DataFrame:
    """Compute source-Pine Stoch arrows, reusing its nonmissing SMA semantics."""
    high = pd.to_numeric(frame.high, errors="coerce")
    low = pd.to_numeric(frame.low, errors="coerce")
    close = pd.to_numeric(frame.close, errors="coerce")
    stoch_high = high.rolling(spec.stoch_length, min_periods=1).max()
    stoch_low = low.rolling(spec.stoch_length, min_periods=1).min()
    stoch = ((close - stoch_low) / (stoch_high - stoch_low) * 100.0).where(stoch_high.ne(stoch_low))
    k = _pine_sma(stoch, spec.k_smooth)
    d = _pine_sma(k, spec.d_smooth)
    crossover = k.gt(d) & k.shift(1).le(d.shift(1))
    crossunder = k.lt(d) & k.shift(1).ge(d.shift(1))
    return pd.DataFrame({
        "k": k,
        "d": d,
        "arrow_long": (crossover & k.lt(spec.oversold) & d.lt(spec.oversold)).fillna(False).astype(bool),
        "arrow_short": (crossunder & k.gt(spec.overbought) & d.gt(spec.overbought)).fillna(False).astype(bool),
    }, index=frame.index)


def compute_features(frame: pd.DataFrame, spec: ParamSpec = ParamSpec()) -> pd.DataFrame:
    """Calculate causal parameterized BB/Stoch features and next-bar targets.

    Current upper/lower bands use the current closed bar.  Each target uses the
    preceding ``spec.bb_length - 1`` closes in its gap-bounded BB segment, then
    literal ceil/floor rounding to 0.01.  Stoch arrows retain the source
    formula's whole-series Pine SMA behavior described on :class:`ParamSpec`.
    """
    spec = _require_spec(spec)
    _valid_frame(frame)
    out = frame.copy()
    close = pd.to_numeric(out.close, errors="coerce")
    supplied_gap = out["_data_gap"] if "_data_gap" in out else out.get("data_gap")
    gap = _gaps(out.index, supplied_gap)
    out["_data_gap"] = gap
    upper = pd.Series(np.nan, index=out.index, dtype=float)
    lower = pd.Series(np.nan, index=out.index, dtype=float)
    target_upper = pd.Series(np.nan, index=out.index, dtype=float)
    target_lower = pd.Series(np.nan, index=out.index, dtype=float)
    for _, series in close.groupby(gap.cumsum(), sort=False):
        ix = series.index
        basis = series.rolling(spec.bb_length, min_periods=spec.bb_length).mean()
        std = series.rolling(spec.bb_length, min_periods=spec.bb_length).std(ddof=0)
        upper.loc[ix] = basis + spec.bb_mult * std
        lower.loc[ix] = basis - spec.bb_mult * std
        prior = series.shift(1).rolling(spec.bb_length - 1, min_periods=spec.bb_length - 1)
        prior_mean = prior.mean()
        prior_std = prior.std(ddof=0)
        delta = spec.bb_mult * prior_std * math.sqrt(spec.bb_length / (spec.bb_length - 1.0 - spec.bb_mult * spec.bb_mult))
        target_upper.loc[ix] = np.ceil((prior_mean + delta) / _TICK) * _TICK
        target_lower.loc[ix] = np.floor((prior_mean - delta) / _TICK) * _TICK
    arrows = _stoch_arrows(out, spec)
    signal = pd.Series(0, index=out.index, dtype=int)
    signal.loc[out.high.lt(lower) & arrows.arrow_long] = 1
    signal.loc[out.low.gt(upper) & arrows.arrow_short] = -1
    out["upper"], out["lower"] = upper, lower
    out["k"], out["d"] = arrows.k, arrows.d
    out["signal"] = signal
    out["target_upper"], out["target_lower"] = target_upper, target_lower
    return out


def _feature_array(frame: pd.DataFrame, name: str, *, finite: bool) -> np.ndarray:
    values = pd.to_numeric(frame[name], errors="coerce").to_numpy(float, copy=True)
    if finite and not np.isfinite(values).all():
        raise ValueError(f"{name} values must be finite")
    values.setflags(write=False)
    return values


def prepare(frame: pd.DataFrame, spec: ParamSpec = ParamSpec()) -> Prepared:
    """Validate already-computed features once and copy immutable replay arrays."""
    spec = _require_spec(spec)
    _valid_frame(frame, need_features=True)
    signals_float = _feature_array(frame, "signal", finite=True)
    if not np.isin(signals_float, (-1.0, 0.0, 1.0)).all():
        raise ValueError("signal values must be -1, 0, or 1")
    signals = signals_float.astype(int, copy=True)
    signals.setflags(write=False)
    supplied_gap = frame["_data_gap"] if "_data_gap" in frame else frame.get("data_gap")
    gaps = _gaps(frame.index, supplied_gap).to_numpy(bool, copy=True)
    gaps.setflags(write=False)
    return Prepared(
        index=frame.index.copy(), spec=spec,
        opens=_feature_array(frame, "open", finite=True),
        highs=_feature_array(frame, "high", finite=True),
        lows=_feature_array(frame, "low", finite=True),
        closes=_feature_array(frame, "close", finite=True),
        signals=signals,
        target_upper=_feature_array(frame, "target_upper", finite=False),
        target_lower=_feature_array(frame, "target_lower", finite=False),
        gaps=gaps,
    )


def _valid_end(end_i: int | None, size: int) -> int:
    if end_i is None:
        return size
    if isinstance(end_i, (bool, np.bool_)) or not isinstance(end_i, Integral) or not 0 <= int(end_i) <= size:
        raise ValueError("end_i must be an exclusive frame bound")
    return int(end_i)


def _cross(a: float, b: float, price: float, *, upward: bool) -> bool:
    """Whether a monotone segment reaches a resting threshold after its start."""
    return a < price <= b if upward else a > price >= b


def replay_entry(prepared: Prepared, signal_i: int, *, side_override: int | None = None,
                 end_i: int | None = None, path_mode: Literal["tv", "adverse_first"] = "tv") -> dict[str, object] | None:
    """Replay one prepared close-confirmed signal as the frozen one-ETH trade.

    The entry is the next observed open.  A target fills half, moves the tail to
    entry after the actual fill, and a complete opposite signal closes remaining
    quantity at that bar's close.  Data gaps and exclusive boundaries censor
    the unliquidated runner without creating an exit fill or fee.
    """
    if not isinstance(prepared, Prepared):
        raise TypeError("prepared must come from prepare()")
    size = len(prepared.index)
    if isinstance(signal_i, (bool, np.bool_)) or not isinstance(signal_i, Integral) or not 0 <= int(signal_i) < size:
        raise ValueError("signal_i must be a valid frame position")
    if side_override is not None and (isinstance(side_override, (bool, np.bool_)) or not isinstance(side_override, Integral) or int(side_override) not in (-1, 1)):
        raise ValueError("side_override must be +1, -1, or None")
    if path_mode not in {"tv", "adverse_first"}:
        raise ValueError("path_mode must be tv or adverse_first")
    signal_i, end = int(signal_i), _valid_end(end_i, size)
    side = int(prepared.signals[signal_i]) if side_override is None else int(side_override)
    entry_i = signal_i + 1
    if side not in (-1, 1) or entry_i >= end or prepared.gaps[entry_i]:
        return None
    opens, highs, lows, closes = prepared.opens, prepared.highs, prepared.lows, prepared.closes
    targets = prepared.target_upper if side == 1 else prepared.target_lower
    target = float(targets[entry_i])
    if not math.isfinite(target):
        return None
    entry = float(opens[entry_i])
    risk = entry * prepared.spec.stop_fraction
    initial_stop = entry - side * risk
    qty, partial, protection = 1.0, False, initial_stop
    fees, gross_pnl = entry * _FEE_RATE, 0.0
    fills: list[dict[str, object]] = [{"i": entry_i, "phase": "entry", "reason": "next_open", "price": entry, "qty": 1.0}]
    tp_i: int | None = None
    tp_price: float | None = None
    ambiguous = 0
    entry_open_beyond_target = entry >= target if side == 1 else entry <= target
    same_open_be_approximation = False
    marketable_be_approximation = False

    def exit_fill(i: int, price: float, reason: str, amount: float, phase: str) -> None:
        nonlocal qty, fees, gross_pnl
        gross_pnl += side * (price - entry) * amount
        fees += price * amount * _FEE_RATE
        qty -= amount
        fills.append({"i": i, "phase": phase, "reason": reason, "price": float(price), "qty": amount})

    def finish(i: int, price: float, reason: str, *, censored: bool = False) -> dict[str, object]:
        marked_gross = gross_pnl + side * (price - entry) * qty
        total_fees = fees if censored else fees + price * qty * _FEE_RATE
        if not censored and qty > 0:
            exit_fill(i, price, reason, qty, "tail" if partial else "full")
            marked_gross, total_fees = gross_pnl, fees
        net_pnl = marked_gross - total_fees
        return {
            "signal_i": signal_i, "entry_i": entry_i, "exit_i": i, "side": side,
            "entry_price": entry, "initial_risk": risk, "initial_stop": initial_stop,
            "exit_price": float(price), "exit_reason": reason, "tp_i": tp_i, "tp_price": tp_price,
            "partial": partial, "gross_r": marked_gross / risk, "net_r": net_pnl / risk,
            "tail_price_r": side * (float(price) - entry) / risk,
            "gross_pnl": marked_gross, "fees": total_fees, "net_pnl": net_pnl,
            "censored": censored, "ambiguous_bar_count": ambiguous,
            "entry_open_beyond_target": entry_open_beyond_target,
            "same_open_be_approximation": same_open_be_approximation,
            "marketable_be_approximation": marketable_be_approximation, "fills": fills,
        }

    def take_partial(i: int, price: float, reason: str) -> None:
        nonlocal partial, protection, tp_i, tp_price
        exit_fill(i, price, reason, 0.5, "partial")
        partial, protection, tp_i, tp_price = True, entry, i, float(price)

    for i in range(entry_i, end):
        if prepared.gaps[i]:
            return finish(i - 1, float(closes[i - 1]), "data_gap_censor", censored=True)
        opening, high, low, close = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
        if not partial:
            target = float(targets[i])
            if not math.isfinite(target):
                return finish(i - 1, float(closes[i - 1]), "target_unavailable_censor", censored=True)
            spans_stop = low <= protection if side == 1 else high >= protection
            spans_target = high >= target if side == 1 else low <= target
            spans_entry = low <= entry if side == 1 else high >= entry
            if spans_target and (spans_stop or spans_entry):
                ambiguous += 1
        stopped_open = opening <= protection if side == 1 else opening >= protection
        target_open = not partial and (opening >= target if side == 1 else opening <= target)
        if stopped_open:
            return finish(i, opening, "break_even_gap" if partial else "initial_stop_gap")
        if target_open:
            take_partial(i, opening, "take_profit_gap")
            if opening <= protection if side == 1 else opening >= protection:
                same_open_be_approximation = True
                return finish(i, opening, "break_even_gap")
        if path_mode == "adverse_first":
            points = [opening, low, high, close] if side == 1 else [opening, high, low, close]
        elif abs(opening - high) < abs(opening - low):
            points = [opening, high, low, close]
        else:
            points = [opening, low, high, close]
        for end_price in points[1:]:
            start = points[0]
            while True:
                hits_stop = _cross(start, end_price, protection, upward=side == -1)
                hits_target = not partial and _cross(start, end_price, target, upward=side == 1)
                if not hits_stop and not hits_target:
                    break
                if hits_stop:
                    event, event_price = "stop", protection
                else:
                    event, event_price = "target", target
                if hits_stop and hits_target:
                    ambiguous += 1
                if event == "stop":
                    return finish(i, event_price, "break_even" if partial else "initial_stop")
                take_partial(i, event_price, "take_profit")
                if event_price <= protection if side == 1 else event_price >= protection:
                    marketable_be_approximation = True
                    return finish(i, event_price, "break_even_marketable")
                start = event_price
            points[0] = end_price
        if int(prepared.signals[i]) == -side:
            return finish(i, close, "opposite_signal_close")
    last = end - 1
    return finish(last, float(closes[last]), "boundary_censor", censored=True)
