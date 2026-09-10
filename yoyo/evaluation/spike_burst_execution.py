"""Independent, long-only next-open execution for the frozen SPIKE V1 rules.

The signal's initial stop uses only close/ATR on the decision bar and the
lowest low of its last five bars (including that bar). It is the lower of
low minus 0.2 ATR and close minus 2 ATR, floored to the supplied price tick.
Entry is the next real open; risk and the 2R activation use this actual fill,
not Pine's signal-close observation. This difference is deliberate.

Subsequent closed bars first test the stop carried from the previous close.
Stop-hit bars never improve peak R. Otherwise their high updates peak R, and
their close may permanently arm the trail at 2 actual-fill R. Once armed,
close minus 4 current ATR can raise protection for the NEXT bar only. There
is no fixed profit target. Unknown current ATR leaves prior protection intact.

Inputs are confirmed, ordinary UTC OHLC/ATR frames at 1H or 4H, open-stamped.
Only bars whose closes are at or before the exclusive entry cutoff ``end``
are inspected. A surviving position is explicitly censored at the last
available complete close. Costs are a fixed 20bp of entry notional round trip;
funding, impact, spreads, and exchange-specific fees are not represented.
No live service, exchange client, monitor, model, or order code is imported.
"""
from __future__ import annotations

import math
from numbers import Integral

import numpy as np
import pandas as pd


def _frame_clock(frame: pd.DataFrame, end: pd.Timestamp):
    """Validate metadata and return a read-only cutoff view, never fill gaps."""
    required = {"open", "high", "low", "close", "atr"}
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique or not required.issubset(frame):
        raise ValueError("Unique OHLC and atr columns are required")
    minutes = frame.attrs.get("minutes")
    if isinstance(minutes, (bool, np.bool_)) or minutes not in (60, 240):
        raise ValueError("Frame attrs minutes must be 60 or 240")
    index = frame.index
    if (not isinstance(index, pd.DatetimeIndex) or index.tz is None
            or str(index.tz) not in ("UTC", "Etc/UTC", "GMT", "Etc/GMT")
            or index.hasnans or not index.is_unique or not index.is_monotonic_increasing):
        raise ValueError("A unique chronological UTC index is required")
    index = index.as_unit("ns")
    step = pd.Timedelta(minutes=int(minutes))
    end = pd.Timestamp(end)
    if pd.isna(end) or end.tz is None or end.utcoffset().total_seconds() != 0 or end.value % step.value:
        raise ValueError("Cutoff must be an aligned UTC timestamp")
    if np.any(index.asi8 % step.value):
        raise ValueError("Open timestamps must be timeframe-aligned")
    count = int((index + step <= end).sum())
    selected = frame.iloc[:count]
    stamps = index[:count]
    if len(stamps) > 1 and np.any(np.diff(stamps.asi8) != step.value):
        raise ValueError("Candle segment must be continuous before cutoff")
    return selected, stamps, step


