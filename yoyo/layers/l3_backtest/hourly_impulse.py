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

The default native-5m/15m transition exit uses an adjacent completed-bar colour
edge, not an opposite-colour state. Optional quarter-hour decision cadence
instead compares samples of unchanged native5 colour, validating raw5 and
native5 management continuity between samples. Its clock follows pandas 2.3.3 Timestamp /
Timedelta arithmetic and TradingView's confirmed-bar availability semantics:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Timestamp.floor.html
https://www.tradingview.com/pine-script-docs/language/execution-model/

The optional V11 launch deadline is a separately preregistered hypothesis, not
an optimized default. Only valid, fully held post-entry raw5 CLOSE observations
available in (entry, entry+60min] can confirm +0.5 of the frozen initial risk.
Neither entry/seed prices, intrabar extrema nor management colour supply this
progress. Source: the V10 NEXT_EXPERIMENT.md launch-progress specification.

The independent V12 frozen-MA exit reads the completed signal hour's supplied
``ma`` once, available at ``signal_time + 1h == decision_time``. After entry,
only valid, fully held raw5 CLOSE values can latch a strict wrong-side exit for
the next real raw5 open. The entry boundary never follows management-bar MA.
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
    if selected["exit_mode"] == "transition_colour" and (minutes not in (5, 15) or confirmations != 1):
        raise ValueError("transition_colour requires management_minutes=5 or 15 and confirmations=1")
    if "decision_minutes" in selected:
        decision_minutes = selected["decision_minutes"]
        if (isinstance(decision_minutes, (bool, np.bool_))
                or not isinstance(decision_minutes, (int, np.integer))
                or decision_minutes not in (5, 15)
                or selected["exit_mode"] != "transition_colour"
                or minutes != 5 or confirmations != 1):
            raise ValueError("decision_minutes requires integer 5 or 15 with transition_colour/native5/confirmations1")
    launch_keys = {"launch_deadline_minutes", "launch_progress_r"}
    if launch_keys.intersection(selected):
        if not launch_keys.issubset(selected):
            raise ValueError("launch_deadline_minutes and launch_progress_r must be supplied together")
        launch_minutes = selected["launch_deadline_minutes"]
        launch_progress = selected["launch_progress_r"]
        if (isinstance(launch_minutes, (bool, np.bool_))
                or not isinstance(launch_minutes, (int, np.integer)) or launch_minutes != 60
                or isinstance(launch_progress, (bool, np.bool_))
                or not isinstance(launch_progress, (int, float, np.integer, np.floating))
                or not np.isfinite(launch_progress) or launch_progress != 0.5
                or selected["exit_mode"] != "transition_colour" or minutes != 5
                or isinstance(confirmations, (bool, np.bool_))
                or confirmations != 1 or selected.get("decision_minutes", 5) != 5):
            raise ValueError("launch deadline requires integer 60 minutes / 0.5R with transition_colour/native5/confirmations1/decision5")
    if "frozen_ma_exit" in selected:
        enabled = selected["frozen_ma_exit"]
        if (not isinstance(enabled, (bool, np.bool_)) or not enabled
                or selected["exit_mode"] != "transition_colour" or minutes != 5
                or isinstance(confirmations, (bool, np.bool_)) or confirmations != 1
                or selected.get("decision_minutes", 5) != 5 or launch_keys.intersection(selected)):
            raise ValueError("frozen_ma_exit requires boolean True with transition_colour/native5/confirmations1/decision5 and no launch policy")
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


def _launch_diagnostics(entry_time: Optional[pd.Timestamp] = None) -> Dict[str, Any]:
    """Add fields only for the opt-in launch policy, including empty requests."""
    return {
        "launch_enabled": True, "launch_deadline_minutes": 60, "launch_progress_r": 0.5,
        "launch_deadline_at": pd.NaT if entry_time is None else entry_time + pd.Timedelta(minutes=60),
        "launch_progress_reached": False, "launch_progress_first_at": pd.NaT,
        "launch_completed_close_count": 0, "launch_max_completed_close_r": np.nan,
        "launch_deadline_checked_at": pd.NaT, "launch_status": "entry_not_validated",
    }


