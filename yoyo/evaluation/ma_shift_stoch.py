"""Closed-bar 5m Stoch replay with a causal, completed-15m SMA40 filter.

The input index names each 5m bar's open.  A bar's signal is known at its
``open_time + 5 minutes`` close and executes only at the following bar open.
The 15m direction is made from three complete UTC-aligned 5m bars and is
visible only once that aggregate's right edge has closed.  This module is
offline research code: it has no data fetching, model, or execution imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_fanshen_exit import compute_signals


BAR = pd.Timedelta(minutes=5)
FIFTEEN = pd.Timedelta(minutes=15)
ROUND_TRIP_COST = 0.002
ONE_WAY_COST = 0.001
ExitMode = Literal["full_opposite", "two_opposite_half"]


@dataclass(frozen=True)
class BacktestResult:
    """Research ledgers; unfinished holdings are in ``open_positions`` only."""

    signals: pd.DataFrame
    trades: pd.DataFrame
    fills: pd.DataFrame
    open_positions: pd.DataFrame
    equity_curve: pd.DataFrame
    stats: dict[str, float | int]


@dataclass(frozen=True)
class ReplayResult:
    """One forced-side control entry with the same causal exit rules."""

    trade: pd.DataFrame
    fills: pd.DataFrame
    open_position: pd.DataFrame


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


_FILL_COLUMNS = [
    "trade_id", "fill_type", "time", "signal_time", "side", "price", "fraction",
    "entry_notional", "gross_return", "cost_return", "net_return", "reason",
]
_TRADE_COLUMNS = [
    "trade_id", "side", "entry_signal_time", "entry_time", "entry_price",
    "exit_signal_time", "exit_time", "exit_price", "exit_fraction", "gross_return",
    "net_return", "cost_return", "fill_count", "exit_mode", "exit_reason",
]
_OPEN_COLUMNS = [
    "trade_id", "side", "entry_signal_time", "entry_time", "entry_price",
    "remaining_fraction", "realised_gross_return", "realised_net_return", "marked_time",
    "marked_price", "marked_gross_return", "marked_net_return", "exit_mode",
]


def _utc(value: object, name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tz is None or stamp.utcoffset() != pd.Timedelta(0):
        raise ValueError(f"{name} must be a timezone-aware UTC timestamp")
    return stamp.tz_convert("UTC")


def _validate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Require complete, UTC, consecutive 5m OHLCV bars before replaying."""
    required = ("open", "high", "low", "close", "volume")
    if not isinstance(frame, pd.DataFrame) or not set(required).issubset(frame.columns):
        raise ValueError("frame requires open, high, low, close, and volume columns")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("frame index must be a timezone-aware UTC DatetimeIndex")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("frame index must be unique and chronological")
    if len(frame) == 0:
        return frame.loc[:, required].copy()
    if any(stamp.utcoffset() != pd.Timedelta(0) for stamp in frame.index):
        raise ValueError("frame index must be UTC")
    if len(frame) > 1 and not frame.index.to_series().diff().iloc[1:].eq(BAR).all():
        raise ValueError("5m OHLCV bars must be continuous")
    if not (frame.index.asi8 % BAR.value == 0).all():
        raise ValueError("frame index must lie on the UTC 5m grid")
    result = frame.loc[:, required].copy()
    for column in required:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    values = result.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("OHLCV values must be finite")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (result["low"] > result["high"]).any():
        raise ValueError("low cannot exceed high")
    if ((result["open"] < result["low"]) | (result["open"] > result["high"]) |
            (result["close"] < result["low"]) | (result["close"] > result["high"])).any():
        raise ValueError("open and close must lie within low/high")
    if (result["volume"] < 0).any():
        raise ValueError("volume cannot be negative")
    return result


def _validate_exit_mode(exit_mode: str) -> ExitMode:
    if exit_mode not in {"full_opposite", "two_opposite_half"}:
        raise ValueError("exit_mode must be full_opposite or two_opposite_half")
    return exit_mode  # type: ignore[return-value]


