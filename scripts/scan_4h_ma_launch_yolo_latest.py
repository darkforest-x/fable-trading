#!/usr/bin/env python3
"""Run the frozen Grade-A 15m YOLO checkpoint on recent confirmed OKX 4h bars.

This is deliberately an out-of-distribution research view requested by the
Owner.  The checkpoint was trained on 15-minute renders; applying its unchanged
bar-count contract to 4-hour candles changes W18/19 from 4.5--4.75 hours to
72--76 hours, core4/5 to 16--20 hours, and post2--9 to 8--36 hours.  Every chart
states that mismatch.  No threshold, weight, ACTIVE state, forward log, or order
state is changed.

The public-market scan freezes its universe before inference.  The default is
the existing radar pool (pinned majors, volume leaders, and liquid movers);
``--all-eligible`` instead freezes every current eligible crypto USDT swap.
Only confirmed 4-hour candles are used.  Six latest endpoints (24 hours) are
scored after a bounded causal warmup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ma_launch_t3_daily_movers as common  # noqa: E402
from scripts.scan_15m_ma_launch_model_compare_all3d import (  # noqa: E402
    inverse_x,
    inverse_y,
    price_text,
    x_at_float,
)
from src.scout_mtf.rank import build_scan_pool  # noqa: E402
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from yoyo.layers.l1_detection.four_hour_similarity import fetch_recent_4h  # noqa: E402
from yoyo.layers.l1_detection.render import (  # noqa: E402
    IMG_HEIGHT,
    IMG_WIDTH,
    ChartTransform,
    render_chart,
)


WEIGHTS = (
    ROOT
    / "analysis/output/ma_launch_owner_grade_a8000_neg24000_v1"
    / "ma_launch_owner_grade_a8000_neg24000_v1_y11s_ft1280_full40/weights/best.pt"
)
EXPECTED_WEIGHT_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
DEFAULT_OUT = ROOT / "analysis/output/ma_launch_4h_yolo_latest_20260901_v1"
MODEL_NAME = "Grade-A 8k + 24k negatives · full40 · native 1280"
WINDOW_LENGTHS = (18, 19)
ALLOWED_CORES = frozenset((4, 5))
ALLOWED_CONFIRMATIONS = frozenset(range(2, 10))
CONFIDENCE = 0.25
NMS_IOU = 0.70
IMAGE_SIZE = 1280
LOOKBACK_ENDPOINTS = 6
EVENT_GAP_BARS = 5
FETCH_LIMIT = 300
BAR_DELTA = pd.Timedelta(hours=4)
HOLDOUT_CONSUMPTION_NUMBER = 4
ALL_UNIVERSE_HOLDOUT_CONSUMPTION_NUMBER = 5
CLASS_NAMES = common.CLASS_NAMES
CLASS_COLORS = common.CLASS_COLORS

CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1400
MAIN_X = 20
MAIN_Y = 112
MAIN_WIDTH = 1880
MAIN_HEIGHT = 760
CONTEXT_BARS = 80
INSET_WIDTH = 700
INSET_HEIGHT = 406


class FourHourYoloError(RuntimeError):
    """Fail closed on immutable-input, source, mapping, or chart drift."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 identity."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    """Hash exact BGR pixels rather than PNG container bytes."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one stable UTF-8 JSON receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    scale: float = 0.55,
    color: tuple[int, int, int] = (28, 28, 28),
    thickness: int = 1,
) -> None:
    """Draw stable anti-aliased OpenCV text."""

    cv2.putText(
        image,
        text,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def dashed_vertical(image: np.ndarray, x: int, y0: int, y1: int) -> None:
    """Draw the completed inference endpoint."""

    for start in range(y0, y1 + 1, 20):
        cv2.line(
            image,
            (x, start),
            (x, min(y1, start + 12)),
            (35, 35, 35),
            2,
            cv2.LINE_AA,
        )


def fetch_one(symbol: str) -> tuple[str, pd.DataFrame, dict[str, Any] | None, str | None]:
    """Fetch one bounded confirmed 4h page with bounded retries."""

    last: Exception | None = None
    for attempt in range(4):
        try:
            frame, audit = fetch_recent_4h(symbol, limit=FETCH_LIMIT)
            frame = frame.copy()
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            if len(frame) < 140:
                raise FourHourYoloError(f"only {len(frame)} confirmed bars")
            diffs = frame["open_time"].diff().iloc[1:]
            if not (diffs == BAR_DELTA).all():
                raise FourHourYoloError(
                    f"non-contiguous 4h history: gaps={int((diffs != BAR_DELTA).sum())}"
                )
            return symbol, frame, audit, None
        except Exception as exc:  # noqa: BLE001 - every exclusion is receipted
            last = exc
            time.sleep(0.8 * (attempt + 1))
    return symbol, pd.DataFrame(), None, f"{type(last).__name__}:{last}"


def choose_device(requested: str | None) -> str:
    """Use an explicit device or prefer MPS/CUDA before CPU."""

    if requested:
        return requested
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "0"
    return "cpu"


def normalized_box_corners(row: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Recover the preserved raw prediction rectangle in 1280x742 pixels."""

    cx = float(row["prediction_cx_norm"])
    cy = float(row["prediction_cy_norm"])
    width = float(row["prediction_w_norm"])
    height = float(row["prediction_h_norm"])
    values = (cx, cy, width, height)
    if not all(np.isfinite(values)) or width <= 0 or height <= 0:
        raise FourHourYoloError(f"invalid raw box: {values}")
    x0 = int(round((cx - width / 2.0) * IMG_WIDTH))
    x1 = int(round((cx + width / 2.0) * IMG_WIDTH))
    y0 = int(round((cy - height / 2.0) * IMG_HEIGHT))
    y1 = int(round((cy + height / 2.0) * IMG_HEIGHT))
    x0, x1 = sorted((max(0, min(IMG_WIDTH - 1, x0)), max(0, min(IMG_WIDTH - 1, x1))))
    y0, y1 = sorted((max(0, min(IMG_HEIGHT - 1, y0)), max(0, min(IMG_HEIGHT - 1, y1))))
    if x1 <= x0 or y1 <= y0:
        raise FourHourYoloError("raw box collapsed after clipping")
    return x0, y0, x1, y1


