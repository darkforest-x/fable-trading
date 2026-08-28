#!/usr/bin/env python3
"""Render every frozen 2026-08-27 Owner-YOLO event with full market context.

Data sources are the already-frozen five-day event ledger and its local 15m
OHLCV snapshots.  No network read or model inference occurs.  Each output has
two evidence surfaces: a dominant 110-bar chart spanning the complete UTC board
day plus every frozen boundary bar available (two hours before and 75 minutes
after), and the exact 1280x742 W18--25 image that the
detector saw.  The preserved raw YOLO rectangle is inverse-projected from model
pixels into absolute fractional-bar/price coordinates and then reprojected onto
the full-context chart.  A dashed line marks ``window_end_time``, the real
completed detection time.

The full chart intentionally contains bars after the model cutoff for human
review only.  Those pixels never enter inference, training, labels, features,
ACTIVE/frozen, forward state, deployment, or trading decisions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from scripts.scan_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    normalized_box_corners,
    pixel_sha256,
)
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import ChartTransform, render_chart


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-20260827-fullcontext-v3"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
BOARD_DAY = pd.Timestamp("2026-08-27T00:00:00Z")
CONTEXT_START = BOARD_DAY - pd.Timedelta(hours=2)
CONTEXT_END = BOARD_DAY + pd.Timedelta(days=1, hours=1, minutes=15)
EXPECTED_EVENTS = 43
EXPECTED_CONTEXT_BARS = 110
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1400
MAIN_X = 20
MAIN_Y = 118
MAIN_WIDTH = 1880
MAIN_HEIGHT = 780
INSET_WIDTH = 700
INSET_HEIGHT = 406


class FullContextError(RuntimeError):
    """Fail-closed source, projection, render, or safety error."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc(value: object) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        return result.tz_localize("UTC")
    return result.tz_convert("UTC")


def resolve_repo_path(value: object) -> Path:
    text = str(value).replace("\\", "/")
    path = (ROOT / text).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise FullContextError(f"path escapes repository: {value}") from exc
    return path