def build_signals(frame: pd.DataFrame) -> pd.DataFrame:
    """Return arrows and causal 15m direction aligned to each 5m close.

    The returned ``direction_15m`` is nullable until a full 15m aggregate has
    closed.  It never uses the in-progress 15m bar.  ``arrow_long`` and
    ``arrow_short`` are exactly the existing K5/SMA3/D3 strict-threshold
    arrows; WVF is deliberately not used as an entry condition.
    """
    ohlcv = _validate_frame(frame)
    if ohlcv.empty:
        return pd.DataFrame(index=ohlcv.index, columns=[
            "close_time", "arrow_long", "arrow_short", "direction_15m",
            "direction_15m_close_time", "entry_long", "entry_short",
        ])
    arrows = compute_signals(ohlcv)[["arrow_long", "arrow_short"]].copy()
    grouped = ohlcv.resample("15min", origin="epoch", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"), count=("close", "size"),
    )
    grouped = grouped.loc[grouped["count"].eq(3)].copy()
    grouped["hl2"] = (grouped["high"] + grouped["low"]) / 2.0
    grouped["sma40"] = grouped["hl2"].rolling(40, min_periods=40).mean()
    grouped["direction_15m"] = np.where(grouped["hl2"] >= grouped["sma40"], 1, -1)
    grouped.loc[grouped["sma40"].isna(), "direction_15m"] = np.nan
    completed = grouped.loc[grouped["direction_15m"].notna(), ["direction_15m"]].copy()
    completed["direction_15m_close_time"] = completed.index + FIFTEEN
    completed = completed.reset_index(drop=True).sort_values("direction_15m_close_time")
    closes = pd.DataFrame({"close_time": ohlcv.index + BAR})
    visible = pd.merge_asof(
        closes.sort_values("close_time"), completed,
        left_on="close_time", right_on="direction_15m_close_time", direction="backward",
    )
    result = arrows.copy()
    result["close_time"] = closes["close_time"].to_numpy()
    result["direction_15m"] = pd.array(visible["direction_15m"], dtype="Int64")
    result["direction_15m_close_time"] = visible["direction_15m_close_time"].to_numpy()
    result["entry_long"] = result["arrow_long"] & result["direction_15m"].eq(1).fillna(False)
    result["entry_short"] = result["arrow_short"] & result["direction_15m"].eq(-1).fillna(False)
    return result[["close_time", "arrow_long", "arrow_short", "direction_15m",
                   "direction_15m_close_time", "entry_long", "entry_short"]]


def _validate_signals(frame: pd.DataFrame, signals: pd.DataFrame) -> pd.DataFrame:
    required = {"arrow_long", "arrow_short"}
    if not isinstance(signals, pd.DataFrame) or not required.issubset(signals.columns):
        raise ValueError("signals requires arrow_long and arrow_short")
    if not signals.index.equals(frame.index):
        raise ValueError("signals index must exactly align to frame")
    result = signals.copy()
    for name in required:
        result[name] = result[name].fillna(False).astype(bool)
    if (result["arrow_long"] & result["arrow_short"]).any():
        raise ValueError("a 5m bar cannot contain both Stoch arrows")
    return result


def _direction_matches(signals: pd.DataFrame, i: int, side: int, use_ma_filter: bool) -> bool:
    if not use_ma_filter:
        return True
    if "direction_15m" not in signals:
        raise ValueError("MA-filtered replay requires direction_15m")
    value = signals["direction_15m"].iloc[i]
    return not pd.isna(value) and int(value) == side


def _arrow_side(signals: pd.DataFrame, i: int) -> int:
    return 1 if bool(signals["arrow_long"].iloc[i]) else -1 if bool(signals["arrow_short"].iloc[i]) else 0