def project_raw_box(
    row: Mapping[str, Any],
    *,
    input_tf: ChartTransform,
    context_tf: ChartTransform,
    context_start_i: int,
) -> tuple[int, int, int, int]:
    """Inverse-project one unchanged YOLO rectangle into the 4h context chart."""

    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(row)
    global_x0 = int(row["window_start_i"]) + inverse_x(input_tf, raw_x0)
    global_x1 = int(row["window_start_i"]) + inverse_x(input_tf, raw_x1)
    price_high = inverse_y(input_tf, raw_y0)
    price_low = inverse_y(input_tf, raw_y1)
    x0 = x_at_float(context_tf, global_x0 - context_start_i)
    x1 = x_at_float(context_tf, global_x1 - context_start_i)
    y0 = context_tf.y_at(price_high)
    y1 = context_tf.y_at(price_low)
    x0, x1 = sorted((max(0, x0), min(MAIN_WIDTH - 1, x1)))
    y0, y1 = sorted((max(0, y0), min(MAIN_HEIGHT - 1, y1)))
    return x0, y0, x1, y1


def delivery_context_start(*, frame_length: int, window_start_i: int) -> int:
    """Keep the scored window visible while retaining latest-market context."""

    if frame_length <= 0:
        raise ValueError("frame_length must be positive")
    if not 0 <= window_start_i < frame_length:
        raise ValueError("window_start_i is outside the frame")
    latest_context_start = max(0, frame_length - CONTEXT_BARS)
    return min(latest_context_start, window_start_i)


