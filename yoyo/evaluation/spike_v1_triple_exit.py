"""Causal, eval-only native SPIKE V1 multi-protection replay.

The frozen V1 source is long-only.  This module keeps its signal-close
reference and following-open execution contract intact: ``path_reference``
supplies the native V1 protection, the currently active stop is tested before
the completed bar can change any protection, and a gap through that stop fills
at that next open.  The optional overlays are observational only and become
effective on the following bar:

* observed MAE >= 0.65 actual entry R caps loss at entry - 0.65R;
* observed MFE >= 0.5 actual entry R moves protection to entry;
* observed MFE > 2 actual entry R locks half of the cumulative MFE.

No full-history excursion is used.  The input event feed is deliberately
explicit so a caller can preselect native events, causally deduplicate them,
or supply matched controls without bypassing the same entry safety checks.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Collection, Iterable, Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import path_reference

ROUND_TRIP_COST = 0.002
ADVERSE_R = 0.65
BE_R = 0.5
LOCK_TRIGGER_R = 2.0
LOCK_FRACTION = 0.5
NATIVE_LONG_ONLY = 1
ALL_ARMS = ("baseline", "adverse65", "be05", "lock50", "triple")


@dataclass(frozen=True)
class AdmissionFilters:
    """Optional fixed filter bundle; all fields are signal-time facts except risk.

    ``volume_ratio`` is the native final-signal ratio supplied by the caller.
    ``base_asset`` and ``stock_linked`` are supplied classifications: this
    module does not infer either from a symbol name.  Actual entry risk is
    calculated from the following open and the frozen V1 initial stop.
    """

    enabled: bool = False
    min_volume_ratio_exclusive: float = 20.0
    min_actual_risk_fraction_exclusive: float = 0.30
    allowed_base_assets: frozenset[str] = frozenset(("USDC", "PAXG"))


@dataclass(frozen=True)
class ReplayResult:
    """Audit-ready result frames for a prepared bar stream and event feed."""

    outcomes: pd.DataFrame
    schedule: pd.DataFrame
    admission: pd.DataFrame


def _as_utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _require_bars(bars: pd.DataFrame) -> None:
    required = {"open", "high", "low", "close", "atr"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError("prepared bars missing: " + ", ".join(sorted(missing)))
    if not isinstance(bars.index, pd.DatetimeIndex) or not bars.index.is_monotonic_increasing or not bars.index.is_unique:
        raise ValueError("prepared bars need a unique, increasing DatetimeIndex")
    prices = bars[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("prepared OHLC must be positive and finite")
    if ((bars.high < bars[["open", "close", "low"]].max(axis=1)) |
            (bars.low > bars[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("prepared bars have invalid OHLC geometry")


def _event_stop(event: Mapping[str, Any]) -> float:
    if "initial_stop" in event and pd.notna(event["initial_stop"]):
        return float(event["initial_stop"])
    required = {"signal_close", "reference_signal_risk"}
    if not required.issubset(event):
        raise ValueError("each event needs initial_stop or signal_close/reference_signal_risk")
    return float(event["signal_close"]) - int(event.get("side", NATIVE_LONG_ONLY)) * float(event["reference_signal_risk"])


def _event_rows(events: pd.DataFrame | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    frame = events.copy() if isinstance(events, pd.DataFrame) else pd.DataFrame(list(events))
    required = {"event_id", "signal_bar_open", "signal_close", "reference_signal_risk"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError("event feed missing: " + ", ".join(sorted(missing)))
    if frame.event_id.isna().any() or frame.event_id.astype(str).duplicated().any():
        raise ValueError("event_id must be unique and present")
    rows: list[dict[str, Any]] = []
    for event in frame.to_dict("records"):
        event["event_id"] = str(event["event_id"])
        event["signal_bar_open"] = _as_utc(event["signal_bar_open"])
        event["side"] = int(event.get("side", NATIVE_LONG_ONLY))
        event["initial_stop"] = _event_stop(event)
        rows.append(event)
    return rows


def _eligible(event: Mapping[str, Any], eligibility: Collection[str] | Mapping[str, bool] | None) -> bool:
    if eligibility is None:
        return bool(event.get("eligible", True))
    event_id = str(event["event_id"])
    if isinstance(eligibility, Mapping):
        return bool(eligibility.get(event_id, False))
    return event_id in eligibility


def _admission_reason(event: Mapping[str, Any], entry: float, initial_stop: float, filters: AdmissionFilters) -> str:
    side = int(event["side"])
    risk = side * (entry - initial_stop)
    if not filters.enabled:
        return "accepted"
    if not math.isfinite(risk) or risk <= 0:
        return "entry_gap_through_initial_stop"
    if not math.isfinite(float(event.get("volume_ratio", math.nan))) or float(event["volume_ratio"]) <= filters.min_volume_ratio_exclusive:
        return "volume_ratio_not_above_threshold"
    if risk / entry <= filters.min_actual_risk_fraction_exclusive:
        return "actual_entry_risk_not_above_threshold"
    if event.get("base_asset") not in filters.allowed_base_assets:
        return "base_asset_not_allowed"
    # A missing classification is not interchangeable with ``False``.
    if "stock_linked" not in event or pd.isna(event["stock_linked"]):
        return "missing_stock_linked_classification"
    if bool(event["stock_linked"]):
        return "stock_linked_asset"
    return "accepted"


def _overlay_stop(arm: str, side: int, entry: float, initial_risk: float, peak_favorable_r: float, peak_adverse_r: float) -> tuple[float | None, str | None]:
    """Return the tightest extra stop known after a completed, held bar."""
    candidates: list[tuple[float, str]] = []
    if arm in ("adverse65", "triple") and peak_adverse_r >= ADVERSE_R:
        candidates.append((entry - side * ADVERSE_R * initial_risk, "adverse65"))
    if arm in ("be05", "triple") and peak_favorable_r >= BE_R:
        candidates.append((entry, "be05"))
    if arm in ("lock50", "triple") and peak_favorable_r > LOCK_TRIGGER_R:
        candidates.append((entry + side * LOCK_FRACTION * peak_favorable_r * initial_risk, "lock50"))
    if not candidates:
        return None, None
    return (max(candidates, key=lambda item: item[0]) if side == 1 else min(candidates, key=lambda item: item[0]))


def _active_stop(native_stop: float, native_source: str, overlay_stop: float | None, overlay_source: str | None, side: int) -> tuple[float, str]:
    if overlay_stop is None:
        return native_stop, native_source
    if (side == 1 and overlay_stop > native_stop) or (side == -1 and overlay_stop < native_stop):
        return overlay_stop, str(overlay_source)
    return native_stop, native_source


def _run_arm(bars: pd.DataFrame, event: Mapping[str, Any], *, tick: float, arm: str, end: pd.Timestamp | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay one admitted event with its native V1 path and one overlay arm."""
    if arm not in ALL_ARMS:
        raise ValueError(f"unknown arm: {arm}")
    side = int(event["side"])
    if side not in (-1, 1):
        raise ValueError("side must be 1 or -1")
    signal_i = int(bars.index.get_loc(event["signal_bar_open"]))
    entry_i = signal_i + 1
    if entry_i >= len(bars):
        raise ValueError("event lacks following-open entry bar")
    entry, initial_stop = float(bars.open.iloc[entry_i]), float(event["initial_stop"])
    initial_risk = side * (entry - initial_stop)
    if not math.isfinite(initial_risk) or initial_risk <= 0:
        # This is native V1's executable next-open gap outcome, not an
        # admission rewrite.  Filtered arms reject it above, but unfiltered
        # baseline parity retains it in the ledger.
        gross, net = 0.0, -ROUND_TRIP_COST
        schedule = [{"event_id": event["event_id"], "arm": arm, "bar_open": bars.index[entry_i], "bar_i": entry_i,
                     "entry_time": bars.index[entry_i], "active_stop": initial_stop, "active_stop_source": "native_initial",
                     "observed_mfe_r_before": 0.0, "observed_mae_r_before": 0.0, "native_stop_before": initial_stop,
                     "overlay_stop_before": None, "filled": True, "fill_source": "entry_next_open_gap", "fill_price": entry,
                     "observed_mfe_r_after": 0.0, "observed_mae_r_after": 0.0, "native_stop_after": initial_stop,
                     "overlay_stop_after": None, "next_bar_stop": initial_stop, "next_bar_stop_source": "native_initial"}]
        return ({"event_id": event["event_id"], "arm": arm, "side": side, "signal_bar_open": event["signal_bar_open"],
                 "signal_close": float(event["signal_close"]), "reference_signal_risk": float(event["reference_signal_risk"]),
                 "entry_time": bars.index[entry_i], "entry_price": entry, "initial_stop": initial_stop, "initial_risk": initial_risk,
                 "risk_fraction_at_entry": math.nan, "exit_time": bars.index[entry_i], "exit_price": entry,
                 "exit_reason": "entry_gap_through_initial_stop", "fill_source": "entry_next_open_gap", "gap_fill": True,
                 "exit_protection_source": "native_initial", "gross_return": gross, "net_return": net, "net_r": math.nan,
                 "mfe_r": 0.0, "mae_r": 0.0, "censored": False, "observed_mfe_r_at_decision": 0.0,
                 "observed_mae_r_at_decision": 0.0, "final_stop": initial_stop}, schedule)
    native_stop, native_source = initial_stop, "native_initial"
    native_peak, native_armed = 0.0, False
    peak_favorable_r = peak_adverse_r = 0.0
    overlay, overlay_source = None, None
    schedule: list[dict[str, Any]] = []
    exit_i: int | None = None
    exit_price, exit_reason, fill_source, protection_source = math.nan, "censored", "censor_close", ""
    for i in range(entry_i, len(bars)):
        bar = bars.iloc[i]
        active, active_source = _active_stop(native_stop, native_source, overlay, overlay_source, side)
        open_crosses = float(bar.open) <= active if side == 1 else float(bar.open) >= active
        intrabar_touches = float(bar.low) <= active if side == 1 else float(bar.high) >= active
        row = {"event_id": event["event_id"], "arm": arm, "bar_open": bars.index[i], "bar_i": i,
               "entry_time": bars.index[entry_i], "active_stop": active, "active_stop_source": active_source,
               "observed_mfe_r_before": peak_favorable_r, "observed_mae_r_before": peak_adverse_r,
               "native_stop_before": native_stop, "overlay_stop_before": overlay,
               "filled": False, "fill_source": "", "fill_price": math.nan}
        if intrabar_touches:
            exit_i = i
            exit_price = float(bar.open) if open_crosses else active
            exit_reason = "protective_stop_gap" if open_crosses else "protective_stop"
            fill_source = "next_open_gap" if open_crosses else "intrabar_stop"
            protection_source = active_source
            row.update(filled=True, fill_source=fill_source, fill_price=exit_price,
                       observed_mfe_r_after=peak_favorable_r, observed_mae_r_after=peak_adverse_r,
                       native_stop_after=native_stop, overlay_stop_after=overlay,
                       next_bar_stop=active, next_bar_stop_source=active_source)
            schedule.append(row)
            break
        favorable = side * ((float(bar.high) if side == 1 else float(bar.low)) - entry) / initial_risk
        adverse = side * (entry - (float(bar.low) if side == 1 else float(bar.high))) / initial_risk
        peak_favorable_r, peak_adverse_r = max(peak_favorable_r, favorable), max(peak_adverse_r, adverse)
        path = path_reference(side, float(event["signal_close"]), float(event["reference_signal_risk"]),
                              native_stop, native_peak, native_armed, float(bar.open), float(bar.high),
                              float(bar.low), float(bar.close), float(bar.atr), tick=tick)
        if not path.alive:
            raise AssertionError("native path contradicted prechecked active stop")
        if (side == 1 and path.protection > native_stop) or (side == -1 and path.protection < native_stop):
            native_source = "native_trailing"
        native_stop, native_peak, native_armed = path.protection, path.peak_r, path.armed
        overlay, overlay_source = _overlay_stop(arm, side, entry, initial_risk, peak_favorable_r, peak_adverse_r)
        next_stop, next_source = _active_stop(native_stop, native_source, overlay, overlay_source, side)
        row.update(observed_mfe_r_after=peak_favorable_r, observed_mae_r_after=peak_adverse_r,
                   native_stop_after=native_stop, overlay_stop_after=overlay,
                   next_bar_stop=next_stop, next_bar_stop_source=next_source)
        schedule.append(row)
    step = bars.index[1] - bars.index[0] if len(bars) > 1 else pd.Timedelta(0)
    if exit_i is None:
        exit_i, exit_price = len(bars) - 1, float(bars.close.iloc[-1])
        exit_time = min(end, bars.index[-1] + step) if end is not None else bars.index[-1] + step
    else:
        exit_time = bars.index[entry_i] if exit_reason == "entry_gap_through_initial_stop" else bars.index[exit_i] + step
    held_end = exit_i if exit_reason == "protective_stop" else exit_i + 1
    held = bars.iloc[entry_i:held_end]
    highs = held.high.to_numpy(float) if len(held) else np.array([entry])
    lows = held.low.to_numpy(float) if len(held) else np.array([entry])
    gross = side * (float(exit_price) / entry - 1.0)
    net = gross - ROUND_TRIP_COST
    risk_fraction = initial_risk / entry
    return ({"event_id": event["event_id"], "arm": arm, "side": side, "signal_bar_open": event["signal_bar_open"],
             "signal_close": float(event["signal_close"]), "reference_signal_risk": float(event["reference_signal_risk"]),
             "entry_time": bars.index[entry_i], "entry_price": entry, "initial_stop": initial_stop,
             "initial_risk": initial_risk, "risk_fraction_at_entry": risk_fraction, "exit_time": exit_time,
             "exit_price": float(exit_price), "exit_reason": exit_reason, "fill_source": fill_source,
             "gap_fill": fill_source == "next_open_gap",
             "exit_protection_source": protection_source, "gross_return": gross, "net_return": net,
             "net_r": net / risk_fraction, "mfe_r": float((highs.max() - entry) / initial_risk) if side == 1 else float((entry - lows.min()) / initial_risk),
             "mae_r": float((entry - lows.min()) / initial_risk) if side == 1 else float((highs.max() - entry) / initial_risk),
             "censored": exit_reason == "censored", "observed_mfe_r_at_decision": peak_favorable_r,
             "observed_mae_r_at_decision": peak_adverse_r, "final_stop": _active_stop(native_stop, native_source, overlay, overlay_source, side)[0]}, schedule)


