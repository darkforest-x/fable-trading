"""Audit whether 15m t-3 YOLO boxes localize candles or the six-MA rope.

The frozen weak-label dataset supplies one positive source bar ``t`` and a
rendered 14--22 bar window.  This audit reads only the pre-holdout OHLCV prefix
ending before 2026-05-04, reconstructs the exact renderer transform, and
compares three geometries without modifying the dataset:

* stored label: the box actually used by YOLO;
* same-x MA envelope: the six moving averages over the stored core bars;
* local MA knot: the tightest same-length MA envelope inside the visible
  causal density span ``[t-12, t-1]``.

The local-MA geometry is a diagnostic proposal, not a gold relabel.  No return
labels, future OHLCV, holdout rows, model weights, thresholds, or production
pointers are read or changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
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
from yoyo.datasets.ma_launch_t3_training import yolo_box_from_core
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "ma_launch_t3_10000_v1"
EXPERIMENT = ROOT / "experiments" / "active" / "exp-15m-ma-launch-t3-yolo10000-v1"
DEFAULT_OUTPUT = EXPERIMENT / "results" / "label_semantics_audit_20260827"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
DENSITY_WINDOW = 12
MA_PAD_PX = 4.0

CURRENT_COLOR = (40, 40, 230)  # BGR red
SAME_X_MA_COLOR = (0, 165, 255)  # BGR gold
LOCAL_MA_COLOR = (220, 180, 0)  # BGR cyan-blue
TEXT_COLOR = (28, 28, 28)


class LabelSemanticsAuditError(ValueError):
    """Raised when a frozen artifact cannot be reconstructed exactly."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read one JSON object per non-empty line."""

    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic UTF-8 JSONL."""

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write stable human-readable JSON."""

    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def percentile(values: Sequence[float], q: float) -> float:
    """Return one finite percentile as a JSON-safe float."""

    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if not len(array):
        return float("nan")
    return float(np.quantile(array, q))


def describe(values: Sequence[float]) -> dict[str, float]:
    """Return compact distribution statistics."""

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


def label_box_pixels(label_path: Path, width: int, height: int) -> tuple[int, tuple[float, float, float, float]]:
    """Parse one positive YOLO label into pixel ``x0,y0,x1,y1``."""

    lines = [line.strip() for line in label_path.read_text().splitlines() if line.strip()]
    if len(lines) != 1:
        raise LabelSemanticsAuditError(f"expected one label in {label_path}, got {len(lines)}")
    fields = lines[0].split()
    if len(fields) != 5:
        raise LabelSemanticsAuditError(f"invalid YOLO label: {label_path}")
    class_id = int(fields[0])
    cx, cy, box_w, box_h = map(float, fields[1:])
    return class_id, (
        (cx - box_w / 2.0) * width,
        (cy - box_h / 2.0) * height,
        (cx + box_w / 2.0) * width,
        (cy + box_h / 2.0) * height,
    )


def yolo_box_pixels(box: Sequence[float], width: int, height: int) -> tuple[float, float, float, float]:
    """Convert normalized YOLO ``cx,cy,w,h`` into pixels."""

    cx, cy, box_w, box_h = map(float, box)
    return (
        (cx - box_w / 2.0) * width,
        (cy - box_h / 2.0) * height,
        (cx + box_w / 2.0) * width,
        (cy + box_h / 2.0) * height,
    )


def clip_box(box: Sequence[float], width: int, height: int) -> tuple[float, float, float, float]:
    """Clip one pixel box while preserving non-zero extent."""

    x0, y0, x1, y1 = map(float, box)
    x0 = float(np.clip(x0, 0, width - 1))
    x1 = float(np.clip(x1, 1, width))
    y0 = float(np.clip(y0, 0, height - 1))
    y1 = float(np.clip(y1, 1, height))
    if x1 <= x0 or y1 <= y0:
        raise LabelSemanticsAuditError(f"degenerate box after clipping: {box}")
    return x0, y0, x1, y1