class LazyTaskSequence(Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]]):
    """Render only the inference slice currently requested by ``infer``.

    A 15-day all-symbol scan contains roughly fifty thousand 1280-pixel images.
    Materializing those images would require well over 100 GB.  This sequence
    retains only small integer task specifications and renders each model batch
    on demand while preserving the scanner's original symbol/endpoint/W order.
    """

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        specs: Sequence[tuple[str, int, int, int]],
    ) -> None:
        self._frames = frames
        self._specs = tuple(specs)

    def __len__(self) -> int:
        return len(self._specs)

    def __getitem__(
        self, index: int | slice
    ) -> tuple[np.ndarray, ChartTransform, dict[str, Any]] | list[
        tuple[np.ndarray, ChartTransform, dict[str, Any]]
    ]:
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        symbol, start_i, endpoint, window_len = self._specs[index]
        frame = self._frames[symbol]
        window = frame.iloc[start_i : endpoint + 1]
        image, transform = render_chart(window, out_path=None)
        return (
            image,
            transform,
            {
                "symbol": symbol,
                "window_len": window_len,
                "window_start_i": start_i,
                "window_end_i": endpoint,
                "window_end_time": utc(frame.iloc[endpoint]["open_time"]).isoformat(),
                "input_pixel_sha256": pixel_sha256(image),
            },
        )


def build_tasks(
    frames: Mapping[str, pd.DataFrame],
    *,
    lookback_endpoints: int = LOOKBACK_ENDPOINTS,
) -> tuple[
    dict[str, pd.DataFrame],
    Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]],
]:
    """Build lazy W18/W19 tasks for a bounded number of confirmed endpoints."""

    if lookback_endpoints <= 0:
        raise ValueError("lookback_endpoints must be positive")
    enriched_frames = {
        symbol: add_mas(frame) for symbol, frame in sorted(frames.items())
    }
    specs: list[tuple[str, int, int, int]] = []
    for symbol, enriched in enriched_frames.items():
        first_endpoint = max(0, len(enriched) - lookback_endpoints)
        for endpoint in range(first_endpoint, len(enriched)):
            for window_len in WINDOW_LENGTHS:
                start_i = endpoint - window_len + 1
                if start_i < 0:
                    continue
                window = enriched.iloc[start_i : endpoint + 1]
                if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                    continue
                specs.append((symbol, start_i, endpoint, window_len))
    return enriched_frames, LazyTaskSequence(enriched_frames, specs)