def simulate_trade(frame: pd.DataFrame, decision_i: int, tick: float,
                   end: pd.Timestamp, *, include_path: bool = False) -> dict:
    """Simulate one long signal, retaining invalid/unfillable candidates.

    ``decision_i`` refers to the original frame's zero-based signal bar.
    Signal ATR and the preceding up-to-five lows are immutable risk inputs;
    entry uses bar i+1 open. Current highs/lows are outcomes only after entry.
    Intrabar fills use the enclosing close for cash release and explicit
    lower/upper clocks; gap fills use their observed open. The caller owns
    candidate selection, source hashes, independent controls, and allocation.
    ``include_path=True`` adds a ``path`` DataFrame containing each visited
    bar's previously effective ``active_stop``, plus entry/exit flags. The
    default output has no path field and remains a flat event record.
    """
    if (isinstance(decision_i, (bool, np.bool_)) or not isinstance(decision_i, Integral)
            or not 0 <= decision_i < len(frame)):
        raise ValueError("decision_i must be a valid integer frame position")
    bars, index, step = _frame_clock(frame, end)
    i = int(decision_i)
    result = dict(valid=False, invalid_reason="", reason="", exit_reason="",
        signal_i=i, decision_i=i, entry_i=i + 1, exit_i=None,
        decision_time=frame.index[i] + step,
        entry_time=pd.NaT, exit_time=pd.NaT, exit_time_lower=pd.NaT,
        exit_time_upper=pd.NaT, exit_timing="", exit_time_exact=False,
        entry_price=np.nan, exit_price=np.nan, initial_stop=np.nan,
        initial_risk=np.nan, initial_risk_frac=np.nan, net_return=np.nan,
        net_r=np.nan, peak_r=0.0, natural_exit=False, censored=False,
        hold_bars=0, fee_return=0.002, fee_bp=20.0)
    path_rows = [] if include_path else None

    def invalid(reason):
        result.update(invalid_reason=reason, reason=reason)
        if include_path:
            result["path"] = pd.DataFrame(columns=["active_stop", "held", "entry", "exit", "entry_price", "exit_price"])
        return result

    if i >= len(bars):
        return invalid("decision_after_cutoff")
    if i + 1 >= len(bars):
        return invalid("no_entry_bar")
    try:
        tick = float(tick)
    except (TypeError, ValueError):
        return invalid("invalid_tick")
    if not np.isfinite(tick) or tick <= 0:
        return invalid("invalid_tick")
    def checked_prices(values):
        if not np.isfinite(values).all():
            raise ValueError("Inspected OHLC values must be finite")
        o, h, l, c = values.T
        if np.any((l <= 0) | (h < np.maximum(o, c)) | (l > np.minimum(o, c))):
            raise ValueError("Invalid inspected OHLC bounds")

    prices = bars.iloc[max(0, i-4):i+2][["open", "high", "low", "close"]].to_numpy(dtype=float)
    checked_prices(prices)
    a = float(bars.atr.iloc[i])
    if not np.isfinite(a) or a <= 0:
        return invalid("invalid_signal_atr")
    signal_close = float(bars.close.iloc[i])
    extreme = float(bars.low.iloc[max(0, i-4):i+1].min())
    raw_stop = min(extreme - 0.2 * a, signal_close - 2.0 * a)
    ratio = raw_stop / tick
    if not np.isfinite(ratio):
        return invalid("invalid_initial_stop")
    stop = math.floor(ratio) * tick
    entry = float(bars.open.iloc[i+1])
    risk = entry - stop
    result.update(entry_time=index[i+1], entry_price=entry, signal_close=signal_close,
                  signal_atr=a, initial_stop=stop, initial_risk=risk,
                  initial_risk_frac=risk / entry)
    if not np.isfinite(stop) or stop <= 0:
        return invalid("invalid_initial_stop")
    if not np.isfinite(risk) or risk <= 0:
        return invalid("entry_at_or_below_stop")

    protection, peak, armed = stop, 0.0, False
    for j in range(i+1, len(bars)):
        row = bars.iloc[j]
        opening, high, low, close = (float(row[k]) for k in ("open", "high", "low", "close"))
        checked_prices(np.array([opening, high, low, close]))
        if path_rows is not None:
            path_rows.append(dict(open_time=index[j], bar_i=j, active_stop=protection,
                held=True, entry=j == i+1, exit=False,
                entry_price=entry if j == i+1 else np.nan, exit_price=np.nan))
        if low <= protection:
            price = min(opening, protection)
            reason = "trailing_stop" if protection > stop else "initial_stop"
            gap = opening <= protection
            if gap:
                reason += "_gap"
            timing = "open" if gap and j > i+1 else "intrabar_unknown"
            break
        peak = max(peak, (high-entry)/risk)
        armed = armed or (close-entry)/risk >= 2.0
        if j == len(bars)-1:
            price, reason, timing = close, "boundary_mark", "close"
            break
        current_atr = float(row.atr)
        if armed and np.isfinite(current_atr) and current_atr > 0:
            candidate = (close - 4.0 * current_atr) / tick
            if np.isfinite(candidate):
                protection = max(protection, math.floor(candidate) * tick)

    lower = index[j] + (step if timing == "close" else pd.Timedelta(0))
    upper = lower if timing != "intrabar_unknown" else index[j] + step
    gross = price / entry - 1.0
    net = gross - 0.002
    fraction = risk / entry
    result.update(valid=True, exit_i=j, exit_price=float(price), exit_reason=reason,
        reason=reason, exit_timing=timing, exit_at_open=timing == "open",
        exit_time=upper, exit_time_lower=lower, exit_time_upper=upper,
        exit_time_exact=timing != "intrabar_unknown", peak_r=peak, mfe_r=peak,
        mfe_return=peak * fraction, initial_risk_frac=fraction,
        natural_exit=reason != "boundary_mark", censored=reason == "boundary_mark",
        hold_bars=j-(i+1)+int(timing != "open"),
        held_hours_lower=(lower-index[i+1])/pd.Timedelta(hours=1),
        held_hours_upper=(upper-index[i+1])/pd.Timedelta(hours=1),
        hold_seconds=(upper-index[i+1])/pd.Timedelta(seconds=1),
        gross_return=gross, gross_bp=10000*gross, net_return=net, net_bp=10000*net,
        gross_r=gross/fraction, net_r=net/fraction, final_protection=protection,
        trail_armed=armed, peak_return=peak*fraction)
    if path_rows is not None:
        path_rows[-1].update(exit=True, exit_price=price)
        result["path"] = pd.DataFrame(path_rows).set_index("open_time")
    return result