def ma_envelope_box(
    transform: Any,
    window: pd.DataFrame,
    start_local: int,
    end_local: int,
    *,
    pad_px: float = 0.0,
) -> tuple[float, float, float, float]:
    """Return the six-MA price envelope for an inclusive local bar span."""

    if not 0 <= start_local <= end_local < len(window):
        raise LabelSemanticsAuditError("MA envelope span is outside rendered window")
    segment = window.iloc[start_local : end_local + 1]
    values = segment.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise LabelSemanticsAuditError("non-finite MA inside positive rendered window")
    x0 = transform.x_at(start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(end_local) + transform.candle_half_w + 2
    y0 = transform.y_at(float(values.max())) - pad_px
    y1 = transform.y_at(float(values.min())) + pad_px
    if y1 <= y0:
        midpoint = (y0 + y1) / 2.0
        y0, y1 = midpoint - 0.5, midpoint + 0.5
    return clip_box((x0, y0, x1, y1), transform.width, transform.height)


def box_iou(left: Sequence[float], right: Sequence[float]) -> float:
    """Return two-dimensional IoU for pixel boxes."""

    ax0, ay0, ax1, ay1 = map(float, left)
    bx0, by0, bx1, by1 = map(float, right)
    intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(
        0.0, min(ay1, by1) - max(ay0, by0)
    )
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - intersection
    return float(intersection / union) if union > 0.0 else 0.0


def interval_iou(a0: float, a1: float, b0: float, b1: float) -> float:
    """Return one-dimensional IoU for two intervals."""

    intersection = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return float(intersection / union) if union > 0.0 else 0.0


def tightest_visible_ma_span(
    window: pd.DataFrame,
    *,
    source_anchor_local: int,
    core_len: int,
) -> tuple[int, int, float, bool]:
    """Find the tightest same-length MA segment in visible ``[t-12,t-1]``.

    The score is the full six-line price envelope divided by mean close.  It
    uses only rows before ``t`` and chooses the latest segment on exact ties.
    """

    full_start = source_anchor_local - DENSITY_WINDOW
    search_start = max(0, full_start)
    search_end = source_anchor_local - 1
    fully_visible = full_start >= 0
    if search_end - search_start + 1 < core_len:
        raise LabelSemanticsAuditError("rendered window cannot fit local MA knot")
    candidates: list[tuple[float, int, int]] = []
    for start in range(search_start, search_end - core_len + 2):
        end = start + core_len - 1
        segment = window.iloc[start : end + 1]
        values = segment.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
        close_mean = float(segment["close"].mean())
        if not np.isfinite(values).all() or not np.isfinite(close_mean) or close_mean <= 0.0:
            continue
        score = float((values.max() - values.min()) / close_mean)
        candidates.append((score, -end, start))
    if not candidates:
        raise LabelSemanticsAuditError("no finite local MA knot candidate")
    score, negative_end, start = min(candidates)
    end = -negative_end
    return start, end, score, fully_visible


def core_price_spans(window: pd.DataFrame, start: int, end: int) -> tuple[float, float]:
    """Return candle high-low span and six-MA envelope over one segment."""

    segment = window.iloc[start : end + 1]
    candle_span = float(segment["high"].max() - segment["low"].min())
    values = segment.loc[:, list(SIX_MA_COLUMNS)].to_numpy(dtype=float)
    ma_span = float(values.max() - values.min())
    return candle_span, ma_span


def audit_positive(
    row: Mapping[str, Any],
    enriched: pd.DataFrame,
    dataset: Path,
) -> dict[str, Any]:
    """Reconstruct one positive's current and MA-derived boxes."""

    window_start = int(row["window_start_i"])
    window_end = int(row["window_end_i"])
    window = enriched.iloc[window_start : window_end + 1].reset_index(drop=True)
    expected_len = int(row["geometry"]["window_len"])
    if len(window) != expected_len:
        raise LabelSemanticsAuditError(f"window length mismatch: {row['sample_id']}")
    transform = make_chart_transform(window)
    core_start = int(row["core_start_i"]) - window_start
    core_end = int(row["core_end_i"]) - window_start
    anchor_local = int(row["source_anchor_i"]) - window_start
    class_id, stored_box = label_box_pixels(dataset / str(row["label_path"]), transform.width, transform.height)
    expected_class = int(row["class_id"])
    if class_id != expected_class:
        raise LabelSemanticsAuditError(f"class mismatch: {row['sample_id']}")

    recomputed_yolo = yolo_box_from_core(transform, window, core_start, core_end)
    candle_box = yolo_box_pixels(recomputed_yolo, transform.width, transform.height)
    same_x_ma_box = ma_envelope_box(transform, window, core_start, core_end)
    local_start, local_end, local_score, full_density_visible = tightest_visible_ma_span(
        window,
        source_anchor_local=anchor_local,
        core_len=int(row["geometry"]["core_len"]),
    )
    local_ma_box = ma_envelope_box(transform, window, local_start, local_end)
    candle_span, same_x_ma_span = core_price_spans(window, core_start, core_end)
    local_candle_span, local_ma_span = core_price_spans(window, local_start, local_end)
    stored_height = stored_box[3] - stored_box[1]
    same_x_ma_height = same_x_ma_box[3] - same_x_ma_box[1]
    max_pixel_delta = max(abs(a - b) for a, b in zip(stored_box, candle_box))
    ma_vertically_contained = (
        stored_box[1] <= same_x_ma_box[1] + 0.01
        and stored_box[3] >= same_x_ma_box[3] - 0.01
    )
    return {
        "sample_id": str(row["sample_id"]),
        "symbol": str(row["symbol"]),
        "direction": str(row["direction"]),
        "split": str(row["split"]),
        "anchor_time": str(row["anchor_time"]),
        "source_path": str(row["source_path"]),
        "image_path": str(row["image_path"]),
        "label_path": str(row["label_path"]),
        "window_start_i": window_start,
        "window_end_i": window_end,
        "source_anchor_i": int(row["source_anchor_i"]),
        "window_len": expected_len,
        "core_len": int(row["geometry"]["core_len"]),
        "confirmation_bars": int(row["geometry"]["confirmation_bars"]),
        "window_start_offset": window_start - int(row["source_anchor_i"]),
        "window_end_offset": window_end - int(row["source_anchor_i"]),
        "current_core_start_offset": int(row["core_start_i"]) - int(row["source_anchor_i"]),
        "current_core_end_offset": int(row["core_end_i"]) - int(row["source_anchor_i"]),
        "local_ma_start_offset": local_start - anchor_local,
        "local_ma_end_offset": local_end - anchor_local,
        "density_window_fully_visible": bool(full_density_visible),
        "stored_box_px": list(map(float, stored_box)),
        "candle_formula_box_px": list(map(float, candle_box)),
        "same_x_ma_box_px": list(map(float, same_x_ma_box)),
        "local_ma_box_px": list(map(float, local_ma_box)),
        "stored_vs_candle_iou": box_iou(stored_box, candle_box),
        "stored_vs_candle_max_pixel_delta": float(max_pixel_delta),
        "stored_vs_same_x_ma_iou": box_iou(stored_box, same_x_ma_box),
        "stored_vs_same_x_ma_vertical_iou": interval_iou(
            stored_box[1], stored_box[3], same_x_ma_box[1], same_x_ma_box[3]
        ),
        "stored_vs_local_ma_iou": box_iou(stored_box, local_ma_box),
        "stored_vs_local_ma_horizontal_iou": interval_iou(
            stored_box[0], stored_box[2], local_ma_box[0], local_ma_box[2]
        ),
        "same_x_ma_vertically_contained_by_stored": bool(ma_vertically_contained),
        "stored_height_px": float(stored_height),
        "same_x_ma_height_px": float(same_x_ma_height),
        "stored_to_same_x_ma_height_ratio": float(stored_height / max(same_x_ma_height, 1e-9)),
        "candle_price_span": candle_span,
        "same_x_ma_price_span": same_x_ma_span,
        "candle_to_same_x_ma_price_span_ratio": float(candle_span / max(same_x_ma_span, 1e-12)),
        "local_candle_price_span": local_candle_span,
        "local_ma_price_span": local_ma_span,
        "local_ma_envelope_fraction_of_close": float(local_score),
    }


def add_shuffled_null(rows: list[dict[str, Any]]) -> None:
    """Add a conservative within-geometry cyclic-permutation null IoU."""

    groups: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[(row["window_len"], row["core_len"], row["confirmation_bars"])].append(index)
    for indexes in groups.values():
        indexes.sort(key=lambda index: rows[index]["sample_id"])
        if len(indexes) < 2:
            for index in indexes:
                rows[index]["shuffled_label_vs_candle_iou"] = float("nan")
            continue
        shifted = indexes[1:] + indexes[:1]
        for target_index, source_index in zip(indexes, shifted):
            rows[target_index]["shuffled_label_vs_candle_iou"] = box_iou(
                rows[source_index]["stored_box_px"],
                rows[target_index]["candle_formula_box_px"],
            )


def choose_examples(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Choose low/median/high height-ratio examples for each direction."""

    selected: list[dict[str, Any]] = []
    for direction in ("LONG", "SHORT"):
        subset = sorted(
            (dict(row) for row in rows if row["direction"] == direction),
            key=lambda row: (row["candle_to_same_x_ma_price_span_ratio"], row["sample_id"]),
        )
        for quantile, label in ((0.10, "low"), (0.50, "median"), (0.90, "high")):
            index = int(round((len(subset) - 1) * quantile))
            item = dict(subset[index])
            item["example_quantile"] = label
            selected.append(item)
    return selected


def draw_box(image: np.ndarray, box: Sequence[float], color: tuple[int, int, int], label: str) -> None:
    """Draw one audit-only rectangle and a readable label."""

    x0, y0, x1, y1 = (int(round(value)) for value in box)
    cv2.rectangle(image, (x0, y0), (x1, y1), color, 4, cv2.LINE_AA)
    (text_w, text_h), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
    text_y = max(text_h + 8, y0 - 7)
    cv2.rectangle(
        image,
        (x0, text_y - text_h - 7),
        (min(image.shape[1] - 1, x0 + text_w + 9), text_y + baseline + 3),
        (255, 255, 255),
        -1,
    )
    cv2.putText(image, label, (x0 + 4, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2, cv2.LINE_AA)


def panel(image: np.ndarray, title: str, subtitle: str, *, width: int = 620) -> np.ndarray:
    """Resize a chart and prepend a two-line header."""

    scale = width / image.shape[1]
    resized = cv2.resize(image, (width, int(round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    header = np.full((66, width, 3), (248, 248, 248), dtype=np.uint8)
    cv2.putText(header, title, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.61, TEXT_COLOR, 2, cv2.LINE_AA)
    cv2.putText(header, subtitle, (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.49, (75, 75, 75), 1, cv2.LINE_AA)
    return np.vstack((header, resized))


def build_contact_sheet(
    selected: Sequence[Mapping[str, Any]],
    enriched_by_source: Mapping[str, pd.DataFrame],
    dataset: Path,
    output: Path,
) -> dict[str, Any]:
    """Render three comparable panels per selected positive."""

    rows: list[np.ndarray] = []
    parity_count = 0
    sample_dir = output / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    representative_path: Path | None = None
    for ordinal, item in enumerate(selected, start=1):
        source = enriched_by_source[str(item["source_path"])]
        window = source.iloc[
            int(item["window_start_i"]) : int(item["window_end_i"]) + 1
        ].reset_index(drop=True)
        base, _ = render_chart(window, out_path=None)
        stored = cv2.imread(str(dataset / str(item["image_path"])), cv2.IMREAD_COLOR)
        if stored is None:
            raise FileNotFoundError(dataset / str(item["image_path"]))
        if np.array_equal(base, stored):
            parity_count += 1

        current = base.copy()
        draw_box(current, item["stored_box_px"], CURRENT_COLOR, "CURRENT candle range")
        same_x = base.copy()
        padded_same = list(item["same_x_ma_box_px"])
        padded_same[1] -= MA_PAD_PX
        padded_same[3] += MA_PAD_PX
        padded_same = clip_box(padded_same, base.shape[1], base.shape[0])
        draw_box(same_x, padded_same, SAME_X_MA_COLOR, "MA envelope, same x")
        overlay = base.copy()
        padded_local = list(item["local_ma_box_px"])
        padded_local[1] -= MA_PAD_PX
        padded_local[3] += MA_PAD_PX
        padded_local = clip_box(padded_local, base.shape[1], base.shape[0])
        draw_box(overlay, item["stored_box_px"], CURRENT_COLOR, "CURRENT")
        draw_box(overlay, padded_same, SAME_X_MA_COLOR, "same-x MA")
        draw_box(overlay, padded_local, LOCAL_MA_COLOR, "local MA knot")

        ratio = float(item["candle_to_same_x_ma_price_span_ratio"])
        identity = (
            f"{item['direction']} {item['symbol']} {item['anchor_time'][:16]} "
            f"[{item['example_quantile']}]"
        )
        geometry = (
            f"W{item['window_len']} core={item['core_len']} | candle/MA span={ratio:.1f}x | "
            f"local={item['local_ma_start_offset']}..{item['local_ma_end_offset']}"
        )
        row_panels = [
            panel(current, "1  CURRENT LABEL (red)", identity),
            panel(same_x, "2  SAME X, MA-ONLY (gold)", geometry),
            panel(overlay, "3  OVERLAY + LOCAL MA KNOT (cyan)", "Audit proposal only; not gold"),
        ]
        rows.append(np.hstack(row_panels))

        sample_path = sample_dir / f"{ordinal:02d}_{item['sample_id']}_overlay.png"
        cv2.imwrite(str(sample_path), overlay, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        if item["direction"] == "LONG" and item["example_quantile"] == "median":
            representative_path = output / "representative_full_resolution_overlay.png"
            cv2.imwrite(str(representative_path), overlay, [cv2.IMWRITE_PNG_COMPRESSION, 3])

    sheet = np.vstack(rows)
    sheet_path = output / "label_semantics_comparison.png"
    cv2.imwrite(str(sheet_path), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    return {
        "contact_sheet_path": str(sheet_path.relative_to(ROOT)),
        "contact_sheet_sha256": sha256_file(sheet_path),
        "representative_path": str(representative_path.relative_to(ROOT)) if representative_path else None,
        "selected_render_parity_exact": parity_count,
        "selected_render_parity_total": len(selected),
    }


def build_distribution_chart(rows: Sequence[Mapping[str, Any]], output: Path) -> Path:
    """Plot label-to-MA geometry distributions and the permutation control."""

    span_ratio = np.asarray([row["candle_to_same_x_ma_price_span_ratio"] for row in rows], dtype=float)
    vertical_iou = np.asarray([row["stored_vs_same_x_ma_vertical_iou"] for row in rows], dtype=float)
    actual_iou = np.asarray([row["stored_vs_candle_iou"] for row in rows], dtype=float)
    shuffled_iou = np.asarray([row["shuffled_label_vs_candle_iou"] for row in rows], dtype=float)
    local_end = np.asarray([row["local_ma_end_offset"] for row in rows], dtype=int)

    fig, axes = plt.subplots(2, 2, figsize=(15, 9), constrained_layout=True)
    clip_at = float(np.quantile(span_ratio, 0.99))
    axes[0, 0].hist(np.clip(span_ratio, None, clip_at), bins=50, color="#d94b45", alpha=0.86)
    axes[0, 0].axvline(float(np.median(span_ratio)), color="#222222", linestyle="--", linewidth=1.8)
    axes[0, 0].set_title("Candle price span / same-x six-MA span")
    axes[0, 0].set_xlabel(f"ratio (values above p99={clip_at:.1f} clipped)")
    axes[0, 0].set_ylabel("positive samples")

    axes[0, 1].hist(vertical_iou, bins=np.linspace(0, 1, 51), color="#e3a927", alpha=0.9)
    axes[0, 1].axvline(float(np.median(vertical_iou)), color="#222222", linestyle="--", linewidth=1.8)
    axes[0, 1].set_title("Vertical IoU: stored box vs same-x MA envelope")
    axes[0, 1].set_xlabel("vertical IoU")
    axes[0, 1].set_ylabel("positive samples")

    finite_null = shuffled_iou[np.isfinite(shuffled_iou)]
    axes[1, 0].boxplot(
        [actual_iou, finite_null],
        tick_labels=["stored vs candle formula", "shuffled vs candle formula"],
        showfliers=False,
    )
    axes[1, 0].set_title("Within-geometry permutation control")
    axes[1, 0].set_ylabel("2D IoU")

    offsets = np.arange(-12, 0)
    counts = Counter(local_end.tolist())
    axes[1, 1].bar(offsets, [counts[int(offset)] for offset in offsets], color="#16a6b6")
    axes[1, 1].axvline(-3, color="#d94b45", linestyle="--", linewidth=2, label="current core always ends t-3")
    axes[1, 1].set_title("Data-derived local MA-knot end offset")
    axes[1, 1].set_xlabel("end offset from candidate t")
    axes[1, 1].set_ylabel("positive samples")
    axes[1, 1].legend(loc="upper left")

    path = output / "label_geometry_distributions.png"
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def build_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_audits: Sequence[Mapping[str, Any]],
    dataset: Path,
    selected: Sequence[Mapping[str, Any]],
    visual_receipt: Mapping[str, Any],
    distribution_path: Path,
) -> dict[str, Any]:
    """Assemble the auditable quantitative result."""

    count = len(rows)
    exact_candle = sum(
        float(row["stored_vs_candle_max_pixel_delta"]) <= 0.001
        for row in rows
    )
    rounding_match = sum(float(row["stored_vs_candle_iou"]) >= 0.9999 for row in rows)
    contained = sum(bool(row["same_x_ma_vertically_contained_by_stored"]) for row in rows)
    full_density = sum(bool(row["density_window_fully_visible"]) for row in rows)
    zero_vertical_overlap = sum(
        float(row["stored_vs_same_x_ma_vertical_iou"]) == 0.0 for row in rows
    )
    vertical_iou_below_tenth = sum(
        float(row["stored_vs_same_x_ma_vertical_iou"]) < 0.10 for row in rows
    )
    local_end_not_t_minus_3 = sum(int(row["local_ma_end_offset"]) != -3 for row in rows)
    actual_iou = [float(row["stored_vs_candle_iou"]) for row in rows]
    shuffled_iou = [float(row["shuffled_label_vs_candle_iou"]) for row in rows]
    return {
        "audit_id": "ma_launch_t3_label_semantics_audit_20260827",
        "status": "confirmed_semantic_mismatch",
        "dataset_path": str(dataset.relative_to(ROOT)),
        "dataset_manifest_sha256": sha256_file(dataset / "manifest.jsonl"),
        "positive_rows": count,
        "positive_by_direction": dict(sorted(Counter(str(row["direction"]) for row in rows).items())),
        "positive_by_split": dict(sorted(Counter(str(row["split"]) for row in rows).items())),
        "sources": {
            "files": len(source_audits),
            "rows_materialized": int(sum(int(row["rows_materialized"]) for row in source_audits)),
            "holdout_ohlcv_rows_materialized": int(
                sum(int(row["holdout_ohlcv_rows_materialized"]) for row in source_audits)
            ),
        },
        "current_label_reconstruction": {
            "stored_vs_candle_formula_iou": describe(actual_iou),
            "iou_at_least_0_9999": rounding_match,
            "max_pixel_delta_at_most_0_001": exact_candle,
            "meaning": "stored box follows core-candle high/low geometry",
        },
        "current_vs_same_x_ma": {
            "candle_to_ma_price_span_ratio": describe(
                [float(row["candle_to_same_x_ma_price_span_ratio"]) for row in rows]
            ),
            "vertical_iou": describe(
                [float(row["stored_vs_same_x_ma_vertical_iou"]) for row in rows]
            ),
            "ma_envelope_vertically_contained_by_stored": contained,
            "ma_envelope_vertically_contained_share": contained / count,
            "zero_vertical_overlap": zero_vertical_overlap,
            "zero_vertical_overlap_share": zero_vertical_overlap / count,
            "vertical_iou_below_0_10": vertical_iou_below_tenth,
            "vertical_iou_below_0_10_share": vertical_iou_below_tenth / count,
        },
        "current_vs_local_ma_knot": {
            "two_dimensional_iou": describe([float(row["stored_vs_local_ma_iou"]) for row in rows]),
            "horizontal_iou": describe(
                [float(row["stored_vs_local_ma_horizontal_iou"]) for row in rows]
            ),
            "local_start_offsets": dict(
                sorted(Counter(int(row["local_ma_start_offset"]) for row in rows).items())
            ),
            "local_end_offsets": dict(
                sorted(Counter(int(row["local_ma_end_offset"]) for row in rows).items())
            ),
            "local_end_not_t_minus_3": local_end_not_t_minus_3,
            "local_end_not_t_minus_3_share": local_end_not_t_minus_3 / count,
        },
        "input_coverage": {
            "full_prior_12_density_window_visible": full_density,
            "full_prior_12_density_window_visible_share": full_density / count,
            "missing_earliest_density_bar": count - full_density,
        },
        "null_control": {
            "method": "cyclically permute stored labels within identical window/core/confirmation geometry",
            "actual_stored_vs_candle_iou": describe(actual_iou),
            "shuffled_label_vs_candle_iou": describe(shuffled_iou),
        },
        "visuals": {
            **dict(visual_receipt),
            "distribution_path": str(distribution_path.relative_to(ROOT)),
            "distribution_sha256": sha256_file(distribution_path),
            "selected_sample_ids": [str(row["sample_id"]) for row in selected],
        },
        "safety": {
            "holdout_read": False,
            "dataset_modified": False,
            "labels_modified": False,
            "training_started": False,
            "weights_modified": False,
            "active_or_frozen_modified": False,
            "production_eligible": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = dataset / "manifest.jsonl"
    manifest = read_jsonl(manifest_path)
    positives = [row for row in manifest if row.get("sample_kind") == "positive_weak"]
    if len(positives) != 9_938:
        raise LabelSemanticsAuditError(f"expected 9,938 positives, got {len(positives)}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in positives:
        grouped[str(row["source_path"])].append(row)

    metric_rows: list[dict[str, Any]] = []
    source_audits: list[dict[str, Any]] = []
    source_items = sorted(grouped.items())
    for ordinal, (source_path, source_rows) in enumerate(source_items, start=1):
        frame, source_audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        if int(source_audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("holdout OHLCV was materialized")
        enriched = add_six_mas(frame)
        source_audits.append(source_audit)
        for row in source_rows:
            metric_rows.append(audit_positive(row, enriched, dataset))
        if ordinal == 1 or ordinal % 20 == 0 or ordinal == len(source_items):
            print(f"audited sources {ordinal}/{len(source_items)}; positives {len(metric_rows)}/{len(positives)}")

    metric_rows.sort(key=lambda row: str(row["sample_id"]))
    add_shuffled_null(metric_rows)
    selected = choose_examples(metric_rows)
    selected_sources = sorted({str(row["source_path"]) for row in selected})
    enriched_by_source: dict[str, pd.DataFrame] = {}
    for source_path in selected_sources:
        frame, audit = read_preholdout_prefix(ROOT / source_path, end_exclusive=HOLDOUT_START)
        if int(audit["holdout_ohlcv_rows_materialized"]) != 0:
            raise AssertionError("holdout OHLCV was materialized during rendering")
        enriched_by_source[source_path] = add_six_mas(frame)

    visual_receipt = build_contact_sheet(selected, enriched_by_source, dataset, output)
    distribution_path = build_distribution_chart(metric_rows, output)
    summary = build_summary(
        metric_rows,
        source_audits=source_audits,
        dataset=dataset,
        selected=selected,
        visual_receipt=visual_receipt,
        distribution_path=distribution_path,
    )
    write_jsonl(output / "per_sample_metrics.jsonl", metric_rows)
    write_jsonl(output / "source_audit.jsonl", source_audits)
    write_json(output / "summary.json", summary)
    receipt = {
        "summary_path": str((output / "summary.json").relative_to(ROOT)),
        "summary_sha256": sha256_file(output / "summary.json"),
        "per_sample_metrics_path": str((output / "per_sample_metrics.jsonl").relative_to(ROOT)),
        "per_sample_metrics_sha256": sha256_file(output / "per_sample_metrics.jsonl"),
        "source_audit_path": str((output / "source_audit.jsonl").relative_to(ROOT)),
        "source_audit_sha256": sha256_file(output / "source_audit.jsonl"),
    }
    write_json(output / "receipt.json", receipt)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
