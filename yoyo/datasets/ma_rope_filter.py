"""Causal, direction-agnostic six-moving-average rope scoring.

The renderer contract in ``yoyo.layers.l1_detection.data`` is the authority
for the six lines: SMA/EMA 20, 60 and 120.  This module keeps an exact local
copy of that small MA calculation so a dataset helper does not import a
business layer.  It adds descriptive metrics for a sample's decision bar:

* ``six_ma_bandwidth``: ``(max(MA) - min(MA)) / close``;
* ``pairwise_cross_density``: raw adjacent-bar order flips over the trailing
  interaction window (15 unordered pairs), plus a reference-normalized score;
* ``rope_persistence_rate``: fraction of the trailing bars whose six-line
  bandwidth is below the explicit reference ceiling;
* ``body_bundle_touch_rate`` / ``body_bundle_cross_rate``: causal trailing
  rates at which the current candle body touches or traverses the MA bundle;
* ``slope_consistency`` and ``startup_tightening`` as direction-neutral
  diagnostics;
* ``rope_score``: a transparent weighted ranking score, never a deletion gate.

Every feature at bar ``t`` is computed from bars ``<= t``.  A manifest row is
anchored at ``source_owner_global[1]`` (or the review sheet's ``cut_global``),
not at ``win_end``: the latter can include post-box bars.  The CLI resolves
stale manifest source names by symbol plus an exact index-to-time check and
reports every unresolved row instead of dropping it.

This is a research/ranking helper only.  It does not alter labels, data
splits, ``training_eligible``, production pointers, or any holdout artifact.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POSITIVE_MANIFEST = (
    PROJECT_ROOT / "datasets" / "owner_short_gold_center_v1" / "positive_manifest.jsonl"
)
DEFAULT_REVIEW_SHEET = PROJECT_ROOT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "kline_fetched"

# Project contract: all rows at or after this instant are outside the allowed
# pre-holdout research surface.  The resolver reads only the prefix required by
# a decision index, and rejects a prefix that contains a holdout timestamp.
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")

MA_PERIODS = (20, 60, 120)
# Exact ordering and arithmetic from the renderer's data.py contract.  Keep
# this local: yoyo.datasets must not reach upward into yoyo.layers.
SMA_MA_COLUMNS = tuple(f"sma{period}" for period in MA_PERIODS)
EMA_MA_COLUMNS = tuple(f"ema{period}" for period in MA_PERIODS)
SIX_MA_COLUMNS = SMA_MA_COLUMNS + EMA_MA_COLUMNS
REQUIRED_OHLC = ("open", "high", "low", "close")
PAIR_COUNT = len(SIX_MA_COLUMNS) * (len(SIX_MA_COLUMNS) - 1) // 2


class RopeFilterError(ValueError):
    """Base class for fail-closed input and feature errors."""


class MissingOHLCError(RopeFilterError):
    """Raised when the causal OHLC prefix is missing or non-finite."""


class InvalidOHLCError(RopeFilterError):
    """Raised when an OHLC prefix violates basic candle invariants."""


class InsufficientHistoryError(RopeFilterError):
    """Raised when a decision bar cannot support all configured features."""


class SourceResolutionError(RopeFilterError):
    """Raised when no unique symbol/index/time-consistent OHLC source exists."""


@dataclass(frozen=True)
class RopeFilterConfig:
    """Explicit, pre-registered ranking configuration.

    The default reference constants are visual/pre-registered geometry
    references, not values fitted on future returns or the 390-row audit.  All
    component *scores* are clipped to ``[0, 1]``.  ``slope_consistency`` is
    deliberately diagnostic only: a parallel expanding bundle must not earn
    positive rope-score weight.
    """

    persistence_window: int = 12
    interaction_window: int = 12
    slope_window: int = 5
    bandwidth_threshold: float = 0.0055
    cross_density_score_reference: float = 0.08
    body_touch_tolerance: float = 0.0005
    body_touch_rate_reference: float = 0.50
    body_cross_rate_reference: float = 0.20
    body_touch_weight: float = 0.70
    body_cross_weight: float = 0.30
    slope_zero_tolerance: float = 1e-6
    weight_bandwidth: float = 0.35
    weight_cross_density: float = 0.30
    weight_body_interaction: float = 0.25
    weight_persistence: float = 0.05
    weight_tightening: float = 0.05
    # Retained as an explicit compatibility/diagnostic field, but non-zero
    # values are rejected because slope alignment is not a rope criterion.
    weight_slope_consistency: float = 0.0

    def __post_init__(self) -> None:
        for name in ("persistence_window", "interaction_window", "slope_window"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in (
            "bandwidth_threshold",
            "cross_density_score_reference",
            "body_touch_tolerance",
            "body_touch_rate_reference",
            "body_cross_rate_reference",
            "slope_zero_tolerance",
        ):
            value = float(getattr(self, name))
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for name in (
            "cross_density_score_reference",
            "body_touch_rate_reference",
            "body_cross_rate_reference",
        ):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        interaction_weights = (float(self.body_touch_weight), float(self.body_cross_weight))
        if any(not np.isfinite(value) or value < 0 for value in interaction_weights):
            raise ValueError("body interaction weights must be finite and non-negative")
        if sum(interaction_weights) <= 0:
            raise ValueError("at least one body interaction weight must be positive")
        weight_names = (
            "weight_bandwidth",
            "weight_cross_density",
            "weight_body_interaction",
            "weight_persistence",
            "weight_tightening",
            "weight_slope_consistency",
        )
        weights = [float(getattr(self, name)) for name in weight_names]
        if any(not np.isfinite(value) or value < 0 for value in weights):
            raise ValueError("score weights must be finite and non-negative")
        if float(self.weight_slope_consistency) != 0.0:
            raise ValueError("weight_slope_consistency must be zero; slope is diagnostic only")
        if sum(weights[:-1]) <= 0:
            raise ValueError("at least one score weight must be positive")

    @property
    def score_weight_sum(self) -> float:
        return sum(
            float(getattr(self, name))
            for name in (
                "weight_bandwidth",
                "weight_cross_density",
                "weight_body_interaction",
                "weight_persistence",
                "weight_tightening",
            )
        )


@dataclass(frozen=True)
class SourceResolution:
    """Auditable result of symbol-candidate plus index-time source resolution."""

    symbol: str
    path: Path
    candidates: Tuple[Path, ...]
    recorded_source_names: Tuple[str, ...]
    resolution_mode: str
    decision_prefix_rows: int

    @property
    def recorded_path_match(self) -> bool:
        return self.path.name in set(self.recorded_source_names)


def _validate_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and numeric-coerce OHLC without dropping a row."""

    missing = [column for column in REQUIRED_OHLC if column not in frame.columns]
    if missing:
        raise MissingOHLCError(f"missing required OHLC columns: {missing}")
    out = frame.copy()
    for column in REQUIRED_OHLC:
        out[column] = pd.to_numeric(out[column], errors="coerce")
        values = out[column].to_numpy(dtype=float, copy=False)
        bad = ~np.isfinite(values)
        if bad.any():
            first = int(np.flatnonzero(bad)[0])
            raise MissingOHLCError(f"non-finite {column} at causal row {first}")

    if (out["close"] <= 0).any():
        first = int(np.flatnonzero((out["close"] <= 0).to_numpy())[0])
        raise InvalidOHLCError(f"close must be positive at causal row {first}")
    if (out["high"] < out["low"]).any():
        first = int(np.flatnonzero((out["high"] < out["low"]).to_numpy())[0])
        raise InvalidOHLCError(f"high < low at causal row {first}")
    for column in ("open", "close"):
        bad = (out[column] < out["low"]) | (out[column] > out["high"])
        if bad.any():
            first = int(np.flatnonzero(bad.to_numpy())[0])
            raise InvalidOHLCError(f"{column} outside high/low at causal row {first}")
    return out


