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

V16's opt-in fast partial adds one50% realization to native15 trueflip exits.
An adjacent completed native5 aligned-to-opposite edge can execute only while
the latest fully completed native15 colour is aligned and the current raw5
open's directional gross gain strictly exceeds20bp. Decimal price strings make
exact fee-boundary equality fail without a floating-point tolerance/grid:
https://docs.python.org/3.9/library/decimal.html

V17 optionally closes the entire still-unrealised position on that same known
fast edge/slow-aligned event when the current open fails the frozen20bp test.
Equality belongs to this failed-launch branch. Neither its decision nor its
fill inspects current raw5 high, low or close; no future failure label is used.

V18 optionally waits for exactly the next completed native5 opposite bar after
that failed-launch edge. The same management segment, latest completed native15
alignment and new executable OPEN must reconfirm failure. Any rejection consumes
the pending edge; it cannot create a partial fill without a fresh true edge.
Only this opt-in adds pending lifecycle diagnostics. Default/explicit-one
confirmation retains every V17 field and execution rule unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional
from decimal import Decimal, localcontext
import json

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
    if "fast_partial_fraction" in selected:
        fraction = selected["fast_partial_fraction"]
        if (isinstance(fraction, (bool, np.bool_))
                or not isinstance(fraction, (int, float, np.integer, np.floating))
                or not np.isfinite(fraction) or fraction != 0.5
                or selected["exit_mode"] != "transition_colour" or minutes != 15
                or isinstance(confirmations, (bool, np.bool_)) or confirmations != 1
                or "decision_minutes" in selected or "frozen_ma_exit" in selected
                or launch_keys.intersection(selected)):
            raise ValueError("fast_partial_fraction requires0.5 with transition_colour/native15/confirmations1 and no other optional exit policy")
    if "fast_failed_launch_exit" in selected:
        if (not isinstance(selected["fast_failed_launch_exit"], (bool, np.bool_))
                or "fast_partial_fraction" not in selected):
            raise ValueError("fast_failed_launch_exit requires a boolean with the native15 fast_partial_fraction policy")
    if "fast_failed_launch_confirmations" in selected:
        failed_confirmations = selected["fast_failed_launch_confirmations"]
        if (isinstance(failed_confirmations, (bool, np.bool_))
                or not isinstance(failed_confirmations, (int, np.integer))
                or failed_confirmations not in (1, 2)
                or (failed_confirmations == 2 and not selected.get("fast_failed_launch_exit", False))):
            raise ValueError("fast_failed_launch_confirmations must be integer1 or2;2 requires fast_failed_launch_exit=True")
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


def _fast_partial_diagnostics() -> Dict[str, Any]:
    """Only the opt-in dual-clock policy adds these source/outcome fields."""
    return {
        "partial_fast_enabled": True, "partial_fast_fraction": 0.5,
        "partial_fast_profit_threshold": 0.002,
        "partial_fast_initial_state": "unknown", "partial_fast_initial_side": np.nan,
        "partial_fast_initial_reason": "entry_not_validated",
        "partial_fast_initial_open_time": pd.NaT, "partial_fast_initial_available_at": pd.NaT,
        "partial_fast_initial_management_segment_id": "", "partial_fast_initial_raw_segment_id": "",
        "partial_fast_initial_ma": np.nan, "partial_fast_initial_hl2": np.nan,
        "partial_fast_first_armed_at": pd.NaT, "partial_fast_reset_count": 0,
        "partial_fast_last_reset_reason": "", "partial_fast_flip_count": 0,
        "partial_fast_fill_count": 0, "partial_fast_realised_net_return": 0.0,
        "partial_fast_events": "[]", "partial_fast_status": "entry_not_validated",
        "partial_fast_trigger_previous_open_time": pd.NaT,
        "partial_fast_trigger_open_time": pd.NaT, "partial_fast_trigger_available_at": pd.NaT,
        "partial_fast_trigger_previous_side": np.nan, "partial_fast_trigger_side": np.nan,
        "partial_fast_trigger_gross_return": np.nan,
        "partial_fast_slow_open_time": pd.NaT, "partial_fast_slow_available_at": pd.NaT,
        "partial_fast_slow_side": np.nan, "partial_fast_slow_state": "unknown",
    }


