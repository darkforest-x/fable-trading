"""Causal SPIKE V6 / CM Williams Vix Fix research primitives.

This module is deliberately a research-only execution model.  It takes already
closed OHLC/ATR bars and precomputed V6 long/short events; it neither recreates
the V6 oracle nor imports monitor, order, model, or TradingView code.  Every
WVF value uses the current or earlier closed bar.  A supplied data gap starts a
new indicator segment, so a previous extreme cannot qualify a later signal.

The execution convention intentionally differs from the Pine reference's
signal-close display: an admitted V6 event enters on the next observed open.
The unfiltered opposite V6 event exits an occupied position at its next
tradable open.  Thus WVF filters admissions only; they never suppress exits.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class WvfSpec:
    """Original CM Williams Vix Fix constants, not fitted quantiles."""

    pd: int = 22
    bbl: int = 20
    mult: float = 2.0
    lb: int = 50
    ph: float = 0.85


@dataclass(frozen=True)
class ExecutionSpec:
    """Frozen V6 execution constants used by this study."""

    stop_bars: int = 5
    stop_buffer_atr: float = 0.2
    risk_floor_atr: float = 2.0
    arm_r: float = 2.0
    trail_atr: float = 4.0
    round_trip_cost: float = 0.002
    tick: float = 0.01


def _finite(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).notna()


def _segment_ids(frame: pd.DataFrame, data_gap: pd.Series | None) -> pd.Series:
    if data_gap is None:
        gap = pd.Series(False, index=frame.index, dtype=bool)
    else:
        gap = pd.Series(data_gap, index=frame.index).fillna(True).astype(bool)
    invalid = ~(_finite(frame.open) & _finite(frame.high) & _finite(frame.low) & _finite(frame.close))
    return (gap | invalid).cumsum().astype(int)


def wvf_features(frame: pd.DataFrame, *, data_gap: pd.Series | None = None,
                 spec: WvfSpec = WvfSpec()) -> pd.DataFrame:
    """Calculate original WVF in independent contiguous closed-bar segments.

    Uses close/high/low through the current closed bar only.  ``wvf_std`` uses
    population standard deviation (``ddof=0``), matching Pine's ``ta.stdev``.
    ``ph`` is a multiplier of the rolling maximum, never an 85th percentile.
    """
    if not {"high", "low", "close"}.issubset(frame):
        raise ValueError("WVF needs high, low, and close columns")
    if min(spec.pd, spec.bbl, spec.lb) < 1 or spec.mult <= 0 or not 0 < spec.ph <= 1:
        raise ValueError("invalid WVF constants")
    out = pd.DataFrame(index=frame.index)
    out["segment_id"] = _segment_ids(frame, data_gap)
    close = pd.to_numeric(frame.close, errors="coerce")
    low = pd.to_numeric(frame.low, errors="coerce")
    high = pd.to_numeric(frame.high, errors="coerce")
    if ((low > high) & low.notna() & high.notna()).any() or (low.le(0) & low.notna()).any():
        raise ValueError("invalid OHLC bounds for WVF")
    pieces: list[pd.DataFrame] = []
    for _, group in out.groupby("segment_id", sort=False):
        ix = group.index
        maximum_close = close.loc[ix].rolling(spec.pd, min_periods=spec.pd).max()
        wvf = (maximum_close - low.loc[ix]) / maximum_close * 100.0
        wvf = wvf.where(maximum_close.gt(0))
        sma = wvf.rolling(spec.bbl, min_periods=spec.bbl).mean()
        std = wvf.rolling(spec.bbl, min_periods=spec.bbl).std(ddof=0)
        upper = sma + spec.mult * std
        rolling_high = wvf.rolling(spec.lb, min_periods=spec.lb).max()
        range_high = rolling_high * spec.ph
        part = pd.DataFrame({"wvf": wvf, "wvf_sma20": sma, "wvf_std20_ddof0": std,
                             "wvf_upper_band": upper, "wvf_rolling_max50": rolling_high,
                             "wvf_range_high": range_high}, index=ix)
        part["wvf_extreme"] = (wvf.ge(upper) | wvf.ge(range_high)).fillna(False).astype(bool)
        pieces.append(part)
    return out.join(pd.concat(pieces).reindex(frame.index))


def wvf_long_reclaim(frame: pd.DataFrame, features: pd.DataFrame, *, window: int) -> pd.DataFrame:
    """Return the pre-registered strict long reclaim decision for each close.

    At a V6 signal close, the latest *preceding* extreme must be within
    ``window`` closed bars, current WVF must no longer be extreme, no subsequent
    close may be below that extreme bar's low, and the current close must exceed
    its high.  This waits for no future bar and does not retrospectively move a
    V6 event.
    """
    if window not in (12, 24):
        raise ValueError("only pre-registered 12 or 24 bar windows are allowed")
    if not {"high", "low", "close"}.issubset(frame) or not {"segment_id", "wvf_extreme"}.issubset(features):
        raise ValueError("OHLC and WVF features are required")
    out = pd.DataFrame(index=frame.index)
    out["wvf_current_extreme"] = features.wvf_extreme.astype(bool)
    out["wvf_last_extreme_i"] = np.nan
    timestamp_dtype = "datetime64[ns, UTC]" if isinstance(frame.index, pd.DatetimeIndex) and frame.index.tz is not None else object
    out["wvf_last_extreme_time"] = pd.Series(pd.NaT, index=frame.index, dtype=timestamp_dtype)
    out["wvf_low_unbroken"] = False
    out["wvf_reclaimed_high"] = False
    out["wvf_admitted"] = False
    out["wvf_filter_reason"] = "no_prior_extreme"
    close = pd.to_numeric(frame.close, errors="coerce")
    low = pd.to_numeric(frame.low, errors="coerce")
    high = pd.to_numeric(frame.high, errors="coerce")
    segment = features.segment_id.to_numpy()
    extreme = features.wvf_extreme.to_numpy(dtype=bool)
    for i in range(len(frame)):
        if bool(extreme[i]):
            out.iloc[i, out.columns.get_loc("wvf_filter_reason")] = "current_wvf_extreme"
            continue
        start = max(0, i - window)
        candidates = [j for j in range(i - 1, start - 1, -1) if segment[j] == segment[i] and extreme[j]]
        if not candidates:
            continue
        j = candidates[0]
        after = close.iloc[j + 1:i + 1]
        unbroken = bool(after.ge(low.iloc[j]).all())
        reclaimed = bool(close.iloc[i] > high.iloc[j])
        out.iloc[i, out.columns.get_loc("wvf_last_extreme_i")] = j
        out.iloc[i, out.columns.get_loc("wvf_last_extreme_time")] = frame.index[j]
        out.iloc[i, out.columns.get_loc("wvf_low_unbroken")] = unbroken
        out.iloc[i, out.columns.get_loc("wvf_reclaimed_high")] = reclaimed
        if not unbroken:
            out.iloc[i, out.columns.get_loc("wvf_filter_reason")] = "extreme_low_close_broken"
        elif not reclaimed:
            out.iloc[i, out.columns.get_loc("wvf_filter_reason")] = "extreme_high_not_reclaimed"
        else:
            out.iloc[i, out.columns.get_loc("wvf_admitted")] = True
            out.iloc[i, out.columns.get_loc("wvf_filter_reason")] = "admitted"
    out["wvf_admitted"] = out.wvf_admitted.astype(bool)
    return out


def make_signal_ledger(signals: pd.DataFrame, reclaim: pd.DataFrame | None, *, variant: str,
                       minutes: int) -> pd.DataFrame:
    """Keep every raw V6 signal before any admission decision or trade replay."""
    if not {"long_signal", "short_signal"}.issubset(signals):
        raise ValueError("signals need long_signal and short_signal")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("minutes must be a positive integer")
    long = signals.long_signal.fillna(False).astype(bool)
    short = signals.short_signal.fillna(False).astype(bool)
    if (long & short).any():
        raise ValueError("a close cannot carry both V6 sides")
    rows: list[dict[str, object]] = []
    for i, stamp in enumerate(signals.index):
        for side, present in ((1, bool(long.iloc[i])), (-1, bool(short.iloc[i]))):
            if not present:
                continue
            admitted = True
            reason = "baseline_or_unfiltered_short"
            if side == 1 and reclaim is not None:
                admitted = bool(reclaim.wvf_admitted.iloc[i])
                reason = str(reclaim.wvf_filter_reason.iloc[i])
            rows.append({"signal_i": i, "signal_bar_open": stamp,
                         "signal_confirm_time": stamp + pd.Timedelta(minutes=minutes), "side": side,
                         "direction": "long" if side == 1 else "short", "variant": variant,
                         "admitted_for_entry": admitted, "wvf_filter_reason": reason,
                         "wvf_current_extreme": None if reclaim is None else bool(reclaim.wvf_current_extreme.iloc[i]),
                         "wvf_last_extreme_i": np.nan if reclaim is None else reclaim.wvf_last_extreme_i.iloc[i]})
    return pd.DataFrame(rows)


def _initial_position(frame: pd.DataFrame, signal_i: int, side: int, spec: ExecutionSpec,
                      gaps: pd.Series) -> dict[str, object] | None:
    if signal_i + 1 >= len(frame):
        return None
    tick = float(spec.tick)
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")
    if signal_i < spec.stop_bars - 1 or bool(gaps.iloc[signal_i - spec.stop_bars + 1:signal_i + 1].any()):
        return None
    history = frame.iloc[signal_i - spec.stop_bars + 1:signal_i + 1]
    values = history[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or np.any(values[:, 2] <= 0)
            or np.any(values[:, 1] < np.maximum(values[:, 0], values[:, 3]))
            or np.any(values[:, 2] > np.minimum(values[:, 0], values[:, 3]))):
        return None
    atr = float(frame.atr.iloc[signal_i])
    close = float(frame.close.iloc[signal_i])
    entry = float(frame.open.iloc[signal_i + 1])
    if not all(math.isfinite(x) for x in (atr, close, entry)) or atr <= 0 or entry <= 0:
        return None
    extreme = float(history.low.min() if side == 1 else history.high.max())
    raw = (min(extreme - spec.stop_buffer_atr * atr, close - spec.risk_floor_atr * atr)
           if side == 1 else max(extreme + spec.stop_buffer_atr * atr, close + spec.risk_floor_atr * atr))
    stop = math.floor(raw / tick) * tick if side == 1 else math.ceil(raw / tick) * tick
    risk = side * (entry - stop)
    if not math.isfinite(stop) or stop <= 0 or not math.isfinite(risk) or risk <= 0:
        return None
    return {"signal_i": signal_i, "signal_bar_open": frame.index[signal_i], "entry_i": signal_i + 1,
            "entry_time": frame.index[signal_i + 1], "side": side, "entry_price": entry,
            "initial_stop": stop, "initial_risk": risk, "initial_risk_frac": risk / entry,
            "protection": stop, "mfe_r": 0.0, "trail_armed": False}


def _close_trade(pos: dict[str, object], *, exit_i: int, exit_time: object, exit_price: float,
                 reason: str, spec: ExecutionSpec) -> dict[str, object]:
    entry, side, risk = float(pos["entry_price"]), int(pos["side"]), float(pos["initial_risk"])
    gross_return = side * (exit_price / entry - 1.0)
    gross_r = side * (exit_price - entry) / risk
    net_return = gross_return - spec.round_trip_cost
    return {**pos, "exit_i": exit_i, "exit_time": exit_time, "exit_price": exit_price,
            "exit_reason": reason, "gross_return": gross_return, "net_return": net_return,
            "gross_r": gross_r, "net_r": net_return / float(pos["initial_risk_frac"]),
            "censored": False, "exit_time_precision": "bar_open_or_intrabar_window"}


def _gap_censor(pos: dict[str, object], *, exit_i: int, exit_time: object) -> dict[str, object]:
    """Preserve an unpriced position whose next observation follows a data gap."""
    return {**pos, "exit_i": exit_i, "exit_time": exit_time, "exit_price": np.nan,
            "exit_reason": "data_gap_censored", "gross_return": np.nan, "net_return": np.nan,
            "gross_r": np.nan, "net_r": np.nan, "censored": True,
            "exit_time_precision": "unknown_gap"}


def simulate_v6_variant(frame: pd.DataFrame, signals: pd.DataFrame, *, admission: pd.Series,
                        variant: str, data_gap: pd.Series | None = None,
                        reclaim: pd.DataFrame | None = None,
                        spec: ExecutionSpec = ExecutionSpec()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay a single-coin, 1x-notional V6 variant without filtering exits.

    ``admission`` is indexed like bars and only controls a signal's new entry.
    Every raw opposite signal still schedules an exit at its following observed
    open.  Stops known at the prior close have priority over a same-bar close
    signal; an entry-bar stop similarly takes priority over that bar's high.
    """
    required = {"open", "high", "low", "close", "atr"}
    if not required.issubset(frame) or not signals.index.equals(frame.index):
        raise ValueError("aligned OHLC/ATR bars and signals are required")
    minutes = frame.attrs.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("frame.attrs minutes must be a positive integer")
    ledger = make_signal_ledger(signals, reclaim, variant=variant, minutes=minutes)
    allowed = pd.Series(admission, index=frame.index).fillna(False).astype(bool)
    gap = pd.Series(False, index=frame.index) if data_gap is None else pd.Series(data_gap, index=frame.index).fillna(True).astype(bool)
    raw_side = np.where(signals.long_signal.fillna(False), 1, np.where(signals.short_signal.fillna(False), -1, 0))
    if (signals.long_signal.fillna(False).astype(bool) & signals.short_signal.fillna(False).astype(bool)).any():
        raise ValueError("ambiguous signal side")
    position: dict[str, object] | None = None
    pending_entry: tuple[int, int] | None = None
    pending_reverse: tuple[int, int] | None = None
    trades: list[dict[str, object]] = []
    for i in range(len(frame)):
        ended_side = 0
        if bool(gap.iloc[i]):
            if position is not None:
                trades.append(_gap_censor(position, exit_i=i, exit_time=frame.index[i]))
            position = None
            pending_entry = pending_reverse = None
            continue
        if pending_reverse is not None and position is not None:
            signal_i, old_side = pending_reverse
            if int(position["side"]) == old_side:
                opening, protection = float(frame.open.iloc[i]), float(position["protection"])
                stop_at_open = opening <= protection if old_side == 1 else opening >= protection
                if stop_at_open:
                    reason = "trailing_stop_gap" if protection != float(position["initial_stop"]) else "initial_stop_gap"
                else:
                    reason = "opposite_v6_next_open"
                trades.append(_close_trade(position, exit_i=i, exit_time=frame.index[i],
                                           exit_price=opening if stop_at_open else opening, reason=reason, spec=spec))
                ended_side = old_side
                position = None
            pending_reverse = None
        if pending_entry is not None:
            signal_i, side = pending_entry
            if position is None and side != ended_side:
                position = _initial_position(frame, signal_i, side, spec, gap)
            pending_entry = None
        if position is not None:
            side, protection = int(position["side"]), float(position["protection"])
            o, h, l, c, atr = (float(frame[name].iloc[i]) for name in ("open", "high", "low", "close", "atr"))
            stopped = l <= protection if side == 1 else h >= protection
            if stopped:
                price = min(o, protection) if side == 1 else max(o, protection)
                reason = "trailing_stop" if protection != float(position["initial_stop"]) else "initial_stop"
                if (o <= protection if side == 1 else o >= protection):
                    reason += "_gap"
                trades.append(_close_trade(position, exit_i=i, exit_time=frame.index[i], exit_price=price, reason=reason, spec=spec))
                ended_side = side
                position = None
            else:
                favorable = h if side == 1 else l
                position["mfe_r"] = max(float(position["mfe_r"]), side * (favorable - float(position["entry_price"])) / float(position["initial_risk"]))
                current_r = side * (c - float(position["entry_price"])) / float(position["initial_risk"])
                position["trail_armed"] = bool(position["trail_armed"]) or current_r >= spec.arm_r
                if bool(position["trail_armed"]) and math.isfinite(atr) and atr > 0:
                    raw = c - side * spec.trail_atr * atr
                    candidate = math.floor(raw / spec.tick) * spec.tick if side == 1 else math.ceil(raw / spec.tick) * spec.tick
                    position["protection"] = max(protection, candidate) if side == 1 else min(protection, candidate)
        side = int(raw_side[i])
        if side:
            if position is not None and side != int(position["side"]):
                pending_reverse = (i, int(position["side"]))
                if bool(allowed.iloc[i]):
                    pending_entry = (i, side)
            elif position is None and side != ended_side and bool(allowed.iloc[i]):
                pending_entry = (i, side)
    if position is not None:
        last = len(frame) - 1
        price = float(frame.close.iloc[last])
        trade = _close_trade(position, exit_i=last, exit_time=frame.index[last], exit_price=price,
                             reason="boundary_mark", spec=spec)
        trade["censored"] = True
        trade["exit_time_precision"] = "last_complete_close"
        trades.append(trade)
    trades_frame = pd.DataFrame(trades)
    if len(trades_frame):
        equity = 1.0
        before, after = [], []
        for trade in trades_frame.itertuples():
            before.append(equity)
            if not bool(trade.censored):
                equity *= 1.0 + float(trade.net_return)
            after.append(equity)
        trades_frame["account_equity_before"] = before
        trades_frame["account_equity_after"] = after
    return ledger, trades_frame