def _with_canonical_timestamp_column(frame: pd.DataFrame) -> pd.DataFrame:
    """Give the renderer helper the ``ts`` column it expects, without lookahead."""

    out = frame.copy()
    if "ts" in out.columns:
        return out
    if "open_time" in out.columns:
        times = pd.to_datetime(out["open_time"], utc=True, errors="coerce")
        if times.isna().any():
            first = int(np.flatnonzero(times.isna().to_numpy())[0])
            raise MissingOHLCError(f"invalid open_time at causal row {first}")
        out["ts"] = (times.astype("int64") // 1_000_000).astype("int64")
        return out
    # Direct unit tests and reusable callers may provide OHLC only.  The
    # canonical add_mas helper only uses ts to recreate open_time; a synthetic
    # monotonic timestamp preserves its exact MA arithmetic without inventing
    # any price information.
    out["ts"] = np.arange(len(out), dtype="int64")
    return out


def _add_renderer_contract_mas(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the exact renderer SMA/EMA arithmetic without a layer import."""

    out = frame.copy()
    close = out["close"]
    for period in MA_PERIODS:
        out[f"sma{period}"] = close.rolling(period).mean()
        out[f"ema{period}"] = close.ewm(span=period, adjust=False).mean()
    mas = out.loc[:, list(SIX_MA_COLUMNS)]
    fast = out.loc[:, ["sma20", "ema20", "sma60", "ema60"]]
    safe_close = close.replace(0, pd.NA)
    out["fast_spread"] = (fast.max(axis=1) - fast.min(axis=1)) / safe_close
    out["full_spread"] = (mas.max(axis=1) - mas.min(axis=1)) / safe_close
    return out


def add_six_mas(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the renderer-contract SMA/EMA 20/60/120 columns.

    Source columns: ``open/high/low/close`` and optionally ``open_time`` or
    ``ts``.  The local implementation is an exact copy of
    ``yoyo.layers.l1_detection.data.add_mas``; this wrapper validates the
    causal OHLC input and supplies the timestamp column required by the
    renderer contract without importing that business layer.
    """

    validated = _validate_ohlc(frame)
    return _add_renderer_contract_mas(_with_canonical_timestamp_column(validated))


def _carried_sign(values: np.ndarray) -> np.ndarray:
    """Return signs where exact zero carries the last non-zero sign.

    Carrying through an equality treats a pass *through* equality as one
    crossing and avoids counting a zero plateau as repeated flips.  NaN resets
    the state, so missing MA values cannot create a crossing.
    """

    result = np.zeros(len(values), dtype=np.int8)
    previous = 0
    for index, value in enumerate(values):
        if not np.isfinite(value):
            previous = 0
            continue
        if value > 0:
            previous = 1
        elif value < 0:
            previous = -1
        result[index] = previous
    return result


def _pairwise_flip_events(mas: pd.DataFrame, valid: pd.Series) -> pd.Series:
    """Count causal pairwise order flips at each bar across all 15 pairs."""

    values = mas.to_numpy(dtype=float)
    events = np.zeros(len(mas), dtype=float)
    for left in range(values.shape[1]):
        for right in range(left + 1, values.shape[1]):
            signs = _carried_sign(values[:, left] - values[:, right])
            flip = np.zeros(len(mas), dtype=float)
            if len(mas) > 1:
                flip[1:] = (
                    (signs[1:] != signs[:-1])
                    & (signs[1:] != 0)
                    & (signs[:-1] != 0)
                ).astype(float)
            events += flip
    return pd.Series(events, index=mas.index, dtype="float64").where(valid)


def compute_rope_series(
    frame: pd.DataFrame,
    config: Optional[RopeFilterConfig] = None,
) -> pd.DataFrame:
    """Compute causal six-line metrics for every row in ``frame``.

    The output is indexed like the input and contains NaN until the longest
    required trailing history is available.  No future row is referenced by a
    row's value.  This function is useful for a whole pre-holdout OHLC prefix;
    ``compute_rope_metrics`` is the fail-closed single-decision-bar wrapper.
    """

    cfg = config or RopeFilterConfig()
    enriched = add_six_mas(frame)
    mas = enriched.loc[:, list(SIX_MA_COLUMNS)]
    close = enriched["close"].astype(float)
    valid = mas.notna().all(axis=1) & close.notna() & (close > 0)

    upper = mas.max(axis=1)
    lower = mas.min(axis=1)
    bandwidth = ((upper - lower) / close.abs()).where(valid)

    out = pd.DataFrame(index=enriched.index)
    out["six_ma_bandwidth"] = bandwidth
    # Names retained as explicit aliases for consumers using the existing
    # detection vocabulary; all are the same close-normalized quantity.
    out["six_line_bandwidth"] = bandwidth
    out["full_spread_close"] = bandwidth

    flip_events = _pairwise_flip_events(mas, valid)
    cross_count = flip_events.rolling(
        cfg.interaction_window, min_periods=cfg.interaction_window
    ).sum()
    cross_density = cross_count / float(PAIR_COUNT * cfg.interaction_window)
    out["pairwise_cross_events"] = flip_events
    out["pairwise_cross_count"] = cross_count
    out["pairwise_cross_density"] = cross_density
    out["rank_flip_density"] = cross_density
    crossing_score = (
        cross_density / float(cfg.cross_density_score_reference)
    ).clip(0.0, 1.0)
    out["pairwise_crossing_score"] = crossing_score

    # A body interaction is an intersection between the current candle body
    # interval and the current six-MA bundle interval.  A cross additionally
    # requires the body interval to span the complete bundle.  Both are
    # trailing rates and use only this bar's OHLC plus MAs through this bar.
    body_low = enriched[["open", "close"]].min(axis=1)
    body_high = enriched[["open", "close"]].max(axis=1)
    touch_margin = close * float(cfg.body_touch_tolerance)
    body_touch = (
        (body_high + touch_margin >= lower)
        & (body_low - touch_margin <= upper)
    ).where(valid)
    body_cross = (
        (body_low - touch_margin <= lower)
        & (body_high + touch_margin >= upper)
    ).where(valid)
    body_valid_window = body_touch.notna().rolling(
        cfg.interaction_window, min_periods=cfg.interaction_window
    ).sum() == cfg.interaction_window
    body_touch_rate = body_touch.astype(float).rolling(
        cfg.interaction_window, min_periods=cfg.interaction_window
    ).mean().where(body_valid_window)
    body_cross_rate = body_cross.astype(float).rolling(
        cfg.interaction_window, min_periods=cfg.interaction_window
    ).mean().where(body_valid_window)
    body_touch_score = (
        body_touch_rate / float(cfg.body_touch_rate_reference)
    ).clip(0.0, 1.0)
    body_cross_score = (
        body_cross_rate / float(cfg.body_cross_rate_reference)
    ).clip(0.0, 1.0)
    body_weight_sum = float(cfg.body_touch_weight + cfg.body_cross_weight)
    body_interaction_rate = (
        cfg.body_touch_weight * body_touch_rate
        + cfg.body_cross_weight * body_cross_rate
    ) / body_weight_sum
    body_interaction_score = (
        cfg.body_touch_weight * body_touch_score
        + cfg.body_cross_weight * body_cross_score
    ) / body_weight_sum
    out["body_bundle_touch_events"] = body_touch.astype(float)
    out["body_bundle_cross_events"] = body_cross.astype(float)
    out["body_bundle_touch_rate"] = body_touch_rate
    out["body_bundle_cross_rate"] = body_cross_rate
    out["body_touch_score"] = body_touch_score
    out["body_cross_score"] = body_cross_score
    out["body_bundle_interaction_rate"] = body_interaction_rate
    out["body_bundle_interaction_score"] = body_interaction_score

    dense = (bandwidth <= cfg.bandwidth_threshold).astype(float).where(valid)
    valid_persistence = (
        valid.astype(float)
        .rolling(cfg.persistence_window, min_periods=cfg.persistence_window)
        .sum()
        == cfg.persistence_window
    )
    persistence = dense.rolling(
        cfg.persistence_window, min_periods=cfg.persistence_window
    ).mean().where(valid_persistence)
    out["rope_persistence_rate"] = persistence

    prior_bandwidth = bandwidth.shift(cfg.persistence_window)
    bandwidth_change = bandwidth - prior_bandwidth
    out["startup_bandwidth_change"] = bandwidth_change
    out["startup_tightening"] = -bandwidth_change

    prior_mas = mas.shift(cfg.slope_window)
    relative_slope = mas / prior_mas - 1.0
    relative_slope = relative_slope / float(cfg.slope_window)
    slope_valid = relative_slope.notna().all(axis=1) & (prior_mas > 0).all(axis=1)
    positive = (relative_slope > cfg.slope_zero_tolerance).sum(axis=1) / len(SIX_MA_COLUMNS)
    negative = (relative_slope < -cfg.slope_zero_tolerance).sum(axis=1) / len(SIX_MA_COLUMNS)
    slope_consistency = pd.concat([positive, negative], axis=1).max(axis=1).where(slope_valid)
    out["slope_consistency"] = slope_consistency

    tightness_score = (1.0 - bandwidth / max(cfg.bandwidth_threshold, np.finfo(float).eps)).clip(0.0, 1.0)
    tightening_score = (
        0.5
        + 0.5 * (-bandwidth_change / max(cfg.bandwidth_threshold, np.finfo(float).eps))
    ).clip(0.0, 1.0)
    out["bandwidth_tightness_score"] = tightness_score
    out["startup_tightening_score"] = tightening_score
    out["rope_persistence_score"] = persistence

    score = (
        cfg.weight_bandwidth * tightness_score
        + cfg.weight_cross_density * crossing_score
        + cfg.weight_body_interaction * body_interaction_score
        + cfg.weight_persistence * persistence
        + cfg.weight_tightening * tightening_score
    ) / cfg.score_weight_sum
    required = pd.concat(
        [
            bandwidth,
            crossing_score,
            persistence,
            body_interaction_score,
            slope_consistency,
            tightening_score,
        ],
        axis=1,
    ).notna().all(axis=1)
    out["rope_score"] = score.where(required)
    out["metrics_ready"] = required
    return out


_REQUIRED_METRIC_COLUMNS = (
    "six_ma_bandwidth",
    "pairwise_cross_density",
    "pairwise_crossing_score",
    "rope_persistence_rate",
    "body_bundle_touch_rate",
    "body_bundle_interaction_score",
    "slope_consistency",
    "rope_score",
)


def _metrics_from_series_row(
    row: pd.Series,
    *,
    decision_bar: int,
    causal_rows_used: int,
) -> Dict[str, Any]:
    """Convert one already-computed causal series row into public metrics."""

    missing = [
        name
        for name in _REQUIRED_METRIC_COLUMNS
        if name not in row or not np.isfinite(float(row[name]))
    ]
    if missing:
        raise InsufficientHistoryError(
            f"decision bar {decision_bar} lacks finite metrics: {missing}; "
            f"causal_rows={causal_rows_used}"
        )
    result: Dict[str, Any] = {
        "decision_bar": int(decision_bar),
        "future_bars_used": 0,
        "causal_rows_used": int(causal_rows_used),
    }
    for key, value in row.to_dict().items():
        if key == "metrics_ready":
            result[key] = bool(value)
        elif isinstance(value, (bool, np.bool_)):
            result[key] = bool(value)
        elif pd.isna(value):
            result[key] = None
        else:
            result[key] = float(value)
    return result


def compute_rope_metrics(
    frame: pd.DataFrame,
    decision_bar: Optional[int] = None,
    config: Optional[RopeFilterConfig] = None,
) -> Dict[str, Any]:
    """Return one fail-closed metric row at a causal decision bar.

    ``frame`` may contain later bars, but they are sliced away *before* moving
    averages and rolling features are calculated.  Thus appending or mutating
    future OHLC cannot change the returned values.
    """

    if len(frame) == 0:
        raise InsufficientHistoryError("empty OHLC frame")
    if decision_bar is None:
        decision_bar = len(frame) - 1
    if not isinstance(decision_bar, (int, np.integer)) or isinstance(decision_bar, bool):
        raise ValueError("decision_bar must be an integer positional index")
    decision_bar = int(decision_bar)
    if decision_bar < 0 or decision_bar >= len(frame):
        raise ValueError(f"decision_bar {decision_bar} outside frame of length {len(frame)}")

    causal = frame.iloc[: decision_bar + 1].copy()
    series = compute_rope_series(causal, config=config)
    return _metrics_from_series_row(
        series.iloc[-1],
        decision_bar=decision_bar,
        causal_rows_used=decision_bar + 1,
    )


def _timestamp_column(frame: pd.DataFrame) -> pd.Series:
    """Parse source timestamps without dropping or reordering rows."""

    if "open_time" in frame.columns:
        times = pd.to_datetime(frame["open_time"], utc=True, errors="coerce")
    elif "ts" in frame.columns:
        values = pd.to_numeric(frame["ts"], errors="coerce")
        times = pd.to_datetime(values, unit="ms", utc=True, errors="coerce")
    else:
        raise MissingOHLCError("source CSV has neither open_time nor ts")
    if times.isna().any():
        first = int(np.flatnonzero(times.isna().to_numpy())[0])
        raise MissingOHLCError(f"invalid source timestamp at row {first}")
    return times


def load_ohlc_prefix(
    path: Path,
    max_index: int,
    holdout_start: pd.Timestamp = HOLDOUT_START,
) -> pd.DataFrame:
    """Read only rows ``0..max_index`` and reject invalid/holdout prefixes."""

    if max_index < 0:
        raise SourceResolutionError(f"negative source index: {max_index}")
    if not path.is_file():
        raise SourceResolutionError(f"OHLC source does not exist: {path}")
    try:
        frame = pd.read_csv(path, nrows=max_index + 1)
    except Exception as exc:  # pandas parser errors are source failures
        raise MissingOHLCError(f"cannot read OHLC source {path}: {exc}") from exc
    if len(frame) <= max_index:
        raise MissingOHLCError(
            f"OHLC source {path} has {len(frame)} rows, needs index {max_index}"
        )
    frame = _validate_ohlc(frame)
    times = _timestamp_column(frame)
    if not times.is_monotonic_increasing or times.duplicated().any():
        raise InvalidOHLCError(f"source timestamps are not strictly increasing: {path}")
    if (times >= holdout_start).any():
        first = int(np.flatnonzero((times >= holdout_start).to_numpy())[0])
        raise SourceResolutionError(
            f"holdout timestamp reached in causal prefix at row {first}: {path}"
        )
    frame["open_time"] = times
    return frame.reset_index(drop=True)


def _as_nonnegative_index(value: Any, field: str) -> Tuple[int, str]:
    """Convert an anchor index without allowing Python negative-index lookup."""

    if isinstance(value, (bool, np.bool_)):
        raise SourceResolutionError(f"{field} must be a non-negative integer")
    try:
        index = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SourceResolutionError(f"{field} must be a non-negative integer") from exc
    if isinstance(value, (float, np.floating)) and not float(value).is_integer():
        raise SourceResolutionError(f"{field} must be a non-negative integer")
    if index < 0:
        raise SourceResolutionError(f"{field} must be a non-negative integer")
    return index, field


def _row_decision_bar(row: Mapping[str, Any]) -> Tuple[int, str]:
    """Resolve the explicit causal anchor and report which field supplied it."""

    if row.get("decision_bar") is not None:
        return _as_nonnegative_index(row["decision_bar"], "decision_bar")
    if row.get("source_owner_global") is not None:
        values = row["source_owner_global"]
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise SourceResolutionError("source_owner_global must have two indices")
        return _as_nonnegative_index(values[1], "source_owner_global[1]")
    if row.get("cut_global") not in (None, ""):
        return _as_nonnegative_index(row["cut_global"], "cut_global")
    if row.get("core_global") is not None:
        values = row["core_global"]
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise SourceResolutionError("core_global must have two indices")
        return _as_nonnegative_index(values[1], "core_global[1]")
    raise SourceResolutionError("missing causal decision index")


def _row_decision_time(row: Mapping[str, Any]) -> pd.Timestamp:
    value = (
        row.get("decision_time")
        or row.get("source_owner_cut_time")
        or row.get("cut_time")
        or row.get("end_time")
    )
    if value in (None, ""):
        raise SourceResolutionError("missing causal decision time")
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp


def _source_name(value: Any) -> str:
    return Path(str(value)).name if value not in (None, "") else ""


def resolve_symbol_source(
    rows: Sequence[Mapping[str, Any]],
    data_root: Path = DEFAULT_DATA_ROOT,
    holdout_start: pd.Timestamp = HOLDOUT_START,
) -> Tuple[SourceResolution, pd.DataFrame]:
    """Resolve one symbol using candidates *and* every row's index/time pair.

    The recorded ``source_csv`` is evidence, not authority: old suffixes can
    be stale after a continued fetch.  A candidate is accepted only if its
    timestamp at each requested decision index equals that row's recorded
    decision time.  Zero or multiple matches fail closed.
    """

    if not rows:
        raise SourceResolutionError("cannot resolve an empty symbol group")
    symbol = str(rows[0].get("symbol") or "")
    if not symbol or any(str(row.get("symbol") or "") != symbol for row in rows):
        raise SourceResolutionError("source rows must share one non-empty symbol")
    requests: List[Tuple[int, pd.Timestamp]] = []
    for row in rows:
        index, _ = _row_decision_bar(row)
        timestamp = _row_decision_time(row)
        if timestamp >= holdout_start:
            raise SourceResolutionError(f"holdout decision row refused: {timestamp}")
        requests.append((index, timestamp))
    max_index = max(index for index, _ in requests)

    data_root = Path(data_root)
    candidates = tuple(sorted(data_root.glob(f"okx_{symbol}_15m_*.csv")))
    if not candidates:
        raise SourceResolutionError(
            f"no OHLC candidate for symbol {symbol} under {data_root}"
        )

    matches: List[Tuple[Path, pd.DataFrame]] = []
    diagnostics: Dict[str, str] = {}
    for candidate in candidates:
        try:
            prefix = load_ohlc_prefix(candidate, max_index, holdout_start)
            mismatches = []
            for row, (index, expected) in zip(rows, requests):
                actual = pd.Timestamp(prefix["open_time"].iloc[index])
                if actual != expected:
                    mismatches.append(
                        f"{row.get('sample_id') or row.get('box_id')}: "
                        f"idx={index} actual={actual.isoformat()} expected={expected.isoformat()}"
                    )
            if mismatches:
                diagnostics[candidate.name] = "; ".join(mismatches[:2])
                continue
            matches.append((candidate, prefix))
        except RopeFilterError as exc:
            diagnostics[candidate.name] = str(exc)

    if len(matches) != 1:
        diagnostic_text = "; ".join(
            f"{name}: {reason}" for name, reason in sorted(diagnostics.items())
        )
        if len(matches) > 1:
            diagnostic_text = "multiple candidates matched exact index/time"
        raise SourceResolutionError(
            f"symbol {symbol} did not resolve to exactly one OHLC source "
            f"(candidates={len(candidates)}, matches={len(matches)}): {diagnostic_text}"
        )

    path, prefix = matches[0]
    recorded_names = tuple(sorted({_source_name(row.get("source_csv")) for row in rows if _source_name(row.get("source_csv"))}))
    if recorded_names and all(path.name == name for name in recorded_names):
        mode = "recorded_path_index_time_verified"
    elif recorded_names:
        mode = "symbol_candidate_index_time_verified_recorded_name_stale"
    else:
        mode = "symbol_candidate_index_time_verified"
    return (
        SourceResolution(
            symbol=symbol,
            path=path,
            candidates=candidates,
            recorded_source_names=recorded_names,
            resolution_mode=mode,
            decision_prefix_rows=len(prefix),
        ),
        prefix,
    )


def read_positive_manifest(path: Path = DEFAULT_POSITIVE_MANIFEST) -> List[Dict[str, Any]]:
    """Read JSONL without dropping malformed/blank accounting silently."""

    rows: List[Dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RopeFilterError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise RopeFilterError(f"manifest row {line_number} is not an object")
            rows.append(value)
    return rows


def read_review_sheet(path: Path = DEFAULT_REVIEW_SHEET) -> List[Dict[str, Any]]:
    """Convert supported review sheets without dropping an input row.

    Supported schemas are the 2,525-row Owner box sheet
    (``box_id/cut_global/cut_time/owner_side``) and the short-tip sheet
    (``stem/symbol/tip_idx/tip_time/owner_keep``).  The latter's
    ``owner_keep`` is review metadata, not ``owner_side``; keep/drop/skip and
    unreviewed rows all remain in the output for independent evaluation.
    """

    rows: List[Dict[str, Any]] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        box_schema = {"box_id", "symbol", "cut_global", "cut_time"}
        tip_schema = {"symbol", "tip_idx", "tip_time"}
        if box_schema.issubset(fields):
            schema = "owner_box"
        elif tip_schema.issubset(fields) and ({"stem", "i"} & fields):
            schema = "short_tip"
        else:
            raise RopeFilterError(
                f"unsupported review sheet schema at {path}; fields={sorted(fields)}"
            )

        for line_number, raw in enumerate(reader, start=2):
            symbol = str(raw.get("symbol") or "")
            if not symbol:
                raise RopeFilterError(f"review sheet row {line_number} lacks symbol")
            if schema == "owner_box":
                box_id = str(raw.get("box_id") or "")
                if not box_id:
                    raise RopeFilterError(f"review sheet row {line_number} lacks box_id")
                if raw.get("cut_global") in (None, "") or not raw.get("cut_time"):
                    raise RopeFilterError(
                        f"review sheet row {line_number} lacks cut_global/cut_time"
                    )
                rows.append(
                    {
                        "sample_id": box_id,
                        "box_id": box_id,
                        "symbol": symbol,
                        "owner_side": str(raw.get("owner_side") or ""),
                        "decision_bar": int(raw["cut_global"]),
                        "decision_time": raw["cut_time"],
                        "cut_global": int(raw["cut_global"]),
                        "cut_time": raw["cut_time"],
                        "source_csv": raw.get("source_csv") or None,
                        "review_schema": schema,
                        "population": "review_sheet",
                    }
                )
                continue

            sample_id = str(raw.get("stem") or raw.get("i") or "")
            if not sample_id:
                raise RopeFilterError(f"review sheet row {line_number} lacks stem/i")
            if raw.get("tip_idx") in (None, "") or not raw.get("tip_time"):
                raise RopeFilterError(
                    f"review sheet row {line_number} lacks tip_idx/tip_time"
                )
            owner_keep = str(raw.get("owner_keep") or "").strip().lower()
            review_status = owner_keep if owner_keep in {"keep", "drop", "skip"} else "unreviewed"
            rows.append(
                {
                    "sample_id": sample_id,
                    "stem": str(raw.get("stem") or ""),
                    "i": str(raw.get("i") or ""),
                    "symbol": symbol,
                    "owner_side": str(raw.get("owner_side") or ""),
                    "owner_keep": owner_keep,
                    "owner_note": str(raw.get("owner_note") or ""),
                    "review_status": review_status,
                    "decision_bar": int(raw["tip_idx"]),
                    "decision_time": raw["tip_time"],
                    "tip_idx": int(raw["tip_idx"]),
                    "tip_time": raw["tip_time"],
                    "source_csv": raw.get("source_csv") or None,
                    "review_schema": schema,
                    "population": "review_sheet",
                }
            )
    return rows


def _base_result_row(row: Mapping[str, Any], population: str) -> Dict[str, Any]:
    decision_bar: Optional[int]
    anchor: Optional[str]
    decision_time: Optional[str]
    try:
        decision_bar, anchor = _row_decision_bar(row)
    except (TypeError, ValueError, RopeFilterError):
        decision_bar, anchor = None, None
    try:
        decision_time = _row_decision_time(row).isoformat()
    except (TypeError, ValueError, RopeFilterError):
        decision_time = None
    owner_side = row.get("owner_side")
    if owner_side in (None, "") and population == "positive_manifest":
        # The frozen population contract is owner_side=short.  Keep this as
        # metadata only; no feature or score uses direction.
        owner_side = "short"
    result = {
        "population": population,
        "sample_id": str(row.get("sample_id") or row.get("box_id") or ""),
        "symbol": str(row.get("symbol") or ""),
        "owner_side": owner_side,
        "recorded_source_csv": row.get("source_csv"),
        "decision_bar": decision_bar,
        "decision_anchor": anchor,
        "decision_time": decision_time,
        "status": "unscored",
        "reason": None,
        "resolved_source_csv": None,
        "source_resolution": None,
        "recorded_source_path_stale": None,
        "rank": None,
    }
    # Preserve review labels and schema-specific metadata beside the common
    # metrics.  None of these fields participates in scoring.
    for key in (
        "box_id",
        "stem",
        "i",
        "owner_keep",
        "owner_note",
        "review_status",
        "review_schema",
        "tip_idx",
        "tip_time",
    ):
        if key in row:
            result[key] = row[key]
    return result


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def score_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    population: str,
    data_root: Path = DEFAULT_DATA_ROOT,
    config: Optional[RopeFilterConfig] = None,
    holdout_start: pd.Timestamp = HOLDOUT_START,
) -> Dict[str, Any]:
    """Score every row and return a ranking-only report with explicit failures."""

    cfg = config or RopeFilterConfig()
    results = [_base_result_row(row, population) for row in rows]
    by_symbol: Dict[str, List[Tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(rows):
        result = results[index]
        if result["decision_bar"] is None:
            result["status"] = "missing_decision_bar"
            result["reason"] = "no valid causal decision index"
            continue
        if result["decision_time"] is None:
            result["status"] = "missing_decision_time"
            result["reason"] = "no timezone-aware causal decision time"
            continue
        if pd.Timestamp(result["decision_time"]) >= holdout_start:
            result["status"] = "holdout_refused"
            result["reason"] = f"decision_time >= holdout_start ({holdout_start.isoformat()})"
            continue
        symbol = str(row.get("symbol") or "")
        if not symbol:
            result["status"] = "missing_symbol"
            result["reason"] = "source symbol is blank"
            continue
        by_symbol[symbol].append((index, row))

    series_computations = 0
    for symbol, grouped in sorted(by_symbol.items()):
        source_rows = [row for _, row in grouped]
        try:
            resolution, prefix = resolve_symbol_source(
                source_rows, data_root=data_root, holdout_start=holdout_start
            )
        except SourceResolutionError as exc:
            reason = str(exc)
            lower = reason.lower()
            if "multiple" in lower or "exactly one" in lower:
                status = "ambiguous_or_mismatched_ohlc_source"
            elif "holdout" in lower:
                status = "holdout_refused"
            else:
                status = "missing_ohlc_source"
            for result_index, _ in grouped:
                results[result_index]["status"] = status
                results[result_index]["reason"] = reason
            continue

        # One validated prefix per symbol is enough: rolling and EWM values at
        # index t depend only on rows <= t.  Recomputing from zero for every
        # decision bar would make the 2,525-row review sheet unnecessarily
        # quadratic while adding no causal protection.
        try:
            series = compute_rope_series(prefix, config=cfg)
        except MissingOHLCError as exc:
            for result_index, _ in grouped:
                results[result_index]["status"] = "missing_ohlc_data"
                results[result_index]["reason"] = str(exc)
            continue
        except RopeFilterError as exc:
            for result_index, _ in grouped:
                results[result_index]["status"] = "invalid_ohlc_data"
                results[result_index]["reason"] = str(exc)
            continue
        series_computations += 1

        for result_index, row in grouped:
            result = results[result_index]
            decision_bar, _ = _row_decision_bar(row)
            result["resolved_source_csv"] = _display_path(
                resolution.path, Path(data_root).parents[1]
            )
            result["source_resolution"] = resolution.resolution_mode
            result["recorded_source_path_stale"] = bool(
                resolution.recorded_source_names and resolution.path.name not in resolution.recorded_source_names
            )
            try:
                if decision_bar < 0 or decision_bar >= len(series):
                    raise InsufficientHistoryError(
                        f"decision bar {decision_bar} outside verified prefix of length {len(series)}"
                    )
                metrics = _metrics_from_series_row(
                    series.iloc[decision_bar],
                    decision_bar=decision_bar,
                    causal_rows_used=decision_bar + 1,
                )
            except InsufficientHistoryError as exc:
                result["status"] = "insufficient_history"
                result["reason"] = str(exc)
                continue
            except MissingOHLCError as exc:
                result["status"] = "missing_ohlc_data"
                result["reason"] = str(exc)
                continue
            except RopeFilterError as exc:
                result["status"] = "invalid_ohlc_data"
                result["reason"] = str(exc)
                continue
            for key, value in metrics.items():
                if key not in {"decision_bar", "future_bars_used", "causal_rows_used"}:
                    result[key] = value
            result["status"] = "scored"
            result["reason"] = None

    rankable = [result for result in results if result["status"] == "scored"]
    rankable.sort(key=lambda result: (-float(result["rope_score"]), result["sample_id"]))
    for rank, result in enumerate(rankable, start=1):
        result["rank"] = rank

    status_counts = Counter(str(result["status"]) for result in results)
    resolution_counts = Counter(
        str(result["source_resolution"])
        for result in results
        if result["source_resolution"] is not None
    )
    missing_rows = [
        {
            "sample_id": result["sample_id"],
            "symbol": result["symbol"],
            "status": result["status"],
            "reason": result["reason"],
        }
        for result in results
        if result["status"] != "scored"
    ]
    return {
        "schema_version": 1,
        "population": population,
        "n_rows": len(results),
        "n_scored": len(rankable),
        "n_symbol_groups": len(by_symbol),
        "series_computations": series_computations,
        "series_computation_policy": "one_verified_causal_prefix_per_symbol",
        "status_counts": dict(sorted(status_counts.items())),
        "source_resolution_counts": dict(sorted(resolution_counts.items())),
        "missing_or_refused_rows": missing_rows,
        "config": asdict(cfg),
        "direction_agnostic": True,
        "selection_policy": "ranking_only_no_rows_removed",
        "holdout_read": False,
        "training_eligible_changed": False,
        "rows": results,
        "ranking": rankable,
    }


def rank_inputs(
    *,
    positive_manifest: Optional[Path] = DEFAULT_POSITIVE_MANIFEST,
    review_sheet: Optional[Path] = None,
    data_root: Path = DEFAULT_DATA_ROOT,
    config: Optional[RopeFilterConfig] = None,
    holdout_start: pd.Timestamp = HOLDOUT_START,
) -> Dict[str, Any]:
    """Score positive-manifest and optional Owner review populations together."""

    reports: Dict[str, Dict[str, Any]] = {}
    if positive_manifest is not None:
        reports["positive_manifest"] = score_rows(
            read_positive_manifest(Path(positive_manifest)),
            population="positive_manifest",
            data_root=Path(data_root),
            config=config,
            holdout_start=holdout_start,
        )
    if review_sheet is not None:
        reports["review_sheet"] = score_rows(
            read_review_sheet(Path(review_sheet)),
            population="review_sheet",
            data_root=Path(data_root),
            config=config,
            holdout_start=holdout_start,
        )
    if not reports:
        raise RopeFilterError("at least one input population is required")
    return {
        "schema_version": 1,
        "populations": reports,
        "n_rows": sum(report["n_rows"] for report in reports.values()),
        "n_scored": sum(report["n_scored"] for report in reports.values()),
        "direction_agnostic": True,
        "decision_anchor_contract": "owner_end_or_review_cut_global; never win_end",
        "holdout_start": holdout_start.isoformat(),
        "holdout_read": False,
        "training_eligible_changed": False,
        "selection_policy": "ranking_only_no_rows_removed",
    }


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy scalars and NaNs into strict JSON values."""

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positive-manifest", type=Path, default=DEFAULT_POSITIVE_MANIFEST)
    parser.add_argument(
        "--review-sheet",
        type=Path,
        default=None,
        help="optional review_sheet.csv; scores every row and preserves owner_side",
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out", type=Path, default=None, help="optional JSON output path")
    parser.add_argument(
        "--holdout-start",
        default=HOLDOUT_START.isoformat(),
        help="UTC guard; rows at or after it are refused and never read",
    )
    parser.add_argument("--bandwidth-threshold", type=float, default=0.0055)
    parser.add_argument("--persistence-window", type=int, default=12)
    parser.add_argument("--interaction-window", type=int, default=12)
    parser.add_argument("--slope-window", type=int, default=5)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; stdout and --out contain all rows, not only winners."""

    args = build_parser().parse_args(argv)
    holdout_start = pd.Timestamp(args.holdout_start)
    if holdout_start.tzinfo is None:
        holdout_start = holdout_start.tz_localize("UTC")
    else:
        holdout_start = holdout_start.tz_convert("UTC")
    config = RopeFilterConfig(
        bandwidth_threshold=args.bandwidth_threshold,
        persistence_window=args.persistence_window,
        interaction_window=args.interaction_window,
        slope_window=args.slope_window,
    )
    report = _json_safe(
        rank_inputs(
            positive_manifest=args.positive_manifest,
            review_sheet=args.review_sheet,
            data_root=args.data_root,
            config=config,
            holdout_start=holdout_start,
        )
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
