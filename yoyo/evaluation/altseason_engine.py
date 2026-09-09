"""Frozen, pure entry/exit mechanics for the 2026-09-10 altcoin study.

This module uses confirmed, continuous, ordinary UTC candles. No exchange,
model, live monitor, order, notification, fitting, outcome ranking or file IO
is imported. The caller owns universe selection and the historical cutoff.

Source: ``yoyo.data.altcoin_features`` supplies current IMACD 34/9, focus12,
0.10 ATR and six SMA/EMA20/60/120 formation semantics. All input features use
OHLCV at or before the decision close; prior12 highs and prior24h quote volume
exclude the decision candle. Four-hour features use only complete UTC groups.

Entry families: current positive focus release; a first close above prior12
highs after recent density and above all six MAs; an armed 20-band pullback in
an ordered rising trend; and a SEPARATE short-history 20-high/RV2 breakout.
The last family uses history counts60..339 and never requires missing long MAs.
The first three retain the shared feature builder's index>=340 warmup.

Execution is long only, next open, with immutable2 decision ATR initial risk.
Ratchet SMA20/60 tests the PREVIOUS effective protection at the close, exits at
the NEXT open, then otherwise raises the protection only when close>currentMA.
Only the original hard stop is an intrabar order. This matches the BICO case's
ratchet definition, not the plain current-MA exit in altcoin_accounting.
Costs are fixed10bp of ENTRY notional on each side, not actual fee schedules.
Same-bar unknown stop/TP order goes to the stop; an observed open beyond TP is
known to precede later intrabar lows. Fold-end marks pay fees and stay censored.
MFE/MAE include fully held candles and known open/fill prices, never extrema
after an intrabar exit. Unknown intrabar exit clocks are explicit intervals.
"""
from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from types import MappingProxyType
from typing import Mapping

import numpy as np
import pandas as pd

from yoyo.data.altcoin_features import MA_COLUMNS, build_altcoin_features


BAR_COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume")
ARMS = {
    "focus_sma60": "ratchet_sma60",
    "focus_md": "md",
    "focus_sma20": "ratchet_sma20",
    "focus_3r": "fixed3r",
    "dense_sma60": "ratchet_sma60",
    "pullback_sma60": "ratchet_sma60",
    "young_breakout_sma20": "ratchet_sma20",
}
EXIT_RULES = frozenset(ARMS.values())
FEE_SIDE = 0.001
STOP_ATR = 2.0
CONTEXT_COLUMNS = (
    "history_count", "near_zero_bars", "relative_volume", "tr_expansion",
    "body_fraction", "close_location", "six_ma_width_atr", "momentum10",
    "prior24h_quote_volume", "prior24h_return", "atr_pct", "above_all6",
    "dense_recent", "prior_width_atr", "prior_crosses", "prior12_high",
    "prior20_high", "md", "sb", "sma20", "sma60", "sma120",
)
CANDIDATE_COLUMNS = ("arm", "exit_rule", "decision_i", "signal_i", "minutes",
                     "decision_bar_open_time", "decision_time", *CONTEXT_COLUMNS)


