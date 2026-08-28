"""Strict second-pass ranking for the 10,000 weak MA-launch positives.

The source rows are completed-history 15-minute examples.  For each exact
four/five-bar core this module reads only the pre-holdout OHLCV prefix and uses
``open/high/low/close``, Pine-RMA ATR14, and SMA/EMA 20/60/120.  Scalar gates
measure the six-line bundle topology, twelve-bar pre-core quietness, price/bundle
contact, wick/reversal cleanliness, post-core release, and boundary cleanliness.
A direction-normalized multivariate profile is then compared with the Owner's
#44 perfect and #42 good references by lock-step Euclidean,
Sakoe-Chiba constrained DTW, and derivative-DTW.  SHORT multiplies directional
channels by -1; time is never reversed.

The five bars after the core are descriptive historical retrieval inputs, not
causal live features.  Outputs are ranked ``perfect-candidate`` examples only:
they are not Gold, do not change the existing dataset or labels, and remain
``training_eligible=false`` and ``production_eligible=false``.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    add_candidate_features,
    read_preholdout_prefix,
    sha256_file,
)
from yoyo.datasets.ma_launch_owner_recrop_review import verify_builder_committed
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS


ROOT = Path(__file__).resolve().parents[2]
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
EXPERIMENT_ID = "exp-15m-ma-launch-owner-perfect-filter10000-v1"
DEFAULT_PREREG = (
    ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
)
DEFAULT_OUTPUT = DEFAULT_PREREG.parent / "results"


class PerfectFilterError(ValueError):
    """Raised when lineage, chronology, geometry, or output contracts drift."""


@dataclass(frozen=True)
class ShapeProfile:
    """One exact core's scalar metrics and aligned multivariate sequence."""

    metrics: dict[str, float]
    sequence: np.ndarray
    core_start_i: int
    core_end_i: int


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _repo_path(value: object) -> Path:
    raw = Path(str(value))
    path = raw.resolve() if raw.is_absolute() else (ROOT / raw).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise PerfectFilterError(f"path escapes repository: {value}") from exc
    return path


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(np.clip(value, low, high))


def _row_indices(row: Mapping[str, Any]) -> tuple[int, int, int]:
    if row.get("source_core_start_i") is not None:
        start_i = int(row["source_core_start_i"])
        end_i = int(row["source_core_end_i"])
        anchor_i = int(row["source_comparison_anchor_i"])
    elif row.get("core_start_source_i") is not None:
        start_i = int(row["core_start_source_i"])
        end_i = int(row["core_end_source_i"])
        start_anchor = start_i - int(row["core_start_offset"])
        end_anchor = end_i - int(row["core_end_offset"])
        if start_anchor != end_anchor:
            raise PerfectFilterError("reference core offsets imply different anchors")
        anchor_i = start_anchor
    else:
        anchor_i = int(row["source_anchor_i"])
        start_i = anchor_i + int(row["core_start_offset"])
        end_i = anchor_i + int(row["core_end_offset"])
    return start_i, end_i, anchor_i


def _carried_sign(values: np.ndarray) -> np.ndarray:
    result = np.zeros(len(values), dtype=np.int8)
    previous = 0
    for index, value in enumerate(values):
        if not np.isfinite(value):
            previous = 0
        elif value > 0.0:
            previous = 1
        elif value < 0.0:
            previous = -1
        result[index] = previous
    return result


def _resample(values: np.ndarray, size: int = 5) -> np.ndarray:
    if values.ndim != 1 or len(values) not in {4, 5}:
        raise PerfectFilterError("core resampling requires four or five values")
    return np.interp(
        np.linspace(0.0, 1.0, size),
        np.linspace(0.0, 1.0, len(values)),
        values,
    )


def _pack_channel(values: np.ndarray, start_i: int, end_i: int) -> np.ndarray:
    return np.r_[
        values[start_i - 12 : start_i],
        _resample(values[start_i : end_i + 1]),
        values[end_i + 1 : end_i + 6],
    ]


def extract_profile(
    frame: pd.DataFrame,
    row: Mapping[str, Any],
    *,
    core_shift: int = 0,
) -> ShapeProfile:
    """Extract one profile from the exact row geometry.

    Source columns and windows: OHLC and six renderer-contract moving averages
    from ``core_start-12`` through ``core_end+5``; ATR14 is read at
    ``comparison_anchor``.  ``core_shift`` exists only for the pre-registered
    boundary null and shifts only the two core edges; the original ATR anchor
    stays fixed so the paired null changes one variable.  No row at or after
    :data:`HOLDOUT_START` can be present in ``frame`` because the caller uses
    ``read_preholdout_prefix``.
    """

    start_i, end_i, anchor_i = _row_indices(row)
    start_i += int(core_shift)
    end_i += int(core_shift)
    core_len = end_i - start_i + 1
    if core_len not in {4, 5}:
        raise PerfectFilterError(f"unsupported core length {core_len}")
    if start_i - 12 < 0 or end_i + 5 >= len(frame) or anchor_i >= len(frame):
        raise PerfectFilterError("profile window falls outside pre-holdout frame")
    if pd.Timestamp(frame["open_time"].iloc[end_i + 5]) >= HOLDOUT_START:
        raise PerfectFilterError("profile touches holdout")
    time_stop = max(end_i + 5, anchor_i)
    times = pd.to_datetime(
        frame["open_time"].iloc[start_i - 12 : time_stop + 1], utc=True
    )
    if len(times) < 2 or not (times.diff().iloc[1:] == pd.Timedelta(minutes=15)).all():
        raise PerfectFilterError("profile crosses a non-15-minute source gap")

    sign = 1.0 if str(row["direction"]) == "LONG" else -1.0
    if str(row["direction"]) not in {"LONG", "SHORT"}:
        raise PerfectFilterError(f"unsupported direction: {row['direction']}")
    atr = float(frame["atr"].iloc[anchor_i])
    if not np.isfinite(atr) or atr <= 0.0:
        raise PerfectFilterError("profile ATR is missing or non-positive")

    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    mas = frame.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
    inspected = np.concatenate(
        [
            open_[start_i - 12 : end_i + 6],
            high[start_i - 12 : end_i + 6],
            low[start_i - 12 : end_i + 6],
            close[start_i - 12 : end_i + 6],
            mas[start_i - 12 : end_i + 6].ravel(),
        ]
    )
    if not np.isfinite(inspected).all():
        raise PerfectFilterError("profile contains non-finite OHLC or moving averages")

    core = np.arange(start_i, end_i + 1)
    pre = np.arange(start_i - 12, start_i)
    post = np.arange(end_i + 1, end_i + 6)
    bandwidth_bps = (mas.max(axis=1) - mas.min(axis=1)) / close * 10_000.0
    bandwidth_atr = (mas.max(axis=1) - mas.min(axis=1)) / atr
    pre_bandwidth = bandwidth_bps[pre]
    pre_median = float(np.median(pre_bandwidth))
    if not np.isfinite(pre_median) or pre_median <= 0.0:
        raise PerfectFilterError("pre-core moving-average bandwidth is invalid")

    pairwise_order_flips = 0
    pairwise_pairs_flipped = 0
    for left in range(len(SIX_MA_COLUMNS)):
        for right in range(left + 1, len(SIX_MA_COLUMNS)):
            signs = _carried_sign(
                mas[start_i - 1 : end_i + 1, left]
                - mas[start_i - 1 : end_i + 1, right]
            )
            flips = (
                (signs[1:] != signs[:-1])
                & (signs[1:] != 0)
                & (signs[:-1] != 0)
            )
            count = int(flips.sum())
            pairwise_order_flips += count
            pairwise_pairs_flipped += int(count > 0)

    ma_low = mas[core].min(axis=1)
    ma_high = mas[core].max(axis=1)
    body_low = np.minimum(open_[core], close[core])
    body_high = np.maximum(open_[core], close[core])
    candle_touch = (high[core] >= ma_low) & (low[core] <= ma_high)
    body_touch = (
        (body_high + 0.05 * atr >= ma_low)
        & (body_low - 0.05 * atr <= ma_high)
    )
    close_distance = np.maximum(
        np.maximum(ma_low - close[core], close[core] - ma_high), 0.0
    ) / atr
    post_progress = sign * (close[end_i + 1 : end_i + 6] - close[end_i]) / atr
    post_steps = sign * np.diff(close[end_i : end_i + 6]) / atr
    post_bodies = sign * (close[post] - open_[post]) / atr
    aligned_core_bodies = sign * (close[core] - open_[core]) / atr
    core_body_abs = np.abs(close[core] - open_[core]) / atr
    core_body_max = float(core_body_abs.max())
    post_body_max = float(np.max(np.abs(close[post] - open_[post])) / atr)
    box_height_norm = float(row.get("box", {}).get("h_norm", 0.0))

    pre_bodies = np.abs(close[pre] - open_[pre]) / atr
    pre_steps = np.diff(close[pre])
    pre_aligned_from_start = sign * (close[pre] - close[start_i - 12]) / atr
    pre_last3_progress = sign * (close[start_i - 1] - close[start_i - 4]) / atr

    ma_center = mas.mean(axis=1)
    core_ma_envelope = float((mas[core].max() - mas[core].min()) / atr)
    core_ma_slope = float(
        sign * (ma_center[end_i] - ma_center[start_i]) / atr / max(core_len - 1, 1)
    )
    core_width_ratio = float(
        bandwidth_atr[end_i] / max(bandwidth_atr[start_i], np.finfo(float).eps)
    )
    core_width_decrease_steps = int(
        (np.diff(bandwidth_atr[core]) < 0.0).sum()
    )

    candle_body_high = np.maximum(open_, close)
    candle_body_low = np.minimum(open_, close)
    total_wick = (
        (high[core] - candle_body_high[core])
        + (candle_body_low[core] - low[core])
    ) / atr
    core_reverse_count = int((aligned_core_bodies < -0.20).sum())
    post_reverse_count = int((post_bodies < -0.20).sum())
    post_path = np.r_[0.0, post_progress]
    post_running_peak = np.maximum.accumulate(post_path)
    post_retrace = float(np.max(post_running_peak - post_path))
    post_min_progress = float(post_path.min())

    ma_origin = float(mas[start_i].mean())
    favourable_wick = np.where(
        sign > 0,
        high - candle_body_high,
        candle_body_low - low,
    ) / atr
    adverse_wick = np.where(
        sign > 0,
        candle_body_low - low,
        high - candle_body_high,
    ) / atr
    channels = (
        sign * (close - ma_origin) / atr,
        sign * (close - open_) / atr,
        favourable_wick,
        adverse_wick,
        sign * (ma_center - ma_origin) / atr,
        (mas.max(axis=1) - mas.min(axis=1)) / atr,
        sign * (close - ma_center) / atr,
    )
    sequence = np.stack(
        [_pack_channel(values, start_i, end_i) for values in channels]
    )
    if sequence.shape != (7, 22) or not np.isfinite(sequence).all():
        raise PerfectFilterError("profile sequence contract drift")

    metrics = {
        "six_ma_end_bandwidth_bps": float(bandwidth_bps[end_i]),
        "six_ma_end_bandwidth_atr": float(bandwidth_atr[end_i]),
        "six_ma_core_mean_bandwidth_bps": float(bandwidth_bps[core].mean()),
        "six_ma_core_envelope_atr": core_ma_envelope,
        "pre12_median_bandwidth_bps": pre_median,
        "tightening_ratio_vs_pre12": float(bandwidth_bps[end_i] / pre_median),
        "core_width_end_start_ratio": core_width_ratio,
        "core_width_decrease_steps": float(core_width_decrease_steps),
        "aligned_ma_slope_atr_per_bar": core_ma_slope,
        "pairwise_order_flips": float(pairwise_order_flips),
        "pairwise_pairs_flipped": float(pairwise_pairs_flipped),
        "candle_bundle_touch_rate": float(candle_touch.mean()),
        "body_bundle_touch_rate": float(body_touch.mean()),
        "close_near_bundle_rate": float((close_distance <= 0.15).mean()),
        "close_to_bundle_q75_atr": float(np.quantile(close_distance, 0.75)),
        "max_close_to_bundle_atr": float(close_distance.max()),
        "pre_body_q90_atr": float(np.quantile(pre_bodies, 0.90)),
        "pre_abs_path_atr": float(np.abs(pre_steps).sum() / atr),
        "pre_last3_directional_progress_atr": float(pre_last3_progress),
        "pre_max_favourable_excursion_atr": float(
            max(0.0, pre_aligned_from_start.max())
        ),
        "core_max_body_atr": core_body_max,
        "core_wick_q90_atr": float(np.quantile(total_wick, 0.90)),
        "core_reverse_body_count": float(core_reverse_count),
        "core_directional_progress_atr": float(
            sign * (close[end_i] - close[start_i]) / atr
        ),
        "post1_progress_atr": float(post_progress[0]),
        "post2_progress_atr": float(post_progress[1]),
        "post3_progress_atr": float(post_progress[2]),
        "post5_progress_atr": float(post_progress[4]),
        "positive_post_steps": float((post_steps > 0.0).sum()),
        "post_min_progress_atr": post_min_progress,
        "post_retrace_atr": post_retrace,
        "post_reverse_body_count": float(post_reverse_count),
        "max_opposite_post_body_atr": float(max(0.0, -post_bodies.min())),
        "post_to_core_max_body_ratio": float(
            post_body_max / max(core_body_max, np.finfo(float).eps)
        ),
        "box_height_norm": box_height_norm,
    }
    return ShapeProfile(metrics, sequence, start_i, end_i)