def replay_native_v1_arms(bars: pd.DataFrame, events: pd.DataFrame | Iterable[Mapping[str, Any]], *, tick: float,
                          arms: Iterable[str] = ("baseline",), filters: AdmissionFilters = AdmissionFilters(),
                          eligibility: Collection[str] | Mapping[str, bool] | None = None,
                          end: pd.Timestamp | str | None = None, native_long_only: bool = True) -> ReplayResult:
    """Replay prepared native V1 events without reading data or selecting signals.

    Eligibility is checked before a position is created.  Matched random
    controls may be included in ``events``, but they must include the same
    frozen stop/reference inputs and pass this exact admission path.
    """
    _require_bars(bars)
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("tick must be positive and finite")
    requested_arms = tuple(arms)
    if not requested_arms or any(arm not in ALL_ARMS for arm in requested_arms):
        raise ValueError("arms must be a nonempty subset of ALL_ARMS")
    finished = _as_utc(end) if end is not None else None
    outcomes: list[dict[str, Any]] = []
    schedules: list[dict[str, Any]] = []
    admissions: list[dict[str, Any]] = []
    for event in _event_rows(events):
        base_audit = {"event_id": event["event_id"], "signal_bar_open": event["signal_bar_open"], "side": event["side"],
                      "eligible": _eligible(event, eligibility), "entry_time": pd.NaT, "entry_price": math.nan,
                      "initial_stop": event["initial_stop"], "actual_risk_fraction": math.nan}
        if native_long_only and event["side"] != NATIVE_LONG_ONLY:
            admissions.append(base_audit | {"accepted": False, "reason": "native_v1_long_only"})
            continue
        if not base_audit["eligible"]:
            admissions.append(base_audit | {"accepted": False, "reason": "ineligible_before_position"})
            continue
        if event["signal_bar_open"] not in bars.index:
            admissions.append(base_audit | {"accepted": False, "reason": "signal_bar_absent"})
            continue
        signal_i = int(bars.index.get_loc(event["signal_bar_open"]))
        if signal_i + 1 >= len(bars):
            admissions.append(base_audit | {"accepted": False, "reason": "no_following_open"})
            continue
        entry = float(bars.open.iloc[signal_i + 1])
        risk_fraction = event["side"] * (entry - event["initial_stop"]) / entry
        reason = _admission_reason(event, entry, event["initial_stop"], filters)
        audit = base_audit | {"entry_time": bars.index[signal_i + 1], "entry_price": entry,
                              "actual_risk_fraction": risk_fraction, "accepted": reason == "accepted", "reason": reason}
        admissions.append(audit)
        if reason != "accepted":
            continue
        for arm in requested_arms:
            outcome, schedule = _run_arm(bars, event, tick=tick, arm=arm, end=finished)
            outcomes.append(outcome)
            schedules.extend(schedule)
    return ReplayResult(pd.DataFrame(outcomes), pd.DataFrame(schedules), pd.DataFrame(admissions))