def infer(
    model: Any,
    tasks: Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]],
    *,
    frames: Mapping[str, pd.DataFrame],
    device: str,
    batch_size: int,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Run the immutable checkpoint and retain only its historical box contract."""

    accepted: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    for start in range(0, len(tasks), batch_size):
        batch = tasks[start : start + batch_size]
        predictions = model.predict(
            source=[item[0] for item in batch],
            imgsz=IMAGE_SIZE,
            conf=CONFIDENCE,
            iou=NMS_IOU,
            batch=min(batch_size, len(batch)),
            device=device,
            verbose=False,
        )
        if len(predictions) != len(batch):
            raise FourHourYoloError("prediction/task count mismatch")
        for prediction, (input_image, transform, meta) in zip(predictions, batch):
            stats["windows_scored"] += 1
            boxes = prediction.boxes
            if boxes is None or len(boxes) == 0:
                continue
            stats["windows_with_any_box"] += 1
            frame = frames[str(meta["symbol"])]
            times = pd.to_datetime(frame["open_time"], utc=True)
            for xywhn, class_id, confidence in zip(
                boxes.xywhn.cpu().numpy(),
                boxes.cls.cpu().numpy(),
                boxes.conf.cpu().numpy(),
            ):
                stats["raw_boxes"] += 1
                cid = int(class_id)
                if cid not in CLASS_NAMES:
                    stats["reject_unknown_class"] += 1
                    continue
                mapped = common.map_prediction_to_core(
                    cx=float(xywhn[0]),
                    width=float(xywhn[2]),
                    transform=transform,
                    window_start_i=int(meta["window_start_i"]),
                    window_end_i=int(meta["window_end_i"]),
                )
                if mapped["core_length_bars"] not in ALLOWED_CORES:
                    stats["reject_core_length"] += 1
                    continue
                if mapped["confirmation_bars"] not in ALLOWED_CONFIRMATIONS:
                    stats["reject_confirmation"] += 1
                    continue
                segment = frame.iloc[mapped["core_start_i"] : mapped["core_end_i"] + 1]
                accepted.append(
                    {
                        **meta,
                        **mapped,
                        "prediction_cx_norm": float(xywhn[0]),
                        "prediction_cy_norm": float(xywhn[1]),
                        "prediction_w_norm": float(xywhn[2]),
                        "prediction_h_norm": float(xywhn[3]),
                        "class_id": cid,
                        "class_name": CLASS_NAMES[cid],
                        "confidence": float(confidence),
                        "core_start_time": utc(times.iloc[mapped["core_start_i"]]).isoformat(),
                        "core_end_time": utc(times.iloc[mapped["core_end_i"]]).isoformat(),
                        "core_high": float(segment["high"].max()),
                        "core_low": float(segment["low"].min()),
                        "input_pixel_sha256": pixel_sha256(input_image),
                    }
                )
                stats["accepted_structural_boxes"] += 1
        done = min(start + batch_size, len(tasks))
        if done % (batch_size * 5) == 0 or done == len(tasks):
            print(f"inference {done}/{len(tasks)} accepted={len(accepted)}", flush=True)
    return accepted, stats


def deduplicate(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the checkpoint's fixed five-bar same-symbol event separation."""

    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_symbol.setdefault(str(candidate["symbol"]), []).append(dict(candidate))
    events: list[dict[str, Any]] = []
    for symbol, rows in sorted(by_symbol.items()):
        kept = common.deduplicate_hits(rows, gap_bars=EVENT_GAP_BARS)
        for peak_row in kept:
            related = [
                item
                for item in rows
                if abs(int(item["core_end_i"]) - int(peak_row["core_end_i"])) < EVENT_GAP_BARS
            ]
            first_bar_open = min(utc(item["window_end_time"]) for item in related)
            last_bar_open = max(utc(item["window_end_time"]) for item in related)
            latest_rows = [
                item for item in related if utc(item["window_end_time"]) == last_bar_open
            ]
            latest_row = max(latest_rows, key=lambda item: float(item["confidence"]))
            event = dict(latest_row)
            event["first_detection_bar_open_time"] = first_bar_open.isoformat()
            event["last_detection_bar_open_time"] = last_bar_open.isoformat()
            event["first_available_at"] = (first_bar_open + BAR_DELTA).isoformat()
            event["last_available_at"] = (last_bar_open + BAR_DELTA).isoformat()
            event["event_peak_confidence"] = float(peak_row["confidence"])
            event["event_peak_bar_open_time"] = utc(peak_row["window_end_time"]).isoformat()
            event["event_peak_available_at"] = (
                utc(peak_row["window_end_time"]) + BAR_DELTA
            ).isoformat()
            event["classes_observed"] = sorted({str(item["class_name"]) for item in related})
            event["candidate_count"] = len(related)
            event["representative_rule"] = (
                "latest_detection_endpoint_then_highest_confidence; "
                "event peak retained separately"
            )
            event["symbol"] = symbol
            events.append(event)
    events.sort(
        key=lambda row: (str(row["first_available_at"]), float(row["confidence"])),
        reverse=True,
    )
    for sequence, row in enumerate(events, 1):
        row["event_id"] = f"4h_yolo_{sequence:03d}_{str(row['symbol']).replace('_USDT_SWAP', '')}"
    return events