def _fast_partial_profit(open_: float, entry: float, direction: float) -> bool:
    """Strict20bp in decimal quote units; never use future highs or tolerance."""
    with localcontext() as context:
        context.prec = 40
        return Decimal(str(direction)) * (Decimal(str(open_))-Decimal(str(entry))) > Decimal("0.002")*Decimal(str(entry))


def _failed_launch_diagnostics() -> Dict[str, Any]:
    """Only True adds fields; False preserves the complete V16 output schema.

    Source MA/HL2/segments remain in the shared partial_fast_events JSON. These
    scalar clocks identify the same failed_launch_exit record, not a new edge.
    """
    return {
        "failed_launch_enabled": True, "failed_launch_count": 0,
        "failed_launch_profit_threshold": 0.002, "failed_launch_status": "entry_not_validated",
        "failed_launch_trigger_previous_open_time": pd.NaT,
        "failed_launch_trigger_previous_available_at": pd.NaT,
        "failed_launch_trigger_open_time": pd.NaT, "failed_launch_trigger_available_at": pd.NaT,
        "failed_launch_trigger_previous_side": np.nan, "failed_launch_trigger_side": np.nan,
        "failed_launch_trigger_open_price": np.nan, "failed_launch_trigger_gross_return": np.nan,
        "failed_launch_slow_open_time": pd.NaT, "failed_launch_slow_available_at": pd.NaT,
        "failed_launch_slow_side": np.nan, "failed_launch_slow_state": "unknown",
    }


def _failed_launch_gross(price: float, entry: float, direction: float) -> float:
    """New full-fill accounting only: exact20bp equals the float20bp cost.

    Decimal quote-unit arithmetic avoids classifying a theoretical break-even
    full exit as a positive winner through float price/entry cancellation. No
    historical V16 partial, full, marked or cost calculations are changed.
    """
    with localcontext() as context:
        context.prec = 40
        return float(Decimal(str(direction))*(Decimal(str(price))-Decimal(str(entry)))/Decimal(str(entry)))


def _failed_confirm_diagnostics() -> Dict[str, Any]:
    """V18-only lifecycle; trigger scalars in failed_launch retain the real edge.

    Confirmation scalars below describe the second opposite observation/fill,
    not a second aligned-to-opposite edge. JSON lifecycle events retain every
    created edge, including cancellations and higher-priority terminations.
    """
    return {
        "failed_confirm_enabled": True, "failed_confirm_required": 2,
        "failed_confirm_create_count": 0, "failed_confirm_confirm_count": 0,
        "failed_confirm_cancel_count": 0, "failed_confirm_priority_termination_count": 0,
        "failed_confirm_status": "entry_not_validated", "failed_confirm_last_reason": "",
        "failed_confirm_events": "[]", "failed_confirm_created_at": pd.NaT,
        "failed_confirm_due_at": pd.NaT, "failed_confirm_previous_open_time": pd.NaT,
        "failed_confirm_open_time": pd.NaT, "failed_confirm_available_at": pd.NaT,
        "failed_confirm_open_price": np.nan, "failed_confirm_gross_return": np.nan,
        "failed_confirm_slow_open_time": pd.NaT, "failed_confirm_slow_available_at": pd.NaT,
        "failed_confirm_slow_side": np.nan, "failed_confirm_slow_state": "unknown",
    }