def _finalize_state(state: dict[str, object], fills: list[dict[str, object]], reason: str,
                    signal_time: pd.Timestamp, exit_time: pd.Timestamp, exit_price: float) -> dict[str, object]:
    fraction = float(state["remaining_fraction"])
    side, entry = int(state["side"]), float(state["entry_price"])
    gross = fraction * side * (exit_price - entry) / entry
    cost = ONE_WAY_COST * fraction
    net = gross - cost
    fills.append(dict(
        trade_id=state["trade_id"], fill_type="exit", time=exit_time, signal_time=signal_time,
        side=side, price=exit_price, fraction=fraction, entry_notional=1.0,
        gross_return=gross, cost_return=cost, net_return=net, reason=reason,
    ))
    state["realised_gross_return"] = float(state["realised_gross_return"]) + gross
    state["realised_net_return"] = float(state["realised_net_return"]) + net
    state["remaining_fraction"] = 0.0
    return dict(
        trade_id=state["trade_id"], side=side, entry_signal_time=state["entry_signal_time"],
        entry_time=state["entry_time"], entry_price=entry, exit_signal_time=signal_time,
        exit_time=exit_time, exit_price=exit_price, exit_fraction=1.0,
        gross_return=state["realised_gross_return"], net_return=state["realised_net_return"],
        cost_return=ROUND_TRIP_COST, fill_count=sum(f["trade_id"] == state["trade_id"] for f in fills),
        exit_mode=state["exit_mode"], exit_reason=reason,
    )


def _partial_exit(state: dict[str, object], fills: list[dict[str, object]], signal_time: pd.Timestamp,
                  exit_time: pd.Timestamp, exit_price: float) -> None:
    fraction = 0.5
    side, entry = int(state["side"]), float(state["entry_price"])
    gross = fraction * side * (exit_price - entry) / entry
    cost = ONE_WAY_COST * fraction
    net = gross - cost
    fills.append(dict(
        trade_id=state["trade_id"], fill_type="exit", time=exit_time, signal_time=signal_time,
        side=side, price=exit_price, fraction=fraction, entry_notional=1.0,
        gross_return=gross, cost_return=cost, net_return=net, reason="opposite_half_1_next_open",
    ))
    state["remaining_fraction"] = 0.5
    state["realised_gross_return"] = float(state["realised_gross_return"]) + gross
    state["realised_net_return"] = float(state["realised_net_return"]) + net
    state["opposite_count"] = 1


def _open_state(trade_id: int, side: int, signal_time: pd.Timestamp, entry_time: pd.Timestamp,
                entry_price: float, exit_mode: ExitMode, fills: list[dict[str, object]]) -> dict[str, object]:
    fills.append(dict(
        trade_id=trade_id, fill_type="entry", time=entry_time, signal_time=signal_time, side=side,
        price=entry_price, fraction=1.0, entry_notional=1.0, gross_return=0.0,
        cost_return=ONE_WAY_COST, net_return=-ONE_WAY_COST, reason="entry_next_open",
    ))
    return dict(
        trade_id=trade_id, side=side, entry_signal_time=signal_time, entry_time=entry_time,
        entry_price=entry_price, remaining_fraction=1.0, realised_gross_return=0.0,
        realised_net_return=-ONE_WAY_COST, opposite_count=0, exit_mode=exit_mode,
    )


def _mark_open(state: dict[str, object], time: pd.Timestamp, price: float) -> dict[str, object]:
    fraction, side, entry = float(state["remaining_fraction"]), int(state["side"]), float(state["entry_price"])
    marked_gross = float(state["realised_gross_return"]) + fraction * side * (price - entry) / entry
    marked_net = float(state["realised_net_return"]) + fraction * side * (price - entry) / entry - ONE_WAY_COST * fraction
    return dict(
        trade_id=state["trade_id"], side=side, entry_signal_time=state["entry_signal_time"],
        entry_time=state["entry_time"], entry_price=entry, remaining_fraction=fraction,
        realised_gross_return=state["realised_gross_return"], realised_net_return=state["realised_net_return"],
        marked_time=time, marked_price=price, marked_gross_return=marked_gross,
        marked_net_return=marked_net, exit_mode=state["exit_mode"],
    )