def _clock(index: pd.DatetimeIndex, minutes: int) -> pd.DatetimeIndex:
    """Require a unique continuous UTC grid, normalized to nanoseconds."""
    if minutes not in (60, 240):
        raise ValueError("Only1H/4H are supported")
    if (not isinstance(index, pd.DatetimeIndex) or index.tz is None
            or str(index.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT")
            or index.hasnans or not index.is_unique or not index.is_monotonic_increasing):
        raise ValueError("Unique chronological UTC timestamps are required")
    normalized = index.as_unit("ns")
    step = pd.Timedelta(minutes=minutes).value
    if np.any(normalized.asi8 % step) or np.any(np.diff(normalized.asi8) != step):
        raise ValueError("Candles must be UTC-aligned and continuous")
    return normalized


def _price_frame(bars: pd.DataFrame, minutes: int, *, require_volume: bool) -> pd.DataFrame:
    """Reject gaps and invalid values without interpolating or changing prices."""
    if not isinstance(bars, pd.DataFrame) or not bars.columns.is_unique:
        raise ValueError("A DataFrame with unique columns is required")
    names = BAR_COLUMNS if require_volume else BAR_COLUMNS[:4]
    if not set(names).issubset(bars):
        raise ValueError("Missing required candle columns: " + ",".join(names))
    result = bars.loc[:, names].copy()
    result.index = _clock(bars.index, minutes)
    values = result.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Candles must be finite")
    o, h, l, c = values[:, :4].T
    if np.any((l <= 0) | (h < np.maximum(o, c)) | (l > np.minimum(o, c))):
        raise ValueError("Invalid OHLC candle bounds")
    if require_volume and np.any(values[:, 4:] < 0):
        raise ValueError("Volumes must be nonnegative")
    if "confirm" in bars and not bars.confirm.map(lambda v: str(v) in ("1", "True")).all():
        raise ValueError("Only confirmed candles are supported")
    result = result.astype(float)
    result.attrs.update(minutes=minutes, period_seconds=minutes * 60)
    return result


def _feature_frame(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Features use current/prior OHLCV; diagnostic windows are documented here.

    Momentum1.0 =100*(close-SMA50)/max(abs(close-SMA50), last50), using known
    closes and min_periods1 after SMA50 exists. Six-line width uses current
    rope_high-rope_low/currentATR. The prior24h windows exclude t and require
    exact24h history. Return is close[t-1]/close[t-1-24h]-1. First-high compares
    the current breakout boolean with its previous value, both past-only.
    """
    if len(bars) < 2:
        return bars.iloc[:0].copy()
    result = build_altcoin_features(bars)
    for col in BAR_COLUMNS:
        result[col] = bars[col]
    result["history_count"] = np.arange(len(result)) + 1
    result["above_all6"] = (result.loc[:, MA_COLUMNS].notna().all(axis=1)
                            & bars.close.gt(result.loc[:, MA_COLUMNS].max(axis=1)))
    result["six_ma_width_atr"] = (result.rope_high-result.rope_low) / result.atr.where(result.atr.gt(0))
    diff = bars.close - bars.close.rolling(50).mean()
    result["momentum10"] = 100 * diff / diff.abs().rolling(50, min_periods=1).max().replace(0, np.nan)
    count = 1440 // minutes
    result["prior24h_quote_volume"] = bars.quote_volume.shift(1).rolling(count).sum()
    result["prior24h_return"] = bars.close.shift(1) / bars.close.shift(count+1) - 1
    result["prior12_high"] = bars.high.shift(1).rolling(12).max()
    result["prior20_high"] = bars.high.shift(1).rolling(20).max()
    breakout = bars.close.gt(result.prior12_high)
    result["first12_breakout"] = breakout & ~breakout.shift(1, fill_value=False)
    young_breakout = bars.close.gt(result.prior20_high)
    result["first20_breakout"] = young_breakout & ~young_breakout.shift(1, fill_value=False)
    result.attrs.update(minutes=minutes, period_seconds=minutes * 60,
                        causal_source="confirmed_closed_ohlcv", parameter_version="34-9-12-0.10")
    return result


def build_features(hourly: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Return1H/4H feature tables including original OHLCV and quote_volume.

    Input must be continuous, UTC open-stamped confirmed1H data. A partial
    leading/trailing4H group is discarded, never completed using a future row.
    Fewer than two complete bars yields an empty feature table because the
    shared source validator cannot establish an interval from one observation.
    This function does not impose the research or acquisition date range.
    """
    one = _price_frame(hourly, 60, require_volume=True)
    grouped = one.resample("4h", origin="epoch", label="left", closed="left")
    four = grouped.agg(dict(open="first", high="max", low="min", close="last",
                            volume="sum", quote_volume="sum"))
    four = four.loc[grouped.close.count().eq(4)]
    four.attrs.update(minutes=240, period_seconds=14_400)
    return {60: _feature_frame(one, 60), 240: _feature_frame(four, 240)}


def candidate_events(features: pd.DataFrame, minutes: int, first_i: int = 0,
                     last_i: int | None = None) -> pd.DataFrame:
    """Emit all eligible candidates, including a last-bar unfillable decision.

    Pullback touch means the candle [low,high] intersects the closed20-MA band.
    It arms on a NEW contact, may recover at that same close, and waits at most
    twelve subsequent bars. Trend loss cancels it. After consumption/expiry,
    continuing contact cannot rearm it until price leaves and touches again.
    Processing starts at history origin so changing an evaluation boundary
    does not manufacture an extra arm. No future labels or exit prices enter.
    """
    if features.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    _clock(features.index, minutes)
    if last_i is None:
        last_i = len(features)-1
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Integral) for v in (first_i, last_i)):
        raise ValueError("Candidate bounds must be integer positions")
    if not 0 <= first_i <= last_i < len(features):
        raise ValueError("Invalid candidate bounds")
    needed = {"ready", "release_side", "first12_breakout", "first20_breakout", "close", "high", "low",
              "ema20", *CONTEXT_COLUMNS}
    if not needed.issubset(features):
        raise ValueError("Candidate features are incomplete")
    values = {name: features[name].to_numpy() for name in needed}
    ready_values = values["ready"].astype(bool)
    s20, e20, s60, s120 = (values[name] for name in ("sma20", "ema20", "sma60", "sma120"))
    lower_band, upper_band = np.minimum(s20, e20), np.maximum(s20, e20)
    trend_values = (ready_values & (s20 > s60) & (s60 > s120)
                    & (s60 > np.r_[np.nan, s60[:-1]]))
    contact_values = trend_values & (values["low"] <= upper_band) & (values["high"] >= lower_band)
    recovery_values = (values["close"] > upper_band) & (values["close"] > np.r_[np.nan, values["high"][:-1]])
    focus_values = ready_values & (values["release_side"] == 1)
    dense_values = (ready_values & values["dense_recent"].astype(bool)
                    & values["above_all6"].astype(bool) & values["first12_breakout"].astype(bool))
    young_values = ((values["history_count"] >= 60) & (values["history_count"] < 340)
                    & values["first20_breakout"].astype(bool) & (values["close"] > s20)
                    & (values["relative_volume"] >= 2))
    rows = []
    armed = None
    previous_contact = False
    for i in range(last_i+1):
        trend, contact = bool(trend_values[i]), bool(contact_values[i])
        if not trend:
            armed = None
        elif armed is not None and i-armed > 12:
            armed = None
        if contact and not previous_contact and armed is None:
            armed = i
        pullback = bool(armed is not None and trend and recovery_values[i])
        if pullback:
            armed = None
        previous_contact = contact
        selected = []
        if focus_values[i]:
            selected.extend(("focus_sma60", "focus_md", "focus_sma20", "focus_3r"))
        if dense_values[i]:
            selected.append("dense_sma60")
        if pullback:
            selected.append("pullback_sma60")
        if young_values[i]:
            selected.append("young_breakout_sma20")
        if i < first_i:
            continue
        for arm in selected:
            row = {name: values[name][i] for name in CONTEXT_COLUMNS}
            row.update(arm=arm, exit_rule=ARMS[arm], decision_i=i, signal_i=i,
                       minutes=minutes, decision_bar_open_time=features.index[i],
                       decision_time=features.index[i]+pd.Timedelta(minutes=minutes))
            rows.append(row)
    return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