def summarize_account(trades: pd.DataFrame) -> dict[str, float | int]:
    """Summarize realized 1x-notional sequential trades; censored rows are excluded."""
    if trades.empty:
        return {"trades": 0, "win_rate": math.nan, "profit_factor": math.nan, "net_r": 0.0,
                "gross_return": 0.0, "net_return": 0.0, "max_drawdown_closed_trade": 0.0}
    done = trades.loc[~trades.censored.astype(bool)].copy()
    if done.empty:
        return {"trades": 0, "win_rate": math.nan, "profit_factor": math.nan, "net_r": 0.0,
                "gross_return": 0.0, "net_return": 0.0, "max_drawdown_closed_trade": 0.0}
    profits = done.net_return.clip(lower=0).sum()
    losses = -done.net_return.clip(upper=0).sum()
    equity = pd.concat([pd.Series([1.0]), (1.0 + done.net_return).cumprod()], ignore_index=True)
    drawdown = 1.0 - equity / equity.cummax()
    return {"trades": int(len(done)), "win_rate": float(done.net_return.gt(0).mean()),
            "profit_factor": math.inf if losses == 0 and profits > 0 else float(profits / losses) if losses else math.nan,
            "net_r": float(done.net_r.sum()), "gross_return": float((1.0 + done.gross_return).prod() - 1.0),
            "net_return": float(equity.iloc[-1] - 1.0), "max_drawdown_closed_trade": float(drawdown.max())}


