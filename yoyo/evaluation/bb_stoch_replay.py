"""Bounded offline replay for the owner-supplied ETH BB × Stoch v2 formula.

The module is deliberately a formula and event-path model, not a broker-parity
claim.  ``compute_features`` uses only closed OHLC rows through each decision
bar.  In particular, its resting BB-touch target is solved from the preceding
199 closes, so a completed bar's final BB is never backdated into that bar's
wick.  ``replay_entry`` then opens on the following observed open and processes
one independent 1 ETH position.  It neither loads prices nor creates entries.

TradingView's bar path is unknowable from OHLC alone.  ``path_mode='tv'`` uses
the documented nearest-extreme convention requested for this study; the fixed
``'adverse_first'`` path is a fixed sensitivity scenario, not an optimised
parameter.
An entry whose observed opening is already through its resting target is marked
``entry_open_beyond_target``.  The native broker's exact same-open order timing
cannot be recovered from bars, so this replay closes that target tranche at the
opening price and reports the flag rather than claiming exact fill parity.
"""
from __future__ import annotations

import math
from numbers import Integral
from typing import Literal

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_fanshen_exit import compute_signals as _original_arrows


_OHLC = ("open", "high", "low", "close")
_N = 200
_TICK = 0.01
_FEE_RATE = 0.001
_STOP_FRACTION = 0.03


def _valid_frame(frame: pd.DataFrame, *, need_features: bool = False) -> None:
    """Validate chronological closed bars without fetching or filling any data."""
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


