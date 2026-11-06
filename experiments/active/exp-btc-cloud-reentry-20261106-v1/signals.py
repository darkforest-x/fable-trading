"""Causal BTC 5m cloud-reentry candidates; no position/PnL simulation.

Input is OKX BTC-USDT-SWAP 5m OHLC(V), indexed by UTC bar open. A complete NY
Monday is 00:00–Tuesday 00:00 local time; lower 1.618 is Monday low minus
0.618 times Monday range and is usable from Tuesday. Each week's first 5m
low-below/close-above reclaim arms later bullish EMA20/50 flips.

Features and gate flags use only information available by signal-bar open,
except the cross and initial stop, which use the just-completed signal bar.
ATR pre-signal values use the prior completed 5m bar. Higher timeframe bars
must be complete and closed. Future rows are consulted only for the requested
next-open exit timestamp/price.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


FIVE_MIN = pd.Timedelta(minutes=5)
NY = "America/New_York"
DEFAULT_CONFIG: dict[str, Any] = {
    "thresholds": {
        "mismatch_abs_atr_max": 0.25,
        "mid_rebound_atr_min": 1.0,
        "d_min": 0.0,
        "d_max": 1.0,
        "h1_streak_min": 1,
    },
    "stop": {"buffer_atr": 0.1, "tick_size": 0.1},
}


def _input_frame(ohlc: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(ohlc, pd.DataFrame) or not isinstance(ohlc.index, pd.DatetimeIndex):
        raise TypeError("ohlc must be a DataFrame with a UTC DatetimeIndex")
    if ohlc.index.tz is None:
        raise ValueError("ohlc index must be timezone-aware UTC bar-open times")
    frame = ohlc.copy()
    frame.index = frame.index.tz_convert("UTC").as_unit("ns")
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError("column names must be unique after lowercase normalization")
    required = {"open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing OHLC columns: {sorted(required.difference(frame.columns))}")
    if frame.empty or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError("ohlc must be nonempty, sorted, and have unique bar opens")
    if (frame.index.asi8 % FIVE_MIN.value != 0).any():
        raise ValueError("timestamps must align to 5-minute UTC bar opens")
    if len(frame) > 1 and not np.all(np.diff(frame.index.asi8) == FIVE_MIN.value):
        raise ValueError("5-minute input must be contiguous; repair archive/cache joins first")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(float)
        if not np.isfinite(frame[column].to_numpy()).all():
            raise ValueError(f"{column} contains non-finite values")
    invalid = (
        (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
        | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
    )
    if invalid.any():
        raise ValueError("OHLC must be positive and satisfy high >= open/close/low >= low")
    return frame


def _settings(config: Mapping[str, Any] | None) -> tuple[dict[str, float], float, float]:
    thresholds = dict(DEFAULT_CONFIG["thresholds"])
    stop = dict(DEFAULT_CONFIG["stop"])
    if config:
        for name, target in (("thresholds", thresholds), ("stop", stop)):
            section = config.get(name, {})
            if not isinstance(section, Mapping):
                raise TypeError(f"config[{name!r}] must be a mapping")
            target.update(section)
    parsed = {key: float(value) for key, value in thresholds.items()}
    buffer_atr = float(stop["buffer_atr"])
    tick_size = float(stop["tick_size"])
    if buffer_atr < 0 or tick_size <= 0:
        raise ValueError("stop buffer_atr must be nonnegative and tick_size positive")
    return parsed, buffer_atr, tick_size


def _atr14(frame: pd.DataFrame) -> np.ndarray:
    """Wilder ATR14 with a 14-TR SMA seed; each row uses its completed OHLC."""
    high, low, close = (frame[c].to_numpy(dtype=float) for c in ("high", "low", "close"))
    prior_close = np.r_[np.nan, close[:-1]]
    tr = np.maximum(high - low, np.maximum(abs(high - prior_close), abs(low - prior_close)))
    tr[0] = high[0] - low[0]
    atr = np.full(len(frame), np.nan)
    if len(frame) >= 14:
        atr[13] = tr[:14].mean()
        for i in range(14, len(frame)):
            atr[i] = (13 * atr[i - 1] + tr[i]) / 14
    return atr


def _higher_bars(frame: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Build complete UTC bars with pandas resample/count, then own EMAs."""
    rule = f"{hours}h"
    count = frame["close"].resample(rule, origin="epoch", closed="left", label="left").count()
    bars = frame.resample(rule, origin="epoch", closed="left", label="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    bars = bars.loc[count.eq(hours * 12)].copy()
    bars.index = bars.index.as_unit("ns")
    bars["close_time"] = bars.index + pd.Timedelta(hours=hours)
    bars["ema20"] = bars["close"].ewm(span=20, adjust=False).mean()
    bars["ema50"] = bars["close"].ewm(span=50, adjust=False).mean()
    bars["above_both"] = (bars["close"] > bars["ema20"]) & (bars["close"] > bars["ema50"])
    if hours == 1:
        streak = np.zeros(len(bars), dtype=int)
        for i, above in enumerate(bars["above_both"].to_numpy(dtype=bool)):
            streak[i] = (streak[i - 1] if i else 0) + 1 if above else 0
        bars["above_both_streak"] = streak
    return bars


def _asof(table: pd.DataFrame, at: pd.Timestamp, max_age: pd.Timedelta) -> pd.Series | None:
    if table.empty:
        return None
    close_times = pd.DatetimeIndex(table["close_time"]).as_unit("ns")
    i = int(close_times.searchsorted(at, side="right") - 1)
    if i < 0 or at - close_times[i] >= max_age:
        return None
    return table.iloc[i]


def _weekly_ranges(index: pd.DatetimeIndex, frame: pd.DataFrame) -> tuple[pd.DatetimeIndex, dict[int, dict[str, Any]]]:
    local = index.tz_convert(NY).as_unit("ns")
    # Do date arithmetic while naive so DST weeks still begin at local midnight.
    naive = local.tz_localize(None)
    week_naive = naive.normalize() - pd.to_timedelta(naive.weekday, unit="D")
    week_starts = week_naive.tz_localize(NY).as_unit("ns")
    keys = week_starts.asi8
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    stops = np.r_[starts[1:], len(frame)]
    groups: dict[int, dict[str, Any]] = {}
    for begin, end in zip(starts, stops):
        positions = np.arange(begin, end)
        week_start = week_starts[begin]
        monday_end = week_start + pd.DateOffset(days=1)
        week_end = week_start + pd.DateOffset(days=7)
        monday_mask = local[positions].date == week_start.date()
        monday_positions = positions[np.asarray(monday_mask)]
        expected = pd.date_range(week_start.tz_convert("UTC"), periods=288, freq=FIVE_MIN).as_unit("ns")
        monday_opens = index.take(monday_positions)
        complete = len(monday_positions) == 288 and monday_opens.equals(expected)
        if complete:
            monday = frame.iloc[monday_positions]
            high, low = float(monday["high"].max()), float(monday["low"].min())
            lower = low - 0.618 * (high - low)
        else:
            high = low = lower = np.nan
        groups[int(week_start.value)] = {
            "positions": positions,
            "week_start_local": week_start,
            "week_start_utc": week_start.tz_convert("UTC"),
            "monday_end_utc": monday_end.tz_convert("UTC"),
            "week_end_utc": week_end.tz_convert("UTC"),
            "monday_high": high,
            "monday_low": low,
            "lower1618": lower,
            "monday_complete": bool(complete),
        }
    return week_starts, groups


def _prior_structure(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    up_idx: np.ndarray,
    down_idx: np.ndarray,
    i: int,
    monday_low: float,
    atr_pre: float,
) -> dict[str, Any]:
    """Find the preceding swing's peak/L1, bear flip/L2, and pre-signal metrics."""
    empty = {
        "L1": np.nan, "L2": np.nan, "Hmid": np.nan,
        "L1_i": None, "L2_i": None, "peak_i": None, "down_i": None,
        "up_i": None, "mismatch_signed": np.nan, "mismatch_abs": np.nan,
        "mid_rebound": np.nan, "prior_leg_above_monday_low": False,
        "prior_leg_available": False,
    }
    dn_pos = int(np.searchsorted(down_idx, i, side="left") - 1)
    if dn_pos < 0:
        return empty
    down_i = int(down_idx[dn_pos])
    l2_i = down_i + int(np.argmin(low[down_i:i])) if down_i < i else None
    l2 = float(low[l2_i]) if l2_i is not None else np.nan
    up_pos = int(np.searchsorted(up_idx, down_i, side="left") - 1)
    result = dict(empty)
    result.update({"L2": l2, "L2_i": l2_i, "down_i": down_i})
    if up_pos < 0:
        return result
    up_i = int(up_idx[up_pos])
    result.update({
        "up_i": up_i,
        "prior_leg_available": True,
        "prior_leg_above_monday_low": bool(np.any(close[up_i:down_i] > monday_low)),
    })
    if down_i <= up_i + 1:
        return result
    peak_i = up_i + int(np.argmax(high[up_i:down_i]))
    l1_start = peak_i + 1
    if l1_start >= down_i:
        return result
    l1_i = l1_start + int(np.argmin(low[l1_start:down_i]))
    hmid = float(np.max(high[l1_i + 1:l2_i])) if l2_i is not None and l2_i - l1_i > 1 else np.nan
    l1 = float(low[l1_i])
    signed = (l2 - l1) / atr_pre if np.isfinite(atr_pre) and atr_pre > 0 else np.nan
    rebound = (hmid - max(l1, l2)) / atr_pre if np.isfinite(hmid) and np.isfinite(atr_pre) and atr_pre > 0 else np.nan
    result.update({
        "L1": l1, "L1_i": l1_i, "peak_i": peak_i, "Hmid": hmid,
        "mismatch_signed": signed,
        "mismatch_abs": abs(signed) if np.isfinite(signed) else np.nan,
        "mid_rebound": rebound,
    })
    return result


def build_candidates(
    ohlc: pd.DataFrame,
    config: Mapping[str, Any] | None = None,
    *,
    include_unarmed: bool = False,
) -> pd.DataFrame:
    """Return armed bullish 5m EMA20/50 flips and causal gate flags.

    With ``include_unarmed=True``, include all bullish flips in weeks with a
    complete Monday range for matched-control sampling. The bottom and trend
    gates remain separate: ``bottom_pass`` is mismatch AND mid-rebound;
    ``trend_pass`` is D AND 1h streak AND prior bull-leg close above Monday low.
    ``baseline_eligible`` is armed AND 12h bullish; strategy-specific flags
    combine that shared base with just one gate family.
    """
    frame = _input_frame(ohlc)
    thresholds, stop_buffer, tick_size = _settings(config)
    index = frame.index
    close = frame["close"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    open_ = frame["open"].to_numpy(dtype=float)
    ema20 = frame["close"].ewm(span=20, adjust=False).mean().to_numpy()
    ema50 = frame["close"].ewm(span=50, adjust=False).mean().to_numpy()
    cloud_low, cloud_high = np.minimum(ema20, ema50), np.maximum(ema20, ema50)
    up = np.flatnonzero((ema20 > ema50) & (np.r_[False, ema20[:-1] <= ema50[:-1]]))
    down = np.flatnonzero((ema20 < ema50) & (np.r_[False, ema20[:-1] >= ema50[:-1]]))
    atr = _atr14(frame)
    h1, h12 = _higher_bars(frame, 1), _higher_bars(frame, 12)
    week_starts, weeks = _weekly_ranges(index, frame)
    week_key = week_starts.asi8

    first_reclaim: dict[int, int] = {}
    for key, week in weeks.items():
        if not week["monday_complete"]:
            continue
        for j in week["positions"]:
            if index[j] >= week["monday_end_utc"] and low[j] < week["lower1618"] and close[j] > week["lower1618"]:
                first_reclaim[key] = int(j)
                break

    rows: list[dict[str, Any]] = []
    for i in up:
        week = weeks[int(week_key[i])]
        if not week["monday_complete"] or index[i] < week["monday_end_utc"]:
            continue
        entry_i = int(i) + 1
        if entry_i >= len(frame):
            continue
        reclaim_i = first_reclaim.get(int(week_key[i]))
        armed = reclaim_i is not None and reclaim_i < i
        if not include_unarmed and not armed:
            continue

        signal_open, signal_close = index[i], index[i] + FIVE_MIN
        prior_i = int(i) - 1
        pre_close = float(close[prior_i]) if prior_i >= 0 else np.nan
        atr_pre = float(atr[prior_i]) if prior_i >= 0 else np.nan
        atr_signal = float(atr[i])
        structure = _prior_structure(high, low, close, up, down, int(i), week["monday_low"], atr_pre)
        d_value = (structure["L2"] - week["monday_low"]) / atr_pre if np.isfinite(structure["L2"]) and np.isfinite(atr_pre) and atr_pre > 0 else np.nan

        # The signal-start snapshot excludes the signal candle itself.
        bias = _asof(h12, signal_open, pd.Timedelta(hours=12))
        h1_row = _asof(h1, signal_open, pd.Timedelta(hours=1))
        bull12h = bool(bias is not None and bias["ema20"] > bias["ema50"])
        h1_streak = int(h1_row["above_both_streak"]) if h1_row is not None else np.nan

        mismatch_pass = np.isfinite(structure["mismatch_abs"]) and structure["mismatch_abs"] <= thresholds["mismatch_abs_atr_max"]
        rebound_pass = np.isfinite(structure["mid_rebound"]) and structure["mid_rebound"] >= thresholds["mid_rebound_atr_min"]
        d_pass = np.isfinite(d_value) and thresholds["d_min"] <= d_value <= thresholds["d_max"]
        h1_pass = np.isfinite(h1_streak) and h1_streak >= thresholds["h1_streak_min"]
        prior_leg_pass = bool(structure["prior_leg_above_monday_low"])
        bottom_pass = bool(mismatch_pass and rebound_pass)
        trend_pass = bool(d_pass and h1_pass and prior_leg_pass)
        baseline_eligible = bool(armed and bull12h)

        stop_raw = cloud_low[i] - stop_buffer * atr_signal
        initial_stop = np.floor((stop_raw + tick_size * 1e-12) / tick_size) * tick_size if np.isfinite(stop_raw) else np.nan
        entry_price = float(open_[entry_i])
        risk = entry_price - initial_stop if np.isfinite(initial_stop) else np.nan
        risk_atr = risk / atr_signal if np.isfinite(risk) and atr_signal > 0 else np.nan

        later_down = down[np.searchsorted(down, entry_i, side="left"):]
        if len(later_down) and int(later_down[0]) + 1 < len(frame):
            down_i = int(later_down[0])
            exit_i, exit_reason, exit_decision = down_i + 1, "ema20_downcross_next_open", index[down_i] + FIVE_MIN
        else:
            exit_i, exit_reason, exit_decision = len(frame) - 1, "dataset_end", pd.NaT

        row = {
            "symbol": "BTC-USDT-SWAP",
            "ny_week": week["week_start_local"].date().isoformat(),
            "week_start_utc": week["week_start_utc"],
            "week_end_utc": week["week_end_utc"],
            "monday_end_utc": week["monday_end_utc"],
            "monday_complete": True,
            "monday_ready": True,
            "monday_high": week["monday_high"],
            "monday_low": week["monday_low"],
            "monday_range": week["monday_high"] - week["monday_low"],
            "monday_lower1618": week["lower1618"],
            "first_reclaim_bar_open": index[reclaim_i] if armed else pd.NaT,
            "first_reclaim_close_time": index[reclaim_i] + FIVE_MIN if armed else pd.NaT,
            "signal_bar_open": signal_open,
            "signal_close_time": signal_close,
            "entry_time": index[entry_i],
            "entry_price": entry_price,
            "exit_time": index[exit_i],
            "exit_price": float(open_[exit_i]),
            "exit_decision_time": exit_decision,
            "exit_reason": exit_reason,
            "setup_armed": bool(armed),
            "baseline_candidate": bool(armed),
            "candidate_kind": "armed_baseline" if armed else "unarmed_flip",
            "bull12h": bull12h,
            "bias_12h_known": bias is not None,
            "bias_12h_close_time": bias["close_time"] if bias is not None else pd.NaT,
            "bias_12h_close": float(bias["close"]) if bias is not None else np.nan,
            "bias_12h_ema20": float(bias["ema20"]) if bias is not None else np.nan,
            "bias_12h_ema50": float(bias["ema50"]) if bias is not None else np.nan,
            "ema20_signal": float(ema20[i]),
            "ema50_signal": float(ema50[i]),
            "cloud_lower": float(cloud_low[i]),
            "cloud_upper": float(cloud_high[i]),
            "atr5_signal": atr_signal,
            "atr5_pre": atr_pre,
            "pre_close": pre_close,
            "P": pre_close,
            "h1_known": h1_row is not None,
            "h1_close_time": h1_row["close_time"] if h1_row is not None else pd.NaT,
            "h1_close": float(h1_row["close"]) if h1_row is not None else np.nan,
            "h1_ema20": float(h1_row["ema20"]) if h1_row is not None else np.nan,
            "h1_ema50": float(h1_row["ema50"]) if h1_row is not None else np.nan,
            "h1_streak": h1_streak,
            "L1": structure["L1"], "L2": structure["L2"], "Hmid": structure["Hmid"], "D": d_value,
            "L1_bar_open": index[structure["L1_i"]] if structure["L1_i"] is not None else pd.NaT,
            "L2_bar_open": index[structure["L2_i"]] if structure["L2_i"] is not None else pd.NaT,
            "prior_bull_leg_start": index[structure["up_i"]] if structure["up_i"] is not None else pd.NaT,
            "prior_bear_flip": index[structure["down_i"]] if structure["down_i"] is not None else pd.NaT,
            "prior_bull_leg_peak": index[structure["peak_i"]] if structure["peak_i"] is not None else pd.NaT,
            "prior_bull_leg_available": structure["prior_leg_available"],
            "previous_bull_leg_any_close_above_monday_low": prior_leg_pass,
            "signed_mismatch_atr": structure["mismatch_signed"],
            "mismatch_abs_atr": structure["mismatch_abs"],
            "mid_rebound_atr": structure["mid_rebound"],
            "mismatch_pass": bool(mismatch_pass),
            "mid_rebound_pass": bool(rebound_pass),
            "D_pass": bool(d_pass),
            "h1_streak_pass": bool(h1_pass),
            "bottom_pass": bottom_pass,
            "trend_pass": trend_pass,
            "baseline_eligible": baseline_eligible,
            "bottom_eligible": bool(baseline_eligible and bottom_pass),
            "trend_eligible": bool(baseline_eligible and trend_pass),
            "initial_stop_model": "cloud_tick",
            "initial_stop_raw": stop_raw,
            "initial_stop": initial_stop,
            "risk_per_unit": risk,
            "risk_atr": risk_atr,
            "risk_valid": bool(np.isfinite(risk) and risk > 0),
            "threshold_mismatch_abs_atr_max": thresholds["mismatch_abs_atr_max"],
            "threshold_mid_rebound_atr_min": thresholds["mid_rebound_atr_min"],
            "threshold_D_min": thresholds["d_min"],
            "threshold_D_max": thresholds["d_max"],
            "threshold_h1_streak_min": thresholds["h1_streak_min"],
        }
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=["signal_bar_open", "entry_time", "exit_time", "setup_armed", "bull12h"])
    result = pd.DataFrame(rows).sort_values("signal_bar_open", kind="stable").reset_index(drop=True)
    time_columns = {
        "week_start_utc", "week_end_utc", "monday_end_utc",
        "first_reclaim_bar_open", "first_reclaim_close_time", "signal_bar_open",
        "signal_close_time", "entry_time", "exit_time", "exit_decision_time",
        "bias_12h_close_time", "h1_close_time", "L1_bar_open", "L2_bar_open",
        "prior_bull_leg_start", "prior_bear_flip", "prior_bull_leg_peak",
    }
    for column in time_columns:
        result[column] = pd.to_datetime(result[column], utc=True)
    return result


__all__ = ["DEFAULT_CONFIG", "build_candidates"]