@dataclass(frozen=True)
class SimulationInputs:
    """Explicit immutable-by-contract snapshot for repeated event evaluation.

    Arrays are private copies with write access disabled; caller DataFrame
    mutations cannot alter the snapshot. The mapping itself is read-only.
    No global caches or caller attrs are mutated. Prepare once per instrument
    and timeframe, then pass to simulate_prepared for candidates and controls.
    """

    index: pd.DatetimeIndex
    prices: np.ndarray
    values: Mapping[str, np.ndarray]
    minutes: int


def prepare_simulation(bars: pd.DataFrame, features: pd.DataFrame,
                       last_i: int | None = None) -> SimulationInputs:
    """Validate/copy known history once; optional cutoff is inclusive.

    OHLC must be valid throughout the requested snapshot. Optional md/MA
    columns may contain NaNs; the selected exit checks only values it needs
    while held. In particular young SMA20 events need no available SMA60/120.
    """
    if not features.index.equals(bars.index):
        raise ValueError("Features and bars must have the identical index")
    if "atr" not in features:
        raise ValueError("Signal ATR is required")
    if last_i is None:
        last_i = len(bars)-1
    if (isinstance(last_i, (bool, np.bool_)) or not isinstance(last_i, Integral)
            or not 0 <= last_i < len(bars)):
        raise ValueError("Invalid snapshot cutoff")
    minutes = int(bars.attrs.get("minutes", features.attrs.get("minutes", 0)))
    if not minutes:
        seconds = bars.attrs.get("period_seconds", features.attrs.get("period_seconds"))
        if seconds is not None:
            minutes = int(seconds)//60
        elif len(bars) > 1:
            minutes = int((bars.index[1]-bars.index[0])/pd.Timedelta(minutes=1))
    b = _price_frame(bars.iloc[:last_i+1], minutes, require_volume=False)
    prices = b.to_numpy(dtype=float, copy=True)
    prices.setflags(write=False)
    values = {}
    for name in ("atr", "md", "sma20", "sma60"):
        if name in features:
            data = features[name].iloc[:last_i+1].to_numpy(dtype=float, copy=True)
            data.setflags(write=False)
            values[name] = data
    return SimulationInputs(b.index.copy(), prices, MappingProxyType(values), minutes)


