"""Pure records and summaries for the fixed direct-versus-SAHI benchmark."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkRecord:
    mode: str
    image: str
    n_gt: int
    n_pred: int
    matched_iou50: int
    elapsed_seconds: float


def select_image_paths(paths: list[Path], *, limit: int, seed: int) -> list[Path]:
    """Return the full sorted set or a deterministic sample without replacement."""
    ordered = sorted(paths)
    if limit < 0:
        raise ValueError(f"limit must be non-negative, observed {limit}")
    if limit == 0 or limit >= len(ordered):
        return ordered
    return random.Random(seed).sample(ordered, limit)


def normalize_xyxy(
    xyxy: tuple[float, float, float, float],
    *,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    """Convert pixel xyxy to normalized YOLO cx/cy/w/h coordinates."""
    if width <= 0 or height <= 0:
        raise ValueError(f"image dimensions must be positive, observed {width}x{height}")
    x1, y1, x2, y2 = xyxy
    return (
        ((x1 + x2) / 2.0) / width,
        ((y1 + y2) / 2.0) / height,
        (x2 - x1) / width,
        (y2 - y1) / height,
    )


def summarize_records(records: list[BenchmarkRecord]) -> dict[str, int | float | None | str]:
    """Aggregate one inference mode without relabeling the custom metrics as mAP."""
    if not records:
        raise ValueError("at least one benchmark record is required")
    modes = {record.mode for record in records}
    if len(modes) != 1:
        raise ValueError(f"records must contain one mode, observed {sorted(modes)}")
    n_gt = sum(record.n_gt for record in records)
    n_pred = sum(record.n_pred for record in records)
    matched = sum(record.matched_iou50 for record in records)
    elapsed = sum(record.elapsed_seconds for record in records)
    return {
        "mode": records[0].mode,
        "n_images": len(records),
        "n_gt_boxes": n_gt,
        "n_pred_boxes": n_pred,
        "matched_iou50": matched,
        "recall_like_iou50": matched / n_gt if n_gt else None,
        "precision_like_iou50": matched / n_pred if n_pred else None,
        "pred_per_gt": n_pred / n_gt if n_gt else None,
        "elapsed_seconds": elapsed,
        "seconds_per_image": elapsed / len(records),
    }