def load_contract(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise FullContextError("unexpected experiment id")
    auth = payload["owner_authorization"]
    if int(auth["holdout_consumption_number_for_this_configuration"]) != 3:
        raise FullContextError("holdout consumption number drifted")
    for key in (
        "training_or_tuning_authorized",
        "threshold_or_weight_change_authorized",
        "production_or_promotion_authorized",
    ):
        if auth.get(key) is not False:
            raise FullContextError(f"unsafe authorization flag: {key}")
    safety = payload["safety"]
    for key in (
        "new_model_inference",
        "threshold_or_weight_changed",
        "training_or_tuning",
        "active_or_frozen_changed",
        "promoted",
        "deployed",
        "forward_state_changed",
        "orders_placed",
        "training_eligible",
        "production_eligible",
    ):
        if safety.get(key) is not False:
            raise FullContextError(f"unsafe prereg safety flag: {key}")
    if int(safety.get("network_reads", -1)) != 0:
        raise FullContextError("network reads must remain zero")
    return payload


def load_events(prereg: Mapping[str, Any]) -> tuple[pd.DataFrame, Path]:
    source = prereg["source_contract"]
    path = resolve_repo_path(source["events_path"])
    if sha256_file(path) != str(source["events_sha256"]):
        raise FullContextError("frozen event ledger hash drifted")
    frame = pd.read_csv(path)
    events = frame.loc[pd.to_datetime(frame["day"], utc=True) == BOARD_DAY].copy()
    events = events.sort_values(
        ["rank", "window_end_i", "class_id", "confidence"],
        ascending=[True, True, True, False],
        kind="stable",
    ).reset_index(drop=True)
    if len(events) != int(source["expected_events"]) or len(events) != EXPECTED_EVENTS:
        raise FullContextError(f"expected 43 frozen events, found {len(events)}")
    if events["symbol"].nunique() != int(source["expected_symbols_with_events"]):
        raise FullContextError("event-symbol coverage drifted")
    end_times = pd.to_datetime(events["window_end_time"], utc=True)
    on_day = int(((end_times >= BOARD_DAY) & (end_times < BOARD_DAY + pd.Timedelta(days=1))).sum())
    after = int((end_times >= BOARD_DAY + pd.Timedelta(days=1)).sum())
    if (on_day, after) != (
        int(source["expected_window_end_on_board_day"]),
        int(source["expected_window_end_after_midnight"]),
    ):
        raise FullContextError("detection-time day split drifted")
    return events, path


def load_enriched_snapshot(snapshot_dir: Path, symbol: str) -> tuple[pd.DataFrame, str]:
    path = snapshot_dir / f"{symbol}.csv"
    if not path.is_file():
        raise FullContextError(f"snapshot missing: {symbol}")
    frame = pd.read_csv(path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame["open_time"].duplicated().any() or not frame["open_time"].is_monotonic_increasing:
        raise FullContextError(f"snapshot time identity invalid: {symbol}")
    return add_mas(frame), sha256_file(path)


def inverse_x(tf: ChartTransform, pixel_x: float) -> float:
    if tf.n_bars <= 1:
        return 0.0
    return (float(pixel_x) - tf.left) / tf.plot_w * (tf.n_bars - 1)


def inverse_y(tf: ChartTransform, pixel_y: float) -> float:
    span = tf.price_max - tf.price_min
    return tf.price_max - (float(pixel_y) - tf.top) / tf.plot_h * span


def x_at_float(tf: ChartTransform, index: float) -> int:
    if tf.n_bars <= 1:
        return tf.left
    return int(round(tf.left + float(index) / (tf.n_bars - 1) * tf.plot_w))


def project_raw_box(
    row: Mapping[str, Any],
    *,
    input_tf: ChartTransform,
    context_tf: ChartTransform,
    context_start_i: int,
) -> dict[str, float | int]:
    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(row)
    global_x0 = int(row["window_start_i"]) + inverse_x(input_tf, raw_x0)
    global_x1 = int(row["window_start_i"]) + inverse_x(input_tf, raw_x1)
    price_high = inverse_y(input_tf, raw_y0)
    price_low = inverse_y(input_tf, raw_y1)
    context_x0 = x_at_float(context_tf, global_x0 - context_start_i)
    context_x1 = x_at_float(context_tf, global_x1 - context_start_i)
    context_y0 = context_tf.y_at(price_high)
    context_y1 = context_tf.y_at(price_low)

    # Reprojection must recover the raw input rectangle to rounding tolerance.
    check_x0 = x_at_float(input_tf, global_x0 - int(row["window_start_i"]))
    check_x1 = x_at_float(input_tf, global_x1 - int(row["window_start_i"]))
    check_y0 = input_tf.y_at(price_high)
    check_y1 = input_tf.y_at(price_low)
    if max(abs(check_x0 - raw_x0), abs(check_x1 - raw_x1), abs(check_y0 - raw_y0), abs(check_y1 - raw_y1)) > 1:
        raise FullContextError("raw-box inverse/forward projection exceeded one pixel")
    return {
        "raw_x0_px": raw_x0,
        "raw_y0_px": raw_y0,
        "raw_x1_px": raw_x1,
        "raw_y1_px": raw_y1,
        "global_x0_bar": global_x0,
        "global_x1_bar": global_x1,
        "price_high": price_high,
        "price_low": price_low,
        "context_x0_px": context_x0,
        "context_y0_px": context_y0,
        "context_x1_px": context_x1,
        "context_y1_px": context_y1,
    }


def put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    scale: float = 0.58,
    color: tuple[int, int, int] = (28, 28, 28),
    thickness: int = 1,
) -> None:
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


def dashed_vertical(
    image: np.ndarray,
    x: int,
    y0: int,
    y1: int,
    *,
    color: tuple[int, int, int] = (35, 35, 35),
    dash: int = 12,
    gap: int = 8,
    thickness: int = 2,
) -> None:
    for start in range(y0, y1 + 1, dash + gap):
        cv2.line(image, (x, start), (x, min(y1, start + dash)), color, thickness, cv2.LINE_AA)


def price_text(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.1f}"
    if magnitude >= 10:
        return f"{value:.3f}"
    if magnitude >= 1:
        return f"{value:.4f}"
    if magnitude >= 0.01:
        return f"{value:.5f}"
    return f"{value:.7f}"


def render_event(
    row: Mapping[str, Any],
    *,
    event_order: int,
    enriched: pd.DataFrame,
) -> tuple[np.ndarray, dict[str, Any]]:
    times = pd.to_datetime(enriched["open_time"], utc=True)
    context_indices = np.flatnonzero((times >= CONTEXT_START) & (times <= CONTEXT_END))
    if len(context_indices) != EXPECTED_CONTEXT_BARS:
        raise FullContextError(f"context must be 110 bars for {row['symbol']}")
    context_start_i, context_end_i = int(context_indices[0]), int(context_indices[-1])
    context = enriched.iloc[context_start_i : context_end_i + 1]
    context_times = pd.to_datetime(context["open_time"], utc=True)
    deltas = context_times.diff().dropna()
    if not (deltas == pd.Timedelta(minutes=15)).all():
        raise FullContextError(f"15m gap in context for {row['symbol']}")

    window_start_i, window_end_i = int(row["window_start_i"]), int(row["window_end_i"])
    model_window = enriched.iloc[window_start_i : window_end_i + 1]
    if len(model_window) != int(row["window_len"]):
        raise FullContextError("model window length drifted")
    model_input, input_tf = render_chart(model_window, out_path=None)
    if pixel_sha256(model_input) != str(row["input_pixel_sha256"]):
        raise FullContextError("exact model input pixel identity drifted")
    raw_overlay = model_input.copy()
    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(row)
    color = common.CLASS_COLORS[int(row["class_id"])]
    cv2.rectangle(raw_overlay, (raw_x0, raw_y0), (raw_x1, raw_y1), color, 4, cv2.LINE_AA)

    main, context_tf = render_chart(context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None)
    projection = project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=context_start_i,
    )

    # Grey side bands distinguish boundary context from the 96-bar UTC board day.
    board_start_local = int(np.flatnonzero(context_times >= BOARD_DAY)[0])
    board_end_local = int(np.flatnonzero(context_times < BOARD_DAY + pd.Timedelta(days=1))[-1])
    left_boundary = (context_tf.x_at(board_start_local - 1) + context_tf.x_at(board_start_local)) // 2
    right_boundary = (context_tf.x_at(board_end_local) + context_tf.x_at(board_end_local + 1)) // 2
    shaded = main.copy()
    cv2.rectangle(shaded, (0, 0), (left_boundary, MAIN_HEIGHT - 1), (230, 232, 235), -1)
    cv2.rectangle(shaded, (right_boundary, 0), (MAIN_WIDTH - 1, MAIN_HEIGHT - 1), (230, 232, 235), -1)
    main = cv2.addWeighted(shaded, 0.28, main, 0.72, 0)

    x0 = max(0, min(MAIN_WIDTH - 1, int(projection["context_x0_px"])))
    x1 = max(0, min(MAIN_WIDTH - 1, int(projection["context_x1_px"])))
    y0 = max(0, min(MAIN_HEIGHT - 1, int(projection["context_y0_px"])))
    y1 = max(0, min(MAIN_HEIGHT - 1, int(projection["context_y1_px"])))
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    cv2.rectangle(main, (x0, y0), (x1, y1), color, 5, cv2.LINE_AA)

    detection_x = x_at_float(context_tf, window_end_i - context_start_i)
    dashed_vertical(main, detection_x, 10, MAIN_HEIGHT - 18)
    put_text(main, "DETECT", (max(4, detection_x - 33), 28), scale=0.48, thickness=2)

    canvas = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
    symbol = str(row["symbol"]).replace("_USDT_SWAP", "")
    detect_utc = utc(row["window_end_time"])
    detect_cst = detect_utc.tz_convert("Asia/Shanghai")
    put_text(
        canvas,
        f"{event_order:02d}/{EXPECTED_EVENTS} | rank #{int(row['rank']):02d} {symbol} | "
        f"{direction} conf {float(row['confidence']):.3f} | board return {float(row['daily_return']) * 100:+.2f}%",
        (24, 40),
        scale=0.78,
        thickness=2,
    )
    put_text(
        canvas,
        f"detection complete {detect_utc:%Y-%m-%d %H:%M} UTC / {detect_cst:%m-%d %H:%M} CST | "
        f"W{int(row['window_len'])} | core {int(row['core_length_bars'])}K | confirm {int(row['confirmation_bars'])}K",
        (24, 76),
        scale=0.64,
        color=(60, 60, 60),
        thickness=1,
    )
    put_text(
        canvas,
        "FULL CONTEXT: 110 x 15m bars (grey = frozen boundary context; white = 2026-08-27 UTC)",
        (24, 106),
        scale=0.52,
        color=(85, 85, 85),
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main

    # Time and price guides are outside the chart pixels and do not alter geometry.
    label_times = [
        CONTEXT_START,
        BOARD_DAY,
        BOARD_DAY + pd.Timedelta(hours=4),
        BOARD_DAY + pd.Timedelta(hours=8),
        BOARD_DAY + pd.Timedelta(hours=12),
        BOARD_DAY + pd.Timedelta(hours=16),
        BOARD_DAY + pd.Timedelta(hours=20),
        BOARD_DAY + pd.Timedelta(days=1),
        CONTEXT_END,
    ]
    time_to_global = {utc(value): idx for idx, value in enumerate(times)}
    for label_time in label_times:
        global_i = time_to_global.get(label_time)
        if global_i is None:
            continue
        px = MAIN_X + x_at_float(context_tf, global_i - context_start_i)
        text = f"{label_time:%m-%d %H:%M}"
        put_text(canvas, text, (max(0, px - 50), MAIN_Y + MAIN_HEIGHT + 25), scale=0.42, color=(80, 80, 80))
    for fraction in np.linspace(0.05, 0.95, 5):
        price = context_tf.price_max - fraction * (context_tf.price_max - context_tf.price_min)
        py = MAIN_Y + int(round(context_tf.top + fraction * context_tf.plot_h))
        put_text(canvas, price_text(price), (CANVAS_WIDTH - 118, py), scale=0.42, color=(75, 75, 75))

    footer_y = 944
    put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.66, thickness=2)
    put_text(canvas, "Top: full path. Colored rectangle is the preserved raw YOLO box in absolute time/price.", (28, footer_y + 35), scale=0.50)
    put_text(canvas, "Dashed DETECT line is when core + 4-6 confirmation bars were fully known.", (28, footer_y + 65), scale=0.50)
    put_text(canvas, "Right: exact detector input and raw rectangle; no full-day pixels entered the model.", (28, footer_y + 95), scale=0.50)
    if detect_utc >= BOARD_DAY + pd.Timedelta(days=1):
        put_text(canvas, "NOTE: core belongs to 08-27 board; detection completed after UTC midnight.", (28, footer_y + 132), scale=0.52, color=(30, 80, 190), thickness=2)
    put_text(canvas, "EXACT MODEL INPUT", (CANVAS_WIDTH - INSET_WIDTH - 18, footer_y), scale=0.60, thickness=2)
    inset = cv2.resize(raw_overlay, (INSET_WIDTH, INSET_HEIGHT), interpolation=cv2.INTER_AREA)
    inset_x, inset_y = CANVAS_WIDTH - INSET_WIDTH - 18, footer_y + 18
    canvas[inset_y : inset_y + INSET_HEIGHT, inset_x : inset_x + INSET_WIDTH] = inset
    cv2.rectangle(canvas, (inset_x, inset_y), (inset_x + INSET_WIDTH - 1, inset_y + INSET_HEIGHT - 1), (65, 65, 65), 2)

    metadata = {
        **projection,
        "event_order": event_order,
        "symbol": str(row["symbol"]),
        "rank": int(row["rank"]),
        "class_id": int(row["class_id"]),
        "class_name": str(row["class_name"]),
        "confidence": float(row["confidence"]),
        "daily_return": float(row["daily_return"]),
        "window_len": int(row["window_len"]),
        "window_start_i": window_start_i,
        "window_end_i": window_end_i,
        "window_end_time": detect_utc.isoformat(),
        "core_start_i": int(row["core_start_i"]),
        "core_end_i": int(row["core_end_i"]),
        "core_length_bars": int(row["core_length_bars"]),
        "confirmation_bars": int(row["confirmation_bars"]),
        "context_start_i": context_start_i,
        "context_end_i": context_end_i,
        "context_start_time": CONTEXT_START.isoformat(),
        "context_end_time": CONTEXT_END.isoformat(),
        "context_bars": len(context),
        "model_input_pixel_sha256": pixel_sha256(model_input),
        "after_board_midnight": bool(detect_utc >= BOARD_DAY + pd.Timedelta(days=1)),
        "boxes_per_document": 1,
        "canvas_width": CANVAS_WIDTH,
        "canvas_height": CANVAS_HEIGHT,
    }
    return canvas, metadata


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise FullContextError(f"could not write PNG: {path}")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate(*, prereg_path: Path = DEFAULT_PREREG, results: Path = DEFAULT_RESULTS) -> dict[str, Any]:
    prereg = load_contract(prereg_path)
    events, events_path = load_events(prereg)
    snapshot_dir = resolve_repo_path(prereg["source_contract"]["snapshot_dir"])
    if results.exists():
        raise FullContextError(f"refusing to overwrite existing results: {results}")
    results.parent.mkdir(parents=True, exist_ok=True)
    snapshot_hashes: dict[str, str] = {}
    frames: dict[str, pd.DataFrame] = {}
    manifest_rows: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="fullcontext_v3_", dir=results.parent) as temp_dir:
        building = Path(temp_dir)
        image_dir = building / "charts"
        image_dir.mkdir(parents=True)
        for order, raw_row in enumerate(events.to_dict("records"), 1):
            row = {
                key: (None if isinstance(value, float) and math.isnan(value) else value)
                for key, value in raw_row.items()
            }
            symbol = str(row["symbol"])
            if symbol not in frames:
                frames[symbol], snapshot_hashes[symbol] = load_enriched_snapshot(snapshot_dir, symbol)
            image, metadata = render_event(row, event_order=order, enriched=frames[symbol])
            direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
            stem = f"{order:02d}_{symbol}_{direction}_{utc(row['window_end_time']):%Y%m%dT%H%MZ}"
            path = image_dir / f"{stem}.png"
            write_png(path, image)
            metadata.update(
                {
                    "image_path": f"experiments/active/{EXPERIMENT_ID}/results/charts/{path.name}",
                    "image_sha256": sha256_file(path),
                    "image_size_bytes": path.stat().st_size,
                }
            )
            manifest_rows.append(metadata)
            print(
                f"render [{order:02d}/{EXPECTED_EVENTS}] #{int(row['rank']):02d} {symbol:<20} "
                f"{direction:<5} {float(row['confidence']):.3f} {utc(row['window_end_time']):%m-%d %H:%M}Z",
                flush=True,
            )

        manifest = building / "manifest.jsonl"
        manifest.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in manifest_rows),
            encoding="utf-8",
        )
        pd.DataFrame(manifest_rows).to_csv(building / "manifest.csv", index=False)
        receipt = {
            "experiment_id": EXPERIMENT_ID,
            "protocol": prereg["protocol"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "holdout_consumption_number_for_this_configuration": 3,
            "source_events_path": str(prereg["source_contract"]["events_path"]),
            "source_events_sha256": sha256_file(events_path),
            "source_snapshot_dir": str(prereg["source_contract"]["snapshot_dir"]),
            "source_snapshot_files": len(snapshot_hashes),
            "source_snapshot_sha256": snapshot_hashes,
            "board_day_utc": BOARD_DAY.isoformat(),
            "events": len(manifest_rows),
            "symbols": len(snapshot_hashes),
            "long_events": sum(row["class_id"] == 0 for row in manifest_rows),
            "short_events": sum(row["class_id"] == 1 for row in manifest_rows),
            "detections_completed_on_board_day": sum(not row["after_board_midnight"] for row in manifest_rows),
            "detections_completed_after_midnight": sum(row["after_board_midnight"] for row in manifest_rows),
            "documents": len(manifest_rows),
            "boxes_per_document_min": min(row["boxes_per_document"] for row in manifest_rows),
            "boxes_per_document_max": max(row["boxes_per_document"] for row in manifest_rows),
            "canvas_width": CANVAS_WIDTH,
            "canvas_height": CANVAS_HEIGHT,
            "context_bars_per_document": EXPECTED_CONTEXT_BARS,
            "exact_model_input_pixel_matches": len(manifest_rows),
            "raw_box_projection_roundtrip_matches": len(manifest_rows),
            "manifest_path": f"experiments/active/{EXPERIMENT_ID}/results/manifest.jsonl",
            "manifest_sha256": sha256_file(manifest),
            "network_reads": 0,
            "new_model_inference": False,
            "threshold_or_weight_changed": False,
            "training_or_tuning": False,
            "active_or_frozen_changed": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "training_eligible": False,
            "production_eligible": False,
        }
        write_json(building / "render_receipt.json", receipt)
        os.replace(building, results)
    print(
        f"full-context render complete: events={receipt['events']} symbols={receipt['symbols']} "
        f"long={receipt['long_events']} short={receipt['short_events']}",
        flush=True,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    generate(prereg_path=args.prereg.resolve(), results=args.results.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
