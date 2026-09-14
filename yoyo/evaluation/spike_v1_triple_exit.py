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

from dataclasses import dataclass, replace
import math
from typing import Any, Collection, Iterable, Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import path_reference, risk_reference

ROUND_TRIP_COST = 0.002
ADVERSE_R = 0.65
BE_R = 0.5
LOCK_TRIGGER_R = 2.0
LOCK_FRACTION = 0.5
NATIVE_LONG_ONLY = 1
ALL_ARMS = ("baseline", "adverse65", "be05", "lock50", "triple", "filters_only", "filtered_triple")


@dataclass(frozen=True)
class ExclusionFilters:
    """Optional fixed exclusion bundle; all fields are signal-time facts except risk.

    ``volume_ratio`` is the native final-signal ratio supplied by the caller.
    ``base_asset`` and ``stock_linked`` are supplied classifications: this
    module does not infer either from a symbol name.  Actual entry risk is
    calculated from the following open and the frozen V1 initial stop.
    """

    enabled: bool = False
    max_volume_ratio_inclusive: float = 20.0
    max_actual_risk_fraction_inclusive: float = 0.30
    excluded_base_assets: frozenset[str] = frozenset(("USDC", "PAXG"))


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


def _completed_bars(bars: pd.DataFrame, end: pd.Timestamp | None) -> pd.DataFrame:
    """Keep only bars fully observable by the exclusive evaluation boundary."""
    if end is None:
        return bars
    if len(bars) < 2:
        return bars.iloc[0:0]
    step = bars.index[1] - bars.index[0]
    if step <= pd.Timedelta(0):
        raise ValueError("prepared bar step must be positive")
    return bars.loc[(bars.index + step) <= end]


@dataclass(frozen=True)
class _PreparedBars:
    """Numpy view of one bar stream, built once outside all event/arm loops."""

    index: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    step: pd.Timedelta

    @classmethod
    def from_frame(cls, bars: pd.DataFrame) -> "_PreparedBars":
        step = bars.index[1] - bars.index[0] if len(bars) > 1 else pd.Timedelta(0)
        return cls(bars.index, *(bars[name].to_numpy(dtype=float, copy=False) for name in ("open", "high", "low", "close", "atr")), step)


def _event_stop(event: Mapping[str, Any]) -> float:
    if "initial_stop" in event and pd.notna(event["initial_stop"]):
        return float(event["initial_stop"])
    required = {"signal_close", "reference_signal_risk"}
    if not required.issubset(event):
        raise ValueError("each event needs initial_stop or signal_close/reference_signal_risk")
    return float(event["signal_close"]) - int(event.get("side", NATIVE_LONG_ONLY)) * float(event["reference_signal_risk"])


def _event_rows(events: pd.DataFrame | Iterable[Mapping[str, Any]], tick: float) -> list[dict[str, Any]]:
    frame = events.copy() if isinstance(events, pd.DataFrame) else pd.DataFrame(list(events))
    required = {"event_id", "signal_bar_open", "signal_close"}
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
        if "reference_signal_risk" not in event or pd.isna(event["reference_signal_risk"]):
            # Matched controls may start at a random causal bar, but their
            # stop/reference must be built with the same V1 risk function.
            # ``signal_atr`` avoids confusing this signal-time fact with a
            # later prepared-bar ATR column.
            if "recent_low" not in event or "signal_atr" not in event:
                raise ValueError("event needs reference_signal_risk or recent_low/signal_atr for native risk_reference")
            reference = risk_reference(event["side"], float(event["signal_close"]), float(event["recent_low"]),
                                       float(event["signal_atr"]), tick=tick)
            if not reference.valid:
                raise ValueError("random event has invalid native risk_reference")
            event["reference_signal_risk"], event["initial_stop"] = reference.risk, reference.stop
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


def _metadata_flags(event: Mapping[str, Any]) -> tuple[str, ...]:
    """Record unknown classifications without silently treating them as exclusions."""
    flags = []
    if not math.isfinite(float(event.get("volume_ratio", math.nan))):
        flags.append("missing_volume_ratio")
    if event.get("base_asset") is None or pd.isna(event.get("base_asset")):
        flags.append("missing_base_asset")
    if "stock_linked" not in event or pd.isna(event["stock_linked"]):
        flags.append("missing_stock_linked_classification")
    return tuple(flags)