def replay_baseline(bars: pd.DataFrame, events: pd.DataFrame | Iterable[Mapping[str, Any]], **kwargs: Any) -> ReplayResult:
    """Convenience API for the frozen native V1 baseline arm only."""
    return replay_native_v1_arms(bars, events, arms=("baseline",), **kwargs)


def replay_triple(bars: pd.DataFrame, events: pd.DataFrame | Iterable[Mapping[str, Any]], **kwargs: Any) -> ReplayResult:
    """Convenience API for the combined adverse/BE/half-MFE protection arm."""
    return replay_native_v1_arms(bars, events, arms=("triple",), **kwargs)


def replay_single_exit(bars: pd.DataFrame, events: pd.DataFrame | Iterable[Mapping[str, Any]], *, arm: str, **kwargs: Any) -> ReplayResult:
    """Run one diagnostic overlay arm (``adverse65``, ``be05``, or ``lock50``)."""
    if arm not in {"adverse65", "be05", "lock50"}:
        raise ValueError("single diagnostic arm must be adverse65, be05, or lock50")
    return replay_native_v1_arms(bars, events, arms=(arm,), **kwargs)


def replay_filters_only(bars: pd.DataFrame, events: pd.DataFrame | Iterable[Mapping[str, Any]], **kwargs: Any) -> ReplayResult:
    """Run the unchanged native baseline after the optional fixed filter bundle."""
    return replay_native_v1_arms(bars, events, arms=("baseline",), **kwargs)
