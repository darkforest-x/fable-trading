"""Offline BTC 1h Parabolic-RSI diamond / 5m six-MA execution replay.

The input index is the *open* time of already closed UTC five-minute candles.
An hourly candle therefore consists of the twelve rows beginning at an exact
UTC hour and is made visible at that hour plus one hour.  A diamond visible at
``01:00`` can first inspect the five-minute candle opened at ``01:00``; its
entry, when confirmed, is the following candle's open.  This module is only a
deterministic research replay: it neither loads data nor sizes an account.

``volatility`` is a causal 14-bar mean true range divided by the current
14-bar close SMA.  ``vol_bucket`` is 0--4 according to the prior, complete
30-day (8,640 five-minute bars) empirical 20/40/60/80 percentiles.  A value of
-1 means that the metric or its strictly prior 30-day reference is unavailable.
Neither current nor later bars enter that bucket's cut points.
"""
from __future__ import annotations

from numbers import Integral
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation.parabolic_rsi_sar import diamonds, pine_sar
from yoyo.evaluation.spike_v6_bb_squeeze import _rsi_wilder


COST = 0.002
BAR = pd.Timedelta(minutes=5)
HOUR = pd.Timedelta(hours=1)
MA_PERIODS = (20, 60, 120)
TRADE_COLUMNS = [
    "trade_id", "side", "entry_i", "exit_i", "entry_time", "exit_time",
    "entry_price", "stop_price", "target_price", "exit_price", "risk_pct",
    "gross_return", "net_return", "gross_r", "net_r", "reason", "dual_touch",
    "holding_bars", "diamond_open_time", "diamond_close_time", "confirm_time",
    "wait_bars",
]