def _exclusion_reason(event: Mapping[str, Any], entry: float, initial_stop: float, filters: ExclusionFilters) -> str:
    """Return an exclusion only for a known disqualifying fact.

    This is deliberately the reverse of a whitelist: the fixed experiment
    removes extreme volume/risk, USDC/PAXG, and stock-linked candidates.  A
    missing supplied classification remains an auditable unknown rather than a
    fabricated rejection.
    """
    side = int(event["side"])
    risk = side * (entry - initial_stop)
    if not filters.enabled:
        return "accepted"
    if not math.isfinite(risk) or risk <= 0:
        return "entry_gap_through_initial_stop"
    volume_ratio = float(event.get("volume_ratio", math.nan))
    if math.isfinite(volume_ratio) and volume_ratio > filters.max_volume_ratio_inclusive:
        return "excluded_volume_ratio_above_threshold"
    if risk / entry > filters.max_actual_risk_fraction_inclusive:
        return "excluded_actual_entry_risk_above_threshold"
    if event.get("base_asset") in filters.excluded_base_assets:
        return "excluded_base_asset"
    if "stock_linked" in event and not pd.isna(event["stock_linked"]) and bool(event["stock_linked"]):
        return "excluded_stock_linked_asset"
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


def _core_arm(arm: str) -> str:
    """Map filter-only reporting labels to their underlying exit contract."""
    return {"filters_only": "baseline", "filtered_triple": "triple"}.get(arm, arm)


def _uses_filter_bundle(arm: str, filters: ExclusionFilters) -> bool:
    return filters.enabled or arm in {"filters_only", "filtered_triple"}


def _active_stop(native_stop: float, native_source: str, overlay_stop: float | None, overlay_source: str | None, side: int) -> tuple[float, str]:
    if overlay_stop is None:
        return native_stop, native_source
    if (side == 1 and overlay_stop > native_stop) or (side == -1 and overlay_stop < native_stop):
        return overlay_stop, str(overlay_source)
    return native_stop, native_source