def render_event(
    row: Mapping[str, Any],
    *,
    frame: pd.DataFrame,
    order: int,
    total: int,
) -> np.ndarray:
    """Render one 4h full-context chart plus the exact scored model input."""

    start_i = int(row["window_start_i"])
    end_i = int(row["window_end_i"])
    model_window = frame.iloc[start_i : end_i + 1]
    clean, input_tf = render_chart(model_window, out_path=None)
    if pixel_sha256(clean) != str(row["input_pixel_sha256"]):
        raise FourHourYoloError("model input pixel replay drifted")
    overlay = clean.copy()
    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(row)
    cv2.rectangle(
        overlay,
        (raw_x0, raw_y0),
        (raw_x1, raw_y1),
        CLASS_COLORS[int(row["class_id"])],
        4,
        cv2.LINE_AA,
    )

    context_end_i = len(frame) - 1
    context_start_i = delivery_context_start(
        frame_length=len(frame),
        window_start_i=start_i,
    )
    context = frame.iloc[context_start_i : context_end_i + 1]
    main, context_tf = render_chart(
        context,
        width=MAIN_WIDTH,
        height=MAIN_HEIGHT,
        out_path=None,
    )
    x0, y0, x1, y1 = project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=context_start_i,
    )
    cv2.rectangle(
        main,
        (x0, y0),
        (x1, y1),
        CLASS_COLORS[int(row["class_id"])],
        5,
        cv2.LINE_AA,
    )
    local_detect = end_i - context_start_i
    detect_x = x_at_float(context_tf, local_detect)
    if local_detect < len(context) - 1:
        shaded = main.copy()
        cv2.rectangle(
            shaded,
            (detect_x + 1, 0),
            (MAIN_WIDTH - 1, MAIN_HEIGHT - 1),
            (228, 231, 235),
            -1,
        )
        main = cv2.addWeighted(shaded, 0.25, main, 0.75, 0)
    dashed_vertical(main, detect_x, 8, MAIN_HEIGHT - 15)
    put_text(main, "DETECT", (max(4, detect_x - 32), 27), scale=0.48, thickness=2)

    canvas = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
    symbol = str(row["symbol"]).replace("_USDT_SWAP", "")
    detect_bar_open = utc(row["window_end_time"])
    representative_available_at = detect_bar_open + BAR_DELTA
    first_at = utc(row["first_available_at"])
    core_start = utc(row["core_start_time"])
    core_end = utc(row["core_end_time"])
    put_text(
        canvas,
        f"{symbol}USDT.P 4h | YOLO OOD RESEARCH | {'CURRENT' if bool(row['is_current_latest_bar']) else 'RECENT'} {direction} conf {float(row['confidence']):.3f} | {order:02d}/{total:02d}",
        (24, 38),
        scale=0.70,
        thickness=2,
    )
    put_text(
        canvas,
        f"core opens {core_start:%m-%d %H:%M}..{core_end:%m-%d %H:%M} UTC | first available after close {first_at:%m-%d %H:%M} UTC | representative available {representative_available_at:%m-%d %H:%M} UTC",
        (24, 72),
        scale=0.47,
        color=(55, 55, 55),
    )
    put_text(
        canvas,
        f"Frozen model: {MODEL_NAME} | W{int(row['window_len'])} core{int(row['core_length_bars'])} post{int(row['confirmation_bars'])} | event peak conf {float(row['event_peak_confidence']):.3f} | latest raw box",
        (24, 102),
        scale=0.46,
        color=(75, 75, 75),
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main

    times = pd.to_datetime(context["open_time"], utc=True).reset_index(drop=True)
    for local_i in np.linspace(0, len(context) - 1, 6).round().astype(int):
        x = MAIN_X + x_at_float(context_tf, int(local_i))
        stamp = utc(times.iloc[int(local_i)])
        put_text(
            canvas,
            f"{stamp:%m-%d %H:%M}",
            (max(0, x - 48), MAIN_Y + MAIN_HEIGHT + 24),
            scale=0.40,
            color=(80, 80, 80),
        )
    for fraction in np.linspace(0.08, 0.92, 5):
        price = context_tf.price_max - fraction * (context_tf.price_max - context_tf.price_min)
        y = MAIN_Y + int(round(context_tf.top + fraction * context_tf.plot_h))
        put_text(
            canvas,
            price_text(price),
            (CANVAS_WIDTH - 118, y),
            scale=0.40,
            color=(75, 75, 75),
        )

    footer_y = 926
    put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.64, thickness=2)
    put_text(
        canvas,
        "Top: latest 80 confirmed 4h bars. Colored rectangle is the unchanged YOLO box; DETECT is the model-input right edge.",
        (28, footer_y + 34),
        scale=0.44,
    )
    put_text(
        canvas,
        "Right: exact W18/W19 image scored by the model. The model was trained only on 15m charts; 4h use is out-of-distribution.",
        (28, footer_y + 64),
        scale=0.44,
    )
    put_text(
        canvas,
        "Research candidate only: confidence is detector confidence, not win probability or trade authorization.",
        (28, footer_y + 94),
        scale=0.44,
        color=(45, 45, 180),
        thickness=2,
    )
    put_text(canvas, "EXACT MODEL INPUT", (CANVAS_WIDTH - INSET_WIDTH - 18, footer_y), scale=0.60, thickness=2)
    inset = cv2.resize(overlay, (INSET_WIDTH, INSET_HEIGHT), interpolation=cv2.INTER_AREA)
    inset_x, inset_y = CANVAS_WIDTH - INSET_WIDTH - 18, footer_y + 18
    canvas[inset_y : inset_y + INSET_HEIGHT, inset_x : inset_x + INSET_WIDTH] = inset
    cv2.rectangle(
        canvas,
        (inset_x, inset_y),
        (inset_x + INSET_WIDTH - 1, inset_y + INSET_HEIGHT - 1),
        (65, 65, 65),
        2,
    )
    return canvas