def compute_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Calculate causal BB/Stoch features and next-bar resting targets.

    Required columns are ``open``, ``high``, ``low``, and ``close``.  The
    ``upper``/``lower`` signal bands are population-standard-deviation BB(200)
    on the current close and drive the strict whole-candle tests.  ``k`` and
    ``d`` plus the original arrow predicates come directly from
    :func:`spike_fanshen_exit.compute_signals`; only ``arrow_long`` and
    ``arrow_short`` are consumed, deliberately excluding its WVF alert gate.
    ``target_upper`` and ``target_lower`` at row ``i`` use close rows
    ``i-199`` through ``i-1`` only, then ceil/floor the solved value to 0.01.
    """
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
    segment = gap.cumsum()
    # A missing five-minute stretch starts a new closed-bar history.  It cannot
    # manufacture an indicator value by silently joining prices across it.
    for _, series in close.groupby(segment, sort=False):
        ix = series.index
        basis = series.rolling(_N, min_periods=_N).mean()
        std = series.rolling(_N, min_periods=_N).std(ddof=0)
        upper.loc[ix] = basis + 2.0 * std
        lower.loc[ix] = basis - 2.0 * std
        prior_mean = series.shift(1).rolling(_N - 1, min_periods=_N - 1).mean()
        prior_std = series.shift(1).rolling(_N - 1, min_periods=_N - 1).std(ddof=0)
        delta = 2.0 * prior_std * math.sqrt(_N / (_N - 1.0 - 4.0))
        # Vector arithmetic keeps 50k-bar studies out of Python row loops.
        # Pine's f_limit is literal ceil/floor, without an epsilon adjustment.
        target_upper.loc[ix] = np.ceil((prior_mean + delta) / _TICK) * _TICK
        target_lower.loc[ix] = np.floor((prior_mean - delta) / _TICK) * _TICK
    arrows = _original_arrows(out.loc[:, ["high", "low", "close"]])
    green, red = arrows.arrow_long.astype(bool), arrows.arrow_short.astype(bool)
    signal = pd.Series(0, index=out.index, dtype=int)
    signal.loc[out.high.lt(lower) & green] = 1
    signal.loc[out.low.gt(upper) & red] = -1
    out["upper"], out["lower"] = upper, lower
    out["k"], out["d"] = arrows.k, arrows.d
    out["signal"] = signal
    out["target_upper"], out["target_lower"] = target_upper, target_lower
    return out


def _valid_end(end_i: int | None, size: int) -> int:
    if end_i is None:
        return size
    if isinstance(end_i, (bool, np.bool_)) or not isinstance(end_i, Integral) or not 0 <= int(end_i) <= size:
        raise ValueError("end_i must be an exclusive frame bound")
    return int(end_i)


def _cross(a: float, b: float, price: float, *, upward: bool) -> bool:
    """Whether a monotone segment reaches a resting threshold after its start."""
    return a < price <= b if upward else a > price >= b


def replay_entry(features: pd.DataFrame, signal_i: int, *, side_override: int | None = None,
                 end_i: int | None = None, path_mode: Literal["tv", "adverse_first"] = "tv") -> dict[str, object] | None:
    """Replay one close-confirmed BB/Stoch decision as a bounded 1 ETH trade.

    A decision at ``signal_i`` enters at ``signal_i + 1``'s observed opening.
    The first target fill sells/buys 0.5 ETH and immediately protects the
    remaining 0.5 ETH at entry; a same-bar pre-target wick is never replayed
    after this state change.  Stop and target opening gaps use adverse and
    favourable observed opens respectively.  A complete opposite composite
    signal exits whatever remains on that bar's close, after all price events.
    ``end_i`` and data gaps censor at the last complete close.
    """
    _valid_frame(features, need_features=True)
    size = len(features)
    if isinstance(signal_i, (bool, np.bool_)) or not isinstance(signal_i, Integral) or not 0 <= int(signal_i) < size:
        raise ValueError("signal_i must be a valid frame position")
    if side_override is not None and (isinstance(side_override, (bool, np.bool_)) or not isinstance(side_override, Integral) or int(side_override) not in (-1, 1)):
        raise ValueError("side_override must be +1, -1, or None")
    if path_mode not in {"tv", "adverse_first"}:
        raise ValueError("path_mode must be tv or adverse_first")
    signal_i, end = int(signal_i), _valid_end(end_i, size)
    side = int(features.signal.iloc[signal_i]) if side_override is None else int(side_override)
    entry_i = signal_i + 1
    if side not in (-1, 1) or entry_i >= end:
        return None
    gap = _gaps(features.index, features["_data_gap"] if "_data_gap" in features else features.get("data_gap")).to_numpy(bool)
    if gap[entry_i]:
        return None
    opens, highs, lows, closes = (features[name].to_numpy(float, copy=False) for name in _OHLC)
    signals = features.signal.to_numpy(int, copy=False)
    targets = (features.target_upper.to_numpy(float, copy=False) if side == 1
               else features.target_lower.to_numpy(float, copy=False))
    target = float(targets[entry_i])
    if not math.isfinite(target):
        return None
    entry = float(opens[entry_i])
    risk, initial_stop = entry * _STOP_FRACTION, entry - side * entry * _STOP_FRACTION
    qty, partial, protection = 1.0, False, initial_stop
    fees = entry * _FEE_RATE
    gross_pnl = 0.0
    fills: list[dict[str, object]] = [{"i": entry_i, "phase": "entry", "reason": "next_open", "price": entry, "qty": 1.0}]
    tp_i: int | None = None
    tp_price: float | None = None
    ambiguous = 0
    entry_open_beyond_target = (entry >= target if side == 1 else entry <= target)
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
        # Censoring marks the runner but does not invent an exit fill or its fee.
        total_fees = fees if censored else fees + price * qty * _FEE_RATE
        if not censored and qty > 0:
            exit_fill(i, price, reason, qty, "tail" if partial else "full")
            marked_gross = gross_pnl
            total_fees = fees
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
        if gap[i]:
            return finish(i - 1, float(closes[i - 1]), "data_gap_censor", censored=True)
        opening, high, low, close = float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])
        if not partial:
            target = float(targets[i])
            if not math.isfinite(target):
                return finish(i - 1, float(closes[i - 1]), "target_unavailable_censor", censored=True)
            spans_stop = low <= protection if side == 1 else high >= protection
            spans_target = high >= target if side == 1 else low <= target
            spans_entry = low <= entry if side == 1 else high >= entry
            # OHLC proves both prices occurred but cannot prove their order;
            # the selected fixed path resolves the trade while retaining this
            # audit count for path-sensitivity reporting.
            if spans_target and (spans_stop or spans_entry):
                ambiguous += 1
        # A known opening exists before the unknown intrabar path.  Protective
        # stops take priority if malformed inputs make both conditions true.
        stopped_open = opening <= protection if side == 1 else opening >= protection
        target_open = not partial and (opening >= target if side == 1 else opening <= target)
        if stopped_open:
            return finish(i, opening, "break_even_gap" if partial else "initial_stop_gap")
        if target_open:
            take_partial(i, opening, "take_profit_gap")
            # The native same-open activation order is not observable in OHLC.
            # Treat a newly marketable BE stop as an immediate observed-open
            # exit and expose the approximation for study/report filtering.
            if (opening <= protection if side == 1 else opening >= protection):
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
                stop_upward = side == -1
                target_upward = side == 1
                hits_stop = _cross(start, end_price, protection, upward=stop_upward)
                hits_target = (not partial and _cross(start, end_price, target, upward=target_upward))
                if not hits_stop and not hits_target:
                    break
                if hits_stop and hits_target:
                    ambiguous += 1
                    # In a numerical tie, retain protective-stop priority.
                    event, event_price = "stop", protection
                elif hits_stop:
                    event, event_price = "stop", protection
                else:
                    event, event_price = "target", target
                if event == "stop":
                    return finish(i, event_price, "break_even" if partial else "initial_stop")
                take_partial(i, event_price, "take_profit")
                # A target may dynamically move through entry while the
                # position is losing.  Once the target tranche fills there,
                # the new BE stop is already marketable.  OHLC cannot provide
                # its native order sequencing, so close the runner at the
                # target event price and retain a separate approximation flag.
                if (event_price <= protection if side == 1 else event_price >= protection):
                    marketable_be_approximation = True
                    return finish(i, event_price, "break_even_marketable")
                # Continue only the unobserved remainder of this monotone
                # segment from the target; earlier wick movement is not reused.
                start = event_price
            points[0] = end_price
        if int(signals[i]) == -side:
            return finish(i, close, "opposite_signal_close")
    last = end - 1
    return finish(last, float(closes[last]), "boundary_censor", censored=True)