def _validate_frozen_ma_entries(entries: pd.DataFrame) -> None:
    """Fail the whole request set if its supposedly known hourly MA is invalid.

    Reads only entry ``ma``, ``signal_time`` and ``decision_time``. No prices,
    future features or outcomes enter this provenance/clock check. Numeric
    epochs are rejected rather than guessing timestamp units. Empty requests
    still need the two opt-in columns, but do not create any observations.
    """
    missing = {"ma", "signal_time"} - set(entries.columns)
    if missing:
        raise ValueError("frozen_ma_exit entries missing columns: {}".format(sorted(missing)))
    numeric_types = (int, float, np.integer, np.floating)
    for row in entries[["ma", "signal_time", "decision_time"]].itertuples(index=False):
        if isinstance(row.ma, (bool, np.bool_)) or not isinstance(row.ma, numeric_types):
            raise ValueError("frozen_ma_exit ma must be a finite positive nonboolean number")
        try:
            boundary = float(row.ma)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError("frozen_ma_exit ma must be finite and positive") from error
        if not np.isfinite(boundary) or boundary <= 0:
            raise ValueError("frozen_ma_exit ma must be finite and positive")
        if any(isinstance(value, numeric_types+(bool, np.bool_)) for value in (row.signal_time, row.decision_time)):
            raise ValueError("frozen_ma_exit requires explicit hourly timestamps, not numeric epochs")
        try:
            signal, decision = _utc(row.signal_time), _utc(row.decision_time)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError("frozen_ma_exit requires valid completed-hour signal timestamps") from error
        if signal != signal.floor("h") or decision != decision.floor("h") or signal+pd.Timedelta(hours=1) != decision:
            raise ValueError("frozen_ma_exit requires aligned signal_time + 1h == decision_time")


def _frozen_ma_diagnostics(boundary: float = np.nan, available_at: Any = pd.NaT) -> Dict[str, Any]:
    return {
        "frozen_ma_enabled": True, "frozen_ma_boundary": boundary,
        "frozen_ma_available_at": available_at, "frozen_ma_entry_distance_atr": np.nan,
        "frozen_ma_trigger_open_time": pd.NaT, "frozen_ma_trigger_available_at": pd.NaT,
        "frozen_ma_trigger_close": np.nan, "frozen_ma_completed_close_count": 0,
        "frozen_ma_status": "entry_not_validated",
    }