def build_overview(chart_paths: Sequence[Path], events: Sequence[Mapping[str, Any]], out: Path) -> None:
    """Build paged contact sheets and a first-page overview alias."""

    if not chart_paths:
        blank = np.full((720, 1280, 3), 247, dtype=np.uint8)
        put_text(blank, "4H YOLO OOD RESEARCH: NO ACCEPTED CANDIDATES IN THE LATEST 24H", (70, 330), scale=0.86, thickness=2)
        put_text(blank, "frozen universe | confirmed OKX 4h bars | frozen conf=0.25 / NMS=0.70", (180, 380), scale=0.62)
        cv2.imwrite(str(out / "overview.png"), blank)
        return
    page_size = 9
    page_paths: list[Path] = []
    for page_no, start in enumerate(range(0, len(chart_paths), page_size), 1):
        subset = chart_paths[start : start + page_size]
        thumb_w, thumb_h = 620, 426
        sheet = np.full((3 * thumb_h + 82, 3 * thumb_w, 3), 240, dtype=np.uint8)
        put_text(
            sheet,
            f"4H YOLO OOD RESEARCH | recent confirmed candidates | page {page_no}",
            (24, 34),
            scale=0.72,
            thickness=2,
        )
        put_text(
            sheet,
            "15m-trained Grade-A full40 1280 applied unchanged to 4h; NOT validated trade signals",
            (24, 66),
            scale=0.48,
            color=(45, 45, 180),
            thickness=2,
        )
        for slot, path in enumerate(subset):
            image = cv2.imread(str(path))
            if image is None:
                raise FourHourYoloError(f"could not read chart for overview: {path}")
            thumb = cv2.resize(image, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
            row, col = divmod(slot, 3)
            y, x = 82 + row * thumb_h, col * thumb_w
            sheet[y : y + thumb_h, x : x + thumb_w] = thumb
            event = events[start + slot]
            label = (
                f"{start + slot + 1:02d} {str(event['symbol']).replace('_USDT_SWAP', '')} "
                f"{'NOW' if bool(event['is_current_latest_bar']) else 'RECENT'} "
                f"{'LONG' if int(event['class_id']) == 0 else 'SHORT'} {float(event['confidence']):.3f}"
            )
            cv2.rectangle(sheet, (x + 4, y + 4), (x + 300, y + 31), (250, 250, 250), -1)
            put_text(sheet, label, (x + 10, y + 25), scale=0.53, thickness=2)
        page = out / f"overview_page_{page_no:02d}.png"
        cv2.imwrite(str(page), sheet)
        page_paths.append(page)
    shutil.copyfile(page_paths[0], out / "overview.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--all-eligible",
        action="store_true",
        help="scan every current live instCategory=1 crypto USDT swap instead of the radar pool",
    )
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        raise FileExistsError(f"refusing to overwrite {out}")
    building = out.with_name(out.name + ".building")
    if building.exists():
        raise FileExistsError(f"stale building directory exists: {building}")
    if sha256_file(WEIGHTS) != EXPECTED_WEIGHT_SHA256:
        raise FourHourYoloError("frozen YOLO weight identity drifted")

    started = time.perf_counter()
    building.mkdir(parents=True)
    candle_dir = building / "candles"
    chart_dir = building / "charts"
    candle_dir.mkdir()
    chart_dir.mkdir()
    try:
        if args.all_eligible:
            ticker_rows = list(common._request(common.TICKERS_URL).get("data") or [])  # noqa: SLF001
            instrument_rows = list(
                common._request(common.INSTRUMENTS_URL).get("data") or []  # noqa: SLF001
            )
            pool_records = [
                {
                    "symbol": inst_id.replace("-", "_"),
                    "inst_id": inst_id,
                    "rank_side": "all_eligible",
                    "rank": rank,
                }
                for rank, inst_id in enumerate(
                    common.eligible_instruments(ticker_rows, instrument_rows), 1
                )
            ]
            universe_rule = (
                "all current live instCategory=1 crypto USDT swaps with a positive ticker; "
                "project BLOCKED_BASES and STOCKISH_BASES excluded"
            )
            holdout_number = ALL_UNIVERSE_HOLDOUT_CONSUMPTION_NUMBER
        else:
            pool = build_scan_pool(
                top_n=15,
                min_vol_usdt=5_000_000.0,
                include_loss=True,
                volume_top=10,
                include_majors=True,
                max_symbols=50,
            )
            pool_records = [asdict(item) for item in pool]
            universe_rule = (
                "pinned majors + 10 additional quote-volume leaders + 15 liquid gainers + "
                "15 liquid losers; deduplicated; max 50"
            )
            holdout_number = HOLDOUT_CONSUMPTION_NUMBER
        if not pool_records:
            raise FourHourYoloError("eligible universe is empty")
        pool_symbols = [str(item["symbol"]) for item in pool_records]
        write_json(
            building / "universe.json",
            {
                "frozen_at": datetime.now(timezone.utc).isoformat(),
                "mode": "all_eligible" if args.all_eligible else "radar",
                "rule": universe_rule,
                "symbols": pool_records,
            },
        )
        print(
            f"frozen {'all-eligible universe' if args.all_eligible else 'radar pool'}: "
            f"{len(pool_symbols)} symbols",
            flush=True,
        )

        frames: dict[str, pd.DataFrame] = {}
        fetch_audits: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(fetch_one, symbol): symbol for symbol in pool_symbols}
            for number, future in enumerate(as_completed(futures), 1):
                symbol, frame, audit, error = future.result()
                if error is not None:
                    failures.append({"symbol": symbol, "error": error})
                else:
                    frames[symbol] = frame
                    path = candle_dir / f"{symbol}.csv"
                    frame.to_csv(path, index=False)
                    fetch_audits.append(
                        {
                            "symbol": symbol,
                            "rows": len(frame),
                            "first": utc(frame.iloc[0]["open_time"]).isoformat(),
                            "last": utc(frame.iloc[-1]["open_time"]).isoformat(),
                            "sha256": sha256_file(path),
                            **dict(audit or {}),
                        }
                    )
                print(
                    f"fetch {number:03d}/{len(pool_symbols)} {symbol:<22} "
                    f"rows={len(frame):>3} {'OK' if error is None else error}",
                    flush=True,
                )
        if not frames:
            raise FourHourYoloError("no usable 4h frames")

        enriched, tasks = build_tasks(frames)
        device = choose_device(args.device)
        from ultralytics import YOLO

        model = YOLO(str(WEIGHTS))
        names = {int(key): str(value) for key, value in model.names.items()}
        if names != CLASS_NAMES:
            raise FourHourYoloError(f"class map drifted: {names}")
        print(f"inference device={device} tasks={len(tasks)}", flush=True)
        candidates, stats = infer(
            model,
            tasks,
            frames=enriched,
            device=device,
            batch_size=max(1, args.batch_size),
        )
        events = deduplicate(candidates)
        for event in events:
            latest_market_bar_open = utc(
                enriched[str(event["symbol"])].iloc[-1]["open_time"]
            )
            event["latest_market_bar_open_time"] = latest_market_bar_open.isoformat()
            event["latest_market_bar_available_at"] = (
                latest_market_bar_open + BAR_DELTA
            ).isoformat()
            event["is_current_latest_bar"] = (
                utc(event["window_end_time"]) == latest_market_bar_open
            )
        chart_paths: list[Path] = []
        for order, event in enumerate(events, 1):
            image = render_event(
                event,
                frame=enriched[str(event["symbol"])],
                order=order,
                total=len(events),
            )
            path = chart_dir / f"{order:02d}_{str(event['symbol']).replace('_USDT_SWAP', '')}_{'LONG' if int(event['class_id']) == 0 else 'SHORT'}.png"
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise FourHourYoloError(f"could not write chart: {path}")
            event["chart"] = f"charts/{path.name}"
            event["chart_sha256"] = sha256_file(path)
            chart_paths.append(path)
        build_overview(chart_paths, events, building)

        pd.DataFrame(candidates).to_csv(building / "accepted_candidates.csv", index=False)
        pd.DataFrame(events).to_csv(building / "signals.csv", index=False)
        latest_bars = Counter(utc(frame.iloc[-1]["open_time"]).isoformat() for frame in frames.values())
        latest_closes = Counter(
            (utc(frame.iloc[-1]["open_time"]) + BAR_DELTA).isoformat()
            for frame in frames.values()
        )
        sides = Counter(str(event["class_name"]) for event in events)
        current_events = [event for event in events if bool(event["is_current_latest_bar"])]
        current_sides = Counter(str(event["class_name"]) for event in current_events)
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": MODEL_NAME,
            "weights": str(WEIGHTS.relative_to(ROOT)),
            "weights_sha256": EXPECTED_WEIGHT_SHA256,
            "source_timeframe": "15m",
            "inference_timeframe": "4h",
            "out_of_distribution": True,
            "research_only": True,
            "holdout_consumed": True,
            "holdout_consumption_number_for_checkpoint": holdout_number,
            "owner_authorization_scope": (
                "Owner requested the latest 4h signals, explicitly required the YOLO model, "
                "and then requested every symbol."
                if args.all_eligible
                else "Owner requested the latest 4h signals, then explicitly required the YOLO model."
            ),
            "universe_mode": "all_eligible" if args.all_eligible else "radar",
            "universe_rule": universe_rule,
            "universe_symbols": len(pool_symbols),
            "usable_symbols": len(frames),
            "excluded_symbols": failures,
            "latest_confirmed_bar_open_counts": dict(sorted(latest_bars.items())),
            "latest_confirmed_bar_available_at_counts": dict(sorted(latest_closes.items())),
            "lookback_confirmed_4h_bars": LOOKBACK_ENDPOINTS,
            "windows_scored": int(stats["windows_scored"]),
            "raw_boxes": int(stats["raw_boxes"]),
            "accepted_structural_boxes": int(stats["accepted_structural_boxes"]),
            "deduplicated_events": len(events),
            "long_events": int(sides["dense_long"]),
            "short_events": int(sides["dense_short"]),
            "current_latest_bar_events": len(current_events),
            "current_latest_bar_long_events": int(current_sides["dense_long"]),
            "current_latest_bar_short_events": int(current_sides["dense_short"]),
            "detector_contract": {
                "confidence": CONFIDENCE,
                "nms_iou": NMS_IOU,
                "imgsz": IMAGE_SIZE,
                "window_lengths": list(WINDOW_LENGTHS),
                "core_lengths": sorted(ALLOWED_CORES),
                "confirmation_bars": sorted(ALLOWED_CONFIRMATIONS),
                "same_symbol_gap_bars": EVENT_GAP_BARS,
            },
            "stats": dict(sorted(stats.items())),
            "fetch_audits": sorted(fetch_audits, key=lambda row: str(row["symbol"])),
            "signals": events,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "threshold_or_weight_changed": False,
            "trained": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "production_eligible": False,
        }
        write_json(building / "summary.json", summary)
        building.replace(out)
        print(
            f"complete usable={len(frames)}/{len(pool_symbols)} candidates={len(candidates)} "
            f"events={len(events)} output={out}",
            flush=True,
        )
        return 0
    except Exception:
        write_json(
            building / "failure_receipt.json",
            {
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "out": str(out),
                "building": str(building),
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