def z_normalize(sequence: np.ndarray) -> np.ndarray:
    """Z-normalize each channel independently without changing time order."""

    values = np.asarray(sequence, dtype=float)
    if values.ndim != 2 or values.shape[1] < 3 or not np.isfinite(values).all():
        raise PerfectFilterError("distance input must be finite channels x time")
    mean = values.mean(axis=1, keepdims=True)
    std = values.std(axis=1, keepdims=True)
    safe = np.where(std <= 1e-12, 1.0, std)
    return (values - mean) / safe


def derivative_transform(sequence: np.ndarray) -> np.ndarray:
    """Return the Keogh derivative used for derivative-DTW."""

    values = np.asarray(sequence, dtype=float)
    if values.ndim != 2 or values.shape[1] < 3:
        raise PerfectFilterError("derivative transform requires at least three points")
    derivative = 0.5 * (
        (values[:, 1:-1] - values[:, :-2])
        + 0.5 * (values[:, 2:] - values[:, :-2])
    )
    return derivative


def constrained_dtw_cost(
    left: np.ndarray,
    right: np.ndarray,
    *,
    radius: int,
) -> float:
    """Return aeon's exact squared multivariate DTW path cost."""

    first = np.asarray(left, dtype=float)
    second = np.asarray(right, dtype=float)
    if first.ndim != 2 or second.ndim != 2 or first.shape[0] != second.shape[0]:
        raise PerfectFilterError("DTW inputs must share their channel count")
    if radius < abs(first.shape[1] - second.shape[1]) or radius < 0:
        raise PerfectFilterError("DTW radius cannot support the input lengths")
    n, m = first.shape[1], second.shape[1]
    cost = np.full((n + 1, m + 1), np.inf, dtype=float)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(1, i - radius), min(m, i + radius) + 1):
            prior_cost = min(cost[i - 1, j - 1], cost[i - 1, j], cost[i, j - 1])
            local = float(np.sum((first[:, i - 1] - second[:, j - 1]) ** 2))
            cost[i, j] = prior_cost + local
    if not np.isfinite(cost[n, m]):
        raise PerfectFilterError("DTW path is unreachable")
    return float(cost[n, m])


def constrained_dtw_distance(
    left: np.ndarray,
    right: np.ndarray,
    *,
    radius: int,
) -> float:
    """Return a fixed-scale RMS transform of the aeon-parity DTW cost."""

    first = np.asarray(left, dtype=float)
    second = np.asarray(right, dtype=float)
    raw = constrained_dtw_cost(first, second, radius=radius)
    scale = first.shape[0] * max(first.shape[1], second.shape[1])
    return float(math.sqrt(raw / scale))


def sequence_distance(
    left: np.ndarray,
    right: np.ndarray,
    *,
    radius: int,
    weights: Mapping[str, float],
) -> dict[str, float]:
    """Return lock-step, DTW, derivative-DTW, and their frozen blend."""

    first = z_normalize(left)
    second = z_normalize(right)
    if first.shape != second.shape:
        raise PerfectFilterError("lock-step sequence shapes differ")
    lockstep = float(math.sqrt(np.mean((first - second) ** 2)))
    dtw = constrained_dtw_distance(first, second, radius=radius)
    ddtw = constrained_dtw_distance(
        derivative_transform(first),
        derivative_transform(second),
        radius=radius,
    )
    weight_values = {name: float(weights[name]) for name in ("lockstep", "dtw", "ddtw")}
    if not math.isclose(sum(weight_values.values()), 1.0, abs_tol=1e-12):
        raise PerfectFilterError("sequence-distance weights must sum to one")
    combined = (
        weight_values["lockstep"] * lockstep
        + weight_values["dtw"] * dtw
        + weight_values["ddtw"] * ddtw
    )
    return {
        "lockstep_distance": lockstep,
        "dtw_distance": dtw,
        "ddtw_distance": ddtw,
        "combined_distance": float(combined),
    }


def segmented_sequence_distance(
    left: np.ndarray,
    right: np.ndarray,
    *,
    radius: int,
    component_weights: Mapping[str, float],
    segment_slices: Mapping[str, Sequence[int]],
    segment_weights: Mapping[str, float],
) -> dict[str, float]:
    """Compare prelude, core, and release without allowing cross-boundary warps."""

    if set(segment_slices) != {"prelude", "core", "release"}:
        raise PerfectFilterError("distance segments must be prelude/core/release")
    if not math.isclose(
        sum(float(value) for value in segment_weights.values()),
        1.0,
        abs_tol=1e-12,
    ):
        raise PerfectFilterError("distance segment weights must sum to one")
    results: dict[str, dict[str, float]] = {}
    for name in ("prelude", "core", "release"):
        bounds = segment_slices[name]
        if len(bounds) != 2:
            raise PerfectFilterError(f"invalid {name} segment bounds")
        start, stop = int(bounds[0]), int(bounds[1])
        if start < 0 or stop <= start or stop > left.shape[1] or stop > right.shape[1]:
            raise PerfectFilterError(f"invalid {name} segment slice")
        results[name] = sequence_distance(
            left[:, start:stop],
            right[:, start:stop],
            radius=min(radius, stop - start - 1),
            weights=component_weights,
        )
    output: dict[str, float] = {}
    for component in (
        "lockstep_distance",
        "dtw_distance",
        "ddtw_distance",
        "combined_distance",
    ):
        output[component] = float(
            sum(
                float(segment_weights[name]) * float(results[name][component])
                for name in ("prelude", "core", "release")
            )
        )
    for name, result in results.items():
        for component, value in result.items():
            output[f"{name}_{component}"] = float(value)
    return output


def segmented_lockstep_distance(
    left: np.ndarray,
    right: np.ndarray,
    *,
    segment_slices: Mapping[str, Sequence[int]],
    segment_weights: Mapping[str, float],
) -> float:
    """Cheap boundary-preserving lock-step distance used only as a prefilter."""

    total = 0.0
    for name in ("prelude", "core", "release"):
        start, stop = (int(value) for value in segment_slices[name])
        first = z_normalize(left[:, start:stop])
        second = z_normalize(right[:, start:stop])
        total += float(segment_weights[name]) * float(
            math.sqrt(np.mean((first - second) ** 2))
        )
    return float(total)