def _replay(frame: pd.DataFrame, signals: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
            exit_mode: ExitMode, use_ma_filter: bool, forced: tuple[int, int] | None = None) -> BacktestResult:
    if start >= end:
        raise ValueError("start must precede end")
    if start.value % BAR.value or end.value % BAR.value:
        raise ValueError("start and end must lie on the UTC 5m grid")
    last_i = int(frame.index.searchsorted(end, side="left")) - 1
    fills: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    curve: list[dict[str, object]] = [dict(time=start, equity=1000.0, event="start", trade_id=pd.NA)]
    state: dict[str, object] | None = None
    next_trade_id = 1
    equity = 1000.0
    if last_i < 0:
        return BacktestResult(signals, _empty(_TRADE_COLUMNS), _empty(_FILL_COLUMNS), _empty(_OPEN_COLUMNS),
                              pd.DataFrame(curve), _stats([], 1000.0, None))
    forced_i = None if forced is None else forced[0]
    forced_side = None if forced is None else forced[1]
    # A start-aligned execution can consume the immediately preceding 5m
    # close; its signal is read through ``prior`` below.  Nothing earlier can
    # open a position because carry-in is forbidden.
    first_i = max(0, int(frame.index.searchsorted(start, side="left")))
    for i in range(first_i, last_i + 1):
        execution_time = frame.index[i]
        prior = i - 1
        if prior >= 0:
            arrow = _arrow_side(signals, prior)
            eligible = arrow and _direction_matches(signals, prior, arrow, use_ma_filter)
            if forced_i == prior:
                arrow, eligible = int(forced_side), True
            if arrow:
                signal_time = frame.index[prior] + BAR
                if signal_time >= start:
                    if state is None:
                        if eligible and (forced is None or forced_i == prior):
                            state = _open_state(next_trade_id, arrow, signal_time, execution_time,
                                                float(frame["open"].iloc[i]), exit_mode, fills)
                            next_trade_id += 1
                    elif arrow == -int(state["side"]):
                        if exit_mode == "two_opposite_half" and int(state["opposite_count"]) == 0:
                            _partial_exit(state, fills, signal_time, execution_time, float(frame["open"].iloc[i]))
                        else:
                            trade = _finalize_state(
                                state, fills,
                                "opposite_next_open" if exit_mode == "full_opposite" else "opposite_half_2_next_open",
                                signal_time, execution_time, float(frame["open"].iloc[i]),
                            )
                            trades.append(trade)
                            equity *= 1.0 + float(trade["net_return"])
                            state = None
                            if eligible and forced is None:
                                state = _open_state(next_trade_id, arrow, signal_time, execution_time,
                                                    float(frame["open"].iloc[i]), exit_mode, fills)
                                next_trade_id += 1
        # Mark every complete 5m close.  The mark reserves a closing 10bp fee
        # on the unclosed fraction, so MTM and eventual ledger fees reconcile.
        close_time = execution_time + BAR
        if close_time > start:
            if state is None:
                curve.append(dict(time=close_time, equity=equity, event="close", trade_id=pd.NA))
            else:
                mark = _mark_open(state, close_time, float(frame["close"].iloc[i]))
                curve.append(dict(time=close_time, equity=equity * (1.0 + float(mark["marked_net_return"])),
                                  event="close_mtm", trade_id=state["trade_id"]))
        if forced is not None and state is None and trades:
            break
    open_rows: list[dict[str, object]] = []
    marked_net: float | None = None
    if state is not None:
        mark_time = frame.index[last_i] + BAR
        mark = _mark_open(state, mark_time, float(frame["close"].iloc[last_i]))
        open_rows.append(mark)
        marked_net = float(mark["marked_net_return"])
        # The final bar already contributed this exact close-time mark above.
    equity_curve = pd.DataFrame(curve)
    stats = _stats(trades, equity, marked_net)
    values = equity_curve["equity"].to_numpy(dtype=float)
    stats["close_mtm_max_drawdown"] = float((values / np.maximum.accumulate(values) - 1.0).min())
    return BacktestResult(
        signals=signals,
        trades=pd.DataFrame(trades, columns=_TRADE_COLUMNS),
        fills=pd.DataFrame(fills, columns=_FILL_COLUMNS),
        open_positions=pd.DataFrame(open_rows, columns=_OPEN_COLUMNS),
        equity_curve=equity_curve,
        stats=stats,
    )