def aggregate_complete(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate only full 30-minute UTC buckets, never inventing a partial bar."""
    if minutes not in (30, 60, 240):
        raise ValueError("study supports 30, 60, or 240 minutes")
    if minutes == 30:
        return frame.copy()
    grouped = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last",
                       "volume": "sum", "quote_volume": "sum"})
    return out.loc[grouped.size().eq(minutes // 30)].copy()


def _data_gap(frame: pd.DataFrame, minutes: int) -> pd.Series:
    step = pd.Timedelta(minutes=minutes)
    gap = frame.index.to_series().diff().ne(step)
    gap.iloc[0] = False
    return gap.astype(bool)


def _legacy_v4(frame: pd.DataFrame, minutes: int, side: int) -> pd.DataFrame:
    """Reconstruct V6's retained V4 provenance from closed feature rows."""
    from yoyo.evaluation.spike_burst_progressive import progressive_fields

    if side not in (1, -1):
        raise ValueError("side must be ±1")
    p, gap = progressive_fields(frame), _data_gap(frame, minutes)
    dense = frame.ready.eq(True) & frame.pastWidth.le(3.0) & frame.pastCrosses.ge(2.0)
    recent_dense = dense.astype(float).shift(1).rolling(12, min_periods=12).sum().gt(0)
    prior_high, prior_low = frame.high.shift(1).rolling(12, min_periods=12).max(), frame.low.shift(1).rolling(12, min_periods=12).min()
    fast_high, fast_low = frame[["s20", "e20"]].max(axis=1, skipna=False), frame[["s20", "e20"]].min(axis=1, skipna=False)
    early = frame.ready.eq(True) & ((frame.close.gt(prior_high) & frame.close.gt(fast_high)) if side == 1 else (frame.close.lt(prior_low) & frame.close.lt(fast_low)))
    quality = (frame.ready.eq(True) & recent_dense & (p.prog_advance.ge(1.5) if side == 1 else p.prog_advance.le(-1.5))
               & p.prog_volume_ratio.ge(1.5) & (frame.md.ge(frame.sb) if side == 1 else frame.md.le(frame.sb))
               & (frame.middle.gt(frame.middle.shift(1)) if side == 1 else frame.middle.lt(frame.middle.shift(1))))
    previous_early = False
    last_accepted = parent_i = None
    parent_high = parent_low = math.nan
    parent_confirmed = False
    rows: list[dict[str, object]] = []
    for i in range(len(frame)):
        if bool(gap.iloc[i]):
            previous_early = False; last_accepted = parent_i = None; parent_high = parent_low = math.nan; parent_confirmed = False
        cooldown = last_accepted is None or i - last_accepted >= 12
        early_signal = bool(early.iloc[i]) and not previous_early and cooldown
        previous_early = bool(early.iloc[i])
        if early_signal:
            last_accepted = parent_i = i
            parent_high, parent_low, parent_confirmed = float(prior_high.iloc[i]), float(prior_low.iloc[i]), False
        age = None if parent_i is None else i - parent_i
        if age is not None and age > 3:
            parent_i = None; parent_high = parent_low = math.nan; parent_confirmed = False; age = None
        confirms = bool(parent_i is not None and not parent_confirmed and age is not None and 0 <= age <= 3
                        and bool(quality.iloc[i]) and (frame.close.iloc[i] > parent_high if side == 1 else frame.close.iloc[i] < parent_low))
        if confirms:
            parent_confirmed = True
        rows.append({"legacy_confirmed": confirms, "legacy_parent_high": parent_high if confirms else math.nan,
                     "legacy_parent_low": parent_low if confirms else math.nan})
    return pd.DataFrame(rows, index=frame.index)


def v6_signals(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Return current V6 long/short oracle signals without a Pine exit proxy."""
    from yoyo.evaluation.spike_burst_progressive import progressive_fields
    from yoyo.evaluation.spike_burst_v6_structure import detect

    progress, gap = progressive_fields(frame), _data_gap(frame, minutes)
    output = pd.DataFrame(index=frame.index)
    for side, label in ((1, "long_signal"), (-1, "short_signal")):
        legacy = _legacy_v4(frame, minutes, side)
        supplied = frame[["open", "high", "low", "close", "md", "sb", "atr", "ropeHigh", "ropeLow", "ready"]].copy()
        supplied["legacy_confirmed"] = legacy.legacy_confirmed.astype(bool)
        supplied["legacy_parent_high"], supplied["legacy_parent_low"] = legacy.legacy_parent_high, legacy.legacy_parent_low
        supplied["advance3"], supplied["volume_ratio3"] = progress.prog_advance, progress.prog_volume_ratio
        supplied["data_gap"], supplied["confirmed"] = gap, True
        output[label] = detect(supplied, side=side).confirmed.astype(bool)
    if (output.long_signal & output.short_signal).any():
        raise ValueError("V6 oracle yielded an ambiguous two-sided close")
    return output


def load_normalized_30m(path: Path) -> pd.DataFrame:
    """Read one frozen normalized OKX receipt with its original UTC open clock."""
    raw = pd.read_csv(path)
    if "time" not in raw:
        raise ValueError(f"normalized source lacks time: {path}")
    raw.index = pd.DatetimeIndex(pd.to_datetime(raw.pop("time"), utc=True))
    raw = raw.sort_index()
    required = ["open", "high", "low", "close", "volume", "quote_volume"]
    if set(required) - set(raw):
        raise ValueError(f"normalized source lacks columns: {path}")
    return raw.loc[:, required]


def prepare_symbol(frame30: pd.DataFrame, *, minutes: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Cache V6 and WVF inputs once per symbol/timeframe before A/B/C replay."""
    from yoyo.evaluation.spike_burst_replay import features

    bars = aggregate_complete(frame30, minutes)
    featured = features(bars)
    featured.attrs["minutes"] = minutes
    gap = _data_gap(featured, minutes)
    signals = v6_signals(featured, minutes)
    wvf = wvf_features(featured, data_gap=gap)
    return featured, signals, wvf_long_reclaim(featured, wvf, window=12), wvf_long_reclaim(featured, wvf, window=24)