def verify_aeon_parity_fixture(controls: Mapping[str, Any]) -> dict[str, Any]:
    """Verify frozen raw DTW costs produced by isolated aeon 1.5.0."""

    base = np.vstack(
        [
            np.linspace(-1.0, 1.0, 13),
            np.sin(np.linspace(0.0, np.pi, 13)),
        ]
    )
    delayed = np.c_[base[:, :1], base[:, :-1]]
    first, second = z_normalize(base), z_normalize(delayed)
    dtw_cost = constrained_dtw_cost(first, second, radius=2)
    ddtw_cost = constrained_dtw_cost(
        derivative_transform(first), derivative_transform(second), radius=2
    )
    fixture = controls["aeon_1_5_parity_fixture"]
    tolerance = float(fixture["absolute_tolerance"])
    expected_dtw = float(fixture["expected_dtw_cost"])
    expected_ddtw = float(fixture["expected_ddtw_cost"])
    if not math.isclose(dtw_cost, expected_dtw, rel_tol=0.0, abs_tol=tolerance):
        raise PerfectFilterError("local DTW cost differs from aeon 1.5 fixture")
    if not math.isclose(ddtw_cost, expected_ddtw, rel_tol=0.0, abs_tol=tolerance):
        raise PerfectFilterError("local derivative-DTW cost differs from aeon 1.5 fixture")
    return {
        "aeon_version": "1.5.0",
        "window_fraction": 0.20,
        "local_dtw_cost": dtw_cost,
        "expected_dtw_cost": expected_dtw,
        "local_ddtw_cost": ddtw_cost,
        "expected_ddtw_cost": expected_ddtw,
        "absolute_tolerance": tolerance,
        "passed": True,
    }


def hard_gate_failures(
    metrics: Mapping[str, float],
    gates: Mapping[str, Any],
    *,
    include_box: bool = True,
    include_release: bool = True,
) -> list[str]:
    """Return every failed gate; an empty list is a strict pass."""

    contracting_topology = (
        float(metrics["core_width_end_start_ratio"])
        <= float(gates["contracting_max_end_start_ratio"])
        and int(metrics["core_width_decrease_steps"])
        >= int(gates["contracting_min_decrease_steps"])
    )
    crossing_topology = (
        float(metrics["core_width_end_start_ratio"])
        <= float(gates["crossing_max_end_start_ratio"])
        and int(metrics["pairwise_order_flips"])
        >= int(gates["crossing_min_pairwise_order_flips"])
    )
    morphology_checks = (
        (
            float(metrics["six_ma_end_bandwidth_atr"])
            <= float(gates["max_six_ma_end_bandwidth_atr"]),
            "six_ma_end_bandwidth_atr",
        ),
        (
            float(metrics["six_ma_core_envelope_atr"])
            <= float(gates["max_six_ma_core_envelope_atr"]),
            "six_ma_core_envelope_atr",
        ),
        (
            float(metrics["core_directional_progress_atr"])
            >= float(gates["min_core_directional_progress_atr"]),
            "core_directional_progress_too_negative",
        ),
        (
            float(metrics["core_directional_progress_atr"])
            <= float(gates["max_core_directional_progress_atr"]),
            "core_directional_progress_too_large",
        ),
        (
            float(metrics["aligned_ma_slope_atr_per_bar"])
            >= float(gates["min_aligned_ma_slope_atr_per_bar"]),
            "aligned_ma_slope_atr_per_bar",
        ),
        (contracting_topology or crossing_topology, "ma_bundle_topology"),
        (
            float(metrics["candle_bundle_touch_rate"])
            >= float(gates["min_candle_bundle_touch_rate"]),
            "candle_bundle_touch_rate",
        ),
        (
            float(metrics["close_to_bundle_q75_atr"])
            <= float(gates["max_close_to_bundle_q75_atr"]),
            "close_to_bundle_q75_atr",
        ),
        (
            float(metrics["pre_body_q90_atr"])
            <= float(gates["max_pre_body_q90_atr"]),
            "pre_body_q90_atr",
        ),
        (
            float(metrics["pre_abs_path_atr"])
            <= float(gates["max_pre_abs_path_atr"]),
            "pre_abs_path_atr",
        ),
        (
            float(metrics["pre_last3_directional_progress_atr"])
            <= float(gates["max_pre_last3_directional_progress_atr"]),
            "pre_last3_directional_progress_atr",
        ),
        (
            float(metrics["pre_max_favourable_excursion_atr"])
            <= float(gates["max_pre_favourable_excursion_atr"]),
            "pre_max_favourable_excursion_atr",
        ),
        (
            float(metrics["core_wick_q90_atr"])
            <= float(gates["max_core_wick_q90_atr"]),
            "core_wick_q90_atr",
        ),
        (
            int(metrics["core_reverse_body_count"])
            <= int(gates["max_core_reverse_body_count"]),
            "core_reverse_body_count",
        ),
        (
            float(metrics["core_max_body_atr"])
            <= float(gates["max_core_body_atr"]),
            "core_max_body_atr",
        ),
    )
    release_checks = (
        (
            float(metrics["post1_progress_atr"])
            >= float(gates["min_post1_progress_atr"]),
            "post1_progress_atr",
        ),
        (
            float(metrics["post2_progress_atr"])
            >= float(gates["min_post2_progress_atr"]),
            "post2_progress_atr",
        ),
        (
            float(metrics["post3_progress_atr"])
            >= float(gates["min_post3_progress_atr"]),
            "post3_progress_atr",
        ),
        (
            float(metrics["post5_progress_atr"])
            >= float(gates["min_post5_progress_atr"]),
            "post5_progress_atr",
        ),
        (
            float(metrics["post_min_progress_atr"])
            >= float(gates["min_post_progress_floor_atr"]),
            "post_min_progress_atr",
        ),
        (
            int(metrics["positive_post_steps"])
            >= int(gates["min_positive_post_steps_out_of_5"]),
            "positive_post_steps",
        ),
        (
            float(metrics["post_retrace_atr"])
            <= float(gates["max_post_retrace_atr"]),
            "post_retrace_atr",
        ),
        (
            int(metrics["post_reverse_body_count"])
            <= int(gates["max_post_reverse_body_count"]),
            "post_reverse_body_count",
        ),
        (
            float(metrics["max_opposite_post_body_atr"])
            <= float(gates["max_opposite_post_body_atr"]),
            "max_opposite_post_body_atr",
        ),
    )
    failures = [name for passed, name in morphology_checks if not passed]
    if include_release:
        failures.extend(name for passed, name in release_checks if not passed)
    if include_box and float(metrics["box_height_norm"]) > float(
        gates["max_box_height_norm"]
    ):
        failures.append("box_height_norm")
    return failures


def _axis_scores(
    metrics: Mapping[str, float],
    *,
    good_distance: float,
    bad_distance: float,
    family_distance: float,
    distance_scale: float,
    axis_weights: Mapping[str, Any],
    worst_axis_weight: float,
) -> dict[str, float]:
    end_width = _clip(
        (1.10 - float(metrics["six_ma_end_bandwidth_atr"])) / 0.85
    )
    envelope = _clip(
        (1.60 - float(metrics["six_ma_core_envelope_atr"])) / 1.35
    )
    contraction = _clip(
        (1.15 - float(metrics["core_width_end_start_ratio"])) / 0.50
    )
    decreases = _clip(float(metrics["core_width_decrease_steps"]) / 4.0)
    interaction = _clip(float(metrics["pairwise_order_flips"]) / 6.0)
    slope = _clip(float(metrics["aligned_ma_slope_atr_per_bar"]) / 0.08)
    topology = max(
        float(np.mean((contraction, decreases))),
        float(np.mean((contraction, interaction))),
    )
    density = float(np.mean((end_width, envelope, topology, slope)))

    pre_body = _clip(1.0 - float(metrics["pre_body_q90_atr"]) / 1.10)
    pre_path = _clip(1.0 - float(metrics["pre_abs_path_atr"]) / 5.50)
    pre_last3 = _clip(
        1.0 - max(0.0, float(metrics["pre_last3_directional_progress_atr"]))
    )
    pre_excursion = _clip(
        1.0 - float(metrics["pre_max_favourable_excursion_atr"]) / 3.00
    )
    quietness = float(np.mean((pre_body, pre_path, pre_last3, pre_excursion)))

    touch = _clip((float(metrics["candle_bundle_touch_rate"]) - 0.40) / 0.60)
    close = _clip(1.0 - float(metrics["close_to_bundle_q75_atr"]) / 1.50)
    body_touch = float(metrics["body_bundle_touch_rate"])
    contact = float(np.mean((touch, close, body_touch)))

    immediate = _clip(float(metrics["post1_progress_atr"]) / 1.50)
    progress2 = _clip(float(metrics["post2_progress_atr"]) / 2.00)
    progress3 = _clip(float(metrics["post3_progress_atr"]) / 2.50)
    progress5 = _clip(float(metrics["post5_progress_atr"]) / 3.50)
    persistence = _clip(float(metrics["positive_post_steps"]) / 5.0)
    retrace = _clip(1.0 - float(metrics["post_retrace_atr"]) / 0.75)
    release = float(
        np.mean((immediate, progress2, progress3, progress5, persistence, retrace))
    )

    core_wick = _clip(1.0 - float(metrics["core_wick_q90_atr"]) / 2.00)
    core_reverse = _clip(
        1.0 - float(metrics["core_reverse_body_count"]) / 2.0
    )
    core_body = _clip(1.0 - float(metrics["core_max_body_atr"]) / 1.20)
    post_adverse = _clip(
        1.0 - float(metrics["max_opposite_post_body_atr"]) / 0.80
    )
    wick_reverse = float(np.mean((core_wick, core_reverse, core_body, post_adverse)))

    absolute_similarity = math.exp(-good_distance / max(distance_scale, 1e-12))
    family_similarity = math.exp(-family_distance / max(distance_scale, 1e-12))
    contrast = 1.0 / (
        1.0 + math.exp(-(bad_distance - good_distance) / max(distance_scale, 1e-12))
    )
    similarity = float(
        0.50 * absolute_similarity + 0.25 * family_similarity + 0.25 * contrast
    )

    axes = {
        "density_topology_score": density,
        "prelude_quietness_score": quietness,
        "price_bundle_contact_score": contact,
        "release_cleanliness_score": release,
        "wick_reverse_cleanliness_score": wick_reverse,
        "reference_similarity_score": similarity,
    }
    weighted = sum(
        float(axis_weights[name]) * axes[f"{name}_score"]
        for name in (
            "density_topology",
            "prelude_quietness",
            "price_bundle_contact",
            "release_cleanliness",
            "wick_reverse_cleanliness",
            "reference_similarity",
        )
    )
    weakest = min(axes.values())
    quality = (1.0 - worst_axis_weight) * weighted + worst_axis_weight * weakest
    return {
        **axes,
        "weakest_primary_axis_score": float(weakest),
        "quality_score": float(quality),
        "reference_contrast_margin": float(bad_distance - good_distance),
    }