def _stats(trades: list[dict[str, object]], closed_equity: float, marked_net: float | None) -> dict[str, float | int]:
    returns = np.array([float(row["net_return"]) for row in trades], dtype=float)
    equity_path = np.r_[1000.0, 1000.0 * np.cumprod(1.0 + returns)]
    drawdown = float((equity_path / np.maximum.accumulate(equity_path) - 1.0).min())
    return dict(
        initial_equity=1000.0, closed_trade_count=len(trades), realised_equity=closed_equity,
        realised_return=closed_equity / 1000.0 - 1.0,
        close_mtm_equity=closed_equity if marked_net is None else closed_equity * (1.0 + marked_net),
        close_mtm_return=closed_equity / 1000.0 - 1.0 if marked_net is None else closed_equity * (1.0 + marked_net) / 1000.0 - 1.0,
        realised_max_drawdown=drawdown,
    )


def run_backtest(frame: pd.DataFrame, start: object, end: object, *,
                 exit_mode: ExitMode = "full_opposite", use_ma_filter: bool = True) -> BacktestResult:
    """Replay all causal Stoch entries in ``[start, end)`` with one position.

    A qualifying 5m close is eligible when ``close_time >= start``; every fill
    occurs at a 5m open strictly before ``end``.  There is no carry-in.  The
    default MA filter requires the arrow direction to agree with the most
    recent completed 15m hl2/SMA40 direction.  ``use_ma_filter=False`` keeps
    identical Stoch exits while providing the requested single-Stoch baseline.
    """
    ohlcv = _validate_frame(frame)
    mode = _validate_exit_mode(exit_mode)
    if not isinstance(use_ma_filter, (bool, np.bool_)):
        raise ValueError("use_ma_filter must be boolean")
    return _replay(ohlcv, build_signals(ohlcv), _utc(start, "start"), _utc(end, "end"), mode, bool(use_ma_filter))


def backtest(frame: pd.DataFrame, start: object, end: object, *, exit_mode: ExitMode = "full_opposite",
             use_ma_filter: bool = True) -> BacktestResult:
    """Alias kept concise for research notebooks; see :func:`run_backtest`."""
    return run_backtest(frame, start, end, exit_mode=exit_mode, use_ma_filter=use_ma_filter)


def replay_one(frame: pd.DataFrame, signals: pd.DataFrame, signal_i: int, side: int,
               exit_mode: ExitMode, end: object) -> ReplayResult:
    """Force one next-open entry for a matched-random control, without MA filtering.

    ``signal_i`` is the control's known-at-close 5m bar; ``side`` is +1 or -1.
    The forced entry ignores whether that bar has an arrow or MA agreement.
    It still exits only on later opposite Stoch arrows.  It never opens a
    reversal after that single control position has closed.
    """
    ohlcv = _validate_frame(frame)
    mode = _validate_exit_mode(exit_mode)
    if isinstance(signal_i, bool) or not isinstance(signal_i, (int, np.integer)) or not 0 <= int(signal_i) < len(ohlcv):
        raise ValueError("signal_i must be a valid frame position")
    if side not in (-1, 1):
        raise ValueError("side must be +1 or -1")
    checked = _validate_signals(ohlcv, signals)
    start = ohlcv.index[int(signal_i)] + BAR
    result = _replay(ohlcv, checked, start, _utc(end, "end"), mode, False, (int(signal_i), int(side)))
    return ReplayResult(result.trades, result.fills, result.open_positions)
