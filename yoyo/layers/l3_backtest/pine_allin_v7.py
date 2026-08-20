"""Causal 15m research replay for the user-supplied ALLIN-V7.2 Pine logic.

Signal columns and windows are deliberately explicit.  At decision bar ``t``
the module uses only ``open/high/low/close`` and ``open_time`` through ``t``:
SMA(hl2, 10/40/60), EMA(close, 100), Pine-style ATR/RMA(14), a 200-bar
linear-interpolation percentile of ``hl2 - SMA40``, a 10-bar difference, and
HMA(10).  Entry is the next bar's open.  Future high/low/close are consulted
only after entry to simulate exits and labels; they never alter a signal.

This is an auditable Python translation, not a Pine compiler.  It intentionally
implements the safer confirmed-bar contract used by the optimized script:
initial protection is active from the entry fill, and a break-even update based
on a completed bar becomes effective on the following bar.  TradingView trade
export parity is therefore a required gate before any deployment claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

EXPECTED_BAR = pd.Timedelta(minutes=15)
HK_TZ = "Asia/Hong_Kong"


@dataclass(frozen=True)
class SignalParameters:
    """Frozen v7.2 signal/barrier parameters; changes require a new arm."""

    fast_len: int = 10
    slow_len: int = 60
    regime_len: int = 100
    atr_len: int = 14
    atr_mult: float = 4.0
    max_sl_percent: float = 3.0
    osc_basis_len: int = 40
    osc_percentile_len: int = 200
    osc_percentile: float = 99.0
    osc_change_lag: int = 10
    osc_hma_len: int = 10
    osc_threshold: float = 0.2
    min_atr_percent: float = 0.1
    max_atr_percent: float = 10.0
    break_even_trigger_percent: float = 1.5
    break_even_offset_percent: float = 0.1
    trailing_trigger_percent: float = 2.5
    trailing_distance_percent: float = 1.0


@dataclass(frozen=True)
class ExecutionParameters:
    """Broker/accounting semantics for one replay.

    ``legacy`` values preserve the first 54-symbol Python audit.  The 15-minute
    ETH contract opts into Pine-V8 semantics explicitly: stop ticks and target
    quantity are frozen on the confirmed signal close, the stop is rounded to
    ``tick_size``, and commission is charged on both fill notionals.  Pine's
    ``strategy.equity`` includes marked open P/L when an opposite signal submits
    a reversal order, so ``sizing_equity_basis='signal_marked'`` also freezes
    that signal-time equity rather than recomputing size after the next open.
    """

    stop_distance_basis: str = "entry"
    sizing_price_basis: str = "entry"
    sizing_equity_basis: str = "entry_realized"
    tick_size: float | None = None
    commission_per_side: float | None = None
    skip_return_basis: str = "gross"
    force_close_at_end: bool = True
    equity_frequency: str | None = "1D"


@dataclass(frozen=True)
class Arm:
    """One preregistered backtest arm."""

    name: str
    signal_kind: str
    sizing_kind: str
    base_leverage: float = 4.0
    risk_per_trade_percent: float = 2.0
    max_leverage: float = 13.0
    time_boosts: bool = False
    skip_logic: bool = True
    use_break_even: bool = True
    use_trailing_stop: bool = False
    opposite_signal_action: str = "reverse"
    entry_directions: tuple[int, ...] = (-1, 1)


@dataclass
class Position:
    direction: int
    signal_i: int
    entry_i: int
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_price: float
    entry_equity: float
    notional: float
    quantity: float
    leverage: float
    stop_price: float
    initial_stop_price: float
    initial_stop_distance: float
    score: float


def pine_rma(values: Sequence[float], length: int) -> np.ndarray:
    """Pine/Wilder RMA: SMA seed, then ``(prev*(n-1)+x)/n``."""

    array = np.asarray(values, dtype=float)
    out = np.full(array.shape, np.nan, dtype=float)
    if length <= 0:
        raise ValueError("length must be positive")
    for start in range(0, max(0, len(array) - length + 1)):
        seed = array[start : start + length]
        if np.isfinite(seed).all():
            seed_i = start + length - 1
            out[seed_i] = float(seed.mean())
            for i in range(seed_i + 1, len(array)):
                value = array[i]
                if not np.isfinite(value):
                    out[i] = out[i - 1]
                else:
                    out[i] = (out[i - 1] * (length - 1) + value) / length
            break
    return out


def _wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(weights.sum())
    return series.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / denominator), raw=True
    )


def _hma(series: pd.Series, length: int) -> pd.Series:
    half = max(1, length // 2)
    root = max(1, int(round(np.sqrt(length))))
    return _wma(2.0 * _wma(series, half) - _wma(series, length), root)


def _true_range(frame: pd.DataFrame) -> np.ndarray:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    previous = np.r_[np.nan, close[:-1]]
    return np.nanmax(
        np.vstack((high - low, np.abs(high - previous), np.abs(low - previous))),
        axis=0,
    )


def in_hour_window(hours: Sequence[int], start: int, end: int) -> np.ndarray:
    """Return half-open HK-hour membership, including overnight windows."""

    values = np.asarray(hours, dtype=int)
    if start == end:
        return np.zeros(values.shape, dtype=bool)
    if start < end:
        return (values >= start) & (values < end)
    return (values >= start) | (values < end)


def add_indicators(frame: pd.DataFrame, params: SignalParameters) -> pd.DataFrame:
    """Add causal Pine-equivalent indicators and confirmed-bar signals."""

    required = {"open_time", "open", "high", "low", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    out = frame.copy()
    source = (out["high"].astype(float) + out["low"].astype(float)) / 2.0
    out["fast_ma"] = source.rolling(params.fast_len, min_periods=params.fast_len).mean()
    out["slow_ma"] = source.rolling(params.slow_len, min_periods=params.slow_len).mean()
    out["regime_ma"] = out["close"].astype(float).ewm(
        span=params.regime_len, adjust=False, min_periods=1
    ).mean()
    out["atr"] = pine_rma(_true_range(out), params.atr_len)
    out["atr_percent"] = out["atr"] / out["close"].astype(float) * 100.0

    basis = source.rolling(params.osc_basis_len, min_periods=params.osc_basis_len).mean()
    difference = source - basis
    percentile = difference.rolling(
        params.osc_percentile_len,
        min_periods=params.osc_percentile_len,
    ).quantile(params.osc_percentile / 100.0, interpolation="linear")
    ratio = pd.Series(
        np.divide(
            difference.to_numpy(dtype=float),
            percentile.to_numpy(dtype=float),
            out=np.zeros(len(out), dtype=float),
            where=np.isfinite(percentile.to_numpy(dtype=float))
            & (percentile.to_numpy(dtype=float) != 0.0),
        ),
        index=out.index,
    )
    change_ratio = ratio - ratio.shift(params.osc_change_lag)
    out["osc"] = _hma(change_ratio, params.osc_hma_len)

    fast = out["fast_ma"]
    slow = out["slow_ma"]
    cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    osc_rising = (out["osc"] > params.osc_threshold) & (out["osc"] > out["osc"].shift(1))
    osc_falling = (out["osc"] < -params.osc_threshold) & (out["osc"] < out["osc"].shift(1))
    filter_long = (out["close"] > slow) & (out["close"] > out["regime_ma"])
    filter_short = (out["close"] < slow) & (out["close"] < out["regime_ma"])
    out["v7_long"] = (cross_up & filter_long & osc_rising).fillna(False)
    out["v7_short"] = (cross_down & filter_short & osc_falling).fillna(False)
    out["cross_long"] = cross_up.fillna(False)
    out["cross_short"] = cross_down.fillna(False)
    out["v7_score"] = out["osc"].abs()
    out["cross_score"] = ((fast - slow).abs() / out["close"].astype(float)).fillna(0.0)

    hk = pd.to_datetime(out["open_time"], utc=True).dt.tz_convert(HK_TZ)
    blocked = in_hour_window(hk.dt.hour.to_numpy(), 21, 23)
    out["calendar_allowed"] = (~blocked) & (hk.dt.dayofweek.to_numpy() != 6)
    out["volatility_allowed"] = out["atr_percent"].between(
        params.min_atr_percent, params.max_atr_percent, inclusive="both"
    )
    out["entry_allowed"] = out["calendar_allowed"] & out["volatility_allowed"]
    out["hk_hour"] = hk.dt.hour.to_numpy(dtype=int)
    out["hk_dayofweek"] = hk.dt.dayofweek.to_numpy(dtype=int)
    return out


def load_development_frame(
    path: Path,
    *,
    safe_end: pd.Timestamp,
    holdout_start: pd.Timestamp,
    chunksize: int = 1_000,
) -> pd.DataFrame:
    """Read a bounded prefix and refuse any chunk that reaches holdout.

    The analysis end must precede holdout by more than one parser chunk under
    the expected 15m cadence.  This run uses a 64-day buffer versus a roughly
    10.4-day chunk, so no holdout row enters the parsed development prefix.
    """

    safe_end = pd.Timestamp(safe_end)
    holdout_start = pd.Timestamp(holdout_start)
    if safe_end.tzinfo is None:
        safe_end = safe_end.tz_localize("UTC")
    if holdout_start.tzinfo is None:
        holdout_start = holdout_start.tz_localize("UTC")
    chunk_span = EXPECTED_BAR * chunksize
    if safe_end + chunk_span >= holdout_start:
        raise ValueError("safe_end needs more than one parser chunk of holdout buffer")

    pieces: list[pd.DataFrame] = []
    columns = ["open_time", "open", "high", "low", "close", "volume"]
    for chunk in pd.read_csv(path, usecols=columns, chunksize=chunksize):
        times = pd.to_datetime(chunk["open_time"], utc=True, errors="raise")
        if bool((times >= holdout_start).any()):
            raise RuntimeError(f"parser reached holdout while reading {path}")
        keep = times < safe_end
        if bool(keep.any()):
            kept = chunk.loc[keep].copy()
            kept["open_time"] = times.loc[keep]
            pieces.append(kept)
        if bool((~keep).any()):
            break
    if not pieces:
        raise ValueError(f"no development rows in {path}")
    frame = pd.concat(pieces, ignore_index=True)
    frame = frame.sort_values("open_time").reset_index(drop=True)
    if frame["open_time"].duplicated().any():
        raise ValueError(f"duplicate timestamps in {path}")
    if frame["open_time"].max() >= safe_end:
        raise AssertionError("development truncation failed")
    numeric = frame[["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    if not np.isfinite(numeric[["open", "high", "low", "close"]].to_numpy()).all():
        raise ValueError(f"non-finite OHLC in {path}")
    if bool((numeric[["open", "high", "low", "close"]] <= 0).any().any()):
        raise ValueError(f"non-positive OHLC in {path}")
    if bool((numeric["high"] < numeric[["open", "close"]].max(axis=1)).any()):
        raise ValueError(f"high below body in {path}")
    if bool((numeric["low"] > numeric[["open", "close"]].min(axis=1)).any()):
        raise ValueError(f"low above body in {path}")
    frame[numeric.columns] = numeric
    return frame


def _position_leverage(
    arm: Arm,
    *,
    equity: float,
    entry_price: float,
    stop_distance: float,
    signal_hour: int,
    signal_dayofweek: int,
) -> float:
    if equity <= 0.0 or entry_price <= 0.0 or stop_distance <= 0.0:
        return 0.0
    if arm.sizing_kind == "risk":
        stop_fraction = stop_distance / entry_price
        raw = (arm.risk_per_trade_percent / 100.0) / stop_fraction
        return float(min(max(raw, 0.0), arm.max_leverage))
    if arm.sizing_kind != "fixed":
        raise ValueError(f"unknown sizing kind {arm.sizing_kind!r}")
    leverage = float(arm.base_leverage)
    if arm.time_boosts and signal_hour == 3:
        leverage *= 1.5
    if arm.time_boosts and signal_dayofweek == 3:
        leverage *= 2.0
    return float(min(leverage, arm.max_leverage))


def _signal_columns(arm: Arm) -> tuple[str, str, str]:
    if arm.signal_kind == "v7":
        return "v7_long", "v7_short", "v7_score"
    if arm.signal_kind == "sma_cross_only":
        return "cross_long", "cross_short", "cross_score"
    raise ValueError(f"unknown signal kind {arm.signal_kind!r}")


def simulate_symbol(
    frame: pd.DataFrame,
    *,
    symbol: str,
    arm: Arm,
    start: pd.Timestamp,
    end: pd.Timestamp,
    params: SignalParameters,
    round_trip_cost: float,
    initial_capital: float = 500.0,
    execution: ExecutionParameters | None = None,
    signal_columns: tuple[str, str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate one symbol and return trades plus daily marked equity."""

    if not 0.0 <= round_trip_cost < 1.0:
        raise ValueError("round_trip_cost must be a fraction")
    execution = execution or ExecutionParameters()
    if execution.stop_distance_basis not in {"entry", "signal_close"}:
        raise ValueError("stop_distance_basis must be entry|signal_close")
    if execution.sizing_price_basis not in {"entry", "signal_close"}:
        raise ValueError("sizing_price_basis must be entry|signal_close")
    if execution.sizing_equity_basis not in {"entry_realized", "signal_marked"}:
        raise ValueError("sizing_equity_basis must be entry_realized|signal_marked")
    if execution.tick_size is not None and execution.tick_size <= 0.0:
        raise ValueError("tick_size must be positive when supplied")
    if execution.commission_per_side is not None and not (
        0.0 <= execution.commission_per_side < 1.0
    ):
        raise ValueError("commission_per_side must be a fraction")
    if execution.skip_return_basis not in {"gross", "net"}:
        raise ValueError("skip_return_basis must be gross|net")
    if arm.opposite_signal_action not in {"reverse", "close_only"}:
        raise ValueError("opposite_signal_action must be reverse|close_only")
    if not arm.entry_directions or any(
        direction not in {-1, 1} for direction in arm.entry_directions
    ):
        raise ValueError("entry_directions must be a non-empty subset of (-1, 1)")
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    if end.tzinfo is None:
        end = end.tz_localize("UTC")
    times = pd.to_datetime(frame["open_time"], utc=True)
    active_indices = np.flatnonzero(((times >= start) & (times < end)).to_numpy())
    if len(active_indices) < 2:
        return pd.DataFrame(), pd.DataFrame()
    first_i, last_i = int(active_indices[0]), int(active_indices[-1])

    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    allowed = frame["entry_allowed"].fillna(False).to_numpy(dtype=bool)
    hours = frame["hk_hour"].to_numpy(dtype=int)
    weekdays = frame["hk_dayofweek"].to_numpy(dtype=int)
    long_col, short_col, score_col = signal_columns or _signal_columns(arm)
    raw_long = frame[long_col].fillna(False).to_numpy(dtype=bool)
    raw_short = frame[short_col].fillna(False).to_numpy(dtype=bool)
    scores = frame[score_col].fillna(0.0).to_numpy(dtype=float)

    equity = float(initial_capital)
    position: Position | None = None
    pending_direction = 0
    pending_signal_i = -1
    pending_sizing_equity = float("nan")
    trades_to_skip = 0
    trade_rows: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []

    def close_position(exit_i: int, exit_price: float, reason: str) -> float:
        nonlocal equity, position, trades_to_skip
        assert position is not None
        exit_price = float(exit_price)
        price_ratio = exit_price / position.entry_price
        gross = position.direction * (price_ratio - 1.0)
        project_net = gross - round_trip_cost
        if execution.commission_per_side is None:
            commission_return = round_trip_cost
            net = project_net
        else:
            commission_return = execution.commission_per_side * (1.0 + price_ratio)
            net = gross - commission_return
        # The replay has no exchange-specific liquidation engine.  Cap loss at
        # account equity rather than inventing a negative-balance facility; a
        # zero account is recorded as bankruptcy and stops taking new orders.
        pnl = max(position.notional * net, -equity)
        equity = equity + pnl
        row = {
            "arm": arm.name,
            "symbol": symbol,
            "direction": "long" if position.direction > 0 else "short",
            "signal_i": position.signal_i,
            "entry_i": position.entry_i,
            "exit_i": int(exit_i),
            "signal_time": position.signal_time,
            "entry_time": position.entry_time,
            "exit_time": times.iloc[int(exit_i)],
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "quantity": position.quantity,
            "initial_stop_price": position.initial_stop_price,
            "initial_stop_distance": position.initial_stop_distance,
            "holding_bars": int(exit_i - position.entry_i),
            "exit_reason": reason,
            "score": position.score,
            "leverage": position.leverage,
            "gross_return": gross,
            "net_return": net,
            "project_net_return": project_net,
            "commission_return": commission_return,
            "pnl": pnl,
            "entry_equity": position.entry_equity,
            "exit_equity": equity,
        }
        trade_rows.append(row)
        if arm.skip_logic:
            skip_return = net if execution.skip_return_basis == "net" else gross
            if skip_return * 100.0 > 20.0:
                trades_to_skip = 7
            elif skip_return * 100.0 > 2.0:
                trades_to_skip = 1
        position = None
        return gross

    def signal_marked_equity(i: int) -> float:
        """Return Pine-like equity visible when the confirmed signal is submitted."""

        if position is None:
            return equity
        open_pnl = position.notional * position.direction * (
            float(close[i]) / position.entry_price - 1.0
        )
        entry_fee = (
            position.notional * execution.commission_per_side
            if execution.commission_per_side is not None
            else 0.0
        )
        return equity + open_pnl - entry_fee

    def open_position(
        i: int,
        direction: int,
        signal_i: int,
        sizing_equity: float,
    ) -> None:
        nonlocal position
        if equity <= 0.0 or sizing_equity <= 0.0 or not np.isfinite(atr[signal_i]):
            return
        entry_price = float(open_[i])
        signal_price = float(close[signal_i])
        stop_reference = (
            signal_price if execution.stop_distance_basis == "signal_close" else entry_price
        )
        stop_distance = min(
            float(atr[signal_i]) * params.atr_mult,
            stop_reference * params.max_sl_percent / 100.0,
        )
        if execution.tick_size is not None:
            stop_ticks = max(1, int(round(stop_distance / execution.tick_size)))
            stop_distance = stop_ticks * execution.tick_size
        sizing_price = (
            signal_price if execution.sizing_price_basis == "signal_close" else entry_price
        )
        target_leverage = _position_leverage(
            arm,
            equity=sizing_equity,
            entry_price=sizing_price,
            stop_distance=stop_distance,
            signal_hour=int(hours[signal_i]),
            signal_dayofweek=int(weekdays[signal_i]),
        )
        if target_leverage <= 0.0:
            return
        target_notional = sizing_equity * target_leverage
        quantity = target_notional / sizing_price
        notional = quantity * entry_price
        leverage = notional / equity
        stop_price = entry_price - direction * stop_distance
        position = Position(
            direction=direction,
            signal_i=signal_i,
            entry_i=i,
            signal_time=times.iloc[signal_i],
            entry_time=times.iloc[i],
            entry_price=entry_price,
            entry_equity=equity,
            notional=notional,
            quantity=quantity,
            leverage=leverage,
            stop_price=stop_price,
            initial_stop_price=stop_price,
            initial_stop_distance=stop_distance,
            score=float(scores[signal_i]),
        )

    for i in range(first_i, last_i + 1):
        # Confirmed signal on t-1 becomes an order at t open.  One reversal
        # order closes and opens; there is no duplicate strategy.close order.
        if pending_direction and pending_signal_i == i - 1:
            closed_opposite = False
            if position is not None and position.direction != pending_direction:
                close_position(i, float(open_[i]), "reverse")
                closed_opposite = True
            if (
                position is None
                and equity > 0.0
                and not (closed_opposite and arm.opposite_signal_action == "close_only")
                and pending_direction in arm.entry_directions
            ):
                open_position(
                    i,
                    pending_direction,
                    pending_signal_i,
                    pending_sizing_equity,
                )
            pending_direction = 0
            pending_signal_i = -1
            pending_sizing_equity = float("nan")

        # The initial protective stop is live from the entry fill.  Gaps beyond
        # the stop fill at the bar open; otherwise the stop fills at its price.
        if position is not None:
            if position.direction > 0 and low[i] <= position.stop_price:
                fill = min(float(open_[i]), position.stop_price)
                close_position(i, fill, "stop")
            elif position is not None and position.direction < 0 and high[i] >= position.stop_price:
                fill = max(float(open_[i]), position.stop_price)
                close_position(i, fill, "stop")

        # A break-even trigger seen on a completed bar can protect only the next
        # bar in this confirmed-bar replay.  This avoids historical intrabar
        # information that calc_on_every_tick cannot reproduce after reload.
        if position is not None and arm.use_break_even:
            if position.direction > 0 and high[i] >= position.entry_price * (
                1.0 + params.break_even_trigger_percent / 100.0
            ):
                position.stop_price = max(
                    position.stop_price,
                    position.entry_price * (1.0 + params.break_even_offset_percent / 100.0),
                )
            elif position.direction < 0 and low[i] <= position.entry_price * (
                1.0 - params.break_even_trigger_percent / 100.0
            ):
                position.stop_price = min(
                    position.stop_price,
                    position.entry_price * (1.0 - params.break_even_offset_percent / 100.0),
                )

        # Like the confirmed-bar break-even update, a trailing update based on
        # this completed bar becomes an order for the next bar.  No intrabar
        # future path is used to move the stop before the trigger is observed.
        if position is not None and arm.use_trailing_stop:
            if position.direction > 0 and high[i] >= position.entry_price * (
                1.0 + params.trailing_trigger_percent / 100.0
            ):
                position.stop_price = max(
                    position.stop_price,
                    float(high[i]) * (1.0 - params.trailing_distance_percent / 100.0),
                )
            elif position.direction < 0 and low[i] <= position.entry_price * (
                1.0 - params.trailing_trigger_percent / 100.0
            ):
                position.stop_price = min(
                    position.stop_price,
                    float(low[i]) * (1.0 + params.trailing_distance_percent / 100.0),
                )

        raw_direction = 1 if raw_long[i] else (-1 if raw_short[i] else 0)
        if raw_direction:
            if trades_to_skip > 0:
                trades_to_skip -= 1
            elif allowed[i] and equity > 0.0:
                if position is None or position.direction != raw_direction:
                    candidate_sizing_equity = (
                        signal_marked_equity(i)
                        if execution.sizing_equity_basis == "signal_marked"
                        else equity
                    )
                    if candidate_sizing_equity > 0.0:
                        pending_direction = raw_direction
                        pending_signal_i = i
                        pending_sizing_equity = candidate_sizing_equity

        if position is None:
            marked = equity
        else:
            open_pnl = position.notional * position.direction * (
                float(close[i]) / position.entry_price - 1.0
            )
            entry_fee = (
                position.notional * execution.commission_per_side
                if execution.commission_per_side is not None
                else 0.0
            )
            marked = equity + open_pnl - entry_fee
        equity_rows.append({"open_time": times.iloc[i], "equity": marked})
        if equity <= 0.0:
            pending_direction = 0
            pending_sizing_equity = float("nan")
            break

    if position is not None and execution.force_close_at_end:
        close_position(last_i, float(close[last_i]), "period_end")
        equity_rows.append({"open_time": times.iloc[last_i], "equity": equity})

    trades = pd.DataFrame(trade_rows)
    daily = pd.DataFrame(equity_rows)
    if not daily.empty:
        marked = daily.set_index("open_time").sort_index()["equity"]
        if execution.equity_frequency is not None:
            marked = marked.resample(execution.equity_frequency).last().ffill()
        daily = marked.div(initial_capital).rename("normalized_equity").reset_index()
        daily["arm"] = arm.name
        daily["symbol"] = symbol
    return trades, daily