def _validate_prereg(prereg: Mapping[str, Any]) -> None:
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise PerfectFilterError("experiment ID drift")
    inputs = prereg["inputs"]
    if pd.Timestamp(inputs["holdout_start_exclusive"]) != HOLDOUT_START:
        raise PerfectFilterError("holdout boundary drift")
    if int(inputs["holdout_ohlcv_rows_allowed"]) != 0:
        raise PerfectFilterError("holdout allowance must remain zero")
    safety = prereg["safety"]
    forbidden_true = (
        "write_yolo_labels",
        "mutate_training_dataset",
        "start_training",
        "manual_owner_review_workflow",
        "training_eligible",
        "production_eligible",
        "holdout_read",
        "active_or_frozen_change",
        "forward_or_order_state_change",
    )
    if any(safety.get(name) is not False for name in forbidden_true):
        raise PerfectFilterError("one or more safety switches are not false")
    weights = prereg["ranking"]["axis_weights"]
    if not math.isclose(sum(float(value) for value in weights.values()), 1.0, abs_tol=1e-12):
        raise PerfectFilterError("axis weights must sum to one")
    if prereg["ranking"].get("forced_output_count") is not None:
        raise PerfectFilterError("strict filter cannot force an output count")


def _profile_key(row: Mapping[str, Any]) -> str:
    return str(row.get("profile_id", row["sample_id"]))


def _rows_by_sample_id(
    rows: Sequence[Mapping[str, Any]], *, label: str
) -> dict[str, dict[str, Any]]:
    indexed = {str(row["sample_id"]): dict(row) for row in rows}
    if len(indexed) != len(rows):
        raise PerfectFilterError(f"{label} has duplicate sample IDs")
    return indexed


