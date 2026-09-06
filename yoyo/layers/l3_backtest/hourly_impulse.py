"""Causal replay for completed-hour impulse entries and faster colour exits.

Signal features are supplied by the caller; this module creates no entry
features. Management inputs use only each completed management bar's ``ma_side``
and, for slope confirmation, ``ma_slope_atr``. Their availability is strictly
``open_time + management_minutes``. Execution uses subsequent 5-minute OHLC
bars, never the unfinished hourly candle. Outcomes may inspect future execution
bars as labels, but cannot modify the supplied signal or fixed initial stop.

Each event is replayed independently for paired exit experiments. Such returns
are NOT a portfolio. ``single_position_ledger`` subsequently enforces one open
position, without compounding. Cost is one fixed round-trip fraction of original
entry notional (including partial exits); funding is explicitly not modelled.
Intrabar SL/TP collisions are stop-first. Excursions on a barrier-exit bar use
the open and fill only because the order of OHLC extrema is unknown.

The optional native-5m transition exit uses an adjacent completed-bar colour
edge, not an opposite-colour state. Its clock follows pandas 2.3.3 Timestamp /
Timedelta arithmetic and TradingView's confirmed-bar availability semantics:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html
https://www.tradingview.com/pine-script-docs/language/execution-model/
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import numpy as np
import pandas as pd


FIVE_MINUTES = pd.Timedelta(minutes=5)
DEFAULT_POLICY = {
    "management_minutes": 15,
    "exit_mode": "colour",
    "confirmations": 1,
    "max_hours": 72,
    "cost_fraction": 0.002,
}


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("Timestamps cannot be missing")
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _validated_frame(frame: pd.DataFrame, columns: tuple, name: str) -> pd.DataFrame:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError("{} missing columns: {}".format(name, sorted(missing)))
    result = frame.copy()
    result["open_time"] = pd.to_datetime(result["open_time"], utc=True)
    stamps = result["open_time"]
    if stamps.isna().any() or stamps.duplicated().any() or not stamps.is_monotonic_increasing:
        raise ValueError("{} open_time must be finite, unique, chronological".format(name))
    return result


def _policy(policy: Mapping[str, Any]) -> Dict[str, Any]:
    selected = dict(DEFAULT_POLICY)
    selected.update(policy)
    if selected["exit_mode"] == "hour_colour":
        selected["management_minutes"] = 60
    if selected["exit_mode"] not in {
        "colour", "hour_colour", "slope_colour", "partial_colour", "fixed_3r",
        "transition_colour",
    }:
        raise ValueError("Unknown exit_mode")
    minutes = selected["management_minutes"]
    confirmations = selected["confirmations"]
    if minutes not in (5, 15, 60):
        raise ValueError("management_minutes must be 5, 15, or 60")
    if isinstance(confirmations, bool) or int(confirmations) != confirmations or confirmations < 1:
        raise ValueError("confirmations must be a positive integer")
    if selected["exit_mode"] == "transition_colour" and (minutes != 5 or confirmations != 1):
        raise ValueError("transition_colour requires management_minutes=5 and confirmations=1")
    # An explicit integer-minute horizon wins over inherited max_hours. Using
    # fractional hours can truncate an exact 5m duration by 1ns in pandas 2.3.3.
    # Without max_minutes the historical max_hours validation is unchanged.
    if "max_minutes" in selected:
        max_minutes = selected["max_minutes"]
        if isinstance(max_minutes, (bool, np.bool_)) or not isinstance(max_minutes, (int, np.integer)) or max_minutes <= 0 or max_minutes % 5 != 0:
            raise ValueError("max_minutes must be a positive integer multiple of five")
    else:
        if not np.isfinite(selected["max_hours"]) or selected["max_hours"] <= 0:
            raise ValueError("max_hours must be finite and positive")
        if (pd.Timedelta(hours=selected["max_hours"]).value % FIVE_MINUTES.value) != 0:
            raise ValueError("max_hours must align to the 5-minute execution grid")
    if not np.isfinite(selected["cost_fraction"]) or selected["cost_fraction"] < 0:
        raise ValueError("cost_fraction must be finite and nonnegative")
    return selected


def _transition_observation(
    bar: Any,
    available_at: pd.Timestamp,
    time_index: Mapping[pd.Timestamp, int],
    prices: np.ndarray,
    segments: np.ndarray,
) -> tuple:
    """Validate one native-5m colour using information available at its close.

    Only the exact just-completed management bar and its source raw5 OHLC are
    inspected. At ``available_at`` only the new raw5 open is read. Management
    segment numbers have their own counting space: source continuity instead
    maps both timestamps to raw5 segment numbers. The slope is not used by this
    exit and a missing slope does not invalidate an otherwise known colour.
    """
    if bar is None:
        return None, "missing_management"
    if bar.open_time + FIVE_MINUTES != available_at:
        return None, "stale_management"
    try:
        side, ma, low, high, close = map(float, (bar.ma_side, bar.ma, bar.low, bar.high, bar.close))
    except (TypeError, ValueError):
        return None, "invalid_management"
    if not np.isfinite([side, ma, low, high, close]).all():
        return None, "nonfinite_management"
    if side not in (-1.0, 1.0) or min(ma, low, high, close) <= 0 or not low <= close <= high:
        return None, "invalid_management"
    if pd.isna(bar.segment_id) or (isinstance(bar.segment_id, (float, np.floating)) and not np.isfinite(bar.segment_id)):
        return None, "unknown_management_segment"
    source_index = time_index.get(bar.open_time)
    next_index = time_index.get(available_at)
    if source_index is None or next_index is None:
        return None, "missing_source"
    source_segments = (segments[source_index], segments[next_index])
    unknown_source_segment = any(
        pd.isna(segment) or (isinstance(segment, (float, np.floating)) and not np.isfinite(segment))
        for segment in source_segments
    )
    if unknown_source_segment or source_segments[0] != source_segments[1]:
        return None, "source_segment_change"
    source_open, source_high, source_low, source_close = prices[source_index]
    if not np.isfinite(prices[source_index]).all() or min(prices[source_index]) <= 0 or not source_low <= min(source_open, source_close) <= max(source_open, source_close) <= source_high:
        return None, "invalid_completed_source"
    if not np.isfinite(prices[next_index, 0]) or prices[next_index, 0] <= 0:
        return None, "invalid_source_open"
    return side, "valid"


def simulate_events(
    raw5: pd.DataFrame,
    management_featured: pd.DataFrame,
    entries: pd.DataFrame,
    policy: Mapping[str, Any],
    *,
    end_exclusive: Optional[Any] = None,
) -> pd.DataFrame:
    """Replay each entry against 5m bars with completed-bar management timing.

    ``decision_time`` is the close of a completed hourly signal and the exact
    entry open. ``direction`` is +1/-1; ``initial_stop`` is the unmodified signal
    extreme. All entry feature columns are copied to each outcome. A missing
    execution open rejects entry. A subsequent missing 5m bar/segment change
    censors at the last known close. No forced phase-end liquidation is counted
    as a completed trade, and censored ``net_return``/``net_r`` are NaN.

    At a shared timestamp: gap-open hard stop, completed-management exit,
    maximum-duration exit, then current-bar intrabar barriers. Management bars
    beginning before entry cannot trigger any exit. In ``partial_colour`` half
    the original position exits once on first opposite colour; its remainder
    exits after any two consecutive opposite management bars.

    ``transition_colour`` is explicitly native 5m / one confirmation. The bar
    ending exactly at entry initializes colour but cannot exit. Only a valid
    aligned-to-opposite edge across adjacent complete bars exits, earliest at
    entry + 5m. Missing/invalid observations reset the edge; a management-segment
    change starts a fresh sequence. An initially opposite/unknown state must
    first observe an aligned complete bar. No other mode uses this state.

    An optional positive integer ``max_minutes`` (multiple of five) takes
    precedence over ``max_hours``. It represents the exact remaining duration
    for delayed entries without converting integer minutes to fractional hours.
    """
    selected = _policy(policy)
    raw = _validated_frame(raw5, ("open_time", "open", "high", "low", "close", "segment_id"), "raw5")
    management = _validated_frame(
        management_featured,
        ("open_time", "ma", "ma_side", "ma_slope_atr", "low", "high", "close", "segment_id"),
        "management_featured",
    )
    required_entries = {"event_id", "decision_time", "direction", "initial_stop", "signal_atr"}
    if not required_entries.issubset(entries.columns):
        raise ValueError("entries missing columns: {}".format(sorted(required_entries - set(entries.columns))))
    if entries["event_id"].duplicated().any():
        raise ValueError("event_id must be unique for independent paired outcomes")
    if entries.empty:
        return pd.DataFrame(columns=list(entries.columns) + ["entry_time", "exit_time", "closed", "outcome", "net_return", "net_r"])

    times = raw["open_time"].to_numpy()
    time_index = {pd.Timestamp(time): i for i, time in enumerate(times)}
    prices = raw[["open", "high", "low", "close"]].to_numpy(dtype=float)
    segments = raw["segment_id"].to_numpy()
    interval = pd.Timedelta(minutes=selected["management_minutes"])
    management_at = {
        row.open_time + interval: row
        for row in management.itertuples(index=False)
    }
    cutoff = _utc(end_exclusive) if end_exclusive is not None else None
    horizon_delta = (pd.Timedelta(minutes=int(selected["max_minutes"]))
                     if "max_minutes" in selected else pd.Timedelta(hours=selected["max_hours"]))
    cost = float(selected["cost_fraction"])
    mode = selected["exit_mode"]
    outputs = []

    for event in entries.to_dict("records"):
        result = dict(event)
        entry_time = _utc(event["decision_time"])
        result.update(
            entry_time=entry_time, entry_price=np.nan, exit_time=pd.NaT,
            exit_price=np.nan, closed=False, outcome="entry_missing",
            gross_return=np.nan, net_return=np.nan, net_r=np.nan,
            risk_pct=np.nan, risk_atr=np.nan, hold_minutes=np.nan,
            partial_fraction=0.0, exit_remaining_fraction=1.0,
            partial_exit_time=pd.NaT, partial_exit_price=np.nan,
            realised_partial_gross_return=0.0,
            marked_gross_return=np.nan, marked_net_return=np.nan,
            max_favourable_r=0.0, max_adverse_r=0.0,
            bars_to_first_positive=np.nan, funding_modelled=False,
        )
        if mode == "transition_colour":
            result.update(
                transition_initial_state="unknown", transition_initial_side=np.nan,
                transition_initial_reason="entry_not_validated",
                transition_initial_open_time=pd.NaT,
                transition_armed_at=pd.NaT, transition_first_armed_at=pd.NaT,
                transition_trigger_previous_open_time=pd.NaT,
                transition_trigger_open_time=pd.NaT,
                transition_trigger_available_at=pd.NaT,
                transition_reset_count=0, transition_last_reset_reason="",
            )
        index = time_index.get(entry_time)
        if index is None or (cutoff is not None and entry_time >= cutoff):
            outputs.append(result)
            continue
        initial = prices[index]
        direction = float(event["direction"])
        stop = float(event["initial_stop"])
        atr = float(event["signal_atr"])
        entry = float(initial[0])
        result["entry_price"] = entry
        # Entry uses only this bar's open, never its still-unseen extrema.
        valid_initial = np.isfinite(entry) and entry > 0
        if not valid_initial or not np.isfinite([direction, stop, atr]).all() or direction not in (-1, 1) or atr <= 0 or stop <= 0:
            result["outcome"] = "entry_invalid"
            outputs.append(result)
            continue
        risk = direction * (entry - stop)
        if risk <= 0:
            result["outcome"] = "entry_invalid_risk"
            outputs.append(result)
            continue
        result["risk_pct"] = risk / entry
        result["risk_atr"] = risk / atr
        target = entry + direction * 3.0 * risk
        deadline = entry_time + horizon_delta
        remaining, realised, opposite_streak = 1.0, 0.0, 0
        last_close, last_time = entry, entry_time
        first_segment = segments[index]
        previous_open = None
        previous_management_close = None
        completed = False
        transition_previous_side = None
        transition_previous_bar = None
        if mode == "transition_colour":
            initial_management = management_at.get(entry_time)
            transition_previous_side, initial_reason = _transition_observation(
                initial_management, entry_time, time_index, prices, segments,
            )
            result["transition_initial_reason"] = initial_reason
            if transition_previous_side is not None:
                transition_previous_bar = initial_management
                result.update(
                    transition_initial_side=transition_previous_side,
                    transition_initial_state="aligned" if direction * transition_previous_side > 0 else "opposite",
                    transition_initial_open_time=initial_management.open_time,
                )
                if direction * transition_previous_side > 0:
                    result["transition_armed_at"] = entry_time
                    result["transition_first_armed_at"] = entry_time

        def record_excursion(high: float, low: float, bars: int) -> None:
            favourable = (high - entry) / risk if direction == 1 else (entry - low) / risk
            adverse = (low - entry) / risk if direction == 1 else (entry - high) / risk
            result["max_favourable_r"] = max(result["max_favourable_r"], favourable)
            result["max_adverse_r"] = min(result["max_adverse_r"], adverse)
            if favourable > 0 and pd.isna(result["bars_to_first_positive"]):
                result["bars_to_first_positive"] = bars

        def finish(time: pd.Timestamp, price: float, outcome: str, closed: bool) -> None:
            gross = realised + remaining * direction * (price / entry - 1.0)
            result.update(
                exit_time=time, exit_price=price, outcome=outcome, closed=closed,
                hold_minutes=(time - entry_time).total_seconds() / 60.0,
                exit_remaining_fraction=remaining,
                realised_partial_gross_return=realised,
                marked_gross_return=gross, marked_net_return=gross - cost,
            )
            if closed:
                result.update(gross_return=gross, net_return=gross - cost, net_r=(gross - cost) / (risk / entry))

        for i in range(index, len(raw)):
            now = pd.Timestamp(times[i])
            if cutoff is not None and now >= cutoff:
                break
            if (previous_open is not None and now != previous_open + FIVE_MINUTES) or pd.isna(segments[i]) or segments[i] != first_segment:
                finish(last_time, last_close, "data_gap_censored", False)
                completed = True
                break
            open_, high, low, close = prices[i]
            if not np.isfinite(open_) or open_ <= 0:
                finish(last_time, last_close, "data_gap_censored", False)
                completed = True
                break
            bars = i - index
            record_excursion(open_, open_, bars)
            # Existing resting protection has priority over a market exit.
            if direction * (open_ - stop) <= 0:
                finish(now, open_, "hard_stop_gap", True)
                completed = True
                break
            if mode == "fixed_3r" and direction * (open_ - target) >= 0:
                finish(now, open_, "target_3r", True)
                completed = True
                break

            management_bar = management_at.get(now)
            if mode == "transition_colour" and now > entry_time:
                current_side, reset_reason = _transition_observation(
                    management_bar, now, time_index, prices, segments,
                )
                consecutive = (
                    transition_previous_bar is not None and management_bar is not None
                    and management_bar.open_time == transition_previous_bar.open_time + FIVE_MINUTES
                    and management_bar.segment_id == transition_previous_bar.segment_id
                )
                if current_side is None or not consecutive:
                    if current_side is not None and transition_previous_bar is not None:
                        reset_reason = "management_sequence_change"
                    transition_previous_side = None
                    result["transition_armed_at"] = pd.NaT
                    if current_side is None or transition_previous_bar is not None:
                        result["transition_reset_count"] += 1
                        result["transition_last_reset_reason"] = reset_reason
                if current_side is not None:
                    if transition_previous_side is not None and direction * transition_previous_side > 0 and direction * current_side < 0:
                        result.update(
                            transition_trigger_previous_open_time=transition_previous_bar.open_time,
                            transition_trigger_open_time=management_bar.open_time,
                            transition_trigger_available_at=now,
                        )
                        finish(now, open_, "transition_colour_exit", True)
                        completed = True
                        break
                    if direction * current_side > 0 and pd.isna(result["transition_armed_at"]):
                        result["transition_armed_at"] = now
                        if pd.isna(result["transition_first_armed_at"]):
                            result["transition_first_armed_at"] = now
                    transition_previous_bar = management_bar
                    transition_previous_side = current_side
                else:
                    transition_previous_bar = None
            elif mode not in {"fixed_3r", "transition_colour"} and management_bar is not None and management_bar.open_time >= entry_time:
                if previous_management_close is not None and now != previous_management_close + interval:
                    opposite_streak = 0
                previous_management_close = now
                finite_side = np.isfinite(management_bar.ma_side)
                opposite = finite_side and direction * management_bar.ma_side < 0
                if mode == "slope_colour":
                    opposite = opposite and np.isfinite(management_bar.ma_slope_atr) and direction * management_bar.ma_slope_atr < 0
                opposite_streak = opposite_streak + 1 if opposite else 0
                needed = 2 if mode == "partial_colour" else selected["confirmations"]
                if opposite_streak >= needed:
                    finish(now, open_, "colour_exit" if mode != "slope_colour" else "slope_colour_exit", True)
                    completed = True
                    break
                if mode == "partial_colour" and opposite and remaining == 1.0:
                    realised = 0.5 * direction * (open_ / entry - 1.0)
                    remaining = 0.5
                    result.update(partial_fraction=0.5, partial_exit_time=now, partial_exit_price=open_)

            if now >= deadline:
                finish(now, open_, "time_exit", True)
                completed = True
                break
            # An unfinished source bar cannot disclose high/low or close.
            if cutoff is not None and now + FIVE_MINUTES > cutoff:
                last_time, last_close = now, open_
                break
            if not np.isfinite(prices[i]).all() or min(prices[i]) <= 0 or not low <= min(open_, close) <= max(open_, close) <= high:
                finish(last_time, last_close, "data_gap_censored", False)
                completed = True
                break
            stopped = low <= stop if direction == 1 else high >= stop
            targeted = mode == "fixed_3r" and (high >= target if direction == 1 else low <= target)
            if stopped:
                record_excursion(max(open_, stop), min(open_, stop), bars)
                finish(now + FIVE_MINUTES, stop, "hard_stop", True)
                completed = True
                break
            if targeted:
                fill = target
                record_excursion(max(open_, fill), min(open_, fill), bars)
                finish(now + FIVE_MINUTES, fill, "target_3r", True)
                completed = True
                break
            record_excursion(high, low, bars)
            last_time, last_close = now + FIVE_MINUTES, close
            previous_open = now

        if not completed:
            finish(last_time, last_close, "right_censored", False)
        outputs.append(result)
    return pd.DataFrame(outputs)


def single_position_ledger(trades: pd.DataFrame) -> pd.DataFrame:
    """Select nonoverlapping trades without inventing compounded portfolio P/L.

    Exact exit/entry timestamps may coincide. Rejected entries never occupy a
    position. An unresolved censored position blocks all later entries because
    an actual strategy exit is unknown, even after its final available mark.
    The output contains all candidate rows and ``portfolio_selected`` plus
    ``portfolio_skip_reason`` for transparent accounting.
    """
    required = {"entry_time", "exit_time", "closed", "outcome"}
    if not required.issubset(trades):
        raise ValueError("trades missing ledger columns")
    result = trades.copy()
    result["entry_time"] = pd.to_datetime(result["entry_time"], utc=True)
    result["exit_time"] = pd.to_datetime(result["exit_time"], utc=True)
    sorting = ["entry_time"] + (["event_id"] if "event_id" in result else [])
    result = result.sort_values(sorting, kind="mergesort").reset_index(drop=True)
    result["portfolio_selected"] = False
    result["portfolio_skip_reason"] = ""
    occupied_until = None
    unresolved = False
    for i, row in result.iterrows():
        if str(row["outcome"]).startswith("entry_") or pd.isna(row["entry_time"]):
            result.at[i, "portfolio_skip_reason"] = "entry_rejected"
        elif unresolved or (occupied_until is not None and row["entry_time"] < occupied_until):
            result.at[i, "portfolio_skip_reason"] = "position_open"
        else:
            result.at[i, "portfolio_selected"] = True
            if bool(row["closed"]) and pd.notna(row["exit_time"]):
                occupied_until = row["exit_time"]
            else:
                unresolved = True
    return result