def auc_from_scores(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Tie-aware binary AUC without a sklearn dependency."""

    score = pd.Series(np.asarray(scores, dtype=float))
    label = np.asarray(labels, dtype=bool)
    positives = int(label.sum())
    negatives = int((~label).sum())
    if positives == 0 or negatives == 0:
        return float("nan")
    ranks = score.rank(method="average").to_numpy(dtype=float)
    rank_sum = float(ranks[label].sum())
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def max_drawdown(equity: Sequence[float]) -> float:
    values = np.asarray(equity, dtype=float)
    if values.size == 0:
        return float("nan")
    peaks = np.maximum.accumulate(values)
    drawdowns = np.divide(values, peaks, out=np.ones_like(values), where=peaks != 0.0) - 1.0
    return float(-np.nanmin(drawdowns))


def profit_factor(pnl: Sequence[float]) -> float:
    values = np.asarray(pnl, dtype=float)
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    if losses == 0.0:
        return float("inf") if gains > 0.0 else float("nan")
    return gains / losses


def deterministic_control_indices(
    candidate_id: str,
    candidates: Iterable[int],
    *,
    n: int,
    seed: str,
) -> list[int]:
    scored = []
    for index in set(int(v) for v in candidates):
        digest = sha256(f"{seed}|{candidate_id}|{index}".encode("utf-8")).hexdigest()
        scored.append((digest, index))
    scored.sort()
    return [index for _, index in scored[:n]]