def _run_arm(bars: _PreparedBars, event: Mapping[str, Any], *, signal_i: int, tick: float, arm: str, end: pd.Timestamp | None,
             store_paths: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay one admitted event with its native V1 path and one overlay arm."""
    if arm not in ALL_ARMS:
        raise ValueError(f"unknown arm: {arm}")
    core_arm = _core_arm(arm)
    side = int(event["side"])
    if side not in (-1, 1):
        raise ValueError("side must be 1 or -1")
    entry_i = signal_i + 1
    if entry_i >= len(bars.index):
        raise ValueError("event lacks following-open entry bar")
    entry, initial_stop = float(bars.open[entry_i]), float(event["initial_stop"])
    initial_risk = side * (entry - initial_stop)
    if not math.isfinite(initial_risk) or initial_risk <= 0:
        # This is native V1's executable next-open gap outcome, not an
        # admission rewrite.  Filtered arms reject it above, but unfiltered
        # baseline parity retains it in the ledger.
        gross, net = 0.0, -ROUND_TRIP_COST
        schedule = ([{"event_id": event["event_id"], "arm": arm, "bar_open": bars.index[entry_i], "bar_i": entry_i,
                     "entry_time": bars.index[entry_i], "active_stop": initial_stop, "active_stop_source": "native_initial",
                     "observed_mfe_r_before": 0.0, "observed_mae_r_before": 0.0, "native_stop_before": initial_stop,
                     "overlay_stop_before": None, "filled": True, "fill_source": "entry_next_open_gap", "fill_price": entry,
                     "observed_mfe_r_after": 0.0, "observed_mae_r_after": 0.0, "native_stop_after": initial_stop,
                     "overlay_stop_after": None, "next_bar_stop": initial_stop, "next_bar_stop_source": "native_initial"}]
                    if store_paths else [])
        return ({"event_id": event["event_id"], "arm": arm, "side": side, "signal_bar_open": event["signal_bar_open"],
                 "signal_close": float(event["signal_close"]), "reference_signal_risk": float(event["reference_signal_risk"]),
                 "entry_time": bars.index[entry_i], "entry_price": entry, "initial_stop": initial_stop, "initial_risk": initial_risk,
                 "risk_fraction_at_entry": math.nan, "exit_time": bars.index[entry_i], "exit_price": entry,
                 "exit_reason": "entry_gap_through_initial_stop", "fill_source": "entry_next_open_gap", "fill_phase": "gap_open", "gap_fill": True,
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
    for i in range(entry_i, len(bars.index)):
        o, h, l, c, atr = bars.open[i], bars.high[i], bars.low[i], bars.close[i], bars.atr[i]
        active, active_source = _active_stop(native_stop, native_source, overlay, overlay_source, side)
        mfe_before, mae_before = peak_favorable_r, peak_adverse_r
        native_before, overlay_before = native_stop, overlay
        open_crosses = o <= active if side == 1 else o >= active
        intrabar_touches = l <= active if side == 1 else h >= active
        if intrabar_touches:
            exit_i = i
            exit_price = float(o) if open_crosses else active
            exit_reason = "protective_stop_gap" if open_crosses else "protective_stop"
            fill_source = "next_open_gap" if open_crosses else "intrabar_stop"
            protection_source = active_source
            if store_paths:
                schedule.append({"event_id": event["event_id"], "arm": arm, "bar_open": bars.index[i], "bar_i": i,
                                 "entry_time": bars.index[entry_i], "active_stop": active, "active_stop_source": active_source,
                                 "observed_mfe_r_before": mfe_before, "observed_mae_r_before": mae_before,
                                 "native_stop_before": native_before, "overlay_stop_before": overlay_before,
                                 "filled": True, "fill_source": fill_source, "fill_price": exit_price,
                                 "observed_mfe_r_after": mfe_before, "observed_mae_r_after": mae_before,
                                 "native_stop_after": native_stop, "overlay_stop_after": overlay,
                                 "next_bar_stop": active, "next_bar_stop_source": active_source})
            break
        favorable = side * ((h if side == 1 else l) - entry) / initial_risk
        adverse = side * (entry - (l if side == 1 else h)) / initial_risk
        peak_favorable_r, peak_adverse_r = max(peak_favorable_r, favorable), max(peak_adverse_r, adverse)
        path = path_reference(side, float(event["signal_close"]), float(event["reference_signal_risk"]),
                              native_stop, native_peak, native_armed, o, h, l, c, atr, tick=tick)
        if not path.alive:
            raise AssertionError("native path contradicted prechecked active stop")
        if (side == 1 and path.protection > native_stop) or (side == -1 and path.protection < native_stop):
            native_source = "native_trailing"
        native_stop, native_peak, native_armed = path.protection, path.peak_r, path.armed
        overlay, overlay_source = _overlay_stop(core_arm, side, entry, initial_risk, peak_favorable_r, peak_adverse_r)
        next_stop, next_source = _active_stop(native_stop, native_source, overlay, overlay_source, side)
        if store_paths:
            schedule.append({"event_id": event["event_id"], "arm": arm, "bar_open": bars.index[i], "bar_i": i,
                             "entry_time": bars.index[entry_i], "active_stop": active, "active_stop_source": active_source,
                             "observed_mfe_r_before": mfe_before, "observed_mae_r_before": mae_before,
                             "native_stop_before": native_before, "overlay_stop_before": overlay_before,
                             "filled": False, "fill_source": "", "fill_price": math.nan,
                             "observed_mfe_r_after": peak_favorable_r, "observed_mae_r_after": peak_adverse_r,
                             "native_stop_after": native_stop, "overlay_stop_after": overlay,
                             "next_bar_stop": next_stop, "next_bar_stop_source": next_source})
    step = bars.step
    if exit_i is None:
        exit_i, exit_price = len(bars.index) - 1, float(bars.close[-1])
        exit_time = min(end, bars.index[-1] + step) if end is not None else bars.index[-1] + step
    else:
        exit_time = bars.index[entry_i] if exit_reason == "entry_gap_through_initial_stop" else bars.index[exit_i] + step
    gross = side * (float(exit_price) / entry - 1.0)
    net = gross - ROUND_TRIP_COST
    risk_fraction = initial_risk / entry
    return ({"event_id": event["event_id"], "arm": arm, "side": side, "signal_bar_open": event["signal_bar_open"],
             "signal_close": float(event["signal_close"]), "reference_signal_risk": float(event["reference_signal_risk"]),
             "entry_time": bars.index[entry_i], "entry_price": entry, "initial_stop": initial_stop,
             "initial_risk": initial_risk, "risk_fraction_at_entry": risk_fraction, "exit_time": exit_time,
             "exit_price": float(exit_price), "exit_reason": exit_reason, "fill_source": fill_source,
             "fill_phase": "gap_open" if fill_source == "next_open_gap" else "intrabar_stop" if fill_source == "intrabar_stop" else "censor",
             "gap_fill": fill_source == "next_open_gap",
             "exit_protection_source": protection_source, "gross_return": gross, "net_return": net,
             "net_r": net / risk_fraction, "mfe_r": peak_favorable_r, "mae_r": peak_adverse_r,
             "censored": exit_reason == "censored", "observed_mfe_r_at_decision": peak_favorable_r,
             "observed_mae_r_at_decision": peak_adverse_r, "final_stop": _active_stop(native_stop, native_source, overlay, overlay_source, side)[0]}, schedule)


def replay_native_v1_arms(bars: pd.DataFrame, events: pd.DataFrame | Iterable[Mapping[str, Any]], *, tick: float,
                          arms: Iterable[str] = ("baseline",), filters: ExclusionFilters = ExclusionFilters(),
                          eligibility: Collection[str] | Mapping[str, bool] | None = None,
                          end: pd.Timestamp | str | None = None, native_long_only: bool = True,
                          store_paths: bool = True) -> ReplayResult:
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
    bars = _completed_bars(bars, finished)
    prepared = _PreparedBars.from_frame(bars)
    outcomes: list[dict[str, Any]] = []
    schedules: list[dict[str, Any]] = []
    admissions: list[dict[str, Any]] = []
    for event in _event_rows(events, tick):
        metadata_flags = _metadata_flags(event)
        base_audit = {"event_id": event["event_id"], "signal_bar_open": event["signal_bar_open"], "side": event["side"],
                      "eligible": _eligible(event, eligibility), "entry_time": pd.NaT, "entry_price": math.nan,
                      "initial_stop": event["initial_stop"], "actual_risk_fraction": math.nan,
                      "metadata_complete": not metadata_flags, "metadata_flags": ";".join(metadata_flags)}
        if native_long_only and event["side"] != NATIVE_LONG_ONLY:
            admissions.extend(base_audit | {"arm": arm, "accepted": False, "reason": "native_v1_long_only"} for arm in requested_arms)
            continue
        if not base_audit["eligible"]:
            admissions.extend(base_audit | {"arm": arm, "accepted": False, "reason": "ineligible_before_position"} for arm in requested_arms)
            continue
        if event["signal_bar_open"] not in bars.index:
            admissions.extend(base_audit | {"arm": arm, "accepted": False, "reason": "signal_bar_absent"} for arm in requested_arms)
            continue
        signal_i = int(bars.index.get_loc(event["signal_bar_open"]))
        if signal_i + 1 >= len(bars):
            admissions.extend(base_audit | {"arm": arm, "accepted": False, "reason": "no_following_open"} for arm in requested_arms)
            continue
        entry = float(bars.open.iloc[signal_i + 1])
        risk_fraction = event["side"] * (entry - event["initial_stop"]) / entry
        for arm in requested_arms:
            arm_filters = replace(filters, enabled=_uses_filter_bundle(arm, filters))
            reason = _exclusion_reason(event, entry, event["initial_stop"], arm_filters)
            admissions.append(base_audit | {"arm": arm, "entry_time": bars.index[signal_i + 1], "entry_price": entry,
                                             "actual_risk_fraction": risk_fraction, "accepted": reason == "accepted", "reason": reason})
            if reason != "accepted":
                continue
            outcome, schedule = _run_arm(prepared, event, signal_i=signal_i, tick=tick, arm=arm, end=finished, store_paths=store_paths)
            outcomes.append(outcome)
            if store_paths:
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
    return replay_native_v1_arms(bars, events, arms=("filters_only",), **kwargs)


def replay_filtered_triple(bars: pd.DataFrame, events: pd.DataFrame | Iterable[Mapping[str, Any]], **kwargs: Any) -> ReplayResult:
    """Run the combined triple protection only after the fixed filter bundle."""
    return replay_native_v1_arms(bars, events, arms=("filtered_triple",), **kwargs)
