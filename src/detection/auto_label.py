"""Rule-based auto labeling of dense MA-cluster regions -> YOLO boxes.

Dense rule (adapted from the old project's strict candidate rule in
tools/build_strict_dense_review_pack.py, mapped onto the 6-MA setup):
  fast_spread = (max - min of SMA/EMA 20/60) / close  <= 0.0028
  full_spread = (max - min of all six MAs) / close    <= 0.0055
A dense segment is a run of >= MIN_DENSE_BARS consecutive bars satisfying
both. Each segment becomes one YOLO box whose x-range spans the segment's
bars and whose y-range spans the MA bundle inside the segment.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import ALL_MA_COLS
from .render import ChartTransform

FAST_SPREAD_MAX = 0.0028
FULL_SPREAD_MAX = 0.0055
MIN_DENSE_BARS = 5
MERGE_GAP_BARS = 2  # merge runs separated by tiny gaps to avoid sliver boxes
# Horizontal pad outside the first/last dense bar (pixels). smoke3 used 12 for
# mAP IoU forgiveness; P2-11 E1 (2026-07-10) tightens back to 6 after owner
# audit found systematic box_too_wide (e.g. PAXG_USDT_015960). Single-variable
# only — do not change y_pad_frac / min_bars / merge_gap in the same experiment.
X_PAD_PX = 6
Y_PAD_FRAC = 0.35

CLASS_ID = 0  # single class: dense_cluster
CLASS_NAME = "dense_cluster"


@dataclass(frozen=True)
class DenseSegment:
    start: int  # inclusive bar index within the window
    end: int    # inclusive


def find_dense_segments(
    df: pd.DataFrame,
    *,
    fast_max: float = FAST_SPREAD_MAX,
    full_max: float = FULL_SPREAD_MAX,
    min_bars: int = MIN_DENSE_BARS,
    merge_gap: int = MERGE_GAP_BARS,
) -> list[DenseSegment]:
    """Find dense runs on the numeric series of one rendered window."""
    dense = (
        (pd.to_numeric(df["fast_spread"], errors="coerce") <= fast_max)
        & (pd.to_numeric(df["full_spread"], errors="coerce") <= full_max)
    ).to_numpy()
    idx = np.flatnonzero(dense)
    if len(idx) == 0:
        return []
    # group consecutive indices, merging gaps <= merge_gap
    runs: list[list[int]] = [[int(idx[0]), int(idx[0])]]
    for i in idx[1:]:
        if int(i) - runs[-1][1] <= merge_gap + 1:
            runs[-1][1] = int(i)
        else:
            runs.append([int(i), int(i)])
    return [DenseSegment(s, e) for s, e in runs if e - s + 1 >= min_bars]


def segment_to_bbox(
    df: pd.DataFrame,
    seg: DenseSegment,
    tf: ChartTransform,
    *,
    x_pad_px: int = X_PAD_PX,
    y_pad_frac: float = Y_PAD_FRAC,
) -> tuple[float, float, float, float] | None:
    """Map a dense segment to a normalized (x_center, y_center, w, h) YOLO box.

    x-range: pixel positions of the first/last bar of the segment (plus candle
    half width and a small pad). y-range: highest/lowest MA value inside the
    segment, padded by a fraction of the bundle height.
    """
    region = df.iloc[seg.start : seg.end + 1]
    values: list[float] = []
    for col in ALL_MA_COLS:
        if col in region.columns:
            values.extend(float(v) for v in region[col] if pd.notna(v))
    if not values:
        return None
    hi, lo = max(values), min(values)
    pad = max((hi - lo) * y_pad_frac, (tf.price_max - tf.price_min) * 0.004)
    x1 = tf.x_at(seg.start) - tf.candle_half_w - x_pad_px
    x2 = tf.x_at(seg.end) + tf.candle_half_w + x_pad_px
    y1 = tf.y_at(hi + pad)
    y2 = tf.y_at(lo - pad)
    x1 = float(np.clip(x1, 0, tf.width - 1))
    x2 = float(np.clip(x2, 1, tf.width))
    y1 = float(np.clip(y1, 0, tf.height - 1))
    y2 = float(np.clip(y2, 1, tf.height))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    xc = (x1 + x2) / 2 / tf.width
    yc = (y1 + y2) / 2 / tf.height
    w = (x2 - x1) / tf.width
    h = (y2 - y1) / tf.height
    return (xc, yc, w, h)


def label_window(df: pd.DataFrame, tf: ChartTransform) -> list[tuple[float, float, float, float]]:
    """Return all YOLO boxes for one rendered window."""
    boxes = []
    for seg in find_dense_segments(df):
        bbox = segment_to_bbox(df, seg, tf)
        if bbox is not None:
            boxes.append(bbox)
    return boxes


def to_yolo_lines(boxes: list[tuple[float, float, float, float]]) -> str:
    return "".join(
        f"{CLASS_ID} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n" for xc, yc, w, h in boxes
    )