def _load_pinned_rows(
    prereg: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    inputs = prereg["inputs"]
    positive_path = _repo_path(inputs["positive_manifest_path"])
    training_path = _repo_path(inputs["training_dataset_manifest_path"])
    training_yaml_path = _repo_path(inputs["training_dataset_yaml_path"])
    training_summary_path = _repo_path(inputs["training_dataset_summary_path"])
    audit_path = _repo_path(inputs["source_audit_path"])
    reference_base_path = _repo_path(inputs["reference_base_manifest_path"])
    reference_density_path = _repo_path(inputs["reference_density_manifest_path"])
    reference_owner_path = _repo_path(inputs["reference_owner_manifest_path"])
    reference_family_path = _repo_path(inputs["reference_family_manifest_path"])
    for path, expected in (
        (positive_path, inputs["positive_manifest_sha256"]),
        (training_path, inputs["training_dataset_manifest_sha256"]),
        (training_yaml_path, inputs["training_dataset_yaml_sha256"]),
        (training_summary_path, inputs["training_dataset_summary_sha256"]),
        (audit_path, inputs["source_audit_sha256"]),
        (reference_base_path, inputs["reference_base_manifest_sha256"]),
        (reference_density_path, inputs["reference_density_manifest_sha256"]),
        (reference_owner_path, inputs["reference_owner_manifest_sha256"]),
        (reference_family_path, inputs["reference_family_manifest_sha256"]),
    ):
        if sha256_file(path) != str(expected):
            raise PerfectFilterError(f"pinned input SHA drift: {path}")
    positives = read_jsonl(positive_path)
    training_yaml = training_yaml_path.read_text(encoding="utf-8")
    if "train: images/train" not in training_yaml or "val: images/val" not in training_yaml:
        raise PerfectFilterError("training data.yaml split paths drifted")
    training_summary = read_json(training_summary_path)
    training_positives = [
        row
        for row in read_jsonl(training_path)
        if str(row.get("sample_kind")) == "positive"
    ]
    source_audits = read_jsonl(audit_path)
    if len(positives) != int(inputs["positive_rows"]):
        raise PerfectFilterError("positive row count drift")
    if len(training_positives) != int(inputs["training_dataset_positive_rows"]):
        raise PerfectFilterError("training-dataset positive row count drift")
    positive_by_id = _rows_by_sample_id(positives, label="positive manifest")
    training_by_source_id = {
        str(row["source_sample_id"]): dict(row) for row in training_positives
    }
    if len(training_by_source_id) != len(training_positives):
        raise PerfectFilterError("training dataset has duplicate positive source IDs")
    if set(positive_by_id) != set(training_by_source_id):
        raise PerfectFilterError("source and training-dataset positive IDs differ")
    joined_positives: list[dict[str, Any]] = []
    for source_row in positives:
        sample_id = str(source_row["sample_id"])
        target = training_by_source_id[sample_id]
        checks = {
            "source_path": str(source_row["source_path"]) == str(target["source_path"]),
            "symbol": str(source_row["symbol"]) == str(target["symbol"]),
            "direction": str(source_row["direction"]) == str(target["direction"]),
            "event_id": str(source_row["event_id"]) == str(target["event_id"]),
            "source_order": int(source_row["source_order"])
            == int(target["source_order"]),
            "core_start_i": int(source_row["source_core_start_i"])
            == int(target["core_start_i"]),
            "core_end_i": int(source_row["source_core_end_i"])
            == int(target["core_end_i"]),
            "core_bars": int(source_row["core_bars"]) == int(target["core_bars"]),
            "core_start_time": pd.Timestamp(source_row["core_start_time"])
            == pd.Timestamp(target["core_start_time"]),
            "core_end_time": pd.Timestamp(source_row["core_end_time"])
            == pd.Timestamp(target["core_end_time"]),
            "window_start_i": int(source_row["window_start_i"])
            == int(target["window_start_i"]),
            "window_end_i": int(source_row["window_end_i"])
            == int(target["window_end_i"]),
            "pre_core_context_bars": int(source_row["pre_core_context_bars"])
            == int(target["pre_core_context_bars"]),
            "post_core_context_bars": int(source_row["post_core_context_bars"])
            == int(target["post_core_context_bars"]),
            "accepted_image_path": str(source_row["image_path"])
            == str(target["accepted_image_path"]),
            "accepted_image_sha256": str(source_row["image_sha256"])
            == str(target["accepted_image_sha256"]),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise PerfectFilterError(
                f"training positive lineage drift for {sample_id}: {failed}"
            )
        joined = dict(source_row)
        joined.update(
            {
                "training_dataset_sample_id": str(target["sample_id"]),
                "training_dataset_split": str(target["split"]),
                "time_block": str(target["time_block"]),
                "training_data_yaml_exposed": str(target["split"])
                in {"train", "val"},
                "model_input_image_path": _relative(
                    training_path.parent / str(target["image_path"])
                ),
                "model_input_image_sha256": str(target["image_sha256"]),
                "model_input_label_path": _relative(
                    training_path.parent / str(target["label_path"])
                ),
                "model_input_label_sha256": str(target["label_sha256"]),
            }
        )
        joined_positives.append(joined)
    exposed = sum(bool(row["training_data_yaml_exposed"]) for row in joined_positives)
    if exposed != int(inputs["training_dataset_exposed_positive_rows"]):
        raise PerfectFilterError("training-dataset exposed-positive count drift")
    expected_exposed = int(training_summary["counts"]["train/positive"]) + int(
        training_summary["counts"]["val/positive"]
    )
    if exposed != expected_exposed:
        raise PerfectFilterError("training build summary positive exposure drift")

    reference_rows_expected = int(inputs["reference_rows_per_manifest"])
    base_rows = read_jsonl(reference_base_path)
    density_rows = read_jsonl(reference_density_path)
    owner_rows = read_jsonl(reference_owner_path)
    family_rows = read_jsonl(reference_family_path)
    if any(
        len(rows) != reference_rows_expected
        for rows in (base_rows, density_rows, owner_rows, family_rows)
    ):
        raise PerfectFilterError("one or more reference manifest row counts drifted")
    base = _rows_by_sample_id(base_rows, label="reference base")
    density = _rows_by_sample_id(density_rows, label="reference density")
    owner = _rows_by_sample_id(owner_rows, label="reference owner")
    if set(base) != set(density) or set(base) != set(owner):
        raise PerfectFilterError("historical reference manifests contain different samples")

    contract = prereg["owner_reference_geometry"]
    references: list[dict[str, Any]] = []

    def exact_reference(spec: Mapping[str, Any], *, role: str, geometry: str) -> dict[str, Any]:
        sample_id = str(spec["sample_id"])
        if sample_id not in base:
            raise PerfectFilterError(f"owner reference is missing: {sample_id}")
        base_row, density_row, owner_row = base[sample_id], density[sample_id], owner[sample_id]
        expected_order = int(spec["source_order"])
        if any(
            int(row["source_order"]) != expected_order
            for row in (density_row, owner_row)
        ):
            raise PerfectFilterError(f"owner reference order drift: {sample_id}")
        for field in ("symbol", "direction", "source_path"):
            if len({str(row[field]) for row in (base_row, density_row, owner_row)}) != 1:
                raise PerfectFilterError(
                    f"owner reference {field} lineage drift: {sample_id}"
                )
        if spec.get("symbol") is not None and str(spec["symbol"]) != str(
            base_row["symbol"]
        ):
            raise PerfectFilterError(f"owner reference symbol drift: {sample_id}")
        result = dict(base_row)
        source = owner_row if geometry == "owner" else density_row
        if source.get("box") is None:
            raise PerfectFilterError(f"reference geometry missing: {sample_id}")
        result.update(
            {
                "source_order": expected_order,
                "core_start_offset": int(source["core_start_offset"]),
                "core_end_offset": int(source["core_end_offset"]),
                "box": dict(source["box"]),
                "reference_role": role,
                "reference_geometry_source": geometry,
                "profile_id": f"{sample_id}::{role}",
            }
        )
        if geometry == "owner":
            anchor_i = int(base_row["source_anchor_i"])
            expected_start = anchor_i + int(source["core_start_offset"])
            expected_end = anchor_i + int(source["core_end_offset"])
            if expected_start != int(source["core_start_source_i"]) or expected_end != int(
                source["core_end_source_i"]
            ):
                raise PerfectFilterError(
                    f"owner reference source-index geometry drift: {sample_id}"
                )
        return result

    for role in ("perfect", "good", "standard_late"):
        for spec in contract[role]:
            references.append(exact_reference(spec, role=role, geometry="owner"))
    for spec in contract["semantic_reject"]:
        references.append(exact_reference(spec, role="semantic_reject", geometry="density"))
    for spec in contract["boundary_wrong_reboxed"]:
        references.append(exact_reference(spec, role="boundary_wrong", geometry="density"))
        references.append(exact_reference(spec, role="boundary_reboxed", geometry="owner"))

    accepted_family: list[dict[str, Any]] = []
    for row in family_rows:
        item = dict(row)
        item["reference_role"] = "accepted_family"
        item["reference_geometry_source"] = "v7"
        item["profile_id"] = f"{item['sample_id']}::accepted_family"
        accepted_family.append(item)
    audits_by_path = {str(row["source_path"]): row for row in source_audits}
    if len(audits_by_path) != len(source_audits):
        raise PerfectFilterError("source audit has duplicate paths")
    required_source_paths = {
        str(row["source_path"])
        for row in (*joined_positives, *references, *accepted_family)
    }
    missing_audits = sorted(required_source_paths - set(audits_by_path))
    if missing_audits:
        raise PerfectFilterError(
            f"source audit is missing {len(missing_audits)} required paths: "
            f"{missing_audits[:3]}"
        )
    return joined_positives, references, accepted_family, audits_by_path


def _score_all(
    positives: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
    accepted_family: Sequence[Mapping[str, Any]],
    profiles: Mapping[str, ShapeProfile],
    prereg: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in references:
        by_role[str(row["reference_role"])].append(dict(row))
    perfect_rows = by_role["perfect"]
    good_rows = by_role["good"]
    standard_rows = by_role["standard_late"]
    positive_anchor_rows = perfect_rows + good_rows
    # A wrong historical box is a boundary control, not evidence that the
    # underlying event is a bad morphology. Only explicit semantic rejects
    # may define the contrastive bad-shape pool.
    bad_rows = by_role["semantic_reject"]
    bad_profile_keys = {_profile_key(row) for row in bad_rows}
    if len(perfect_rows) != 1 or len(good_rows) != 1 or len(standard_rows) != 1:
        raise PerfectFilterError("owner anchor cardinality drift")
    if len(by_role["semantic_reject"]) != 6 or len(by_role["boundary_wrong"]) != 5:
        raise PerfectFilterError("owner contrastive reference cardinality drift")
    distance_contract = prereg["sequence_distance"]
    radius = int(distance_contract["sakoe_chiba_radius_bars"])
    component_weights = distance_contract["weights"]
    segment_slices = distance_contract["segment_slices"]
    segment_weights = distance_contract["segment_weights"]
    prefilter_k = int(distance_contract["nearest_prefilter_lockstep_k"])
    if prefilter_k < 1:
        raise PerfectFilterError("reference prefilter size must be positive")

    def nearest(
        row: Mapping[str, Any],
        pool: Sequence[Mapping[str, Any]],
        *,
        exclude_self: bool = False,
    ) -> tuple[float, dict[str, float], str]:
        source_key = _profile_key(row)
        source = profiles[source_key]
        targets = [
            target_row
            for target_row in pool
            if not (exclude_self and _profile_key(target_row) == source_key)
        ]
        if not targets:
            raise PerfectFilterError("reference distance pool is empty after exclusion")
        if len(targets) > prefilter_k:
            targets = sorted(
                targets,
                key=lambda target_row: (
                    segmented_lockstep_distance(
                        source.sequence,
                        profiles[_profile_key(target_row)].sequence,
                        segment_slices=segment_slices,
                        segment_weights=segment_weights,
                    ),
                    _profile_key(target_row),
                ),
            )[:prefilter_k]
        choices: list[tuple[dict[str, float], str]] = []
        for target_row in targets:
            target_key = _profile_key(target_row)
            distance = segmented_sequence_distance(
                source.sequence,
                profiles[target_key].sequence,
                radius=radius,
                component_weights=component_weights,
                segment_slices=segment_slices,
                segment_weights=segment_weights,
            )
            choices.append((distance, target_key))
        best, target_key = min(
            choices, key=lambda value: value[0]["combined_distance"]
        )
        return float(best["combined_distance"]), best, target_key

    bad_to_good = [nearest(row, positive_anchor_rows)[0] for row in bad_rows]
    distance_scale = float(np.median(bad_to_good))
    if not np.isfinite(distance_scale) or distance_scale <= 0.0:
        raise PerfectFilterError("reference distance scale is invalid")

    family_leave_one_out = [
        nearest(row, accepted_family, exclude_self=True)[0] for row in accepted_family
    ]

    axis_weights = prereg["ranking"]["axis_weights"]
    worst_axis_weight = float(prereg["ranking"]["worst_axis_weight"])

    def score_one(
        row: Mapping[str, Any], *, leave_anchor_out: bool = False
    ) -> dict[str, Any]:
        profile = profiles[_profile_key(row)]
        good_distance, good_parts, nearest_good = nearest(
            row, positive_anchor_rows, exclude_self=leave_anchor_out
        )
        bad_distance, bad_parts, nearest_bad = nearest(
            row,
            bad_rows,
            exclude_self=_profile_key(row) in bad_profile_keys,
        )
        family_distance, family_parts, nearest_family = nearest(row, accepted_family)
        axes = _axis_scores(
            profile.metrics,
            good_distance=good_distance,
            bad_distance=bad_distance,
            family_distance=family_distance,
            distance_scale=distance_scale,
            axis_weights=axis_weights,
            worst_axis_weight=worst_axis_weight,
        )
        failures = hard_gate_failures(profile.metrics, prereg["hard_gates"])
        return {
            **dict(row),
            "strict_metrics": profile.metrics,
            "hard_gate_pass": not failures,
            "hard_gate_failures": failures,
            "good_reference_distance": good_distance,
            "bad_reference_distance": bad_distance,
            "accepted_family_distance": family_distance,
            "nearest_good_profile_id": nearest_good,
            "nearest_bad_profile_id": nearest_bad,
            "nearest_family_profile_id": nearest_family,
            "nearest_good_distance_parts": good_parts,
            "nearest_bad_distance_parts": bad_parts,
            "nearest_family_distance_parts": family_parts,
            **axes,
            "training_eligible": False,
            "production_eligible": False,
        }

    scored_references = [
        score_one(row, leave_anchor_out=row in positive_anchor_rows)
        for row in references
    ]
    gates = prereg["hard_gates"]
    positive_failures = [
        str(row["profile_id"])
        for row in scored_references
        if str(row["reference_role"]) in {"perfect", "good"}
        and hard_gate_failures(row["strict_metrics"], gates)
    ]
    standard_morphology_failures = hard_gate_failures(
        profiles[_profile_key(standard_rows[0])].metrics,
        gates,
        include_box=True,
        include_release=False,
    )
    standard_all_failures = hard_gate_failures(
        profiles[_profile_key(standard_rows[0])].metrics,
        gates,
    )
    release_failure_names = {
        "post1_progress_atr",
        "post2_progress_atr",
        "post3_progress_atr",
        "post5_progress_atr",
        "post_min_progress_atr",
        "positive_post_steps",
        "post_retrace_atr",
        "post_reverse_body_count",
        "max_opposite_post_body_atr",
    }
    if not standard_morphology_failures or not (
        release_failure_names & set(standard_all_failures)
    ):
        raise PerfectFilterError(
            "late #48 must fail both pre-release lateness and release gates"
        )
    bad_false_passes = [
        str(row["profile_id"])
        for row in scored_references
        if str(row["reference_role"]) == "semantic_reject"
        and not hard_gate_failures(row["strict_metrics"], gates)
    ]
    if positive_failures or bad_false_passes:
        raise PerfectFilterError(
            "owner reference separation failed: "
            f"positive_failures={positive_failures}, bad_false_passes={bad_false_passes}"
        )

    reference_scores = {
        str(row["profile_id"]): float(row["quality_score"])
        for row in scored_references
    }
    anchor_scores = [
        float(row["quality_score"])
        for row in scored_references
        if str(row["reference_role"]) in {"perfect", "good"}
    ]
    perfect_threshold = min(anchor_scores)
    strong_threshold = 0.90 * perfect_threshold
    anchor_good_combined_limit = max(
        float(row["good_reference_distance"])
        for row in scored_references
        if str(row["reference_role"]) in {"perfect", "good"}
    )
    anchor_good_lockstep_limit = max(
        float(row["nearest_good_distance_parts"]["lockstep_distance"])
        for row in scored_references
        if str(row["reference_role"]) in {"perfect", "good"}
    )
    accepted_family_limit = float(np.quantile(family_leave_one_out, 0.95))

    scored: list[dict[str, Any]] = []
    distance_scored_rows = 0
    for row in positives:
        profile = profiles[_profile_key(row)]
        failures = hard_gate_failures(profile.metrics, gates)
        if failures:
            scored.append(
                {
                    **dict(row),
                    "strict_metrics": profile.metrics,
                    "hard_gate_pass": False,
                    "hard_gate_failures": failures,
                    "good_reference_distance": None,
                    "bad_reference_distance": None,
                    "accepted_family_distance": None,
                    "nearest_good_profile_id": None,
                    "nearest_bad_profile_id": None,
                    "nearest_family_profile_id": None,
                    "reference_gate_pass": None,
                    "reference_gate_failures": [],
                    "nearest_good_distance_parts": {},
                    "nearest_bad_distance_parts": {},
                    "nearest_family_distance_parts": {},
                    "density_topology_score": None,
                    "prelude_quietness_score": None,
                    "price_bundle_contact_score": None,
                    "release_cleanliness_score": None,
                    "wick_reverse_cleanliness_score": None,
                    "reference_similarity_score": None,
                    "weakest_primary_axis_score": None,
                    "quality_score": 0.0,
                    "reference_contrast_margin": None,
                    "training_eligible": False,
                    "production_eligible": False,
                }
            )
        else:
            scored.append(score_one(row))
            distance_scored_rows += 1
    for row in scored:
        score = float(row["quality_score"])
        if not bool(row["hard_gate_pass"]):
            row["quality_tier"] = "REJECT"
            continue
        reference_failures: list[str] = []
        if float(row["good_reference_distance"]) > anchor_good_combined_limit:
            reference_failures.append("good_combined_distance")
        if (
            float(row["nearest_good_distance_parts"]["lockstep_distance"])
            > anchor_good_lockstep_limit
        ):
            reference_failures.append("good_lockstep_distance")
        if float(row["accepted_family_distance"]) > accepted_family_limit:
            reference_failures.append("accepted_family_distance")
        row["reference_gate_pass"] = not reference_failures
        row["reference_gate_failures"] = reference_failures
        if reference_failures:
            row["quality_tier"] = "STRICT_PASS"
        elif score >= perfect_threshold:
            row["quality_tier"] = "PERFECT_CANDIDATE"
        elif score >= strong_threshold:
            row["quality_tier"] = "STRONG_CANDIDATE"
        else:
            row["quality_tier"] = "STRICT_PASS"
    calibration = {
        "distance_scale": distance_scale,
        "bad_to_good_distances": bad_to_good,
        "accepted_family_leave_one_out_distances": family_leave_one_out,
        "accepted_family_leave_one_out_p95": accepted_family_limit,
        "max_good_combined_distance": anchor_good_combined_limit,
        "max_good_lockstep_distance": anchor_good_lockstep_limit,
        "perfect_score_threshold": perfect_threshold,
        "strong_score_threshold": strong_threshold,
        "reference_scores": reference_scores,
        "scored_references": scored_references,
        "owner_positive_failures": positive_failures,
        "owner_bad_false_passes": bad_false_passes,
        "standard_late_morphology_failures": standard_morphology_failures,
        "standard_late_all_failures": standard_all_failures,
        "distance_scored_candidate_rows": distance_scored_rows,
        "distance_skipped_hard_reject_rows": len(positives) - distance_scored_rows,
    }
    return scored, calibration


def _deduplicate(scored: list[dict[str, Any]], contract: Mapping[str, Any]) -> None:
    gap = pd.Timedelta(minutes=int(contract["same_symbol_direction_event_gap_minutes"]))
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        row["exact_dedup_kept"] = False
        row["event_dedup_kept"] = False
        row["time_block_cap_kept"] = False
        row["symbol_cap_kept"] = False
        row["shortlist_kept"] = False
        row["shortlist_order"] = None
    seen_geometry: set[tuple[str, int, int, str]] = set()
    seen_images: set[str] = set()
    eligible = sorted(
        (
            row
            for row in scored
            if str(row["quality_tier"])
            in {"PERFECT_CANDIDATE", "STRONG_CANDIDATE"}
            and bool(row["training_data_yaml_exposed"])
        ),
        key=lambda value: (-float(value["quality_score"]), str(value["sample_id"])),
    )
    for row in eligible:
        geometry_key = (
            str(row["source_path"]),
            int(row["source_core_start_i"]),
            int(row["source_core_end_i"]),
            str(row["direction"]),
        )
        image_sha = str(row["image_sha256"])
        if geometry_key in seen_geometry or image_sha in seen_images:
            continue
        seen_geometry.add(geometry_key)
        seen_images.add(image_sha)
        row["exact_dedup_kept"] = True
        grouped[(str(row["symbol"]), str(row["direction"]))].append(row)
    winners: list[dict[str, Any]] = []
    for rows in grouped.values():
        # Greedy non-maximum suppression by quality.  Checking against every
        # kept winner prevents anchored clusters such as 0h/3h/6h from keeping
        # the 3h and 6h rows, which would still be near-duplicates.
        selected: list[dict[str, Any]] = []
        for row in sorted(
            rows,
            key=lambda value: (-float(value["quality_score"]), str(value["sample_id"])),
        ):
            stamp = pd.Timestamp(row["core_end_time"])
            if any(
                abs(stamp - pd.Timestamp(kept["core_end_time"])) <= gap
                for kept in selected
            ):
                continue
            selected.append(row)
        winners.extend(selected)
    for row in winners:
        row["event_dedup_kept"] = True

    time_cap = int(contract["max_shortlist_per_symbol_per_direction_time_block"])
    by_time_block: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in winners:
        by_time_block[
            (str(row["symbol"]), str(row["direction"]), str(row["time_block"]))
        ].append(row)
    time_winners: list[dict[str, Any]] = []
    for rows in by_time_block.values():
        chosen = sorted(
            rows,
            key=lambda value: (-float(value["quality_score"]), str(value["sample_id"])),
        )[:time_cap]
        for row in chosen:
            row["time_block_cap_kept"] = True
        time_winners.extend(chosen)

    cap = int(contract["max_shortlist_per_symbol_per_direction"])
    by_symbol: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in time_winners:
        by_symbol[(str(row["symbol"]), str(row["direction"]))].append(row)
    capped: list[dict[str, Any]] = []
    for rows in by_symbol.values():
        chosen = sorted(
            rows,
            key=lambda value: (-float(value["quality_score"]), str(value["sample_id"])),
        )[:cap]
        for row in chosen:
            row["symbol_cap_kept"] = True
        capped.extend(chosen)

    queues: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in capped:
        queues[(str(row["direction"]), str(row["time_block"]))].append(row)
    for rows in queues.values():
        rows.sort(
            key=lambda value: (-float(value["quality_score"]), str(value["sample_id"]))
        )
    order = 0
    while any(queues.values()):
        for key in sorted(queues):
            if not queues[key]:
                continue
            row = queues[key].pop(0)
            order += 1
            row["shortlist_order"] = order
            row["shortlist_kept"] = True


def _boundary_null_sample_ids(
    positives: Sequence[Mapping[str, Any]], controls: Mapping[str, Any]
) -> set[str]:
    sample_size = min(int(controls["boundary_shift_null_sample_rows"]), len(positives))
    seed = int(controls["boundary_shift_null_seed"])
    ordered = sorted(
        positives,
        key=lambda row: hashlib.sha256(
            f"{seed}|{row['sample_id']}".encode("utf-8")
        ).hexdigest(),
    )[:sample_size]
    return {str(row["sample_id"]) for row in ordered}


def _boundary_null_summary(
    *,
    sample_size: int,
    true_shape_passes: int,
    true_full_passes: int,
    offsets: Mapping[int, Mapping[str, int]],
) -> dict[str, Any]:
    return {
        "sample_rows": sample_size,
        "true_shape_only_passes": true_shape_passes,
        "true_shape_only_pass_rate": true_shape_passes / sample_size,
        "true_full_no_box_passes": true_full_passes,
        "true_full_no_box_pass_rate": true_full_passes / sample_size,
        "shift_offsets": {
            str(shift): {
                "valid": int(values["valid"]),
                "shape_only_passes": int(values["shape_passes"]),
                "shape_only_pass_rate": (
                    int(values["shape_passes"]) / int(values["valid"])
                    if int(values["valid"])
                    else None
                ),
                "full_no_box_passes": int(values["full_passes"]),
                "full_no_box_pass_rate": (
                    int(values["full_passes"]) / int(values["valid"])
                    if int(values["valid"])
                    else None
                ),
            }
            for shift, values in offsets.items()
        },
        "note": "Shape-only excludes release and box height; full-no-box includes release but excludes box height because shifted geometry has no rendered box.",
    }


def _flatten_row(row: Mapping[str, Any]) -> dict[str, Any]:
    output = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "features",
            "strict_metrics",
            "box",
            "nearest_good_distance_parts",
            "nearest_bad_distance_parts",
            "nearest_family_distance_parts",
        }
        and not isinstance(value, (dict, list))
    }
    output["hard_gate_failures"] = ",".join(str(value) for value in row["hard_gate_failures"])
    output["reference_gate_failures"] = ",".join(
        str(value) for value in row["reference_gate_failures"]
    )
    for prefix in (
        "strict_metrics",
        "nearest_good_distance_parts",
        "nearest_bad_distance_parts",
        "nearest_family_distance_parts",
    ):
        for key, value in row[prefix].items():
            output[f"{prefix}.{key}"] = value
    return output


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    flattened = [_flatten_row(row) for row in rows]
    fields = sorted({key for row in flattened for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flattened)


def _safe_link(source: Path, destination: Path, expected_sha: str) -> None:
    if not source.exists() or sha256_file(source) != expected_sha:
        raise PerfectFilterError(f"shortlist source image drift: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, destination)
    if sha256_file(destination) != expected_sha:
        raise PerfectFilterError(f"hard-linked shortlist image drift: {destination}")


def _contact_sheet(rows: Sequence[Mapping[str, Any]], building: Path) -> np.ndarray:
    chosen = list(rows[:100])
    columns, tile_w, tile_h = 5, 320, 210
    row_count = max(1, math.ceil(len(chosen) / columns))
    canvas = np.full((row_count * tile_h, columns * tile_w, 3), 245, dtype=np.uint8)
    for slot, row in enumerate(chosen):
        path = building / str(row["shortlist_image_path_building"])
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise PerfectFilterError(f"contact-sheet image is unreadable: {path}")
        preview = cv2.resize(image, (tile_w, tile_h - 28), interpolation=cv2.INTER_AREA)
        y, x = (slot // columns) * tile_h, (slot % columns) * tile_w
        canvas[y + 28 : y + tile_h, x : x + tile_w] = preview
        cv2.putText(
            canvas,
            f"{slot + 1:03d} {row['symbol']} {row['direction']} {float(row['quality_score']):.3f}",
            (x + 5, y + 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.39,
            (35, 42, 48),
            1,
            cv2.LINE_AA,
        )
    return canvas


def _card(row: Mapping[str, Any], *, page_from_images: str = "../images") -> str:
    image_name = Path(str(row["shortlist_image_path"])).name
    model_image_name = Path(str(row["shortlist_model_input_image_path"])).name
    tier_dir = str(row["quality_tier"]).lower()
    metrics = row["strict_metrics"]
    return (
        "<article><h2>"
        + f"#{int(row['quality_rank']):04d} · {html.escape(str(row['symbol']))} · {row['direction']}"
        + "</h2><p class='tier'>"
        + f"{row['quality_tier']} · score {float(row['quality_score']):.3f}"
        + "</p><div class='pair'><figure><figcaption>红框审核原图</figcaption><img loading='lazy' src='"
        + f"{page_from_images}/{tier_dir}/{html.escape(image_name)}'"
        + "></figure><figure><figcaption>模型实际输入（无框）</figcaption><img loading='lazy' src='"
        + f"../model_inputs/{tier_dir}/{html.escape(model_image_name)}'"
        + "></figure></div><p>"
        + f"均线末宽 {float(metrics['six_ma_end_bandwidth_atr']):.2f}ATR · "
        + f"收拢比 {float(metrics['core_width_end_start_ratio']):.2f} · "
        + f"交叉 {int(metrics['pairwise_order_flips'])} · "
        + f"5根释放 {float(metrics['post5_progress_atr']):.2f}ATR"
        + "</p><p>"
        + f"密集 {float(row['density_topology_score']):.2f} · "
        + f"前置安静 {float(row['prelude_quietness_score']):.2f} · "
        + f"接触 {float(row['price_bundle_contact_score']):.2f} · "
        + f"释放 {float(row['release_cleanliness_score']):.2f} · "
        + f"影线/反向K {float(row['wick_reverse_cleanliness_score']):.2f} · "
        + f"参考 {float(row['reference_similarity_score']):.2f}"
        + "</p></article>"
    )


def _write_gallery(
    building: Path,
    rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    page_size: int,
) -> None:
    public = building / "public"
    pages = public / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    links: list[str] = []
    for tier in ("PERFECT_CANDIDATE", "STRONG_CANDIDATE", "STRICT_PASS"):
        tier_rows = [row for row in rows if row["quality_tier"] == tier]
        for page_index in range(math.ceil(len(tier_rows) / page_size)):
            chunk = tier_rows[page_index * page_size : (page_index + 1) * page_size]
            filename = f"{tier.lower()}_{page_index + 1:03d}.html"
            page = """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><style>
body{margin:0;background:#edf1f4;color:#17212b;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}header,main{max-width:1550px;margin:auto;padding:18px}header{background:#fff;border-bottom:1px solid #ccd5dd}main{display:grid;grid-template-columns:1fr 1fr;gap:16px}article{background:white;padding:12px;border-radius:11px;box-shadow:0 2px 9px #22334418}h2{font-size:16px;margin:0 0 4px}.tier{font-weight:700;color:#a54a00}p{font-size:13px;color:#586671}.pair{display:grid;grid-template-columns:1fr 1fr;gap:6px}figure{margin:0}figcaption{font-size:12px;color:#657482;margin:0 0 3px}img{display:block;width:100%;height:auto;border:1px solid #d1dae1}@media(max-width:850px){main{grid-template-columns:1fr}.pair{grid-template-columns:1fr}}
</style></head><body><header><a href='../index.html'>← 返回总览</a><h1>""" + html.escape(tier) + f" · 第 {page_index + 1} 页</h1><p>原始 1280×742 PNG 字节复用，没有重渲染或缩放另存。</p></header><main>" + "".join(_card(row) for row in chunk) + "</main></body></html>"
            (pages / filename).write_text(page, encoding="utf-8")
            links.append(
                f"<a href='pages/{filename}'>{tier} · {page_index + 1:03d} · {len(chunk)}张</a>"
            )
    tier_counts = summary["shortlist_tier_counts"]
    index = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>15m 完美形态严格二筛</title><style>
body{{margin:0;background:#edf1f4;color:#17212b;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header,main{{max-width:1500px;margin:auto;padding:20px}}header{{background:#fff8df;border-bottom:1px solid #d7bf76}}.facts{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.fact,.links a{{background:white;padding:13px;border-radius:10px;box-shadow:0 1px 6px #22334418}}.links{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}}.links a{{text-decoration:none;color:#075b9a}}img{{max-width:100%;border:1px solid #ccd5dd}}code{{word-break:break-all}}@media(max-width:900px){{.facts{{grid-template-columns:1fr 1fr}}.links{{grid-template-columns:1fr 1fr}}}}
</style></head><body><header><h1>15m 正样本“完美形态”严格二筛</h1><p>以历史 NEIRO #44（完美）与 FIL #42（很好）的精确 sample_id 和框坐标为正锚；NMR #48 仅作“形态可接受但偏晚”的边界样本。六均线拓扑、前置安静、价格接触、释放、影线/反向K与分段时序距离分别计算。原图逐字节复用；不改训练集、不写 label、不训练、不碰 holdout。</p><div class='facts'><div class='fact'>原始弱正例<br><b>{int(summary['n_input']):,}</b></div><div class='fact'>实际训练可见<br><b>{int(summary['n_training_data_yaml_exposed_input']):,}</b></div><div class='fact'>完美候选<br><b>{int(tier_counts.get('PERFECT_CANDIDATE',0)):,}</b></div><div class='fact'>强候选<br><b>{int(tier_counts.get('STRONG_CANDIDATE',0)):,}</b></div></div></header><main><h2>高清画廊</h2><div class='links'>{''.join(links)}</div><h2>前 100 张总览</h2><p>总览图只用于快速浏览；画廊里的单图仍是 1280×742 原始 PNG。</p><img src='../contact_sheet_top100.jpg'><h2>口径</h2><p>只有当前 data.yaml 实际暴露的 9,976 张正例能进入画廊；全部 10,000 张仍保留在完整排名中。PERFECT_CANDIDATE / STRONG_CANDIDATE 只是自动严格候选，不是 Owner Gold，也没有获得训练资格。全部分数与淘汰原因见 <code>ranked_manifest.jsonl</code> 和 <code>ranked_manifest.csv</code>。</p></main></body></html>"""
    (public / "index.html").write_text(index, encoding="utf-8")


def build(
    prereg_path: Path = DEFAULT_PREREG,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the frozen filter and publish a ranked, non-training shortlist."""

    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    _validate_prereg(prereg)
    final_dir = output_dir.resolve() if output_dir else DEFAULT_OUTPUT
    building = final_dir.with_name(f"{final_dir.name}.building")
    if final_dir.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite output: {final_dir}")
    builder_commit = verify_builder_committed(
        [
            Path(__file__),
            ROOT / "scripts" / "filter_15m_ma_launch_owner_perfect10000.py",
            prereg_path,
        ]
    )
    positives, references, accepted_family, source_contracts = _load_pinned_rows(prereg)
    aeon_parity = verify_aeon_parity_fixture(prereg["controls"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in positives:
        grouped[str(row["source_path"])].append(row)
    for row in (*references, *accepted_family):
        grouped[str(row["source_path"])].append(row)

    profiles: dict[str, ShapeProfile] = {}
    null_ids = _boundary_null_sample_ids(positives, prereg["controls"])
    null_true_shape_passes = 0
    null_true_full_passes = 0
    null_direction_flip_shape_passes = 0
    null_direction_flip_full_passes = 0
    null_offsets = {
        int(shift): {"valid": 0, "shape_passes": 0, "full_passes": 0}
        for shift in prereg["controls"]["boundary_shift_null_offsets_bars"]
    }
    used_source_audits: list[dict[str, Any]] = []
    for source_number, (source_path, rows) in enumerate(sorted(grouped.items()), 1):
        frame, audit = read_preholdout_prefix(
            _repo_path(source_path), end_exclusive=HOLDOUT_START
        )
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("filter materialized holdout OHLCV")
        contract = source_contracts.get(source_path)
        if contract is not None:
            if int(audit["rows_materialized"]) != int(contract["rows_materialized"]):
                raise PerfectFilterError(f"source row count drift: {source_path}")
            if str(audit["bounded_prefix_sha256"]) != str(contract["bounded_prefix_sha256"]):
                raise PerfectFilterError(f"source prefix SHA drift: {source_path}")
        enriched = add_candidate_features(frame)
        audit["source_path"] = source_path
        audit["manifest_rows"] = len(rows)
        audit["hash_pinned_by_positive_source_audit"] = contract is not None
        used_source_audits.append(audit)
        for row in rows:
            profile_key = _profile_key(row)
            if profile_key in profiles:
                continue
            profile = extract_profile(enriched, row)
            profiles[profile_key] = profile
            sample_id = str(row["sample_id"])
            if sample_id in null_ids and row.get("source_core_start_i") is not None:
                null_true_shape_passes += int(
                    not hard_gate_failures(
                        profile.metrics,
                        prereg["hard_gates"],
                        include_box=False,
                        include_release=False,
                    )
                )
                null_true_full_passes += int(
                    not hard_gate_failures(
                        profile.metrics,
                        prereg["hard_gates"],
                        include_box=False,
                    )
                )
                for shift, values in null_offsets.items():
                    try:
                        shifted = extract_profile(enriched, row, core_shift=shift)
                    except PerfectFilterError:
                        continue
                    values["valid"] += 1
                    values["shape_passes"] += int(
                        not hard_gate_failures(
                            shifted.metrics,
                            prereg["hard_gates"],
                            include_box=False,
                            include_release=False,
                        )
                    )
                    values["full_passes"] += int(
                        not hard_gate_failures(
                            shifted.metrics,
                            prereg["hard_gates"],
                            include_box=False,
                        )
                    )
                flipped_row = dict(row)
                flipped_row["direction"] = (
                    "SHORT" if str(row["direction"]) == "LONG" else "LONG"
                )
                flipped = extract_profile(enriched, flipped_row)
                null_direction_flip_shape_passes += int(
                    not hard_gate_failures(
                        flipped.metrics,
                        prereg["hard_gates"],
                        include_box=False,
                        include_release=False,
                    )
                )
                null_direction_flip_full_passes += int(
                    not hard_gate_failures(
                        flipped.metrics,
                        prereg["hard_gates"],
                        include_box=False,
                    )
                )
        if source_number == 1 or source_number % 20 == 0 or source_number == len(grouped):
            print(
                f"perfect-filter source {source_number:03d}/{len(grouped):03d} "
                f"rows={len(frame):>7} profiles={len(profiles):>5}",
                file=sys.stderr,
                flush=True,
            )

    expected_profiles = len(positives) + len(references) + len(accepted_family)
    if len(profiles) != expected_profiles:
        raise PerfectFilterError(
            f"profile count drift: got {len(profiles)}, expected {expected_profiles}"
        )
    scored, calibration = _score_all(
        positives, references, accepted_family, profiles, prereg
    )
    _deduplicate(scored, prereg["deduplication"])
    boundary_null = _boundary_null_summary(
        sample_size=len(null_ids),
        true_shape_passes=null_true_shape_passes,
        true_full_passes=null_true_full_passes,
        offsets=null_offsets,
    )
    direction_flip_null = {
        "sample_rows": len(null_ids),
        "true_shape_only_passes": null_true_shape_passes,
        "direction_flipped_shape_only_passes": null_direction_flip_shape_passes,
        "true_shape_only_pass_rate": null_true_shape_passes / len(null_ids),
        "direction_flipped_shape_only_pass_rate": null_direction_flip_shape_passes
        / len(null_ids),
        "true_full_no_box_passes": null_true_full_passes,
        "direction_flipped_full_no_box_passes": null_direction_flip_full_passes,
        "true_full_no_box_pass_rate": null_true_full_passes / len(null_ids),
        "direction_flipped_full_no_box_pass_rate": null_direction_flip_full_passes
        / len(null_ids),
        "time_reversed": False,
        "box_height_excluded": True,
    }

    scored.sort(
        key=lambda row: (
            not bool(row["hard_gate_pass"]),
            -float(row["quality_score"]),
            str(row["sample_id"]),
        )
    )
    for rank, row in enumerate(scored, 1):
        row["quality_rank"] = rank

    building.mkdir(parents=True)
    public = building / "public"
    for tier in ("perfect_candidate", "strong_candidate", "strict_pass"):
        (public / "images" / tier).mkdir(parents=True, exist_ok=True)
        (public / "model_inputs" / tier).mkdir(parents=True, exist_ok=True)
    shortlist = [row for row in scored if bool(row["shortlist_kept"])]
    for row in shortlist:
        tier_dir = str(row["quality_tier"]).lower()
        source = _repo_path(row["image_path"])
        filename = f"{int(row['quality_rank']):05d}_{source.name}"
        relative_building = Path("public") / "images" / tier_dir / filename
        destination = building / relative_building
        _safe_link(source, destination, str(row["image_sha256"]))
        row["shortlist_image_path_building"] = str(relative_building)
        row["shortlist_image_path"] = _relative(final_dir / relative_building)
        model_source = _repo_path(row["model_input_image_path"])
        model_relative = (
            Path("public") / "model_inputs" / tier_dir / f"{int(row['quality_rank']):05d}_{model_source.name}"
        )
        _safe_link(
            model_source,
            building / model_relative,
            str(row["model_input_image_sha256"]),
        )
        row["shortlist_model_input_image_path"] = _relative(
            final_dir / model_relative
        )

    ranked_manifest = building / "ranked_manifest.jsonl"
    ranked_csv = building / "ranked_manifest.csv"
    write_jsonl(ranked_manifest, scored)
    _write_csv(ranked_csv, scored)
    write_jsonl(building / "source_audit.jsonl", used_source_audits)

    failure_counts = Counter(
        reason for row in scored for reason in row["hard_gate_failures"]
    )
    reference_failure_counts = Counter(
        reason for row in scored for reason in row["reference_gate_failures"]
    )
    hard_pass = [row for row in scored if bool(row["hard_gate_pass"])]
    shortlist_tiers = Counter(str(row["quality_tier"]) for row in shortlist)
    summary: dict[str, Any] = {
        "experiment_id": EXPERIMENT_ID,
        "builder_commit": builder_commit,
        "preregistration_sha256": sha256_file(prereg_path),
        "n_input": len(scored),
        "n_training_data_yaml_exposed_input": sum(
            bool(row["training_data_yaml_exposed"]) for row in scored
        ),
        "n_hard_pass": len(hard_pass),
        "hard_pass_rate": len(hard_pass) / len(scored),
        "n_reference_gate_pass": sum(
            row["reference_gate_pass"] is True for row in scored
        ),
        "raw_quality_tier_counts": dict(
            Counter(str(row["quality_tier"]) for row in scored)
        ),
        "n_after_exact_dedup": sum(bool(row["exact_dedup_kept"]) for row in scored),
        "n_after_event_dedup": sum(bool(row["event_dedup_kept"]) for row in scored),
        "n_shortlist": len(shortlist),
        "n_training_data_yaml_exposed_shortlist": sum(
            bool(row["training_data_yaml_exposed"]) for row in shortlist
        ),
        "shortlist_tier_counts": dict(shortlist_tiers),
        "shortlist_direction_counts": dict(
            Counter(str(row["direction"]) for row in shortlist)
        ),
        "hard_fail_reason_counts": dict(failure_counts),
        "reference_fail_reason_counts": dict(reference_failure_counts),
        "unique_shortlist_symbols": len({str(row["symbol"]) for row in shortlist}),
        "calibration": calibration,
        "boundary_shift_null": boundary_null,
        "direction_flip_null": direction_flip_null,
        "aeon_distance_parity": aeon_parity,
        "holdout_ohlcv_rows_materialized": 0,
        "original_png_bytes_reused": True,
        "model_input_png_bytes_reused": True,
        "rerender_or_resize": False,
        "training_eligible": False,
        "production_eligible": False,
        "training_started": False,
        "manual_owner_review_workflow_created": False,
    }
    contact = _contact_sheet(shortlist, building)
    if not cv2.imwrite(
        str(building / "contact_sheet_top100.jpg"),
        contact,
        [cv2.IMWRITE_JPEG_QUALITY, 94],
    ):
        raise PerfectFilterError("failed to write contact sheet")
    _write_gallery(
        building,
        shortlist,
        summary,
        int(prereg["output"]["html_page_size"]),
    )
    summary["ranked_manifest_sha256"] = sha256_file(ranked_manifest)
    summary["ranked_csv_sha256"] = sha256_file(ranked_csv)
    summary["contact_sheet_sha256"] = sha256_file(
        building / "contact_sheet_top100.jpg"
    )
    summary["gallery_index_sha256"] = sha256_file(public / "index.html")
    write_json(building / "summary.json", summary)
    write_json(
        building / "build_receipt.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "builder_commit": builder_commit,
            "preregistration_sha256": sha256_file(prereg_path),
            "summary_sha256": sha256_file(building / "summary.json"),
            "ranked_manifest_sha256": summary["ranked_manifest_sha256"],
            "n_input": len(scored),
            "n_shortlist": len(shortlist),
            "holdout_ohlcv_rows_materialized": 0,
            "training_eligible": False,
            "production_eligible": False,
        },
    )
    os.replace(building, final_dir)
    return summary
