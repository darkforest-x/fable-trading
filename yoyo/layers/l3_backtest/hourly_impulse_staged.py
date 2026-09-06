"""Fixed-risk partial profit taking and delayed 15m-to-1h management.

Source semantics are the committed ``hourly_impulse`` replay: entries carry
completed-hour features, management uses only ``ma_side`` from bars completed
after entry, and raw 5m OHLC is inspected only as outcome data. No stop moves.
Profit targets are fractions of ORIGINAL position size, specified in initial R.
The 20bp default cost is the same fixed original-notional round-trip convention
as the baseline; partial legs allocate that cost by original position fraction.

At an execution open, priority is gap hard stop, previously resting target
limits, completed current-management colour, an already scheduled takeover,
completed hourly colour if takeover just occurred, then the duration limit.
A threshold seen in this open/bar schedules takeover for the NEXT 5m open.
Consequently takeover cannot cancel a 15m exit confirmed at its activation
timestamp. Within a completed 5m bar, hard stop precedes all new target fills.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Any, Dict, Mapping, Optional

import numpy as np
import pandas as pd

from .hourly_impulse import FIVE_MINUTES, _policy, _utc, _validated_frame, simulate_events


def _staged_policy(policy: Mapping[str, Any]) -> Dict[str, Any]:
    selected = _policy(policy)
    if (selected["exit_mode"] != "colour" or selected["management_minutes"] != 15
            or selected["confirmations"] != 1):
        raise ValueError("Staged replay requires first-opposite 15m colour management")
    targets = []
    for item in selected.get("partial_targets", []):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("partial_targets must contain [positive R, original fraction] pairs")
        level, fraction = map(float, item)
        if not np.isfinite([level, fraction]).all() or level <= 0 or not 0 < fraction <= 1:
            raise ValueError("Target R and fraction must be finite and positive")
        if targets and level <= targets[-1][0]:
            raise ValueError("Target R levels must be strictly increasing")
        targets.append((level, fraction))
    if sum(fraction for _, fraction in targets) > 1.0 + 1e-12:
        raise ValueError("Original-position target fractions cannot sum above one")
    takeover = selected.get("takeover_r")
    if takeover is not None:
        takeover = float(takeover)
        if not np.isfinite(takeover) or takeover <= 0:
            raise ValueError("takeover_r must be finite and positive")
    selected.update(partial_targets=targets, takeover_r=takeover)
    return selected


def simulate_staged_events(
    raw5: pd.DataFrame,
    management15: pd.DataFrame,
    management60: pd.DataFrame,
    entries: pd.DataFrame,
    policy: Mapping[str, Any],
    *,
    end_exclusive: Optional[Any] = None,
) -> pd.DataFrame:
    """Replay resting partial targets and optional delayed hourly takeover.

    Management requires ``open_time >= entry_time`` and is available only at
    ``open_time + 15/60 minutes``. Pre-takeover bars may inform hourly management
    once completed, but bars beginning before entry never do. At takeover the
    latest already-completed post-entry hourly bar is checked even between hour
    boundaries; subsequent checks occur when each new hourly bar completes.
    Source gaps and
    truncation retain partial realised amounts separately; their whole-trade
    returns remain NaN. No-target/no-takeover policies delegate to the baseline
    and return its exact schema and values. Otherwise added fields describe
    partial fills and takeover clocks without altering baseline field meanings.
    """
    selected = _staged_policy(policy)
    if not selected["partial_targets"] and selected["takeover_r"] is None:
        return simulate_events(raw5, management15, entries, selected, end_exclusive=end_exclusive)

    raw = _validated_frame(raw5, ("open_time", "open", "high", "low", "close", "segment_id"), "raw5")
    columns = ("open_time", "ma", "ma_side", "ma_slope_atr", "low", "high", "close", "segment_id")
    mg15 = _validated_frame(management15, columns, "management15")
    mg60 = _validated_frame(management60, columns, "management60")
    required = {"event_id", "decision_time", "direction", "initial_stop", "signal_atr"}
    if not required.issubset(entries.columns):
        raise ValueError("entries missing columns: {}".format(sorted(required - set(entries.columns))))
    if entries["event_id"].duplicated().any():
        raise ValueError("event_id must be unique for independent paired outcomes")
    if entries.empty:
        return pd.DataFrame(columns=list(entries.columns) + ["entry_time", "exit_time", "closed", "outcome", "net_return", "net_r"])

    times = raw["open_time"].to_numpy()
    time_index = {pd.Timestamp(time): i for i, time in enumerate(times)}
    prices = raw[["open", "high", "low", "close"]].to_numpy(dtype=float)
    segments = raw["segment_id"].to_numpy()
    management_at = {
        minutes: {row.open_time + pd.Timedelta(minutes=minutes): row for row in frame.itertuples(index=False)}
        for minutes, frame in ((15, mg15), (60, mg60))
    }
    hourly_availability = list(management_at[60])
    hourly_bars = list(management_at[60].values())
    cutoff = _utc(end_exclusive) if end_exclusive is not None else None
    horizon = pd.Timedelta(hours=selected["max_hours"])
    cost = float(selected["cost_fraction"])
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
            realised_partial_gross_return=0.0, realised_partial_net_return=0.0,
            marked_gross_return=np.nan, marked_net_return=np.nan,
            max_favourable_r=0.0, max_adverse_r=0.0,
            bars_to_first_positive=np.nan, funding_modelled=False,
            partial_fills=[], partial_fill_count=0,
            takeover_trigger_time=pd.NaT, takeover_time=pd.NaT,
            takeover_active=False, exit_management_minutes=15,
        )
        index = time_index.get(entry_time)
        if index is None or (cutoff is not None and entry_time >= cutoff):
            outputs.append(result)
            continue
        direction, stop, atr = map(float, (event["direction"], event["initial_stop"], event["signal_atr"]))
        entry = float(prices[index, 0])
        result["entry_price"] = entry
        if (not np.isfinite([entry, direction, stop, atr]).all() or entry <= 0
                or direction not in (-1, 1) or stop <= 0 or atr <= 0):
            result["outcome"] = "entry_invalid"
            outputs.append(result)
            continue
        risk = direction * (entry - stop)
        if risk <= 0:
            result["outcome"] = "entry_invalid_risk"
            outputs.append(result)
            continue
        result.update(risk_pct=risk / entry, risk_atr=risk / atr)
        targets = [(r, fraction, entry + direction * r * risk) for r, fraction in selected["partial_targets"]]
        # A short-side target at/below zero cannot trade on positive-price OHLC.
        takeover_price = None if selected["takeover_r"] is None else entry + direction * selected["takeover_r"] * risk
        filled = set()
        remaining, realised = 1.0, 0.0
        last_close, last_time = entry, entry_time
        previous_open = None
        segment = segments[index]
        completed, active = False, False
        activation = None
        deadline = entry_time + horizon

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
                realised_partial_net_return=realised - cost * (1.0 - remaining),
                marked_gross_return=gross, marked_net_return=gross - cost,
                takeover_active=active, exit_management_minutes=60 if active else 15,
            )
            if closed:
                result.update(gross_return=gross, net_return=gross - cost, net_r=(gross - cost) / (risk / entry))

        def partial_fill(target_index: int, time: pd.Timestamp, price: float) -> None:
            nonlocal remaining, realised
            level, requested, _ = targets[target_index]
            fraction = min(requested, remaining)
            gross = fraction * direction * (price / entry - 1.0)
            realised += gross
            remaining = max(0.0, remaining - fraction)
            if remaining < 1e-12:
                remaining = 0.0
            filled.add(target_index)
            result["partial_fills"].append({
                "time": time, "price": price, "fraction": fraction,
                "target_r": level, "gross_return": gross,
                "net_return": gross - cost * fraction,
            })
            result["partial_fraction"] = 1.0 - remaining
            result["partial_fill_count"] += 1
            if pd.isna(result["partial_exit_time"]):
                result["partial_exit_time"] = time
            result["partial_exit_price"] = sum(fill["fraction"] * fill["price"] for fill in result["partial_fills"]) / (1.0 - remaining)

        def opposite_at(now: pd.Timestamp, minutes: int) -> bool:
            bar = management_at[minutes].get(now)
            return bool(bar is not None and bar.open_time >= entry_time
                        and np.isfinite(bar.ma_side) and direction * bar.ma_side < 0)

        def latest_hourly_opposite(now: pd.Timestamp) -> bool:
            # Backward availability lookup cannot disclose the current hour.
            hour_index = bisect_right(hourly_availability, now) - 1
            if hour_index < 0:
                return False
            bar = hourly_bars[hour_index]
            return bool(bar.open_time >= entry_time and np.isfinite(bar.ma_side)
                        and direction * bar.ma_side < 0)

        for i in range(index, len(raw)):
            now = pd.Timestamp(times[i])
            if cutoff is not None and now >= cutoff:
                break
            if ((previous_open is not None and now != previous_open + FIVE_MINUTES)
                    or pd.isna(segments[i]) or segments[i] != segment):
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
            if direction * (open_ - stop) <= 0:
                finish(now, open_, "hard_stop_gap", True)
                completed = True
                break
            # Resting limits were placed with the entry and beat market exits.
            for target_index, (_, _, target) in enumerate(targets):
                if target_index not in filled and direction * (open_ - target) >= 0:
                    partial_fill(target_index, now, open_)
            if remaining == 0:
                finish(now, open_, "partial_targets_complete", True)
                completed = True
                break
            if opposite_at(now, 60 if active else 15):
                finish(now, open_, "colour_exit", True)
                completed = True
                break
            if not active and activation is not None and now >= activation:
                active = True
                result["takeover_time"] = now
                if latest_hourly_opposite(now):
                    finish(now, open_, "colour_exit", True)
                    completed = True
                    break
            if now >= deadline:
                finish(now, open_, "time_exit", True)
                completed = True
                break
            if (not active and activation is None and takeover_price is not None
                    and direction * (open_ - takeover_price) >= 0):
                activation = now + FIVE_MINUTES
                result["takeover_trigger_time"] = now
            if cutoff is not None and now + FIVE_MINUTES > cutoff:
                last_time, last_close = now, open_
                break
            if (not np.isfinite(prices[i]).all() or min(prices[i]) <= 0
                    or not low <= min(open_, close) <= max(open_, close) <= high):
                # This open is observable even if subsequent extrema are not;
                # previously resting partial limits may already have filled.
                finish(now, open_, "data_gap_censored", False)
                completed = True
                break
            stopped = low <= stop if direction == 1 else high >= stop
            if stopped:
                record_excursion(max(open_, stop), min(open_, stop), bars)
                finish(now + FIVE_MINUTES, stop, "hard_stop", True)
                completed = True
                break
            for target_index, (_, _, target) in enumerate(targets):
                touched = high >= target if direction == 1 else low <= target
                if target_index not in filled and touched:
                    partial_fill(target_index, now + FIVE_MINUTES, target)
                    if remaining == 0:
                        record_excursion(max(open_, target), min(open_, target), bars)
                        finish(now + FIVE_MINUTES, target, "partial_targets_complete", True)
                        completed = True
                        break
            if completed:
                break
            record_excursion(high, low, bars)
            favourable_extreme = high if direction == 1 else low
            if (not active and activation is None and takeover_price is not None
                    and direction * (favourable_extreme - takeover_price) >= 0):
                activation = now + FIVE_MINUTES
                result["takeover_trigger_time"] = now + FIVE_MINUTES
            last_time, last_close = now + FIVE_MINUTES, close
            previous_open = now
        if not completed:
            finish(last_time, last_close, "right_censored", False)
        outputs.append(result)
    return pd.DataFrame(outputs)
