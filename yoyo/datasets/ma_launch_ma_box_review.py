"""Build a pre-holdout review pack for six-MA-localized YOLO box geometry.

Inputs are the frozen 15-minute t-3 weak-label manifest and each row's bounded
OHLCV source prefix.  At anchor ``t`` every proposed box searches only the
completed density interval ``[t-12, t-1]``.  The rendered chart is always 20
bars wide and ends at ``t``, ``t+1`` or ``t+2``; that display offset changes
where an already-derived box appears but never changes which bars define it.

For each candidate core length 4--7, the horizontal segment is the contiguous
same-length interval with the smallest full six-MA price envelope divided by
mean close.  Vertical bounds come only from the six MA values, receive four
source-pixel padding, and can be symmetrically expanded to a declared minimum
height at the baseline ``imgsz=960`` loader scale.  No candle high/low enters
the proposed geometry.

This module creates an Owner review surface and data-quality evidence only.  It
does not write YOLO labels, training data, model weights, eligibility flags,
ACTIVE/frozen pointers, forward state, deployment state, or order state.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import (
    read_preholdout_prefix,
    sha256_file,
)
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-15m-ma-launch-ma-box-review50-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_AMENDMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "amendment_20260827.json"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
WINDOW_BARS = 20
DENSITY_BARS = 12
CORE_LENGTHS = (4, 5, 6, 7)
MODEL_MIN_HEIGHTS = (16, 24, 32)
BASELINE_IMGSZ = 960
SOURCE_WIDTH = 1280
SOURCE_HEIGHT = 742
SOURCE_TO_MODEL_SCALE = BASELINE_IMGSZ / SOURCE_WIDTH
BASE_PADDING_SOURCE_PX = 4.0
POSITION_BINS = (("left", 0.0, 1.0 / 3.0), ("middle", 1.0 / 3.0, 2.0 / 3.0), ("right", 2.0 / 3.0, 1.0))

BLUE = (203, 116, 34)  # BGR
GOLD = (0, 166, 235)
INK = (35, 42, 48)
LIGHT = (247, 249, 251)


class MABoxReviewError(ValueError):
    """Raised when frozen identity, chronology, or geometry fails closed."""


@dataclass(frozen=True)
class SpanChoice:
    """One data-derived contiguous MA segment inside ``[t-12,t-1]``."""

    start_local: int
    end_local: int
    start_offset: int
    end_offset: int
    core_len: int
    envelope_fraction_close: float


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_builder_committed(paths: Sequence[Path]) -> str:
    """Require behavior and preregistration to be committed on main."""

    if git_output("branch", "--show-current") != "main":
        raise RuntimeError("MA-box review builder must run on main")
    relatives = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = git_output("status", "--short", "--", *relatives)
    if dirty:
        raise RuntimeError(f"MA-box review builder inputs are not committed:\n{dirty}")
    commits = [git_output("log", "-1", "--format=%H", "--", relative) for relative in relatives]
    if any(len(commit) != 40 for commit in commits):
        raise RuntimeError("could not resolve builder/config commits")
    return git_output("rev-parse", "HEAD")


def stable_crop_end_offset(sample_id: str) -> int:
    """Return deterministic display-only ``t``/``t+1``/``t+2`` crop end."""

    digest = hashlib.sha256(f"{EXPERIMENT_ID}|crop|{sample_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 3


def window_bounds(anchor_i: int, sample_id: str) -> tuple[int, int, int]:
    """Return a fixed W20 window that always contains ``[t-12,t-1]``."""

    end_offset = stable_crop_end_offset(sample_id)
    end_i = anchor_i + end_offset
    start_i = end_i - WINDOW_BARS + 1
    if start_i > anchor_i - DENSITY_BARS or end_i < anchor_i:
        raise MABoxReviewError("fixed window does not contain the causal density span")
    return start_i, end_i, end_offset


def select_tightest_span(window: pd.DataFrame, *, anchor_local: int, core_len: int) -> SpanChoice:
    """Select one same-length segment without using bar ``t`` or later.

    Score inputs are the six MA columns and close values from the proposed
    segment only.  The segment is contained in ``[t-12,t-1]``.  Exact ties
    prefer the later segment so the tie-break does not invent earlier context.
    """

    if core_len not in CORE_LENGTHS:
        raise MABoxReviewError(f"unsupported core length: {core_len}")
    search_start = anchor_local - DENSITY_BARS
    search_end = anchor_local - 1
    if search_start < 0 or search_end >= len(window):
        raise MABoxReviewError("causal density span is outside W20")
    candidates: list[tuple[float, int, int]] = []
    for start in range(search_start, search_end - core_len + 2):
        end = start + core_len - 1
        segment = window.iloc[start : end + 1]
        values = segment.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
        mean_close = float(segment["close"].mean())
        if not np.isfinite(values).all() or not np.isfinite(mean_close) or mean_close <= 0.0:
            continue
        score = float((values.max() - values.min()) / mean_close)
        candidates.append((score, -end, start))
    if not candidates:
        raise MABoxReviewError("no finite MA span candidate")
    score, negative_end, start = min(candidates)
    end = -negative_end
    return SpanChoice(
        start_local=start,
        end_local=end,
        start_offset=start - anchor_local,
        end_offset=end - anchor_local,
        core_len=core_len,
        envelope_fraction_close=score,
    )


def _preserve_extent(low: float, high: float, limit: float) -> tuple[float, float]:
    """Shift an interval inside ``[0,limit]`` without shrinking it."""

    width = high - low
    if width <= 0.0 or width > limit:
        raise MABoxReviewError("invalid requested box extent")
    if low < 0.0:
        high -= low
        low = 0.0
    if high > limit:
        low -= high - limit
        high = limit
    if low < -1e-6 or high > limit + 1e-6 or abs((high - low) - width) > 1e-5:
        raise MABoxReviewError("could not preserve box extent inside canvas")
    return max(0.0, low), min(limit, high)


def ma_box_for_span(
    transform: Any,
    window: pd.DataFrame,
    span: SpanChoice,
    *,
    min_model_height_px: int | None,
) -> dict[str, Any]:
    """Return an MA-only box with symmetric padding and loader-size metadata."""

    segment = window.iloc[span.start_local : span.end_local + 1]
    values = segment.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise MABoxReviewError("non-finite MA inside proposed span")
    step = transform.plot_w / max(transform.n_bars - 1, 1)
    x0 = transform.x_at(span.start_local) - step / 2.0
    x1 = transform.x_at(span.end_local) + step / 2.0
    y0_raw = float(transform.y_at(float(values.max())))
    y1_raw = float(transform.y_at(float(values.min())))
    if y1_raw < y0_raw:
        y0_raw, y1_raw = y1_raw, y0_raw
    center_y = (y0_raw + y1_raw) / 2.0
    padded_height = max(1.0, y1_raw - y0_raw) + 2.0 * BASE_PADDING_SOURCE_PX
    requested_source_height = padded_height
    if min_model_height_px is not None:
        requested_source_height = max(
            requested_source_height,
            float(min_model_height_px) / SOURCE_TO_MODEL_SCALE,
        )
    y0 = center_y - requested_source_height / 2.0
    y1 = center_y + requested_source_height / 2.0
    x0, x1 = _preserve_extent(x0, x1, float(transform.width))
    y0, y1 = _preserve_extent(y0, y1, float(transform.height))
    box = (x0, y0, x1, y1)
    ma_x = [transform.x_at(i) for i in range(span.start_local, span.end_local + 1)]
    ma_y = [transform.y_at(float(value)) for value in values.ravel()]
    contains_all_mas = (
        min(ma_x) >= x0 - 1e-6
        and max(ma_x) <= x1 + 1e-6
        and min(ma_y) >= y0 - 1e-6
        and max(ma_y) <= y1 + 1e-6
    )
    source_height = y1 - y0
    source_width = x1 - x0
    model_height = source_height * SOURCE_TO_MODEL_SCALE
    model_width = source_width * SOURCE_TO_MODEL_SCALE
    center_fraction = ((x0 + x1) / 2.0) / transform.width
    return {
        "x0": float(x0),
        "y0": float(y0),
        "x1": float(x1),
        "y1": float(y1),
        "cx_norm": float((x0 + x1) / 2.0 / transform.width),
        "cy_norm": float((y0 + y1) / 2.0 / transform.height),
        "w_norm": float(source_width / transform.width),
        "h_norm": float(source_height / transform.height),
        "source_width_px": float(source_width),
        "source_height_px": float(source_height),
        "baseline_model_width_px": float(model_width),
        "baseline_model_height_px": float(model_height),
        "scale_aug_0_9_width_px": float(model_width * 0.9),
        "scale_aug_0_9_height_px": float(model_height * 0.9),
        "raw_ma_height_source_px": float(y1_raw - y0_raw),
        "base_padding_source_px": BASE_PADDING_SOURCE_PX,
        "min_model_height_px": min_model_height_px,
        "contains_all_six_ma_points": bool(contains_all_mas),
        "center_fraction": float(center_fraction),
    }


def position_bin(center_fraction: float) -> str:
    for name, lower, upper in POSITION_BINS:
        if lower <= center_fraction < upper or (name == "right" and center_fraction == 1.0):
            return name
    raise MABoxReviewError(f"invalid center fraction: {center_fraction}")


def _hash_order(sample_id: str, salt: str) -> str:
    return hashlib.sha256(f"{EXPERIMENT_ID}|{salt}|{sample_id}".encode()).hexdigest()


def choose_positive_review_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Choose 50 time-stratified positives: 25/side and 40/10 train/val."""

    allocations = {("LONG", "train"): 20, ("LONG", "val"): 5, ("SHORT", "train"): 20, ("SHORT", "val"): 5}
    chosen: list[dict[str, Any]] = []
    used_symbols: set[str] = set()
    for (direction, split), target in allocations.items():
        group = [dict(row) for row in rows if row.get("direction") == direction and row.get("split") == split]
        group.sort(key=lambda row: (pd.Timestamp(row["anchor_time"]), str(row["sample_id"])))
        if len(group) < target:
            raise MABoxReviewError(f"insufficient {direction}/{split} positives")
        bins: list[list[dict[str, Any]]] = [[] for _ in range(5)]
        for rank, row in enumerate(group):
            bin_index = min(4, (rank * 5) // len(group))
            row["time_bin"] = bin_index
            bins[bin_index].append(row)
        base, remainder = divmod(target, 5)
        for bin_index, bucket in enumerate(bins):
            wanted = base + (1 if bin_index < remainder else 0)
            ordered = sorted(bucket, key=lambda row: _hash_order(str(row["sample_id"]), "positive-review"))
            unique = [row for row in ordered if str(row["symbol"]) not in used_symbols]
            selected = unique[:wanted]
            if len(selected) < wanted:
                selected_ids = {str(row["sample_id"]) for row in selected}
                selected.extend(row for row in ordered if str(row["sample_id"]) not in selected_ids)
                selected = selected[:wanted]
            if len(selected) != wanted:
                raise MABoxReviewError(f"could not fill review stratum {direction}/{split}/bin{bin_index}")
            chosen.extend(selected)
            used_symbols.update(str(row["symbol"]) for row in selected)
    if len(chosen) != 50 or len({row["sample_id"] for row in chosen}) != 50:
        raise MABoxReviewError("positive review selection is not 50 unique rows")
    return sorted(chosen, key=lambda row: (str(row["direction"]), str(row["split"]), int(row["time_bin"]), str(row["anchor_time"]), str(row["sample_id"])))


def describe(values: Sequence[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return {key: float("nan") for key in ("min", "p10", "p25", "median", "p75", "p90", "max", "mean")}
    return {
        "min": float(array.min()),
        "p10": float(np.quantile(array, 0.10)),
        "p25": float(np.quantile(array, 0.25)),
        "median": float(np.median(array)),
        "p75": float(np.quantile(array, 0.75)),
        "p90": float(np.quantile(array, 0.90)),
        "max": float(array.max()),
        "mean": float(array.mean()),
    }


def metric_row(
    row: Mapping[str, Any],
    enriched: pd.DataFrame,
    *,
    sample_kind: str,
    all_variants: bool,
) -> tuple[dict[str, Any], pd.DataFrame, Any]:
    """Compute one fixed-W20 MA geometry audit without rendering."""

    anchor_i = int(row["source_anchor_i"] if sample_kind == "positive" else row["pseudo_t_i"])
    sample_id = str(row["sample_id"])
    start_i, end_i, end_offset = window_bounds(anchor_i, sample_id)
    if start_i < 0 or end_i >= len(enriched):
        raise MABoxReviewError(f"W20 out of source bounds: {sample_id}")
    window = enriched.iloc[start_i : end_i + 1].reset_index(drop=True)
    if len(window) != WINDOW_BARS:
        raise MABoxReviewError(f"W20 length mismatch: {sample_id}")
    transform = make_chart_transform(window)
    anchor_local = anchor_i - start_i
    lengths = CORE_LENGTHS if all_variants else (5,)
    variants: dict[str, Any] = {}
    for core_len in lengths:
        span = select_tightest_span(window, anchor_local=anchor_local, core_len=core_len)
        if all_variants:
            horizontal_box = ma_box_for_span(transform, window, span, min_model_height_px=24)
            variants[f"L{core_len}_min24"] = {"span": span.__dict__, "box": horizontal_box}
        if core_len == 5:
            for minimum in (None, *MODEL_MIN_HEIGHTS):
                name = "exact" if minimum is None else f"min{minimum}"
                variants[f"L5_{name}"] = {
                    "span": span.__dict__,
                    "box": ma_box_for_span(transform, window, span, min_model_height_px=minimum),
                }
    l5 = variants["L5_min24"] if all_variants else variants["L5_min24"]
    identity_time = row.get("anchor_time") if sample_kind == "positive" else row.get("pseudo_t_time")
    out = {
        "sample_id": sample_id,
        "sample_kind": sample_kind,
        "negative_kind": row.get("negative_kind"),
        "symbol": str(row["symbol"]),
        "direction": row.get("direction"),
        "split": str(row["split"]),
        "anchor_time": str(identity_time),
        "source_path": str(row["source_path"]),
        "source_anchor_i": anchor_i,
        "window_start_i": start_i,
        "window_end_i": end_i,
        "window_end_offset": end_offset,
        "window_bars": WINDOW_BARS,
        "density_search_start_offset": -DENSITY_BARS,
        "density_search_end_offset": -1,
        "l5_envelope_fraction_close": float(l5["span"]["envelope_fraction_close"]),
        "l5_start_offset": int(l5["span"]["start_offset"]),
        "l5_end_offset": int(l5["span"]["end_offset"]),
        "l5_min24_model_height_px": float(l5["box"]["baseline_model_height_px"]),
        "l5_min24_scale0_9_height_px": float(l5["box"]["scale_aug_0_9_height_px"]),
        "l5_min24_model_width_px": float(l5["box"]["baseline_model_width_px"]),
        "l5_min24_center_fraction": float(l5["box"]["center_fraction"]),
        "l5_min24_position_bin": position_bin(float(l5["box"]["center_fraction"])),
        "all_six_ma_inside_l5_min24": bool(l5["box"]["contains_all_six_ma_points"]),
    }
    if all_variants:
        out["variants"] = variants
    return out, window, transform


def choose_negative_review_rows(rows: Sequence[Mapping[str, Any]], per_kind: int = 12) -> list[dict[str, Any]]:
    """Choose density-quantile examples from easy and hard negative metrics."""

    chosen: list[dict[str, Any]] = []
    for kind in ("easy", "hard"):
        subset = sorted((dict(row) for row in rows if row.get("negative_kind") == kind), key=lambda row: (float(row["l5_envelope_fraction_close"]), str(row["sample_id"])))
        if len(subset) < per_kind:
            raise MABoxReviewError(f"insufficient {kind} negative metrics")
        indexes = [int(round(value)) for value in np.linspace(0, len(subset) - 1, per_kind)]
        picked: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index in indexes:
            candidates = range(index, len(subset))
            selected = next((subset[i] for i in candidates if str(subset[i]["sample_id"]) not in seen_ids), None)
            if selected is None:
                selected = next(row for row in subset if str(row["sample_id"]) not in seen_ids)
            selected = dict(selected)
            selected["density_quantile_label"] = f"q{len(picked)/(per_kind-1):.2f}"
            picked.append(selected)
            seen_ids.add(str(selected["sample_id"]))
        chosen.extend(picked)
    return chosen


def _draw_box(image: np.ndarray, box: Mapping[str, Any], label: str, color: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = (int(round(float(box[key]))) for key in ("x0", "y0", "x1", "y1"))
    cv2.rectangle(image, (x0, y0), (x1, y1), color, 4, cv2.LINE_AA)
    (width, height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)
    text_y = max(height + 7, y0 - 6)
    cv2.rectangle(image, (x0, text_y - height - 6), (min(image.shape[1] - 1, x0 + width + 8), text_y + baseline + 2), (255, 255, 255), -1)
    cv2.putText(image, label, (x0 + 3, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2, cv2.LINE_AA)


def _panel(image: np.ndarray, title: str, subtitle: str, width: int = 470) -> np.ndarray:
    scale = width / image.shape[1]
    resized = cv2.resize(image, (width, int(round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    header = np.full((60, width, 3), LIGHT, dtype=np.uint8)
    cv2.putText(header, title, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.57, INK, 2, cv2.LINE_AA)
    cv2.putText(header, subtitle, (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (78, 86, 94), 1, cv2.LINE_AA)
    return np.vstack((header, resized))


def render_positive_assets(
    metric: Mapping[str, Any],
    window: pd.DataFrame,
    output_images: Path,
) -> dict[str, Any]:
    """Write one clean W20 chart and two four-panel audit rows."""

    raw, _ = render_chart(window, out_path=None)
    sample_id = str(metric["sample_id"])
    raw_path = output_images / f"{sample_id}.png"
    cv2.imwrite(str(raw_path), raw, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    horizontal_panels: list[np.ndarray] = []
    for core_len in CORE_LENGTHS:
        variant = metric["variants"][f"L{core_len}_min24"]
        canvas = raw.copy()
        _draw_box(canvas, variant["box"], f"L{core_len} / min24", BLUE)
        horizontal_panels.append(
            _panel(
                canvas,
                f"L{core_len} bars",
                f"offset {variant['span']['start_offset']}..{variant['span']['end_offset']} | model h={variant['box']['baseline_model_height_px']:.1f}px",
            )
        )
    horizontal = np.hstack(horizontal_panels)
    horizontal_path = output_images / f"{sample_id}_horizontal.png"
    cv2.imwrite(str(horizontal_path), horizontal, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    vertical_panels: list[np.ndarray] = []
    for name, title in (("exact", "Exact + 4px pad"), ("min16", "Minimum 16px"), ("min24", "Minimum 24px"), ("min32", "Minimum 32px")):
        variant = metric["variants"][f"L5_{name}"]
        canvas = raw.copy()
        _draw_box(canvas, variant["box"], title, GOLD)
        vertical_panels.append(
            _panel(
                canvas,
                title,
                f"baseline model h={variant['box']['baseline_model_height_px']:.1f}px | scale0.9={variant['box']['scale_aug_0_9_height_px']:.1f}px",
            )
        )
    vertical = np.hstack(vertical_panels)
    vertical_path = output_images / f"{sample_id}_vertical.png"
    cv2.imwrite(str(vertical_path), vertical, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    return {
        "image_path": str(raw_path.relative_to(ROOT)),
        "image_sha256": sha256_file(raw_path),
        "horizontal_comparison_path": str(horizontal_path.relative_to(ROOT)),
        "horizontal_comparison_sha256": sha256_file(horizontal_path),
        "vertical_comparison_path": str(vertical_path.relative_to(ROOT)),
        "vertical_comparison_sha256": sha256_file(vertical_path),
    }


def render_negative_asset(metric: Mapping[str, Any], window: pd.DataFrame, output_images: Path) -> dict[str, Any]:
    """Render one empty-label negative with a counterfactual MA-knot outline."""

    raw, _ = render_chart(window, out_path=None)
    variant = metric["variants"]["L5_min24"]
    _draw_box(raw, variant["box"], "counterfactual L5/min24 (NOT A LABEL)", GOLD)
    path = output_images / f"neg_{metric['negative_kind']}_{metric['sample_id']}.png"
    cv2.imwrite(str(path), raw, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    return {"counterfactual_image_path": str(path.relative_to(ROOT)), "counterfactual_image_sha256": sha256_file(path)}


def _materialized_asset_path(asset_path: str, *, building: Path, final_dir: Path) -> Path:
    """Resolve a final artifact reference while the atomic build is unfinished."""

    final_path = ROOT / asset_path
    try:
        relative = final_path.relative_to(final_dir)
    except ValueError:
        return final_path
    return building / relative


def _remap_asset_paths(
    assets: dict[str, Any],
    fields: Sequence[str],
    *,
    building: Path,
    final_dir: Path,
) -> None:
    """Replace temporary ``results.building`` references with final paths."""

    for field in fields:
        materialized = ROOT / str(assets[field])
        try:
            relative = materialized.relative_to(building)
        except ValueError as exc:
            raise MABoxReviewError(f"asset is outside atomic build directory: {materialized}") from exc
        assets[field] = str((final_dir / relative).relative_to(ROOT))


def make_overview(
    rows: Sequence[Mapping[str, Any]],
    output_path: Path,
    field: str,
    *,
    building: Path,
    final_dir: Path,
    count: int = 12,
) -> None:
    """Create a two-column static overview from rendered comparison images."""

    selected = list(rows)[:count]
    panels: list[np.ndarray] = []
    for row in selected:
        image_path = _materialized_asset_path(str(row[field]), building=building, final_dir=final_dir)
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(image_path)
        width = 1200
        scale = width / image.shape[1]
        panels.append(cv2.resize(image, (width, int(round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA))
    canvas = np.vstack(panels)
    cv2.imwrite(str(output_path), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3])


def build_distribution_chart(all_metrics: Sequence[Mapping[str, Any]], output_path: Path) -> None:
    """Plot MA-density overlap and proposed loader box-size distributions."""

    groups = {
        "positive": [row for row in all_metrics if row["sample_kind"] == "positive"],
        "negative hard": [row for row in all_metrics if row.get("negative_kind") == "hard"],
        "negative easy": [row for row in all_metrics if row.get("negative_kind") == "easy"],
    }
    colors = {"positive": "#2f6f9f", "negative hard": "#d39c29", "negative easy": "#9aa5ad"}
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2), constrained_layout=True)
    combined = np.asarray([float(row["l5_envelope_fraction_close"]) * 100.0 for row in all_metrics])
    cap = float(np.quantile(combined, 0.99))
    bins = np.linspace(0.0, cap, 55)
    for name, rows in groups.items():
        values = np.asarray([float(row["l5_envelope_fraction_close"]) * 100.0 for row in rows])
        axes[0].hist(np.clip(values, None, cap), bins=bins, histtype="step", density=True, linewidth=2.0, label=f"{name} n={len(rows):,}", color=colors[name])
    axes[0].set_title("L5 six-MA envelope distribution")
    axes[0].set_xlabel(f"envelope / mean close (%) | >p99 {cap:.2f}% clipped")
    axes[0].set_ylabel("density")
    axes[0].legend()

    positives = groups["positive"]
    axes[1].hist([float(row["l5_min24_model_width_px"]) for row in positives], bins=40, color="#2f6f9f", alpha=0.86)
    axes[1].set_title("Proposed L5 box width at imgsz=960")
    axes[1].set_xlabel("model-input pixels")
    axes[1].set_ylabel("positive samples")

    position_counts = Counter(str(row["l5_min24_position_bin"]) for row in positives)
    labels = ["left", "middle", "right"]
    axes[2].bar(labels, [position_counts[label] for label in labels], color="#d39c29")
    axes[2].set_title("Proposed L5 box center positions")
    axes[2].set_xlabel("normalized image third")
    axes[2].set_ylabel("positive samples")
    fig.savefig(output_path, dpi=150, facecolor="white")
    plt.close(fig)


def _box_style(box: Mapping[str, Any]) -> str:
    return (
        f"left:{100*float(box['x0'])/SOURCE_WIDTH:.6f}%;"
        f"top:{100*float(box['y0'])/SOURCE_HEIGHT:.6f}%;"
        f"width:{100*(float(box['x1'])-float(box['x0']))/SOURCE_WIDTH:.6f}%;"
        f"height:{100*(float(box['y1'])-float(box['y0']))/SOURCE_HEIGHT:.6f}%"
    )


def build_review_html(rows: Sequence[Mapping[str, Any]], prereg_sha: str, manifest_sha: str) -> str:
    """Build an offline one-sample-at-a-time geometry review surface."""

    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        item = {
            "sample_id": row["sample_id"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "split": row["split"],
            "anchor_time": row["anchor_time"],
            "source_order": index,
            "time_bin": row["time_bin"],
            "image": Path(os.path.relpath(ROOT / str(row["image_path"]), (ROOT / str(row["review_html_path"])).parent)).as_posix(),
            "image_sha256": row["image_sha256"],
            "horizontal": {
                str(length): {
                    "style": _box_style(row["variants"][f"L{length}_min24"]["box"]),
                    "start": row["variants"][f"L{length}_min24"]["span"]["start_offset"],
                    "end": row["variants"][f"L{length}_min24"]["span"]["end_offset"],
                }
                for length in CORE_LENGTHS
            },
            "vertical": {
                name: {
                    "style": _box_style(row["variants"][f"L5_{name}"]["box"]),
                    "model_height": row["variants"][f"L5_{name}"]["box"]["baseline_model_height_px"],
                    "scale09_height": row["variants"][f"L5_{name}"]["box"]["scale_aug_0_9_height_px"],
                }
                for name in ("exact", "min16", "min24", "min32")
            },
        }
        items.append(item)
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"), sort_keys=True).replace("</", "<\\/")
    config = json.dumps({"experiment_id": EXPERIMENT_ID, "prereg_sha256": prereg_sha, "review_manifest_sha256": manifest_sha}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return REVIEW_HTML.replace("__ITEMS__", payload).replace("__CONFIG__", config).replace("__TOTAL__", str(len(items)))


def validate_owner_review_payload(
    payload: Mapping[str, Any],
    review_rows: Sequence[Mapping[str, Any]],
    *,
    prereg_sha256: str,
    review_manifest_sha256: str,
) -> dict[str, Any]:
    """Validate exported review answers against frozen sample identity.

    This gate verifies only that the Owner reviewed the exact rendered files.
    It deliberately does not convert protocol choices into Gold labels or
    training eligibility.
    """

    if int(payload.get("schema_version", -1)) != 1:
        raise MABoxReviewError("unsupported owner review schema")
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise MABoxReviewError("owner review experiment id drifted")
    if payload.get("prereg_sha256") != prereg_sha256:
        raise MABoxReviewError("owner review preregistration hash drifted")
    if payload.get("review_manifest_sha256") != review_manifest_sha256:
        raise MABoxReviewError("owner review manifest hash drifted")
    expected = {str(row["sample_id"]): row for row in review_rows}
    answers = list(payload.get("answers") or [])
    if len(expected) != len(review_rows):
        raise MABoxReviewError("review manifest contains duplicate sample ids")
    if int(payload.get("n_total", -1)) != len(expected):
        raise MABoxReviewError("owner review n_total drifted")
    answer_ids = [str(answer.get("sample_id")) for answer in answers]
    if len(answer_ids) != len(set(answer_ids)):
        raise MABoxReviewError("owner review contains duplicate answers")
    if set(answer_ids) != set(expected):
        missing = sorted(set(expected) - set(answer_ids))
        extra = sorted(set(answer_ids) - set(expected))
        raise MABoxReviewError(f"owner review is incomplete or has unknown ids: missing={missing[:5]} extra={extra[:5]}")
    allowed_decisions = {"ACCEPT", "ADJUST", "UNCERTAIN"}
    decision_counts: Counter[str] = Counter()
    length_counts: Counter[int] = Counter()
    height_counts: Counter[int] = Counter()
    for answer in answers:
        sample_id = str(answer["sample_id"])
        row = expected[sample_id]
        for field in ("symbol", "direction", "anchor_time", "image_sha256"):
            if answer.get(field) != row.get(field):
                raise MABoxReviewError(f"owner review identity drift for {sample_id}: {field}")
        decision = str(answer.get("decision"))
        if decision not in allowed_decisions:
            raise MABoxReviewError(f"invalid owner review decision for {sample_id}: {decision}")
        preferred_length = answer.get("preferred_core_len")
        preferred_height = answer.get("preferred_min_model_px")
        if preferred_length is not None and int(preferred_length) not in CORE_LENGTHS:
            raise MABoxReviewError(f"invalid core length for {sample_id}")
        if preferred_height is not None and int(preferred_height) not in MODEL_MIN_HEIGHTS:
            raise MABoxReviewError(f"invalid minimum height for {sample_id}")
        if decision == "ACCEPT" and (preferred_length is None or preferred_height is None):
            raise MABoxReviewError(f"accepted answer lacks geometry choices: {sample_id}")
        decision_counts[decision] += 1
        if preferred_length is not None:
            length_counts[int(preferred_length)] += 1
        if preferred_height is not None:
            height_counts[int(preferred_height)] += 1
    declared_answered = int(payload.get("n_answered", -1))
    if declared_answered != len(answers) or payload.get("complete") is not True:
        raise MABoxReviewError("owner review export is not declared complete")
    return {
        "experiment_id": EXPERIMENT_ID,
        "status": "owner_review_complete_pending_global_protocol_decision",
        "review_manifest_sha256": review_manifest_sha256,
        "preregistration_sha256": prereg_sha256,
        "n_reviewed": len(answers),
        "decision_counts": dict(sorted(decision_counts.items())),
        "preferred_core_length_counts": {str(key): value for key, value in sorted(length_counts.items())},
        "preferred_min_model_height_counts": {str(key): value for key, value in sorted(height_counts.items())},
        "sample_owner_confirmed": False,
        "training_eligible": False,
        "production_eligible": False,
        "next_gate": "Owner explicitly chooses one global geometry protocol; then every proposed full-dataset sample still requires sample-level Gold confirmation.",
    }


REVIEW_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><link rel="icon" href="data:,"><title>15m 六均线框协议 Review50</title>
<style>
:root{--ink:#18222c;--muted:#657382;--line:#d4dde5;--bg:#eef2f5;--card:#fff;--blue:#2376a8;--gold:#d79a20;--green:#19845c;--red:#bd4650;--amber:#ac771e}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}header{position:sticky;top:0;z-index:10;background:#fffffff5;border-bottom:1px solid var(--line)}.top,main{max-width:1540px;margin:auto;padding:14px 18px}.title{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}h1{font-size:24px;margin:0}.muted{color:var(--muted)}.progress{height:7px;background:#e1e7ec;border-radius:99px;margin:9px 0;overflow:hidden}.progress span{display:block;height:100%;background:var(--green);width:0}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.toolbar button,.toolbar select{border:1px solid #b8c4ce;background:#fff;border-radius:8px;padding:7px 10px;font:inherit;cursor:pointer}.toolbar .primary{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:800}.stats{margin-left:auto;font-weight:800}.notice{background:#fff7df;border:1px solid #e6c56e;border-radius:10px;padding:10px 13px;line-height:1.55;margin-bottom:13px}.card{background:var(--card);border-radius:13px;box-shadow:0 2px 13px #1b304018;overflow:hidden}.head{display:flex;gap:10px;align-items:center;padding:11px 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}.badge{border-radius:99px;padding:4px 9px;font-weight:800}.LONG{background:#dff1ff;color:#126899}.SHORT{background:#ffe4e7;color:#a32734}.identity{font-weight:800}.meta{font-size:13px;color:var(--muted)}.current{margin-left:auto;font-weight:800}.section{padding:12px 14px;border-bottom:1px solid var(--line)}.section h2{font-size:17px;margin:0 0 5px}.hint{color:var(--muted);font-size:13px;margin-bottom:9px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}.variant{border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}.vtitle{padding:7px 9px;font-weight:800;font-size:13px;background:#f5f7f9}.stage{position:relative;aspect-ratio:1280/742;background:#fff}.stage img{position:absolute;inset:0;width:100%;height:100%;display:block}.box{position:absolute;border:3px solid var(--blue);background:#2376a80d;pointer-events:none}.vertical .box{border-color:var(--gold);background:#d79a200d}.choicearea{display:grid;grid-template-columns:1fr 1fr 1.2fr;gap:12px;padding:13px 14px}.group b{display:block;margin-bottom:7px}.options{display:flex;gap:7px;flex-wrap:wrap}.options button{border:1px solid #b8c4ce;background:#fff;border-radius:8px;padding:9px 12px;font-weight:800;cursor:pointer}.options button.active{background:#193f56;color:#fff;border-color:#193f56}.actions button{color:#fff;border:0}.actions [data-decision=ACCEPT]{background:var(--green)}.actions [data-decision=ADJUST]{background:var(--red)}.actions [data-decision=UNCERTAIN]{background:var(--amber)}textarea{width:100%;margin-top:9px;border:1px solid #b8c4ce;border-radius:8px;padding:8px;font:inherit}.nav{display:flex;gap:8px;margin-top:10px}.nav button{border:1px solid #b8c4ce;background:#fff;border-radius:8px;padding:8px 11px;cursor:pointer}footer{max-width:1540px;margin:auto;padding:13px 18px 50px;color:var(--muted);font-size:13px;line-height:1.55}@media(max-width:980px){header{position:static}.grid{grid-template-columns:repeat(2,1fr)}.choicearea{grid-template-columns:1fr}.stats{width:100%;margin-left:0}}@media(max-width:560px){.grid{grid-template-columns:1fr}}
</style></head><body><header><div class="top"><div class="title"><h1>15m 六均线框协议 Review50</h1><span class="muted" id="position"></span></div><div class="progress"><span id="bar"></span></div><div class="toolbar"><select id="side"><option value="ALL">全部方向</option><option value="LONG">LONG</option><option value="SHORT">SHORT</option></select><select id="status"><option value="ALL">全部状态</option><option value="PENDING" selected>未审核</option><option value="ACCEPT">接受</option><option value="ADJUST">需调整</option><option value="UNCERTAIN">待定</option></select><button id="prev">上一张</button><button id="next">下一张</button><button id="export" class="primary">导出审核 JSON</button><span class="stats" id="stats"></span></div></div></header><main><div class="notice"><strong>这仍是协议小样，不是 Gold 数据集。</strong>所有图固定 W20；框只用 t-12..t-1 的六均线，绝不读取 K 线 high/low 定上下边界。第一行只比较横向 4/5/6/7 根，统一使用模型输入最小 24px；第二行固定 L5，只比较纵向最小高度。页面不写仓库、不生成 YOLO label、没有训练按钮。</div><section class="card"><div class="head"><span id="badge" class="badge"></span><span class="identity" id="identity"></span><span class="meta" id="meta"></span><span class="current" id="current"></span></div><div class="section"><h2>横向范围：哪一种最像完整的均线密集结？</h2><div class="hint">每个 L 都在 t-12..t-1 内独立寻找同长度最密段；颜色和文字标签之外，底图完全相同。</div><div class="grid" id="horizontal"></div></div><div class="section vertical"><h2>纵向高度：多厚才既包住均线又不会变成超薄小目标？</h2><div class="hint">全部固定 L5；16/24/32 指未增强的 imgsz=960 模型输入像素，scale=0.9 时会再缩小 10%。</div><div class="grid" id="vertical"></div></div><div class="choicearea"><div class="group"><b>本张横向更合适</b><div class="options" id="lengthChoices"></div></div><div class="group"><b>本张最小高度更合适</b><div class="options" id="heightChoices"></div></div><div class="group"><b>整体裁决</b><div class="options actions"><button data-decision="ACCEPT">接受候选</button><button data-decision="ADJUST">需调整</button><button data-decision="UNCERTAIN">待定</button></div><textarea id="note" rows="2" maxlength="1200" placeholder="说明哪一处不对（可空）"></textarea><div class="nav"><button id="clear">清除本张</button></div></div></div></section></main><footer>进度只保存在本浏览器 localStorage。导出 JSON 后仍需 fail-closed 接回；协议共识不等于逐样本 Gold，不会自动物化全量标签或启动训练。</footer><script>
const ITEMS=__ITEMS__,CONFIG=__CONFIG__,BY=new Map(ITEMS.map(x=>[x.sample_id,x])),KEY=`ma-box-review::${CONFIG.experiment_id}::${CONFIG.review_manifest_sha256}`;let state={answers:{},cursor:null,side:'ALL',status:'PENDING'};try{state={...state,...JSON.parse(localStorage.getItem(KEY)||'{}')}}catch(_){ }const $=id=>document.getElementById(id);const decision=i=>state.answers[i.sample_id]?.decision||'PENDING';function filtered(){return ITEMS.filter(i=>(state.side==='ALL'||i.direction===state.side)&&(state.status==='ALL'||decision(i)===state.status))}function current(){return BY.get(state.cursor)||filtered()[0]||ITEMS[0]}function save(){localStorage.setItem(KEY,JSON.stringify(state))}function boxPanel(item,title,spec,vertical=false){return `<div class="variant"><div class="vtitle">${title}</div><div class="stage"><img src="${item.image}"><div class="box" style="${spec.style}"></div></div></div>`}function stats(){const c={PENDING:0,ACCEPT:0,ADJUST:0,UNCERTAIN:0};ITEMS.forEach(i=>c[decision(i)]++);const done=ITEMS.length-c.PENDING;$('stats').textContent=`已审 ${done}/${ITEMS.length} · 接受 ${c.ACCEPT} · 调整 ${c.ADJUST} · 待定 ${c.UNCERTAIN}`;$('bar').style.width=`${100*done/ITEMS.length}%`}function render(){let list=filtered();if(!list.length){state.status='ALL';$('status').value='ALL';list=filtered()}let item=current();if(!list.some(i=>i.sample_id===item.sample_id)){item=list[0];state.cursor=item.sample_id}const a=state.answers[item.sample_id]||{},idx=list.findIndex(i=>i.sample_id===item.sample_id);$('position').textContent=`筛选内 ${idx+1}/${list.length} · 全局 ${item.source_order}/__TOTAL__`;$('badge').textContent=item.direction;$('badge').className=`badge ${item.direction}`;$('identity').textContent=`${item.symbol} · ${item.sample_id}`;$('meta').textContent=`${item.anchor_time} · ${item.split} · time-bin ${item.time_bin}`;$('current').textContent=a.decision||'PENDING';$('horizontal').innerHTML=[4,5,6,7].map(L=>{const s=item.horizontal[String(L)];return boxPanel(item,`L${L} · t${s.start}..t${s.end}`,s)}).join('');$('vertical').innerHTML=['exact','min16','min24','min32'].map(name=>{const s=item.vertical[name];return boxPanel(item,`${name} · ${s.model_height.toFixed(1)}px / scale0.9 ${s.scale09_height.toFixed(1)}px`,s,true)}).join('');$('lengthChoices').innerHTML=[4,5,6,7].map(v=>`<button data-length="${v}" class="${Number(a.preferred_core_len)===v?'active':''}">L${v}</button>`).join('');$('heightChoices').innerHTML=[16,24,32].map(v=>`<button data-height="${v}" class="${Number(a.preferred_min_model_px)===v?'active':''}">${v}px</button>`).join('');document.querySelectorAll('[data-length]').forEach(b=>b.onclick=()=>setField('preferred_core_len',Number(b.dataset.length)));document.querySelectorAll('[data-height]').forEach(b=>b.onclick=()=>setField('preferred_min_model_px',Number(b.dataset.height)));document.querySelectorAll('[data-decision]').forEach(b=>b.classList.toggle('active',b.dataset.decision===a.decision));$('note').value=a.note||'';stats();save()}function setField(k,v){const i=current();state.answers[i.sample_id]={...(state.answers[i.sample_id]||{}),sample_id:i.sample_id,symbol:i.symbol,direction:i.direction,anchor_time:i.anchor_time,image_sha256:i.image_sha256,[k]:v,reviewed_at:new Date().toISOString()};save();render()}function decide(v){const i=current(),a=state.answers[i.sample_id]||{};if(v==='ACCEPT'&&(!a.preferred_core_len||!a.preferred_min_model_px)){alert('接受前必须选横向 L 和最小高度。');return}state.answers[i.sample_id]={...a,sample_id:i.sample_id,symbol:i.symbol,direction:i.direction,anchor_time:i.anchor_time,image_sha256:i.image_sha256,decision:v,note:$('note').value||null,reviewed_at:new Date().toISOString()};save();step(1)}function step(d){const list=filtered();if(!list.length)return;let n=list.findIndex(i=>i.sample_id===current().sample_id);if(n<0)n=0;state.cursor=list[Math.max(0,Math.min(list.length-1,n+d))].sample_id;save();render()}$('prev').onclick=()=>step(-1);$('next').onclick=()=>step(1);$('side').onchange=e=>{state.side=e.target.value;state.cursor=null;save();render()};$('status').onchange=e=>{state.status=e.target.value;state.cursor=null;save();render()};document.querySelectorAll('[data-decision]').forEach(b=>b.onclick=()=>decide(b.dataset.decision));$('note').oninput=e=>{const i=current(),a=state.answers[i.sample_id]||{};state.answers[i.sample_id]={...a,sample_id:i.sample_id,symbol:i.symbol,direction:i.direction,anchor_time:i.anchor_time,image_sha256:i.image_sha256,note:e.target.value||null};save()};$('clear').onclick=()=>{delete state.answers[current().sample_id];save();render()};$('export').onclick=()=>{const answers=ITEMS.map(i=>state.answers[i.sample_id]).filter(Boolean),out={schema_version:1,...CONFIG,exported_at:new Date().toISOString(),n_total:ITEMS.length,n_answered:answers.filter(a=>a.decision).length,complete:answers.filter(a=>a.decision).length===ITEMS.length,answers},blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${CONFIG.experiment_id}_answers_${out.n_answered}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),0)};if(!state.cursor)state.cursor=filtered()[0]?.sample_id||ITEMS[0].sample_id;$('side').value=state.side;$('status').value=state.status;render();
</script></body></html>'''


def summarize_metrics(all_metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups = {
        "positive": [row for row in all_metrics if row["sample_kind"] == "positive"],
        "negative_hard": [row for row in all_metrics if row.get("negative_kind") == "hard"],
        "negative_easy": [row for row in all_metrics if row.get("negative_kind") == "easy"],
    }
    positive_median = float(np.median([float(row["l5_envelope_fraction_close"]) for row in groups["positive"]]))
    result: dict[str, Any] = {
        "counts": {name: len(rows) for name, rows in groups.items()},
        "l5_envelope_fraction_close": {name: describe([float(row["l5_envelope_fraction_close"]) for row in rows]) for name, rows in groups.items()},
        "share_at_or_denser_than_positive_median": {
            name: float(np.mean([float(row["l5_envelope_fraction_close"]) <= positive_median for row in rows]))
            for name, rows in groups.items()
        },
    }
    positives = groups["positive"]
    result["proposed_l5_min24"] = {
        "model_width_px": describe([float(row["l5_min24_model_width_px"]) for row in positives]),
        "model_height_px": describe([float(row["l5_min24_model_height_px"]) for row in positives]),
        "scale0_9_height_px": describe([float(row["l5_min24_scale0_9_height_px"]) for row in positives]),
        "position_bins": dict(sorted(Counter(str(row["l5_min24_position_bin"]) for row in positives).items())),
        "center_fraction": describe([float(row["l5_min24_center_fraction"]) for row in positives]),
        "all_six_ma_inside": sum(bool(row["all_six_ma_inside_l5_min24"]) for row in positives),
    }
    return result


def build(prereg_path: Path = DEFAULT_PREREG, output_dir: Path | None = None) -> dict[str, Any]:
    """Build the official non-training Review50 artifact atomically."""

    prereg_path = prereg_path.resolve()
    prereg = read_json(prereg_path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise MABoxReviewError("unexpected review experiment id")
    amendment_path = DEFAULT_AMENDMENT.resolve()
    amendment = read_json(amendment_path)
    if amendment.get("experiment_id") != EXPERIMENT_ID:
        raise MABoxReviewError("unexpected review amendment experiment id")
    builder_commit = verify_builder_committed(
        [
            Path(__file__).resolve(),
            prereg_path,
            amendment_path,
            ROOT / "scripts" / "build_15m_ma_launch_ma_box_review50.py",
        ]
    )
    source_manifest = (ROOT / prereg["source"]["dataset_manifest_path"]).resolve()
    source_prereg = (ROOT / prereg["source"]["dataset_preregistration_path"]).resolve()
    for path, expected in ((source_manifest, prereg["source"]["dataset_manifest_sha256"]), (source_prereg, prereg["source"]["dataset_preregistration_sha256"])):
        if sha256_file(path) != expected:
            raise MABoxReviewError(f"hash-pinned source drifted: {path}")
    manifest = read_jsonl(source_manifest)
    positives = [row for row in manifest if row.get("sample_kind") == "positive_weak"]
    negatives = [row for row in manifest if str(row.get("sample_kind", "")).startswith("negative_")]
    if len(positives) != 9_938 or len(negatives) != 26_874:
        raise MABoxReviewError("frozen dataset row counts drifted")
    selected_positive_source = choose_positive_review_rows(positives)
    selected_ids = {str(row["sample_id"]) for row in selected_positive_source}

    final_dir = output_dir.resolve() if output_dir else prereg_path.parent / "results"
    building = final_dir.with_name(f"{final_dir.name}.building")
    try:
        final_dir.relative_to(ROOT)
    except ValueError as exc:
        raise MABoxReviewError("review output must stay inside the repository") from exc
    if final_dir.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite review artifact: {final_dir}")
    public = building / "public"
    positive_images = public / "images" / "positive"
    negative_images = public / "images" / "negative"
    positive_images.mkdir(parents=True)
    negative_images.mkdir(parents=True)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest:
        grouped[str(row["source_path"])].append(row)
    all_metrics: list[dict[str, Any]] = []
    review_metrics: list[dict[str, Any]] = []
    source_audits: list[dict[str, Any]] = []
    unavailable_negatives: list[dict[str, Any]] = []
    for ordinal, (source_path, source_rows) in enumerate(sorted(grouped.items()), start=1):
        frame, source_audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        if int(source_audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("holdout OHLCV materialized")
        enriched = add_six_mas(frame)
        source_audits.append(source_audit)
        for source_row in source_rows:
            is_positive = source_row.get("sample_kind") == "positive_weak"
            try:
                metric, window, _ = metric_row(
                    source_row,
                    enriched,
                    sample_kind="positive" if is_positive else "negative",
                    all_variants=str(source_row["sample_id"]) in selected_ids,
                )
            except MABoxReviewError as exc:
                if is_positive:
                    raise
                unavailable_negatives.append(
                    {
                        "sample_id": str(source_row["sample_id"]),
                        "sample_kind": str(source_row["sample_kind"]),
                        "negative_kind": str(source_row["negative_kind"]),
                        "symbol": str(source_row["symbol"]),
                        "split": str(source_row["split"]),
                        "pseudo_t_i": int(source_row["pseudo_t_i"]),
                        "pseudo_t_time": str(source_row["pseudo_t_time"]),
                        "source_path": str(source_row["source_path"]),
                        "reason": str(exc),
                    }
                )
                continue
            all_metrics.append({key: value for key, value in metric.items() if key != "variants"})
            if str(source_row["sample_id"]) in selected_ids:
                selected_source = next(row for row in selected_positive_source if str(row["sample_id"]) == str(source_row["sample_id"]))
                metric["time_bin"] = int(selected_source["time_bin"])
                assets = render_positive_assets(metric, window, positive_images)
                _remap_asset_paths(
                    assets,
                    ("image_path", "horizontal_comparison_path", "vertical_comparison_path"),
                    building=building,
                    final_dir=final_dir,
                )
                metric.update(assets)
                review_metrics.append(metric)
        if ordinal == 1 or ordinal % 20 == 0 or ordinal == len(grouped):
            accounted = len(all_metrics) + len(unavailable_negatives)
            print(f"review50 audited sources {ordinal}/{len(grouped)}; rows {accounted}/{len(manifest)}")

    all_metrics.sort(key=lambda row: (str(row["sample_kind"]), str(row["sample_id"])))
    unavailable_negatives.sort(key=lambda row: (str(row["negative_kind"]), str(row["sample_id"])))
    if len(all_metrics) + len(unavailable_negatives) != len(manifest):
        raise MABoxReviewError("valid plus unavailable metric rows do not account for the frozen manifest")
    review_metrics.sort(key=lambda row: (str(row["direction"]), str(row["split"]), int(row["time_bin"]), str(row["anchor_time"]), str(row["sample_id"])))
    negative_review_source = choose_negative_review_rows(all_metrics)
    negative_review: list[dict[str, Any]] = []
    negative_rows_by_id = {str(row["sample_id"]): row for row in negatives}
    by_source_negative_ids: dict[str, list[str]] = defaultdict(list)
    for row in negative_review_source:
        by_source_negative_ids[str(row["source_path"])].append(str(row["sample_id"]))
    for source_path, ids in sorted(by_source_negative_ids.items()):
        frame, audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("holdout OHLCV materialized in negative render")
        enriched = add_six_mas(frame)
        for sample_id in ids:
            source_row = negative_rows_by_id[sample_id]
            metric, window, _ = metric_row(source_row, enriched, sample_kind="negative", all_variants=True)
            quantile = next(row["density_quantile_label"] for row in negative_review_source if str(row["sample_id"]) == sample_id)
            metric["density_quantile_label"] = quantile
            assets = render_negative_asset(metric, window, negative_images)
            _remap_asset_paths(
                assets,
                ("counterfactual_image_path",),
                building=building,
                final_dir=final_dir,
            )
            metric.update(assets)
            negative_review.append(metric)
    if len(review_metrics) != 50 or len(negative_review) != 24:
        raise MABoxReviewError("review asset counts drifted")

    # The final path is needed for relative browser image URLs before atomic rename.
    review_html_final = final_dir / "public" / "index.html"
    for row in review_metrics:
        row["review_html_path"] = str(review_html_final.relative_to(ROOT))
    manifest_path_building = building / "review_manifest.jsonl"
    write_jsonl(manifest_path_building, review_metrics)
    review_manifest_sha = sha256_file(manifest_path_building)
    prereg_sha = sha256_file(prereg_path)
    amendment_sha = sha256_file(amendment_path)
    html_text = build_review_html(review_metrics, prereg_sha, review_manifest_sha)
    html_path = public / "index.html"
    html_path.write_text(html_text, encoding="utf-8")
    write_jsonl(building / "negative_review_manifest.jsonl", negative_review)
    write_jsonl(building / "unavailable_negative_audit.jsonl", unavailable_negatives)
    write_jsonl(building / "source_audit.jsonl", source_audits)

    distribution_path = building / "box_and_negative_distributions.png"
    build_distribution_chart(all_metrics, distribution_path)
    long_rows = [row for row in review_metrics if row["direction"] == "LONG"]
    short_rows = [row for row in review_metrics if row["direction"] == "SHORT"]
    make_overview(long_rows, building / "horizontal_long_overview.png", "horizontal_comparison_path", building=building, final_dir=final_dir, count=6)
    make_overview(short_rows, building / "horizontal_short_overview.png", "horizontal_comparison_path", building=building, final_dir=final_dir, count=6)
    make_overview(long_rows, building / "vertical_long_overview.png", "vertical_comparison_path", building=building, final_dir=final_dir, count=6)
    make_overview(short_rows, building / "vertical_short_overview.png", "vertical_comparison_path", building=building, final_dir=final_dir, count=6)

    summary = summarize_metrics(all_metrics)
    summary.update(
        {
            "experiment_id": EXPERIMENT_ID,
            "status": "review50_ready_pending_owner_protocol_and_sample_confirmation",
            "builder_commit": builder_commit,
            "amendment_sha256": amendment_sha,
            "source_manifest_sha256": sha256_file(source_manifest),
            "review_rows": 50,
            "review_unique_symbols": len({row["symbol"] for row in review_metrics}),
            "review_direction_counts": dict(sorted(Counter(str(row["direction"]) for row in review_metrics).items())),
            "review_split_counts": dict(sorted(Counter(str(row["split"]) for row in review_metrics).items())),
            "review_time_bins": dict(sorted(Counter(int(row["time_bin"]) for row in review_metrics).items())),
            "review_answers_preselected": 0,
            "negative_review_rows": len(negative_review),
            "data_availability": {
                "manifest_rows_accounted": len(all_metrics) + len(unavailable_negatives),
                "positive_metric_rows": sum(row["sample_kind"] == "positive" for row in all_metrics),
                "negative_metric_rows": sum(row["sample_kind"] == "negative" for row in all_metrics),
                "negative_unavailable_rows": len(unavailable_negatives),
                "negative_unavailable_by_kind": dict(
                    sorted(Counter(str(row["negative_kind"]) for row in unavailable_negatives).items())
                ),
                "negative_unavailable_by_reason": dict(
                    sorted(Counter(str(row["reason"]) for row in unavailable_negatives).items())
                ),
                "policy": "Every frozen negative is accounted for; rows without a complete six-MA causal W20 are excluded from density estimates rather than imputed.",
            },
            "render_contract": {
                "source_shape": [SOURCE_HEIGHT, SOURCE_WIDTH, 3],
                "window_bars": WINDOW_BARS,
                "window_end_offsets": dict(sorted(Counter(int(row["window_end_offset"]) for row in all_metrics).items())),
                "density_search_offsets": [-DENSITY_BARS, -1],
                "horizontal_variants": list(CORE_LENGTHS),
                "vertical_model_minimum_variants_px": list(MODEL_MIN_HEIGHTS),
                "baseline_imgsz": BASELINE_IMGSZ,
                "source_to_model_scale": SOURCE_TO_MODEL_SCALE,
                "base_padding_source_px": BASE_PADDING_SOURCE_PX,
            },
            "outputs": {
                "review_html": str(review_html_final.relative_to(ROOT)),
                "review_html_sha256": sha256_file(html_path),
                "review_manifest": str((final_dir / "review_manifest.jsonl").relative_to(ROOT)),
                "review_manifest_sha256": review_manifest_sha,
                "unavailable_negative_audit": str((final_dir / "unavailable_negative_audit.jsonl").relative_to(ROOT)),
                "distribution_chart": str((final_dir / distribution_path.name).relative_to(ROOT)),
            },
            "holdout": {"read": False, "ohlcv_rows_materialized": 0},
            "safety": {
                "yolo_labels_written": 0,
                "training_images_written": 0,
                "models_trained": 0,
                "training_eligible": False,
                "production_eligible": False,
                "active_or_frozen_changed": False,
                "remote_writes": 0,
            },
        }
    )
    write_json(building / "summary.json", summary)
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": builder_commit,
        "preregistration_sha256": prereg_sha,
        "amendment_sha256": amendment_sha,
        "source_manifest_sha256": sha256_file(source_manifest),
        "counts": {
            "manifest_rows_accounted": len(all_metrics) + len(unavailable_negatives),
            "positive_metric_rows": sum(row["sample_kind"] == "positive" for row in all_metrics),
            "negative_metric_rows": sum(row["sample_kind"] == "negative" for row in all_metrics),
            "negative_unavailable_rows": len(unavailable_negatives),
            "review_positive_images": len(review_metrics),
            "review_negative_images": len(negative_review),
            "review_answers_preselected": 0,
        },
        "qa": {
            "fixed_w20": all(int(row["window_bars"]) == WINDOW_BARS for row in all_metrics),
            "box_search_ends_before_t": all(int(row["density_search_end_offset"]) == -1 for row in all_metrics),
            "all_six_ma_inside_l5_min24": all(bool(row["all_six_ma_inside_l5_min24"]) for row in all_metrics),
            "manifest_accounting": len(all_metrics) + len(unavailable_negatives) == len(manifest),
            "negative_accounting": sum(row["sample_kind"] == "negative" for row in all_metrics) + len(unavailable_negatives) == len(negatives),
            "review_unique_sample_ids": len({row["sample_id"] for row in review_metrics}) == 50,
            "review_images_exist": all(
                _materialized_asset_path(str(row["image_path"]), building=building, final_dir=final_dir).is_file()
                for row in review_metrics
            ),
            "html_item_count": html_text.count('"source_order":') == 50,
        },
        "artifacts": {
            "summary_sha256": sha256_file(building / "summary.json"),
            "review_manifest_sha256": review_manifest_sha,
            "negative_review_manifest_sha256": sha256_file(building / "negative_review_manifest.jsonl"),
            "unavailable_negative_audit_sha256": sha256_file(building / "unavailable_negative_audit.jsonl"),
            "source_audit_sha256": sha256_file(building / "source_audit.jsonl"),
            "review_html_sha256": sha256_file(html_path),
            "distribution_chart_sha256": sha256_file(distribution_path),
        },
        "holdout": {"read": False, "ohlcv_rows_materialized": 0},
        "training_started": False,
        "errors": [],
    }
    if not all(receipt["qa"].values()):
        raise MABoxReviewError(f"review50 QA failed: {receipt['qa']}")
    write_json(building / "build_receipt.json", receipt)
    os.replace(building, final_dir)
    return receipt