def _as_utc_time(value: Any, name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tz is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    return stamp.tz_convert("UTC")


def _bound(index: pd.DatetimeIndex, value: Any, name: str, *, allow_end: bool) -> int:
    stamp = _as_utc_time(value, name)
    if stamp < index[0] or stamp > index[-1] + BAR or (stamp == index[-1] + BAR and not allow_end):
        raise ValueError(f"{name} is outside the five-minute frame")
    position = int(index.searchsorted(stamp, side="left"))
    if position < len(index) and index[position] != stamp:
        raise ValueError(f"{name} must fall on the five-minute grid")
    return position


def _validate_frame(frame5: pd.DataFrame) -> pd.DataFrame:
    required = ["open", "high", "low", "close", "volume"]
    if not isinstance(frame5, pd.DataFrame) or not set(required).issubset(frame5):
        raise ValueError("frame5 requires UTC DatetimeIndex and open/high/low/close/volume")
    if not isinstance(frame5.index, pd.DatetimeIndex) or frame5.index.tz is None:
        raise ValueError("frame5 requires a timezone-aware UTC DatetimeIndex")
    if str(frame5.index.tz) != "UTC":
        raise ValueError("frame5 index must use UTC")
    if len(frame5) < 12 or not frame5.index.is_monotonic_increasing or not frame5.index.is_unique:
        raise ValueError("frame5 index must be unique, chronological, and contain a complete hour")
    index = frame5.index
    if not index.equals(pd.date_range(index[0], periods=len(index), freq="5min", tz="UTC")):
        raise ValueError("frame5 must be a continuous five-minute UTC grid")
    if index[0].minute or index[0].second or index[0].microsecond or len(frame5) % 12:
        raise ValueError("frame5 must begin on a UTC hour and contain complete 12-bar hours")
    out = frame5.loc[:, required].astype(float).copy()
    values = out.to_numpy()
    if not np.isfinite(values).all() or (out[["open", "high", "low", "close"]] <= 0).any().any() or (out.volume < 0).any():
        raise ValueError("frame5 has non-finite prices, non-positive OHLC, or negative volume")
    if (out.low > out[["open", "close"]].min(axis=1)).any() or (out.high < out[["open", "close"]].max(axis=1)).any():
        raise ValueError("frame5 has invalid OHLC geometry")
    return out


def _hourly(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate exact 12-row UTC-hour groups and label them by close time."""
    groups = np.arange(len(frame)) // 12
    rows = []
    for group in range(groups[-1] + 1):
        part = frame.iloc[group * 12:(group + 1) * 12]
        if len(part) != 12:  # Defensive: _validate_frame already makes this impossible.
            raise ValueError("every hourly candle must contain exactly 12 five-minute bars")
        rows.append((part.open.iloc[0], part.high.max(), part.low.min(), part.close.iloc[-1], part.volume.sum()))
    index = frame.index[::12] + HOUR
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=index)


def _volatility(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    previous_close = frame.close.shift(1)
    true_range = pd.concat([frame.high - frame.low, (frame.high - previous_close).abs(), (frame.low - previous_close).abs()], axis=1).max(axis=1)
    level = true_range.rolling(14, min_periods=14).mean() / frame.close.rolling(14, min_periods=14).mean()
    prior = level.shift(1)
    # 30 UTC days at five minutes.  Quantiles only exist once the whole prior
    # window exists, so a later mutation cannot revise an already-ready bucket.
    cuts = np.column_stack([prior.rolling(8640, min_periods=8640).quantile(q).to_numpy() for q in (.2, .4, .6, .8)])
    values = level.to_numpy(float)
    bucket = np.full(len(frame), -1, dtype=int)
    ready = np.isfinite(values) & np.isfinite(cuts).all(axis=1)
    bucket[ready] = (values[ready, None] > cuts[ready]).sum(axis=1)
    return values, bucket


def prepare(frame5: pd.DataFrame) -> dict[str, Any]:
    """Precompute causal 1h diamonds, 5m MAs, and causal volatility buckets.

    ``frame5`` must contain all continuous five-minute OHLCV rows, indexed by
    UTC candle open time.  RSI14 uses the shared Wilder implementation and the
    shared ChartPrime SAR/diamond port; the strong-diamond stop is always the
    source hourly low for a long or hourly high for a short.
    """
    frame = _validate_frame(frame5)
    hourly = _hourly(frame)
    rsi = _rsi_wilder(hourly.close, 14).to_numpy(float)
    sar, below = pine_sar(rsi)
    strong = diamonds(sar, below)
    side = np.where(strong["strong_up"], 1, np.where(strong["strong_dn"], -1, 0)).astype(int)
    stop = np.where(side == 1, hourly.low.to_numpy(float), np.where(side == -1, hourly.high.to_numpy(float), np.nan))
    ma: dict[str, np.ndarray] = {}
    for period in MA_PERIODS:
        ma[f"sma{period}"] = frame.close.rolling(period, min_periods=period).mean().to_numpy(float)
        ma[f"ema{period}"] = frame.close.ewm(span=period, adjust=False, min_periods=period).mean().to_numpy(float)
    stack = np.column_stack([ma[name] for period in MA_PERIODS for name in (f"sma{period}", f"ema{period}")])
    above = (frame.open.to_numpy()[:, None] > stack).all(axis=1) & (frame.close.to_numpy()[:, None] > stack).all(axis=1)
    below_ma = (frame.open.to_numpy()[:, None] < stack).all(axis=1) & (frame.close.to_numpy()[:, None] < stack).all(axis=1)
    volatility, vol_bucket = _volatility(frame)
    return {
        "frame": frame, "hourly": hourly, "rsi": rsi, "sar": sar, "ma": ma,
        "open": frame.open.to_numpy(float), "high": frame.high.to_numpy(float),
        "low": frame.low.to_numpy(float), "close": frame.close.to_numpy(float),
        "sixma_long": above, "sixma_short": below_ma,
        "diamond_side": side, "diamond_stop": stop,
        # ``hourly`` is close-labelled: the close of rows 0..11 is visible at
        # the open of row 12, which is the first complete eligible 5m candle.
        "diamond_frame_i": (np.arange(len(hourly), dtype=int) + 1) * 12,
        "volatility": volatility, "vol_bucket": vol_bucket,
    }


def _entry_invalid(side: int, opening: float, stop: float) -> bool:
    return opening <= stop if side == 1 else opening >= stop


def trace_trade(ctx: dict[str, Any], entry_i: int, side: int, stop_price: float | None = None,
                end_i: int | None = None, *, risk_pct: float | None = None) -> dict[str, Any]:
    """Trace one next-open fixed-stop/3R position through an exclusive boundary.

    The entry uses ``frame.open[entry_i]``.  A stop gap fills at the observed
    open, a target gap fills at its fixed target, and an intrabar stop/target
    dual touch is conservatively reported as a stop.  ``exit_time`` is the
    open time of the OHLC bar containing the observed fill (intrabar ordering
    is deliberately not inferred).  An unclosed terminal position has
    ``reason='open'`` and no fictional mark-to-market exit.
    """
    frame = ctx["frame"]
    n = len(frame)
    if isinstance(entry_i, (bool, np.bool_)) or not isinstance(entry_i, Integral) or not 0 <= int(entry_i) < n:
        raise ValueError("entry_i must be a valid frame position")
    if side not in (-1, 1):
        raise ValueError("side must be +1 or -1")
    end = n if end_i is None else int(end_i)
    if not 0 < end <= n or int(entry_i) >= end:
        raise ValueError("end_i must be an exclusive position after entry_i")
    entry_i = int(entry_i)
    opening_values = np.asarray(ctx.get("open", frame.open.to_numpy(float)), dtype=float)
    high_values = np.asarray(ctx.get("high", frame.high.to_numpy(float)), dtype=float)
    low_values = np.asarray(ctx.get("low", frame.low.to_numpy(float)), dtype=float)
    entry = float(opening_values[entry_i])
    if stop_price is None:
        if risk_pct is None or not np.isfinite(risk_pct) or risk_pct <= 0:
            raise ValueError("one positive stop_price or risk_pct is required")
        stop_price = entry * (1 - side * float(risk_pct))
    elif risk_pct is not None:
        raise ValueError("supply stop_price or risk_pct, not both")
    stop = float(stop_price)
    risk = side * (entry - stop)
    if not np.isfinite(stop) or risk <= 0:
        return dict(entry_i=entry_i, entry_price=entry, stop_price=stop, side=side,
                    reason="invalid_entry_gap", status="invalid", open=False)
    target = entry + side * 3 * risk
    risk_pct_value = risk / entry
    base = dict(side=side, entry_i=entry_i, entry_time=frame.index[entry_i], entry_price=entry,
                stop_price=stop, target_price=target, risk_pct=risk_pct_value)
    if _entry_invalid(side, entry, stop):
        return base | dict(reason="invalid_entry_gap", status="invalid", open=False)
    for i in range(entry_i, end):
        opening, high, low = float(opening_values[i]), float(high_values[i]), float(low_values[i])
        stop_gap = opening <= stop if side == 1 else opening >= stop
        target_gap = opening >= target if side == 1 else opening <= target
        stop_hit = low <= stop if side == 1 else high >= stop
        target_hit = high >= target if side == 1 else low <= target
        if stop_gap:
            price, reason, dual = opening, "stop_gap", False
        elif target_gap:
            price, reason, dual = target, "target_gap", False
        elif stop_hit:
            price, reason, dual = stop, "stop", bool(target_hit)
        elif target_hit:
            price, reason, dual = target, "target", False
        else:
            continue
        gross_return = side * (price - entry) / entry
        net_return = gross_return - COST
        gross_r = side * (price - entry) / risk
        return base | dict(exit_i=i, exit_time=frame.index[i], exit_price=price, reason=reason,
                           status="closed", open=False, dual_touch=dual, gross_return=gross_return,
                           net_return=net_return, gross_r=gross_r, net_r=net_return / risk_pct_value,
                           holding_bars=i - entry_i + 1)
    return base | dict(exit_i=None, exit_time=pd.NaT, exit_price=np.nan, reason="open", status="open",
                       open=True, dual_touch=False, gross_return=np.nan, net_return=np.nan, gross_r=np.nan,
                       net_r=np.nan, holding_bars=end - entry_i)


def _event_table(ctx: dict[str, Any]) -> pd.DataFrame:
    hourly = ctx["hourly"]
    side = np.asarray(ctx["diamond_side"], dtype=int)
    at = np.flatnonzero(side != 0)
    rows = []
    for diamond_id, hour_i in enumerate(at):
        rows.append(dict(diamond_id=diamond_id, hour_i=int(hour_i), frame_i=int(ctx["diamond_frame_i"][hour_i]),
                         side=int(side[hour_i]), hourly_open_time=hourly.index[hour_i] - HOUR,
                         hourly_close_time=hourly.index[hour_i], hourly_open=float(hourly.open.iloc[hour_i]),
                         hourly_close=float(hourly.close.iloc[hour_i]), rsi=float(ctx["rsi"][hour_i]),
                         sar=float(ctx["sar"][hour_i]), stop_price=float(ctx["diamond_stop"][hour_i]),
                         confirm_bar_open_time=pd.NaT, confirm_time=pd.NaT, entry_time=pd.NaT, status="outside_window"))
    result = pd.DataFrame(rows, columns=["diamond_id", "hour_i", "frame_i", "side", "hourly_open_time", "hourly_close_time", "hourly_open", "hourly_close", "rsi", "sar", "stop_price", "confirm_bar_open_time", "confirm_time", "entry_time", "status"])
    for name in ("confirm_bar_open_time", "confirm_time", "entry_time"):
        result[name] = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns, UTC]")
    return result


def simulate(ctx: dict[str, Any], start: Any, end: Any, arm: str = "sixma") -> dict[str, pd.DataFrame]:
    """Run one serial stream from ``start`` through exclusive ``end``.

    ``arm='sixma'`` waits for a strict close-confirmed six-MA candle.  A new
    strong diamond replaces any pending one, stop touch while waiting cancels
    it, and a position blocks later diamonds.  ``arm='direct'`` is the frozen
    same-diamond next-five-minute-open reference without confirmation.  Open
    positions at ``end`` are returned separately and never force-closed.
    """
    if arm not in {"sixma", "direct"}:
        raise ValueError("arm must be 'sixma' or 'direct'")
    frame = ctx["frame"]
    low_values = np.asarray(ctx.get("low", frame.low.to_numpy(float)), dtype=float)
    high_values = np.asarray(ctx.get("high", frame.high.to_numpy(float)), dtype=float)
    start_i, end_i = _bound(frame.index, start, "start", allow_end=False), _bound(frame.index, end, "end", allow_end=True)
    if start_i >= end_i:
        raise ValueError("start must precede end")
    events = _event_table(ctx)
    trades: list[dict[str, Any]] = []
    opens: list[dict[str, Any]] = []
    event_at = {int(row.frame_i): int(row.diamond_id) for _, row in events.iterrows()}
    pending_id: int | None = None
    position_until = start_i
    i = start_i
    while i < end_i:
        event_id = event_at.get(i)
        if event_id is not None:
            if i < start_i or i >= end_i:
                pass
            elif i < position_until:
                events.loc[event_id, "status"] = "occupied_skip"
            elif arm == "direct":
                event = events.loc[event_id]
                trace = trace_trade(ctx, i, int(event.side), float(event.stop_price), end_i)
                if trace["status"] == "invalid":
                    events.loc[event_id, "status"] = trace["reason"]
                else:
                    events.loc[event_id, ["entry_time", "status"]] = [trace["entry_time"], "open" if trace["open"] else "entered"]
                    if trace["open"]:
                        opens.append(_open_row(trace, event, None, len(trades)))
                        position_until = end_i
                    else:
                        trades.append(_trade_row(trace, event, None, len(trades)))
                        position_until = int(trace["exit_i"]) + 1
            else:
                if pending_id is not None:
                    events.loc[pending_id, "status"] = "replaced"
                pending_id = event_id
                events.loc[event_id, "status"] = "pending"
        if arm == "sixma" and pending_id is not None and i >= int(events.loc[pending_id, "frame_i"]):
            event = events.loc[pending_id]
            side, stop = int(event.side), float(event.stop_price)
            hit_stop = low_values[i] <= stop if side == 1 else high_values[i] >= stop
            if hit_stop:
                events.loc[pending_id, "status"] = "cancelled_stop"
                pending_id = None
            else:
                confirmed = bool(ctx["sixma_long"][i] if side == 1 else ctx["sixma_short"][i])
                if confirmed:
                    # The condition reads the candle only after its close;
                    # the next 5m candle opens at that same knowledge time.
                    events.loc[pending_id, ["confirm_bar_open_time", "confirm_time"]] = [frame.index[i], frame.index[i] + BAR]
                    if i + 1 >= end_i:
                        events.loc[pending_id, "status"] = "pending_entry_terminal"
                        pending_id = None
                    else:
                        trace = trace_trade(ctx, i + 1, side, stop, end_i)
                        if trace["status"] == "invalid":
                            events.loc[pending_id, "status"] = trace["reason"]
                        else:
                            events.loc[pending_id, ["entry_time", "status"]] = [trace["entry_time"], "open" if trace["open"] else "entered"]
                            if trace["open"]:
                                opens.append(_open_row(trace, event, i, len(trades)))
                                position_until = end_i
                            else:
                                trades.append(_trade_row(trace, event, i, len(trades)))
                                position_until = int(trace["exit_i"]) + 1
                        pending_id = None
        i += 1
    if pending_id is not None:
        events.loc[pending_id, "status"] = "pending_terminal"
    return {"events": events, "trades": pd.DataFrame(trades, columns=TRADE_COLUMNS), "open_positions": pd.DataFrame(opens, columns=TRADE_COLUMNS)}


def _trade_row(trace: dict[str, Any], event: pd.Series, confirm_i: int | None, trade_id: int) -> dict[str, Any]:
    row = {name: trace.get(name) for name in TRADE_COLUMNS}
    row.update(trade_id=trade_id, diamond_open_time=event.hourly_open_time, diamond_close_time=event.hourly_close_time,
               confirm_time=pd.NaT if confirm_i is None else trace["entry_time"],
               wait_bars=int(trace["entry_i"]) - int(event.frame_i))
    return row


def _open_row(trace: dict[str, Any], event: pd.Series, confirm_i: int | None, trade_id: int) -> dict[str, Any]:
    row = {name: trace.get(name) for name in TRADE_COLUMNS}
    row.update(trade_id=trade_id, diamond_open_time=event.hourly_open_time, diamond_close_time=event.hourly_close_time,
               confirm_time=pd.NaT if confirm_i is None else trace["entry_time"],
               wait_bars=int(trace["entry_i"]) - int(event.frame_i))
    return row
