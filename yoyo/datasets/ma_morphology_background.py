"""Fail-closed morphology screen for clear six-MA background candidates.

This module screens a proposed 4/5-bar pseudo core without assigning a
completed-path label.  It uses only the window that is visible to the chart:
11 bars before the core and five bars after it.  The input is truncated at
that right edge before indicators are calculated, so later OHLC changes cannot
change a result.  A 1,200-bar prefix immediately before that window seeds the
displayed EMA values.  The screen deliberately accepts only clearly separated
backgrounds; dense or ambiguous windows remain review-only.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas


WIDEST_PRE_BARS = 11
WIDEST_POST_BARS = 5
EMA_SUPPORT_BARS = 1200
DEFAULT_THRESHOLD = 1.6
THRESHOLD_PROVENANCE = (
    "experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/"
    "preregistration.json#negative_sampling.easy_definition."
    "ma_spread_end_atr_min_any"
)
REQUIRED_COLUMNS = ("open_time", "open", "high", "low", "close")


def _result(
    *,
    accepted: bool,
    reasons: list[str],
    metrics: dict[str, Any],
    visible_start_i: int | None,
    visible_end_i: int | None,
    decision_close_utc: str | None,
) -> dict[str, Any]:
    """Return only JSON-compatible scalar values for a screening decision."""

    return {
        "accepted": bool(accepted),
        "reasons": list(reasons),
        "metrics": metrics,
        "visible_start_i": visible_start_i,
        "visible_end_i": visible_end_i,
        "decision_close_utc": decision_close_utc,
    }


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result == value else None


def _base_metrics(threshold: float | object) -> dict[str, Any]:
    """Declare the frozen threshold and no-outcome provenance on every result."""

    numeric_threshold = (
        float(threshold) if isinstance(threshold, (int, float, np.number)) else np.nan
    )
    is_default_threshold = bool(
        np.isfinite(numeric_threshold) and numeric_threshold == DEFAULT_THRESHOLD
    )
    return {
        "threshold": float(numeric_threshold) if np.isfinite(numeric_threshold) else None,
        "default_threshold": DEFAULT_THRESHOLD,
        "threshold_provenance": (
            THRESHOLD_PROVENANCE if is_default_threshold else "caller_supplied_override"
        ),
        "threshold_override": not is_default_threshold,
        "visible_pre_bars": WIDEST_PRE_BARS,
        "visible_post_bars": WIDEST_POST_BARS,
        "ema_support_bars_before_visible_start": EMA_SUPPORT_BARS,
        "uses_profit_or_outcome": False,
        "uses_direction": False,
    }


def _append_reason(reasons: list[str], condition: bool, reason: str) -> None:
    if condition:
        reasons.append(reason)


def screen_window(
    frame: pd.DataFrame,
    *,
    core_start_i: int,
    core_end_i: int,
    bar_minutes: int,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Screen one 4/5-bar candidate as a clearly non-dense background.

    Inputs used are ``open_time`` and OHLC through ``core_end_i + 5`` only.
    ATR14 is the inherited Pine-RMA implementation exposed by
    :func:`add_candidate_features`; close and HL2 each receive renderer-style
    SMA/EMA 20/60/120.  The maximum causal ATR14 across the full visible
    window normalizes every bar, including the pre-core and post-core edges.
    No profit, trade outcome, direction, or bars after the visible right edge
    are read.
    """

    metrics = _base_metrics(threshold)
    reasons: list[str] = []
    start = _integer(core_start_i)
    end = _integer(core_end_i)
    minutes = _integer(bar_minutes)
    numeric_threshold = (
        float(threshold)
        if isinstance(threshold, (int, float, np.number))
        else np.nan
    )
    if start is None or end is None:
        return _result(
            accepted=False,
            reasons=["invalid_core_indices"],
            metrics=metrics,
            visible_start_i=None,
            visible_end_i=None,
            decision_close_utc=None,
        )
    visible_start = start - WIDEST_PRE_BARS
    visible_end = end + WIDEST_POST_BARS
    if minutes is None or minutes <= 0:
        reasons.append("invalid_bar_minutes")
    if not np.isfinite(numeric_threshold) or numeric_threshold <= 0:
        reasons.append("invalid_threshold")
    if end < start or end - start + 1 not in {4, 5}:
        reasons.append("core_length_must_be_4_or_5")
    if not isinstance(frame, pd.DataFrame):
        reasons.append("frame_must_be_a_dataframe")
    if reasons:
        return _result(
            accepted=False,
            reasons=reasons,
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )
    missing = [name for name in REQUIRED_COLUMNS if name not in frame.columns]
    if missing:
        metrics["missing_columns"] = missing
        return _result(
            accepted=False,
            reasons=["missing_required_columns"],
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )
    support_start = visible_start - EMA_SUPPORT_BARS
    if visible_start < 0 or visible_end >= len(frame):
        reasons.append("visible_window_unavailable")
    if support_start < 0:
        reasons.append("insufficient_ema_support")
    if reasons:
        return _result(
            accepted=False,
            reasons=reasons,
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )

    # This bounded support slice is the behavior boundary: rows after
    # visible_end never enter validation, indicators, or returned metrics.
    support = frame.iloc[support_start : visible_end + 1].copy().reset_index(drop=True)
    visible_local_start = EMA_SUPPORT_BARS
    visible_local_end = len(support) - 1
    metrics["support_start_i"] = support_start
    metrics["support_rows"] = int(len(support))

    times = pd.to_datetime(support["open_time"], utc=True, errors="coerce")
    if times.isna().any():
        reasons.append("invalid_open_time_in_support")
    elif not times.diff().iloc[1:].eq(pd.Timedelta(minutes=minutes)).all():
        reasons.append("source_gap_or_duplicate_in_support")

    numeric = support.loc[:, ["open", "high", "low", "close"]].apply(
        pd.to_numeric, errors="coerce"
    )
    values = numeric.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        reasons.append("nonfinite_ohlc_in_support")
    else:
        open_, high, low, close = (numeric[name] for name in ("open", "high", "low", "close"))
        _append_reason(reasons, bool((close <= 0).any()), "nonpositive_close_in_support")
        _append_reason(reasons, bool((high < low).any()), "high_below_low_in_support")
        _append_reason(
            reasons,
            bool((high < pd.concat((open_, close), axis=1).max(axis=1)).any()),
            "high_below_body_in_support",
        )
        _append_reason(
            reasons,
            bool((low > pd.concat((open_, close), axis=1).min(axis=1)).any()),
            "low_above_body_in_support",
        )
    if reasons:
        return _result(
            accepted=False,
            reasons=reasons,
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )

    support.loc[:, ["open", "high", "low", "close"]] = numeric
    visible = support.iloc[visible_local_start : visible_local_end + 1]
    unique_closes = int(visible["close"].nunique(dropna=True))
    nonzero_range_bars = int((visible["high"] > visible["low"]).sum())
    metrics.update(
        {
            "visible_bars": int(len(visible)),
            "unique_close_values": unique_closes,
            "nonzero_high_low_bars": nonzero_range_bars,
        }
    )
    _append_reason(reasons, unique_closes < 4, "insufficient_unique_close_activity")
    _append_reason(reasons, nonzero_range_bars < 4, "insufficient_nonzero_range_activity")
    if reasons:
        return _result(
            accepted=False,
            reasons=reasons,
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )

    try:
        close_features = add_candidate_features(support)
        hl2_input = support.copy()
        hl2_input["close"] = (hl2_input["high"] + hl2_input["low"]) / 2.0
        hl2_features = add_six_mas(hl2_input)
    except (TypeError, ValueError, FloatingPointError) as exc:
        metrics["feature_error"] = type(exc).__name__
        return _result(
            accepted=False,
            reasons=["feature_computation_failed"],
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )

    atr = close_features["atr"].iloc[
        visible_local_start : visible_local_end + 1
    ].to_numpy(dtype=float)
    close_mas = close_features.loc[
        visible_local_start : visible_local_end, list(SIX_MA_COLUMNS)
    ].to_numpy(dtype=float)
    hl2_mas = hl2_features.loc[
        visible_local_start : visible_local_end, list(SIX_MA_COLUMNS)
    ].to_numpy(dtype=float)
    if (
        not np.isfinite(atr).all()
        or not np.isfinite(close_mas).all()
        or not np.isfinite(hl2_mas).all()
    ):
        return _result(
            accepted=False,
            reasons=["nonfinite_atr_or_six_ma_in_visible_window"],
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )
    atr_max = float(atr.max())
    if atr_max <= 0:
        return _result(
            accepted=False,
            reasons=["nonpositive_visible_atr"],
            metrics=metrics,
            visible_start_i=visible_start,
            visible_end_i=visible_end,
            decision_close_utc=None,
        )
    close_spread = close_mas.max(axis=1) - close_mas.min(axis=1)
    hl2_spread = hl2_mas.max(axis=1) - hl2_mas.min(axis=1)
    required_spread = numeric_threshold * atr_max
    close_failures = np.flatnonzero(close_spread < required_spread)
    hl2_failures = np.flatnonzero(hl2_spread < required_spread)
    metrics.update(
        {
            "atr14_max_visible": atr_max,
            "required_spread": float(required_spread),
            "min_close_six_ma_spread": float(close_spread.min()),
            "min_hl2_six_ma_spread": float(hl2_spread.min()),
            "close_spread_at_visible_start": float(close_spread[0]),
            "close_spread_at_visible_end": float(close_spread[-1]),
            "hl2_spread_at_visible_start": float(hl2_spread[0]),
            "hl2_spread_at_visible_end": float(hl2_spread[-1]),
            "close_spread_failure_indices": [int(visible_start + item) for item in close_failures],
            "hl2_spread_failure_indices": [int(visible_start + item) for item in hl2_failures],
        }
    )
    _append_reason(reasons, len(close_failures) > 0, "close_six_ma_spread_below_threshold")
    _append_reason(reasons, len(hl2_failures) > 0, "hl2_six_ma_spread_below_threshold")
    decision_close = times.iloc[-1] + pd.Timedelta(minutes=minutes)
    return _result(
        accepted=not reasons,
        reasons=reasons,
        metrics=metrics,
        visible_start_i=visible_start,
        visible_end_i=visible_end,
        decision_close_utc=decision_close.isoformat(),
    )