def simulate_event(bars: pd.DataFrame, features: pd.DataFrame, i: int,
                   exit_rule: str, last_i: int, *, include_protection: bool = False) -> dict:
    """Convenience wrapper; for many events prepare_simulation once instead."""
    inputs = prepare_simulation(bars, features, last_i)
    return simulate_prepared(inputs, i, exit_rule, last_i, include_protection=include_protection)


def simulate_prepared(inputs: SimulationInputs, i: int, exit_rule: str,
                      last_i: int, *, include_protection: bool = False) -> dict:
    """Evaluate one long event, retaining unfillable or invalid-risk candidates.

    ``i`` is the decision bar; ``last_i`` the last fully available OHLC bar.
    Intrabar exit_time uses the enclosing CLOSE as an explicit upper bound;
    exit_time_lower/upper and held_hours_lower/upper carry the uncertainty.
    entry/close-trigger/next-open clocks are exact. There is no maximum-hold
    timeout: a surviving position is marked only at the supplied fold boundary.
    Optional protection is a list of dictionaries for each visited bar,
    including the prior effective close-protection level and hard initial stop.
    """
    if exit_rule in ARMS:
        exit_rule = ARMS[exit_rule]
    if exit_rule not in EXIT_RULES:
        raise ValueError("Unknown exit rule")
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, Integral) for v in (i, last_i)):
        raise ValueError("Event positions must be integers")
    if not isinstance(inputs, SimulationInputs):
        raise ValueError("Prepared simulation inputs are required")
    if not 0 <= i <= last_i < len(inputs.index):
        raise ValueError("Invalid event positions")
    index, prices, values, minutes = inputs.index, inputs.prices, inputs.values, inputs.minutes
    step = pd.Timedelta(minutes=minutes)
    result = dict(signal_i=int(i), decision_i=int(i), signal_time=index[i],
        decision_time=index[i]+step, entry_i=i+1, entry_time=pd.NaT,
        exit_rule=exit_rule, valid=False, invalid_reason="", reason="", exit_reason="",
        natural_exit=False, censored=False, exit_at_open=False, exit_timing="",
        fee_return=2*FEE_SIDE, fee_bp=2*FEE_SIDE*10_000, period_seconds=minutes*60,
        trigger_i=None, trigger_time=pd.NaT)
    if include_protection:
        result["protection"] = []
    if i+1 > last_i:
        result.update(invalid_reason="no_entry_bar", reason="no_entry_bar")
        return result
    atr = float(values["atr"][i])
    entry = float(prices[i+1, 0])
    risk = STOP_ATR*atr
    result.update(entry_time=index[i+1], entry_price=entry, signal_atr=atr,
                  initial_risk=risk, initial_risk_frac=risk/entry)
    if not np.isfinite(risk) or not 0 < risk < entry:
        result.update(invalid_reason="invalid_initial_risk", reason="invalid_initial_risk")
        return result
    stop, target = entry-risk, entry+3*risk
    result.update(initial_stop=stop, target_price=target if exit_rule == "fixed3r" else np.nan)
    ma_name = exit_rule.removeprefix("ratchet_") if exit_rule.startswith("ratchet_") else None
    if ma_name and ma_name not in values:
        raise ValueError("Required protection MA is missing")
    if exit_rule == "md" and "md" not in values:
        raise ValueError("md is required for the md exit")
    level = stop
    if ma_name:
        initial_ma = float(values[ma_name][i])
        if not np.isfinite(initial_ma) or initial_ma <= 0:
            result.update(invalid_reason="missing_initial_protection", reason="missing_initial_protection")
            return result
        if float(prices[i, 3]) > initial_ma:
            level = max(level, initial_ma)
    mfe = mae = 0.0
    pending = None
    for j in range(i+1, last_i+1):
        o, h, l, c = prices[j]
        if include_protection:
            result["protection"].append(dict(bar_i=j, bar_open=index[j],
                protection=level, initial_stop=stop))
        # This observed open belongs to the held path before any current-bar
        # action; later extrema do not if the position exits at this open.
        mfe, mae = max(mfe, o/entry-1, 0), max(mae, 1-o/entry, 0)
        if o <= stop:
            price, reason, timing = o, "initial_stop_gap", "open"
            break
        if pending is not None:
            price, reason, timing = o, pending, "open"
            break
        if exit_rule == "fixed3r" and o >= target:
            price, reason, timing = target, "take_profit_gap", "open"
            break
        if l <= stop:
            price, reason, timing = stop, "initial_stop", "intrabar_unknown"
            break
        if exit_rule == "fixed3r" and h >= target:
            price, reason, timing = target, "take_profit", "intrabar_unknown"
            break
        mfe, mae = max(mfe, h/entry-1, 0), max(mae, 1-l/entry, 0)
        if j == last_i:
            price, reason, timing = c, "boundary_mark", "close"
            break
        if ma_name:
            ma = float(values[ma_name][j])
            if not np.isfinite(ma) or ma <= 0:
                result.update(invalid_reason="missing_protection_while_held", reason="missing_protection_while_held")
                return result
            if c < level:
                pending = exit_rule
            elif c > ma:
                level = max(level, ma)
        elif exit_rule == "md":
            md = float(values["md"][j])
            if not np.isfinite(md):
                result.update(invalid_reason="missing_md_while_held", reason="missing_md_while_held")
                return result
            if md <= 0:
                pending = "md"
        if pending is not None:
            result.update(trigger_i=j, trigger_time=index[j]+step)
    gross = price/entry-1
    net = gross-2*FEE_SIDE
    mfe, mae = max(mfe, gross, 0), max(mae, -gross, 0)
    exact = timing != "intrabar_unknown"
    lower = index[j] + (step if timing == "close" else pd.Timedelta(0))
    upper = lower if exact else index[j]+step
    risk_frac = risk/entry
    result.update(valid=True, exit_i=j, exit_price=float(price), exit_reason=reason,
        reason=reason, exit_at_open=timing == "open", exit_timing=timing,
        exit_time=upper, exit_time_lower=lower, exit_time_upper=upper,
        exit_time_exact=exact, natural_exit=reason != "boundary_mark", censored=reason == "boundary_mark",
        held_hours_lower=(lower-result["entry_time"])/pd.Timedelta(hours=1),
        held_hours_upper=(upper-result["entry_time"])/pd.Timedelta(hours=1),
        hold_seconds=(upper-result["entry_time"])/pd.Timedelta(seconds=1),
        hold_bars=j-(i+1)+(timing != "open"),
        gross_return=gross, net_return=net, gross_bp=gross*10_000, net_bp=net*10_000,
        gross_r=gross/risk_frac, net_r=net/risk_frac,
        mfe_return=mfe, mae_return=mae, mfe_r=mfe/risk_frac, mae_r=mae/risk_frac,
        capture_ratio=gross/mfe if mfe > 0 else np.nan,
        entry_fee_per_base=entry*FEE_SIDE, exit_fee_per_base=entry*FEE_SIDE,
        total_fee_per_base=entry*2*FEE_SIDE, gross_pnl_per_base=price-entry,
        net_pnl_per_base=price-entry-entry*2*FEE_SIDE)
    return result