def _transition_observation(
    bar: Any,
    available_at: pd.Timestamp,
    time_index: Mapping[pd.Timestamp, int],
    prices: np.ndarray,
    segments: np.ndarray,
    interval: pd.Timedelta = FIVE_MINUTES,
    *,
    source_through: Optional[pd.Timestamp] = None,
) -> tuple:
    """Validate one native colour using only completed source bars and an open.

    The exact management bar ending at ``available_at`` supplies colour. All
    raw5 OHLC from its open through the last completed raw5 bar are validated;
    at ``source_through`` (default available_at) only the new raw5 open is read.
    A 15m seed at a +5/+10 entry phase additionally validates the intervening
    completed raw5 bars, never the unfinished entry bar's extrema. Management
    segment numbers have their own counting space: source continuity instead
    maps both timestamps to raw5 segment numbers. The slope is not used by this
    exit and a missing slope does not invalidate an otherwise known colour.
    """
    if bar is None:
        return None, "missing_management"
    if bar.open_time + interval != available_at:
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
    through = available_at if source_through is None else source_through
    source_span = through - bar.open_time
    if through < available_at or source_span.value % FIVE_MINUTES.value != 0:
        return None, "missing_source"
    source_indices = [
        time_index.get(bar.open_time + offset * FIVE_MINUTES)
        for offset in range(int(source_span / FIVE_MINUTES) + 1)
    ]
    if any(index is None for index in source_indices):
        return None, "missing_source"
    source_segments = [segments[index] for index in source_indices]
    unknown_source_segment = any(
        pd.isna(segment) or (isinstance(segment, (float, np.floating)) and not np.isfinite(segment))
        for segment in source_segments
    )
    if unknown_source_segment or any(segment != source_segments[0] for segment in source_segments[1:]):
        return None, "source_segment_change"
    for source_index in source_indices[:-1]:
        source_open, source_high, source_low, source_close = prices[source_index]
        if not np.isfinite(prices[source_index]).all() or min(prices[source_index]) <= 0 or not source_low <= min(source_open, source_close) <= max(source_open, source_close) <= source_high:
            return None, "invalid_completed_source"
    next_index = source_indices[-1]
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
    maximum-duration exit, then current-bar intrabar barriers. For state exits,
    management bars beginning before entry cannot trigger an exit. In ``partial_colour`` half
    the original position exits once on first opposite colour; its remainder
    exits after any two consecutive opposite management bars.

    ``transition_colour`` supports native 5m or 15m / one confirmation. The
    latest bar ending at or before entry initializes colour but cannot exit.
    Only a valid aligned-to-opposite edge across adjacent complete bars exits.
    Native 15m is seeded at entry.floor("15min"); its next completed bar can
    straddle a +5/+10-phase entry, with earliest exits +15/+10/+5 minutes at
    entry phases 0/+5/+10. It updates ONLY on expected 15m closes: intervening
    raw5 bars preserve state but still check hard stops. Missing/invalid expected
    observations reset the edge; a management-segment change starts a fresh
    sequence. Initially opposite/unknown must first observe an aligned complete
    bar. Native 5m retains its exact-entry seed and earliest +5m exit.

    Optional ``decision_minutes=15`` is a different specification: retain
    native 5m features, but sample their latest completed colour only at UTC
    :00/:15/:30/:45. The exact-entry native5 seed may form the first edge with
    the next quarter-hour sample; later samples must be adjacent quarters.
    Valid unsampled colours neither arm, update nor latch the sampled state.
    Missing/invalid native5 observations or native segment changes still reset
    it on EVERY 5m step. A known sample after a reset can seed a new sequence.
    All raw5 risk/quality checks are unchanged. Explicit decision_minutes=5 is
    identical to omission; this option is not supported by any other mode.

    An optional positive integer ``max_minutes`` (multiple of five) takes
    precedence over ``max_hours``. It represents the exact remaining duration
    for delayed entries without converting integer minutes to fractional hours.

    Paired opt-in keys ``launch_deadline_minutes=60, launch_progress_r=0.5``
    are supported only by native5 transition / decision5 / one confirmation.
    A valid, fully held raw5 CLOSE reaching +0.5 frozen initial R, available
    at entry+5 through entry+60 minutes inclusive, permanently cancels this
    deadline. Management missing/invalid colour resets only the colour edge,
    not this independent price-progress state. With no progress, entry+60
    exits at its real raw5 open after existing gap stops, colour exits and
    maximum-duration exits. A just-completed boundary close can cancel first.
    Missing source/clock/open still censors; an unfinished exit bar's HLC is
    not required. Diagnostics count only complete, validated, unstopped bars
    while actually held and at most the first twelve post-entry raw5 closes.
    ``launch_status`` is entry_not_validated, pending (internal only),
    progress_confirmed (permanent), prior_exit, timeout_exit, or unknown_source.
    ``launch_deadline_checked_at`` is set only if the live path actually
    reaches the deadline check after all higher-priority original exits.
    In this opt-in branch a floating raw ``segment_id`` that is not finite
    also censors as unknown source before any price exit; finite opaque source
    IDs retain existing equality/continuity semantics. Default modes are not
    affected by this additional source validation.
    Omission of both keys preserves historical output columns and values.

    Independent ``frozen_ma_exit=True`` requires a real boolean true value;
    omit the key to disable it (False and numeric truthy values are rejected).
    It supports only native5 transition/decision5/one confirmation and cannot
    combine with launch-deadline keys. Every supplied ``ma`` must be numeric,
    finite, positive and nonboolean; ``signal_time`` and ``decision_time`` must
    be valid aligned hours exactly one hour apart, or the whole call raises.
    The frozen boundary is entry ``ma``, available at the signal hour's close,
    not a later management MA. For an executable entry its signed distance is
    direction*(entry_price-boundary)/signal_atr. Any fully held, valid raw5
    CLOSE strictly on the wrong side latches an exit, even if entry already
    starts wrong-side. Equality, seed/pre-entry closes and wicks never trigger.
    At the next actual raw5 open, gap stop, original trueflip and total horizon
    precede ``frozen_ma_exit``; a rebound cannot cancel the latch. A source gap,
    invalid open or nonfinite floating raw segment censors without a fill.
    Invalid management colour only resets the old edge, not this price latch.
    Trigger fields may remain populated when an original exit wins that same
    timestamp. Counted closes exclude intrabar-stop bars and unfinished exit
    bars. Returned frozen_ma_status is entry_not_validated, prior_exit,
    structure_exit or unknown_source; watching/exit_pending are internal only.
    No extra fields or entry validation affect old modes when this key is absent.
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
    frozen_ma_enabled = "frozen_ma_exit" in selected
    if frozen_ma_enabled:
        _validate_frozen_ma_entries(entries)
    if entries.empty:
        empty_columns = list(entries.columns) + ["entry_time", "exit_time", "closed", "outcome", "net_return", "net_r"]
        if "launch_deadline_minutes" in selected:
            empty_columns += [name for name in _launch_diagnostics() if name not in empty_columns]
        if frozen_ma_enabled:
            empty_columns += [name for name in _frozen_ma_diagnostics() if name not in empty_columns]
        return pd.DataFrame(columns=empty_columns)

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
    sampled_cadence = selected.get("decision_minutes") == 15
    launch_enabled = "launch_deadline_minutes" in selected
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
        if sampled_cadence:
            result.update(transition_decision_minutes=15, transition_sample_count=0,
                          transition_trigger_previous_available_at=pd.NaT)
        if launch_enabled:
            result.update(_launch_diagnostics(entry_time))
        if frozen_ma_enabled:
            result.update(_frozen_ma_diagnostics(float(event["ma"]), entry_time))
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
        if launch_enabled:
            result["launch_status"] = "pending"
        if frozen_ma_enabled:
            result.update(frozen_ma_entry_distance_atr=direction*(entry-result["frozen_ma_boundary"])/atr,
                          frozen_ma_status="watching")
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
        transition_observed_bar = None
        transition_previous_sample_at = None
        transition_seed_pending = False
        if mode == "transition_colour":
            initial_available_at = entry_time if interval == FIVE_MINUTES else entry_time.floor("15min")
            initial_management = management_at.get(initial_available_at)
            transition_previous_side, initial_reason = _transition_observation(
                initial_management, initial_available_at, time_index, prices, segments,
                interval, source_through=entry_time,
            )
            result["transition_initial_reason"] = initial_reason
            if transition_previous_side is not None:
                transition_previous_bar = initial_management
                if sampled_cadence:
                    transition_observed_bar = initial_management
                    transition_previous_sample_at = entry_time
                    transition_seed_pending = True
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
            if launch_enabled and not result["launch_progress_reached"]:
                result["launch_status"] = ("timeout_exit" if outcome == "launch_timeout_exit"
                                           else "prior_exit" if closed else "unknown_source")
            if frozen_ma_enabled:
                result["frozen_ma_status"] = ("structure_exit" if outcome == "frozen_ma_exit"
                                              else "prior_exit" if closed else "unknown_source")

        for i in range(index, len(raw)):
            now = pd.Timestamp(times[i])
            if cutoff is not None and now >= cutoff:
                break
            invalid_optional_segment = ((launch_enabled or frozen_ma_enabled)
                                        and isinstance(segments[i], (float, np.floating))
                                        and not np.isfinite(segments[i]))
            if (previous_open is not None and now != previous_open + FIVE_MINUTES) or pd.isna(segments[i]) or segments[i] != first_segment or invalid_optional_segment:
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
            if sampled_cadence and now > entry_time:
                # Observation continuity and decision sampling are separate
                # clocks. Never turn an unsampled colour into a latched exit.
                current_side, reset_reason = _transition_observation(
                    management_bar, now, time_index, prices, segments,
                )
                observed_consecutive = (
                    transition_observed_bar is not None and management_bar is not None
                    and management_bar.open_time == transition_observed_bar.open_time + FIVE_MINUTES
                    and management_bar.segment_id == transition_observed_bar.segment_id
                )
                if current_side is None or (transition_observed_bar is not None and not observed_consecutive):
                    if current_side is not None:
                        reset_reason = "management_sequence_change"
                    transition_previous_side, transition_previous_bar = None, None
                    transition_previous_sample_at, transition_seed_pending = None, False
                    result["transition_armed_at"] = pd.NaT
                    result["transition_reset_count"] += 1
                    result["transition_last_reset_reason"] = reset_reason
                transition_observed_bar = management_bar if current_side is not None else None
                if now == now.floor("15min"):
                    result["transition_sample_count"] += 1
                    if current_side is not None:
                        sampled_consecutive = transition_previous_sample_at is not None and (
                            (transition_seed_pending and now == entry_time.floor("15min") + pd.Timedelta(minutes=15))
                            or (not transition_seed_pending and now == transition_previous_sample_at + pd.Timedelta(minutes=15))
                        )
                        if transition_previous_sample_at is not None and not sampled_consecutive:
                            transition_previous_side, transition_previous_bar = None, None
                            result["transition_armed_at"] = pd.NaT
                            result["transition_reset_count"] += 1
                            result["transition_last_reset_reason"] = "sampled_sequence_change"
                        if transition_previous_side is not None and direction * transition_previous_side > 0 and direction * current_side < 0:
                            result.update(
                                transition_trigger_previous_open_time=transition_previous_bar.open_time,
                                transition_trigger_previous_available_at=transition_previous_sample_at,
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
                        transition_previous_bar, transition_previous_side = management_bar, current_side
                        transition_previous_sample_at, transition_seed_pending = now, False
            elif mode == "transition_colour" and now > entry_time and (interval == FIVE_MINUTES or now == now.floor("15min")):
                current_side, reset_reason = _transition_observation(
                    management_bar, now, time_index, prices, segments,
                    interval,
                )
                consecutive = (
                    transition_previous_bar is not None and management_bar is not None
                    and management_bar.open_time == transition_previous_bar.open_time + interval
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
            if frozen_ma_enabled and now == result["frozen_ma_trigger_available_at"]:
                finish(now, open_, "frozen_ma_exit", True)
                completed = True
                break
            if launch_enabled and now == result["launch_deadline_at"]:
                result["launch_deadline_checked_at"] = now
                if not result["launch_progress_reached"]:
                    finish(now, open_, "launch_timeout_exit", True)
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
            if frozen_ma_enabled:
                result["frozen_ma_completed_close_count"] += 1
                if pd.isna(result["frozen_ma_trigger_available_at"]) and direction*(close-result["frozen_ma_boundary"]) < 0:
                    result.update(frozen_ma_trigger_open_time=now,
                                  frozen_ma_trigger_available_at=now+FIVE_MINUTES,
                                  frozen_ma_trigger_close=close, frozen_ma_status="exit_pending")
            if launch_enabled and now + FIVE_MINUTES <= result["launch_deadline_at"]:
                # This raw5 bar is complete, validated and remained held: a
                # resting stop above has already won any intrabar collision.
                # Colour validity has no authority over observed price progress.
                progress = direction * (close - entry)
                progress_r = progress / risk
                result["launch_completed_close_count"] += 1
                prior_max = result["launch_max_completed_close_r"]
                result["launch_max_completed_close_r"] = progress_r if pd.isna(prior_max) else max(prior_max, progress_r)
                if not result["launch_progress_reached"] and progress >= selected["launch_progress_r"] * risk:
                    result.update(launch_progress_reached=True,
                                  launch_progress_first_at=now + FIVE_MINUTES,
                                  launch_status="progress_confirmed")
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