def _native40_frame(frame: pd.DataFrame, minutes: int, name: str) -> None:
    """Provenance gate only for the new branch; never infer native metadata."""
    if (frame.attrs.get("ma_kind") != "SMA" or frame.attrs.get("ma_length") != 40
            or frame.attrs.get("bar_minutes") != minutes):
        raise ValueError(name+" must carry native{}m SMA40 feature metadata".format(minutes))


def _partial_source(bar: Any, side: Optional[float], time_index: Mapping, segments: np.ndarray) -> dict:
    """JSON-safe observed source diagnostics; invalid values stay null, not0."""
    if bar is None:
        return {"open_time": None, "side": None, "ma": None, "hl2": None,
                "management_segment_id": None, "raw_segment_id": None}
    def finite(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if np.isfinite(value) else None
    high, low = finite(bar.high), finite(bar.low)
    raw_index = time_index.get(bar.open_time)
    return {"open_time": bar.open_time.isoformat(), "side": side, "ma": finite(bar.ma),
            "hl2": high/2+low/2 if high is not None and low is not None else None,
            "management_segment_id": None if pd.isna(bar.segment_id) else str(bar.segment_id),
            "raw_segment_id": None if raw_index is None or pd.isna(segments[raw_index]) else str(segments[raw_index])}


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
    fast_management_featured: Optional[pd.DataFrame] = None,
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

    ``fast_partial_fraction=0.5`` instead adds one partial to native15 trueflip
    only, using a separate native5 ``fast_management_featured`` frame. Both
    frames require explicit native SMA40 attrs; missing or unrelated fast data
    is rejected, never inferred. The latest completed native5 seed initializes
    its own adjacent aligned-to-opposite edges, with the same observation and
    reset rules. A fresh edge is consumed even if not profitable or slow-side
    aligned; it is not latched for a later price. On that exact next raw5 open,
    only a valid latest native15 observation (floor15, no older fallback),
    still aligned, plus strictly >20bp directional gross gain can realize50%
    of ORIGINAL notional once. Remaining50% keeps original slow exits, fixed
    K1 stop and horizon. No stop is moved. Source/open gap, gap stop, original
    slow full exit and total deadline precede partial evaluation; intrabar
    hard stops then act on remaining notional. Partial never reads current HLC.
    Every evaluated fast edge is recorded as JSON in partial_fast_events;
    higher-priority terminal events need not evaluate lower-priority fast edges.
    Censoring preserves realised partial diagnostics but full net remains NaN.
    Floating nonfinite raw segment IDs censor in this branch, as in V11/V12.
    Costs are deducted once on original notional after weighted realized and
    remaining gross returns; the event threshold stays fixed20bp. Final status
    is entry_not_validated/no_partial_exit/partial_closed/partial_censored/
    unknown_source; watching is internal only. Default output is unchanged.

    ``fast_failed_launch_exit=True`` adds a full exit only to the above policy:
    before any partial, a valid fast edge with latest slow colour aligned exits
    at the current executable open when the SAME strict20bp Decimal test fails.
    Equality is failure. No current-bar HLC, future edge, realised winner label,
    seed-only opposite state or alternative threshold enters the condition.
    Original source/gap stop/slow exit/horizon priority remains unchanged. The
    full exit precedes current HLC validation and intrabar stops because those
    values are not known at the open. An earlier source gap still censors.
    This new full-fill gross uses Decimal quote differences before converting
    to float: exact20bp minus the float20bp cost is zero, not a tiny winner.
    The shared JSON action is failed_launch_exit; no partial is fabricated.
    failed_launch_status is entry_not_validated/failed_launch_closed/prior_exit/
    unknown_source (watching is internal); partial_fast_status also becomes
    failed_launch_closed on this outcome. False adds no fields or behaviour.
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
    fast_partial_enabled = "fast_partial_fraction" in selected
    failed_launch_enabled = bool(selected.get("fast_failed_launch_exit", False))
    failed_confirm_enabled = selected.get("fast_failed_launch_confirmations", 1) == 2
    if fast_partial_enabled:
        if fast_management_featured is None:
            raise ValueError("fast_partial_fraction requires fast_management_featured")
        _native40_frame(management_featured, 15, "management_featured")
        _native40_frame(fast_management_featured, 5, "fast_management_featured")
        fast_management = _validated_frame(fast_management_featured,
            ("open_time", "ma", "ma_side", "ma_slope_atr", "low", "high", "close", "segment_id"),
            "fast_management_featured")
    elif fast_management_featured is not None:
        raise ValueError("fast_management_featured is only valid with fast_partial_fraction")
    if frozen_ma_enabled:
        _validate_frozen_ma_entries(entries)
    if entries.empty:
        empty_columns = list(entries.columns) + ["entry_time", "exit_time", "closed", "outcome", "net_return", "net_r"]
        if "launch_deadline_minutes" in selected:
            empty_columns += [name for name in _launch_diagnostics() if name not in empty_columns]
        if frozen_ma_enabled:
            empty_columns += [name for name in _frozen_ma_diagnostics() if name not in empty_columns]
        if fast_partial_enabled:
            empty_columns += [name for name in _fast_partial_diagnostics() if name not in empty_columns]
        if failed_launch_enabled:
            empty_columns += [name for name in _failed_launch_diagnostics() if name not in empty_columns]
        if failed_confirm_enabled:
            empty_columns += [name for name in _failed_confirm_diagnostics() if name not in empty_columns]
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
    fast_management_at = ({row.open_time + FIVE_MINUTES: row for row in fast_management.itertuples(index=False)}
                          if fast_partial_enabled else {})
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
        if fast_partial_enabled:
            result.update(_fast_partial_diagnostics())
        if failed_launch_enabled:
            result.update(_failed_launch_diagnostics())
        if failed_confirm_enabled:
            result.update(_failed_confirm_diagnostics())
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
        if failed_launch_enabled:
            result["failed_launch_status"] = "watching"
        if failed_confirm_enabled:
            result["failed_confirm_status"] = "watching"
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
        fast_previous_side, fast_previous_bar, fast_events = None, None, []
        failed_pending, failed_confirm_events = None, []
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

        if fast_partial_enabled:
            fast_seed = fast_management_at.get(entry_time)
            fast_previous_side, fast_reason = _transition_observation(
                fast_seed, entry_time, time_index, prices, segments,
            )
            result.update(partial_fast_initial_reason=fast_reason, partial_fast_status="watching")
            if fast_previous_side is not None:
                fast_previous_bar = fast_seed
                seed_source = _partial_source(fast_seed, fast_previous_side, time_index, segments)
                result.update(partial_fast_initial_side=fast_previous_side,
                    partial_fast_initial_state="aligned" if direction*fast_previous_side > 0 else "opposite",
                    partial_fast_initial_open_time=fast_seed.open_time, partial_fast_initial_available_at=entry_time,
                    partial_fast_initial_management_segment_id=seed_source["management_segment_id"],
                    partial_fast_initial_raw_segment_id=seed_source["raw_segment_id"],
                    partial_fast_initial_ma=seed_source["ma"], partial_fast_initial_hl2=seed_source["hl2"])
                if direction*fast_previous_side > 0:
                    result["partial_fast_first_armed_at"] = entry_time

        def record_excursion(high: float, low: float, bars: int) -> None:
            favourable = (high - entry) / risk if direction == 1 else (entry - low) / risk
            adverse = (low - entry) / risk if direction == 1 else (entry - high) / risk
            result["max_favourable_r"] = max(result["max_favourable_r"], favourable)
            result["max_adverse_r"] = min(result["max_adverse_r"], adverse)
            if favourable > 0 and pd.isna(result["bars_to_first_positive"]):
                result["bars_to_first_positive"] = bars

        def finish(time: pd.Timestamp, price: float, outcome: str, closed: bool) -> None:
            nonlocal failed_pending
            gross = (_failed_launch_gross(price, entry, direction) if outcome == "fast_failed_launch"
                     else realised + remaining * direction * (price / entry - 1.0))
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
            if fast_partial_enabled:
                result["partial_fast_events"] = json.dumps(fast_events, sort_keys=True, allow_nan=False)
                result["partial_fast_status"] = (("partial_closed" if closed else "partial_censored") if remaining < 1
                                                  else "no_partial_exit" if closed else "unknown_source")
            if failed_launch_enabled:
                result["failed_launch_status"] = ("failed_launch_closed" if outcome == "fast_failed_launch"
                                                  else "prior_exit" if closed else "unknown_source")
                if outcome == "fast_failed_launch":
                    result["partial_fast_status"] = "failed_launch_closed"
            if failed_confirm_enabled:
                if failed_pending is not None:
                    # No lower-priority management or current-HLC inspection
                    # is added to justify a terminal fill/source censor.
                    log_failed_confirm("terminated", outcome, max(time, now), terminal={
                        "outcome": outcome, "closed": closed,
                        "exit_time": time.isoformat(), "exit_price": float(price)})
                    result["failed_confirm_priority_termination_count"] += 1
                    failed_pending = None
                result["failed_confirm_events"] = json.dumps(failed_confirm_events, sort_keys=True, allow_nan=False)
                result["failed_confirm_status"] = ("confirmed_closed" if outcome == "fast_failed_launch"
                                                   else "prior_exit" if closed else "unknown_source")

        def log_failed_confirm(action: str, reason: str, observed_at: pd.Timestamp,
                               observation: Optional[dict] = None, terminal: Optional[dict] = None) -> None:
            failed_confirm_events.append({
                "pending_id": failed_pending["id"], "action": action, "reason": reason,
                "created_at": failed_pending["created_at"].isoformat(),
                "due_at": failed_pending["due_at"].isoformat(),
                "observed_at": observed_at.isoformat(), "edge": failed_pending["edge"],
                "observation": observation, "terminal": terminal,
            })
            result["failed_confirm_last_reason"] = reason

        for i in range(index, len(raw)):
            now = pd.Timestamp(times[i])
            if cutoff is not None and now >= cutoff:
                break
            invalid_optional_segment = ((launch_enabled or frozen_ma_enabled or fast_partial_enabled)
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
            if fast_partial_enabled and now > entry_time:
                fast_bar = fast_management_at.get(now)
                fast_side, fast_reason = _transition_observation(fast_bar, now, time_index, prices, segments)
                fast_consecutive = (fast_previous_bar is not None and fast_bar is not None
                    and fast_bar.open_time == fast_previous_bar.open_time + FIVE_MINUTES
                    and fast_bar.segment_id == fast_previous_bar.segment_id)
                if failed_pending is not None:
                    # Only the immediately next completed native5 bar can
                    # confirm. Its OPEN is known; its HLC is still future.
                    slow_available = now.floor("15min")
                    slow_bar = management_at.get(slow_available)
                    slow_side, slow_reason = _transition_observation(slow_bar, slow_available, time_index, prices, segments,
                        interval, source_through=now)
                    slow_state = "unknown" if slow_side is None else "aligned" if direction*slow_side > 0 else "opposite"
                    qualifies = _fast_partial_profit(open_, entry, direction)
                    observation = {
                        "available_at": now.isoformat(), "open_price": float(open_),
                        "gross_return": _failed_launch_gross(open_, entry, direction),
                        "profit_qualified": qualifies,
                        "previous_fast": _partial_source(fast_previous_bar, fast_previous_side, time_index, segments),
                        "current_fast": _partial_source(fast_bar, fast_side, time_index, segments),
                        "fast_reason": fast_reason,
                        "fast_consecutive": False if pd.isna(fast_consecutive) else bool(fast_consecutive),
                        "slow": _partial_source(slow_bar, slow_side, time_index, segments),
                        "slow_available_at": slow_available.isoformat(), "slow_state": slow_state, "slow_reason": slow_reason,
                    }
                    pending_bar = failed_pending["bar"]
                    same_pending_sequence = (fast_bar is not None
                        and fast_bar.open_time == pending_bar.open_time+FIVE_MINUTES
                        and fast_bar.segment_id == pending_bar.segment_id)
                    cancellation = ("confirmation_clock_mismatch" if now != failed_pending["due_at"]
                        else fast_reason if fast_side is None
                        else "management_sequence_change" if not fast_consecutive or not same_pending_sequence
                        else "fast_not_opposite" if direction*fast_side >= 0
                        else "already_partial" if remaining < 1
                        else "slow_unknown" if slow_side is None
                        else "slow_not_aligned" if slow_state != "aligned"
                        else "profit_recovered" if qualifies else None)
                    if cancellation is None:
                        edge = failed_pending["edge"]
                        # V17 trigger scalars still denote the original real
                        # aligned->opposite edge, not this opposite->opposite
                        # observation. The new fields identify the actual fill.
                        result.update(failed_launch_count=1,
                            failed_launch_trigger_previous_open_time=pd.Timestamp(edge["previous_fast"]["open_time"]),
                            failed_launch_trigger_previous_available_at=pd.Timestamp(edge["previous_fast"]["open_time"])+FIVE_MINUTES,
                            failed_launch_trigger_open_time=pd.Timestamp(edge["current_fast"]["open_time"]),
                            failed_launch_trigger_available_at=pd.Timestamp(edge["available_at"]),
                            failed_launch_trigger_previous_side=edge["previous_fast"]["side"],
                            failed_launch_trigger_side=edge["current_fast"]["side"],
                            failed_launch_trigger_open_price=edge["open_price"],
                            failed_launch_trigger_gross_return=edge["gross_return"],
                            failed_launch_slow_open_time=pd.Timestamp(edge["slow"]["open_time"]),
                            failed_launch_slow_available_at=pd.Timestamp(edge["slow_available_at"]),
                            failed_launch_slow_side=edge["slow"]["side"], failed_launch_slow_state=edge["slow_state"],
                            failed_confirm_confirm_count=1,
                            failed_confirm_previous_open_time=pending_bar.open_time,
                            failed_confirm_open_time=fast_bar.open_time, failed_confirm_available_at=now,
                            failed_confirm_open_price=open_, failed_confirm_gross_return=observation["gross_return"],
                            failed_confirm_slow_open_time=slow_bar.open_time, failed_confirm_slow_available_at=slow_available,
                            failed_confirm_slow_side=slow_side, failed_confirm_slow_state=slow_state)
                        log_failed_confirm("confirmed", "consecutive_opposite_failed_profit", now, observation)
                        failed_pending = None
                        finish(now, open_, "fast_failed_launch", True)
                        completed = True
                        break
                    log_failed_confirm("cancelled", cancellation, now, observation)
                    result["failed_confirm_cancel_count"] += 1
                    failed_pending = None
                if fast_side is None or not fast_consecutive:
                    if fast_side is not None and fast_previous_bar is not None:
                        fast_reason = "management_sequence_change"
                    if fast_side is None or fast_previous_bar is not None:
                        result["partial_fast_reset_count"] += 1
                        result["partial_fast_last_reset_reason"] = fast_reason
                    fast_previous_side = None
                if fast_side is not None:
                    if fast_previous_side is not None and direction*fast_previous_side > 0 and direction*fast_side < 0:
                        slow_available = now.floor("15min")
                        slow_bar = management_at.get(slow_available)
                        slow_side, slow_reason = _transition_observation(slow_bar, slow_available, time_index, prices, segments,
                            interval, source_through=now)
                        slow_state = "unknown" if slow_side is None else "aligned" if direction*slow_side > 0 else "opposite"
                        qualifies = _fast_partial_profit(open_, entry, direction)
                        action = ("already_partial" if remaining < 1 else "slow_unknown" if slow_side is None
                            else "slow_not_aligned" if slow_state != "aligned" else "insufficient_profit" if not qualifies else "executed")
                        if failed_launch_enabled and action == "insufficient_profit":
                            action = "failed_launch_pending" if failed_confirm_enabled else "failed_launch_exit"
                        event_gross = (_failed_launch_gross(open_, entry, direction) if action in {"failed_launch_exit", "failed_launch_pending"}
                                       else float(direction*(open_/entry-1.0)))
                        fast_events.append({"available_at":now.isoformat(),"open_price":float(open_),
                            "gross_return":event_gross,"profit_threshold":0.002,
                            "profit_qualified":qualifies,"action":action,
                            "previous_fast":_partial_source(fast_previous_bar,fast_previous_side,time_index,segments),
                            "current_fast":_partial_source(fast_bar,fast_side,time_index,segments),
                            "slow":_partial_source(slow_bar,slow_side,time_index,segments),
                            "slow_available_at":slow_available.isoformat(),"slow_state":slow_state,"slow_reason":slow_reason})
                        result["partial_fast_flip_count"] += 1
                        if action == "failed_launch_pending":
                            result["failed_confirm_create_count"] += 1
                            failed_pending = {"id": result["failed_confirm_create_count"],
                                "bar": fast_bar, "edge": fast_events[-1],
                                "created_at": now, "due_at": now+FIVE_MINUTES}
                            result.update(failed_confirm_created_at=now, failed_confirm_due_at=now+FIVE_MINUTES)
                            log_failed_confirm("created", "failed_profit_edge", now)
                        elif action == "failed_launch_exit":
                            result.update(failed_launch_count=1,
                                failed_launch_trigger_previous_open_time=fast_previous_bar.open_time,
                                failed_launch_trigger_previous_available_at=fast_previous_bar.open_time+FIVE_MINUTES,
                                failed_launch_trigger_open_time=fast_bar.open_time,
                                failed_launch_trigger_available_at=now,
                                failed_launch_trigger_previous_side=fast_previous_side,
                                failed_launch_trigger_side=fast_side,
                                failed_launch_trigger_open_price=open_,
                                failed_launch_trigger_gross_return=event_gross,
                                failed_launch_slow_open_time=slow_bar.open_time,
                                failed_launch_slow_available_at=slow_available,
                                failed_launch_slow_side=slow_side, failed_launch_slow_state=slow_state)
                            # This full fill is already executable at the open;
                            # its current HLC and any later source failure have
                            # no authority to cancel it or add a partial fill.
                            finish(now, open_, "fast_failed_launch", True)
                            completed = True
                            break
                        elif action == "executed":
                            realised = 0.5*direction*(open_/entry-1.0)
                            remaining = 0.5
                            result.update(partial_fraction=0.5,partial_exit_time=now,partial_exit_price=open_,
                                partial_fast_fill_count=1,partial_fast_realised_net_return=realised-0.5*cost,
                                partial_fast_trigger_previous_open_time=fast_previous_bar.open_time,
                                partial_fast_trigger_open_time=fast_bar.open_time,partial_fast_trigger_available_at=now,
                                partial_fast_trigger_previous_side=fast_previous_side,partial_fast_trigger_side=fast_side,
                                partial_fast_trigger_gross_return=direction*(open_/entry-1.0),
                                partial_fast_slow_open_time=slow_bar.open_time,partial_fast_slow_available_at=slow_available,
                                partial_fast_slow_side=slow_side,partial_fast_slow_state=slow_state)
                    if direction*fast_side > 0 and pd.isna(result["partial_fast_first_armed_at"]):
                        result["partial_fast_first_armed_at"] = now
                    fast_previous_bar, fast_previous_side = fast_bar, fast_side
                else:
                    fast_previous_bar = None
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
