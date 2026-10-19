#!/usr/bin/env python3
"""Export every raw YOLO box from the frozen ETH 30-day scan.

The published ETH 30-day scan kept 1,057 structurally accepted predictions,
then reduced them to 53 five-bar events and 41 overlapping episodes.  It did
not persist the 261 predictions rejected by the structural mapping checks.
This exporter replays the exact frozen model input schedule on the already
frozen OHLC snapshot, retains all pre-structural-filter boxes, and renders one
full-context 1920x1400 chart per raw box.  A chart contains exactly one
preserved ``cx/cy/w/h`` rectangle plus the exact detector input inset.

The replay is not a new scan configuration: it uses the published weight,
renderer, snapshot, confidence, NMS IoU, W18--25 schedule, MPS device and
batch size from the source run.  It performs no network read, training,
tuning, threshold change, promotion, deployment, forward mutation, or order
action.  The source scan artifacts are never overwritten; the exporter
refuses to use an existing output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from scripts.render_15m_ma_launch_owner_yolo_20260827_fullcontext import (
    dashed_vertical,
    price_text,
    project_raw_box,
    put_text,
    x_at_float,
)
from scripts.scan_15m_ma_launch_owner_yolo_eth30d import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CONTEXT_BARS,
    INSET_HEIGHT,
    INSET_WIDTH,
    MAIN_HEIGHT,
    MAIN_WIDTH,
    MAIN_X,
    MAIN_Y,
    context_bounds,
    load_preregistration,
)
from scripts.scan_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    normalized_box_corners,
    pixel_sha256,
)
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas
from yoyo.layers.l1_detection.render import ChartTransform, render_chart


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-eth30d-20260828-v1"
SOURCE_OUT = ROOT / "analysis" / "output" / "ma_launch_owner_yolo_eth30d_20260828_v1"
SOURCE_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_owner_yolo_eth30d_20260828_raw_detections_v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
EXPECTED_BATCH_SIZE = 32
EXPECTED_RAW_BOXES = 1318
EXPECTED_ACCEPTED = 1057
EXPECTED_FIVE_BAR_EVENTS = 53
EXPECTED_EPISODES = 41
BAR_DELTA = pd.Timedelta(minutes=15)
SYMBOL = "ETH_USDT_SWAP"


class ExportError(RuntimeError):
    """Raised when source identity, replay parity, or rendering is unsafe."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to timezone-aware UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for one file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_png(path: Path, image: np.ndarray) -> None:
    """Write one PNG with the same OpenCV compression used by the source scan."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise ExportError(f"could not write PNG: {path}")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write one deterministic JSON object per manifest line."""

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Write rows with a stable union of manifest fields."""

    columns = sorted({key for row in rows for key in row})
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def repo_relative(path: Path) -> str:
    """Return a portable repository-relative path for lineage fields."""

    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def load_source_data() -> tuple[
    dict[str, Any],
    pd.DataFrame,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Load and hash the frozen source scan without reading the network."""

    prereg = load_preregistration(DEFAULT_PREREG)
    scan_receipt_path = SOURCE_RESULTS / "scan_receipt.json"
    fetch_receipt_path = SOURCE_RESULTS / "fetch_receipt.json"
    if not scan_receipt_path.is_file() or not fetch_receipt_path.is_file():
        raise ExportError("published ETH30d scan/fetch receipts are missing")
    scan_receipt = json.loads(scan_receipt_path.read_text(encoding="utf-8"))
    fetch_receipt = json.loads(fetch_receipt_path.read_text(encoding="utf-8"))
    if scan_receipt.get("experiment_id") != EXPERIMENT_ID:
        raise ExportError("source scan receipt experiment identity drifted")
    if fetch_receipt.get("experiment_id") != EXPERIMENT_ID:
        raise ExportError("source fetch receipt experiment identity drifted")
    totals = scan_receipt.get("scan_totals", {})
    expected_totals = {
        "raw_boxes": EXPECTED_RAW_BOXES,
        "accepted_structural_boxes": EXPECTED_ACCEPTED,
        "deduplicated_events": EXPECTED_FIVE_BAR_EVENTS,
    }
    for key, expected in expected_totals.items():
        if int(totals.get(key, -1)) != expected:
            raise ExportError(f"source scan total drifted: {key}={totals.get(key)!r}")
    if int(scan_receipt.get("overlap_episodes", -1)) != EXPECTED_EPISODES:
        raise ExportError("source episode count drifted")

    snapshot_path = SOURCE_OUT / "kline_snapshot" / f"{SYMBOL}.csv"
    expected_snapshot_sha = str(fetch_receipt["snapshot"]["sha256"])
    if not snapshot_path.is_file() or sha256_file(snapshot_path) != expected_snapshot_sha:
        raise ExportError("frozen ETH OHLC snapshot is missing or hash-drifted")
    frame = pd.read_csv(snapshot_path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    times = frame["open_time"]
    if len(frame) != 3078 or times.iloc[0] != utc("2026-07-27T00:00:00Z"):
        raise ExportError("snapshot shape or start time drifted")
    if times.duplicated().any() or not times.is_monotonic_increasing:
        raise ExportError("snapshot timestamps are not unique and increasing")
    if not (times.diff().dropna() == BAR_DELTA).all():
        raise ExportError("snapshot contains a non-15m gap")

    day_rows = pd.read_csv(SOURCE_OUT / "eth_daily_returns.csv").to_dict("records")
    if len(day_rows) != 30:
        raise ExportError("source daily ledger must contain 30 rows")
    candidates = pd.read_csv(SOURCE_OUT / "accepted_candidates.csv").to_dict("records")
    events = pd.read_csv(SOURCE_OUT / "five_bar_events.csv").to_dict("records")
    episodes = pd.read_csv(SOURCE_OUT / "episodes.csv").to_dict("records")
    if len(candidates) != EXPECTED_ACCEPTED:
        raise ExportError(f"source candidate count drifted: {len(candidates)}")
    if len(events) != EXPECTED_FIVE_BAR_EVENTS:
        raise ExportError(f"source five-bar event count drifted: {len(events)}")
    if len(episodes) != EXPECTED_EPISODES:
        raise ExportError(f"source episode count drifted: {len(episodes)}")

    source = {
        "scan_receipt": scan_receipt,
        "fetch_receipt": fetch_receipt,
        "scan_receipt_sha256": sha256_file(scan_receipt_path),
        "fetch_receipt_sha256": sha256_file(fetch_receipt_path),
        "snapshot_path": snapshot_path,
        "snapshot_sha256": expected_snapshot_sha,
        "candidates_path": SOURCE_OUT / "accepted_candidates.csv",
        "events_path": SOURCE_OUT / "five_bar_events.csv",
        "episodes_path": SOURCE_OUT / "episodes.csv",
    }
    return prereg, frame, candidates, events, episodes, source


def build_tasks(
    enriched: pd.DataFrame,
    *,
    day_row: Mapping[str, Any],
    detector: Mapping[str, Any],
) -> tuple[list[tuple[np.ndarray, ChartTransform, dict[str, Any]]], Counter[str]]:
    """Build exactly the source scan's endpoint/window tasks for one day."""

    times = pd.to_datetime(enriched["open_time"], utc=True)
    day = utc(day_row["day"])
    endpoint_end = day + pd.Timedelta(days=1) + int(
        detector["scan_endpoint_extension_after_day_bars"]
    ) * BAR_DELTA
    endpoint_indices = np.flatnonzero((times >= day) & (times < endpoint_end))
    tasks: list[tuple[np.ndarray, ChartTransform, dict[str, Any]]] = []
    stats: Counter[str] = Counter()
    for endpoint in endpoint_indices:
        end_i = int(endpoint)
        for window_len in map(int, detector["window_lengths"]):
            start_i = end_i - window_len + 1
            if start_i < 0:
                stats["skip_insufficient_rows"] += 1
                continue
            window = enriched.iloc[start_i : end_i + 1]
            if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                stats["skip_ma_warmup"] += 1
                continue
            image, transform = render_chart(window, out_path=None)
            tasks.append(
                (
                    image,
                    transform,
                    {
                        "scan_day": day.isoformat(),
                        "day": day.isoformat(),
                        "rank": int(day_row["rank"]),
                        "symbol": str(day_row["symbol"]),
                        "inst_id": str(day_row["inst_id"]),
                        "daily_return": float(day_row["daily_return"]),
                        "window_len": window_len,
                        "window_start_i": start_i,
                        "window_end_i": end_i,
                        "window_end_time": utc(times.iloc[end_i]).isoformat(),
                    },
                )
            )
    return tasks, stats


def source_prediction_id(meta: Mapping[str, Any], box_index: int) -> str:
    """Build a stable ID from the source scan day, task window and box order."""

    scan_day = utc(meta["scan_day"])
    return (
        f"raw_{scan_day:%Y%m%d}_end{int(meta['window_end_i']):04d}"
        f"_w{int(meta['window_len']):02d}_box{box_index:02d}"
    )


def _mapped_fields(
    *,
    xywhn: Sequence[float],
    transform: ChartTransform,
    meta: Mapping[str, Any],
    frame: pd.DataFrame,
) -> dict[str, Any]:
    """Map a raw rectangle to integer core coordinates like the source scan."""

    mapped = common.map_prediction_to_core(
        cx=float(xywhn[0]),
        width=float(xywhn[2]),
        transform=transform,
        window_start_i=int(meta["window_start_i"]),
        window_end_i=int(meta["window_end_i"]),
    )
    times = pd.to_datetime(frame["open_time"], utc=True)
    core_start = int(mapped["core_start_i"])
    core_end = int(mapped["core_end_i"])
    if not (0 <= core_start <= core_end < len(frame)):
        raise ExportError(f"mapped core is outside source frame: {mapped}")
    segment = frame.iloc[core_start : core_end + 1]
    return {
        **mapped,
        "core_start_time": utc(times.iloc[core_start]).isoformat(),
        "core_end_time": utc(times.iloc[core_end]).isoformat(),
        "core_high": float(segment["high"].max()),
        "core_low": float(segment["low"].min()),
    }


def predict_all_raw(
    model: Any,
    tasks: Sequence[tuple[np.ndarray, ChartTransform, dict[str, Any]]],
    *,
    batch_size: int,
    detector: Mapping[str, Any],
    device: str,
    day: pd.Timestamp,
    frame: pd.DataFrame,
    stats: Counter[str],
) -> list[dict[str, Any]]:
    """Replay the source adapter while retaining boxes before all filters."""

    rows: list[dict[str, Any]] = []
    times = pd.to_datetime(frame["open_time"], utc=True)
    allowed_cores = set(map(int, detector["mapped_core_length_bars_allowed"]))
    allowed_confirmations = set(map(int, detector["mapped_confirmation_bars_allowed"]))
    for start in range(0, len(tasks), batch_size):
        batch = tasks[start : start + batch_size]
        images = [item[0] for item in batch]
        predictions = model.predict(
            source=images,
            imgsz=int(detector["imgsz"]),
            conf=float(detector["confidence"]),
            iou=float(detector["nms_iou"]),
            batch=min(batch_size, len(images)),
            device=device,
            verbose=False,
        )
        if len(predictions) != len(batch):
            raise ExportError("prediction count differs from source task count")
        for prediction, (input_image, transform, meta) in zip(predictions, batch):
            stats["windows_scored"] += 1
            boxes = prediction.boxes
            if boxes is None or len(boxes) == 0:
                continue
            stats["windows_with_any_box"] += 1
            input_hash = pixel_sha256(input_image)
            for box_index, (xywhn, class_id, confidence) in enumerate(
                zip(
                    boxes.xywhn.cpu().numpy(),
                    boxes.cls.cpu().numpy(),
                    boxes.conf.cpu().numpy(),
                ),
                1,
            ):
                stats["raw_boxes"] += 1
                cid = int(class_id)
                xywh = [float(value) for value in xywhn]
                row: dict[str, Any] = {
                    **meta,
                    "source_prediction_id": source_prediction_id(meta, box_index),
                    "box_index_in_source_prediction": box_index,
                    "prediction_cx_norm": xywh[0],
                    "prediction_cy_norm": xywh[1],
                    "prediction_w_norm": xywh[2],
                    "prediction_h_norm": xywh[3],
                    "input_width": int(transform.width),
                    "input_height": int(transform.height),
                    "input_n_bars": int(transform.n_bars),
                    "input_pixel_sha256": input_hash,
                    "class_id": cid,
                    "class_name": common.CLASS_NAMES.get(cid, f"unknown_{cid}"),
                    "confidence": float(confidence),
                    "accepted_structural": False,
                    "structural_status": "rejected",
                    "rejection_reason": "",
                    "candidate_id": "",
                    "five_bar_event_id": "",
                    "episode_id": "",
                    "episode_candidate_count": 0,
                    "episode_sequence": 0,
                }
                mapped: dict[str, Any] | None = None
                if cid not in common.CLASS_NAMES:
                    stats["reject_unknown_class"] += 1
                    row["rejection_reason"] = "unknown_class"
                else:
                    mapped = _mapped_fields(
                        xywhn=xywh,
                        transform=transform,
                        meta=meta,
                        frame=frame,
                    )
                    row.update(mapped)
                    if int(mapped["core_length_bars"]) not in allowed_cores:
                        stats["reject_core_length"] += 1
                        row["rejection_reason"] = "core_length"
                    elif int(mapped["confirmation_bars"]) not in allowed_confirmations:
                        stats["reject_confirmation"] += 1
                        row["rejection_reason"] = "confirmation"
                    elif utc(times.iloc[int(mapped["core_end_i"])]).floor("D") != day:
                        stats["reject_core_outside_ranked_day"] += 1
                        row["rejection_reason"] = "core_outside_ranked_day"
                    else:
                        stats["accepted_structural"] += 1
                        stats["accepted_structural_boxes"] += 1
                        row["accepted_structural"] = True
                        row["structural_status"] = "accepted_structural"
                        row["rejection_reason"] = ""
                rows.append(row)
    return rows


def _bucket(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Key a prediction by its source task and class for parity matching."""

    return (
        str(row["day"]),
        int(row["window_start_i"]),
        int(row["window_end_i"]),
        int(row["window_len"]),
        int(row["class_id"]),
    )


def _prediction_values(row: Mapping[str, Any]) -> np.ndarray:
    """Return the four coordinates and confidence used for replay parity."""

    return np.asarray(
        [
            float(row["prediction_cx_norm"]),
            float(row["prediction_cy_norm"]),
            float(row["prediction_w_norm"]),
            float(row["prediction_h_norm"]),
            float(row["confidence"]),
        ],
        dtype=float,
    )


def attach_existing_lineage(
    raw_rows: list[dict[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Match accepted replay rows to the published candidate/event ledgers."""

    accepted = [row for row in raw_rows if bool(row["accepted_structural"])]
    if len(accepted) != len(candidates):
        raise ExportError(
            f"accepted replay count {len(accepted)} differs from published {len(candidates)}"
        )
    by_bucket: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_bucket[_bucket(candidate)].append(dict(candidate))
    matched_candidates = 0
    for row in accepted:
        pool = by_bucket.get(_bucket(row), [])
        if not pool:
            raise ExportError(f"published candidate bucket missing: {_bucket(row)}")
        distances = [
            float(np.max(np.abs(_prediction_values(row) - _prediction_values(item))))
            for item in pool
        ]
        best_index = int(np.argmin(distances))
        if distances[best_index] > 1e-5:
            raise ExportError(
                "replayed accepted prediction differs from published candidate: "
                f"id={row['source_prediction_id']} max_abs={distances[best_index]:.3g}"
            )
        candidate = pool.pop(best_index)
        row["candidate_id"] = str(candidate["candidate_id"])
        row["episode_id"] = str(candidate.get("episode_id", ""))
        matched_candidates += 1
    if any(pool for pool in by_bucket.values()):
        remaining = sum(len(pool) for pool in by_bucket.values())
        raise ExportError(f"published candidate rows left unmatched: {remaining}")

    event_by_bucket: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for event_number, event in enumerate(events, 1):
        event_by_bucket[_bucket(event)].append(
            {**dict(event), "five_bar_event_id": f"five_bar_event_{event_number:04d}"}
        )
    matched_events = 0
    for row in accepted:
        pool = event_by_bucket.get(_bucket(row), [])
        if not pool:
            continue
        distances = [
            float(np.max(np.abs(_prediction_values(row) - _prediction_values(item))))
            for item in pool
        ]
        best_index = int(np.argmin(distances))
        if distances[best_index] <= 1e-5:
            event = pool.pop(best_index)
            row["five_bar_event_id"] = str(event["five_bar_event_id"])
            matched_events += 1
    if any(pool for pool in event_by_bucket.values()):
        remaining = sum(len(pool) for pool in event_by_bucket.values())
        if remaining != len(events):
            raise ExportError(f"event lineage matching is ambiguous: {remaining} remain")

    episode_map = {str(row["episode_id"]): dict(row) for row in episodes}
    for row in accepted:
        episode_id = str(row.get("episode_id", ""))
        episode = episode_map.get(episode_id)
        if episode is not None:
            row["episode_candidate_count"] = int(episode["episode_candidate_count"])
            row["episode_sequence"] = int(episode["episode_sequence"])
            row["episode_max_confidence"] = float(episode["episode_max_confidence"])
            row["is_episode_representative"] = bool(
                _same_prediction(row, episode)
            )
        else:
            row["is_episode_representative"] = False
    for row in raw_rows:
        if not bool(row["accepted_structural"]):
            row["is_episode_representative"] = False
    return {
        "matched_candidates": matched_candidates,
        "matched_five_bar_event_representatives": matched_events,
        "published_candidates": len(candidates),
        "published_five_bar_events": len(events),
        "published_episodes": len(episodes),
    }


def _same_prediction(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Compare a raw row to a representative row across all prediction fields."""

    if _bucket(left) != _bucket(right):
        return False
    return bool(np.allclose(_prediction_values(left), _prediction_values(right), atol=1e-5, rtol=1e-5))


def _color_for(row: Mapping[str, Any]) -> tuple[int, int, int]:
    """Return the source long/short color, with a safe fallback for unknown classes."""

    return common.CLASS_COLORS.get(int(row["class_id"]), (30, 30, 30))


def render_raw_detection(
    row: Mapping[str, Any],
    *,
    order: int,
    total: int,
    enriched: pd.DataFrame,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Render one raw detection in the source 1920x1400 full-context style."""

    times = pd.to_datetime(enriched["open_time"], utc=True)
    decision_i = int(row["window_end_i"])
    window_start_i = int(row["window_start_i"])
    window_len = int(row["window_len"])
    model_window = enriched.iloc[window_start_i : decision_i + 1]
    if len(model_window) != window_len:
        raise ExportError(f"model window length drifted for {row['source_prediction_id']}")
    clean, input_tf = render_chart(model_window, out_path=None)
    if pixel_sha256(clean) != str(row["input_pixel_sha256"]):
        raise ExportError(f"model input pixel identity drifted for {row['source_prediction_id']}")
    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(row)
    raw_overlay = clean.copy()
    cv2.rectangle(raw_overlay, (raw_x0, raw_y0), (raw_x1, raw_y1), _color_for(row), 4, cv2.LINE_AA)

    context_start_i, context_end_i = context_bounds(len(enriched), decision_i)
    context = enriched.iloc[context_start_i : context_end_i + 1]
    context_times = pd.to_datetime(context["open_time"], utc=True)
    if len(context) != CONTEXT_BARS or not (
        context_times.diff().dropna() == BAR_DELTA
    ).all():
        raise ExportError(f"full context is not 128 contiguous bars for {row['source_prediction_id']}")
    main, context_tf = render_chart(context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None)
    projection = project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=context_start_i,
    )
    box_start_i = max(0, min(len(enriched) - 1, int(round(float(projection["global_x0_bar"])))))
    box_end_i = max(0, min(len(enriched) - 1, int(round(float(projection["global_x1_bar"])))))
    x0 = max(0, min(MAIN_WIDTH - 1, int(projection["context_x0_px"])))
    x1 = max(0, min(MAIN_WIDTH - 1, int(projection["context_x1_px"])))
    y0 = max(0, min(MAIN_HEIGHT - 1, int(projection["context_y0_px"])))
    y1 = max(0, min(MAIN_HEIGHT - 1, int(projection["context_y1_px"])))
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    cv2.rectangle(main, (x0, y0), (x1, y1), _color_for(row), 5, cv2.LINE_AA)

    detection_local = decision_i - context_start_i
    detection_x = x_at_float(context_tf, detection_local)
    if detection_local < CONTEXT_BARS - 1:
        shaded = main.copy()
        cv2.rectangle(
            shaded,
            (detection_x + 1, 0),
            (MAIN_WIDTH - 1, MAIN_HEIGHT - 1),
            (228, 231, 235),
            -1,
        )
        main = cv2.addWeighted(shaded, 0.25, main, 0.75, 0)
    dashed_vertical(main, detection_x, 10, MAIN_HEIGHT - 18)
    put_text(main, "DETECT", (max(4, detection_x - 33), 28), scale=0.48, thickness=2)

    canvas = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    class_name = str(row["class_name"])
    direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
    if not class_name.startswith("dense_"):
        direction = class_name.upper()
    detect_utc = utc(row["window_end_time"])
    detect_cst = detect_utc.tz_convert("Asia/Shanghai")
    status = "ACCEPTED STRUCTURAL" if bool(row["accepted_structural"]) else f"REJECTED {row['rejection_reason']}"
    put_text(
        canvas,
        f"ETHUSDT.P 15m | RAW {order:04d}/{total:04d} | {direction} conf {float(row['confidence']):.3f} | {status}",
        (24, 40),
        scale=0.70,
        thickness=2,
    )
    put_text(
        canvas,
        f"raw id {row['source_prediction_id']} | detection {detect_utc:%Y-%m-%d %H:%M} UTC / {detect_cst:%m-%d %H:%M} CST | W{window_len}",
        (24, 76),
        scale=0.56,
        color=(60, 60, 60),
    )
    mapped_text = "mapped core unavailable"
    if "core_start_i" in row:
        mapped_text = (
            f"mapped core {int(row['core_length_bars'])}K "
            f"({utc(row['core_start_time']):%m-%d %H:%M}..{utc(row['core_end_time']):%H:%M} UTC) "
            f"| confirmation {int(row['confirmation_bars'])}K"
        )
    put_text(
        canvas,
        f"128-bar context | colored rectangle = this raw YOLO box | {mapped_text}",
        (24, 106),
        scale=0.50,
        color=(85, 85, 85),
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main

    for local_i in (0, 24, 48, 72, 96, 127):
        px = MAIN_X + x_at_float(context_tf, local_i)
        stamp = utc(context_times.iloc[local_i])
        put_text(
            canvas,
            f"{stamp:%m-%d %H:%M}",
            (max(0, px - 50), MAIN_Y + MAIN_HEIGHT + 25),
            scale=0.42,
            color=(80, 80, 80),
        )
    for fraction in np.linspace(0.05, 0.95, 5):
        price = context_tf.price_max - fraction * (context_tf.price_max - context_tf.price_min)
        py = MAIN_Y + int(round(context_tf.top + fraction * context_tf.plot_h))
        put_text(canvas, price_text(price), (CANVAS_WIDTH - 118, py), scale=0.42, color=(75, 75, 75))

    footer_y = 944
    put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.66, thickness=2)
    put_text(
        canvas,
        "Top: 128 consecutive ETH 15m bars. The rectangle is this raw YOLO box, without deduplication.",
        (28, footer_y + 35),
        scale=0.50,
    )
    put_text(
        canvas,
        "Dashed DETECT is the source scan's window endpoint; grey bars after it are review-only future.",
        (28, footer_y + 65),
        scale=0.50,
    )
    put_text(
        canvas,
        "Right: exact 1280x742 detector input with this one raw rectangle; later context never entered inference.",
        (28, footer_y + 95),
        scale=0.50,
    )
    put_text(canvas, "EXACT MODEL INPUT", (CANVAS_WIDTH - INSET_WIDTH - 18, footer_y), scale=0.60, thickness=2)
    inset = cv2.resize(raw_overlay, (INSET_WIDTH, INSET_HEIGHT), interpolation=cv2.INTER_AREA)
    inset_x, inset_y = CANVAS_WIDTH - INSET_WIDTH - 18, footer_y + 18
    canvas[inset_y : inset_y + INSET_HEIGHT, inset_x : inset_x + INSET_WIDTH] = inset
    cv2.rectangle(
        canvas,
        (inset_x, inset_y),
        (inset_x + INSET_WIDTH - 1, inset_y + INSET_HEIGHT - 1),
        (65, 65, 65),
        2,
    )

    metadata = {
        **projection,
        "raw_detection_sequence": order,
        "raw_detections_total": total,
        "source_prediction_id": str(row["source_prediction_id"]),
        "scan_day": str(row["scan_day"]),
        "symbol": SYMBOL,
        "class_id": int(row["class_id"]),
        "class_name": class_name,
        "direction": direction,
        "confidence": float(row["confidence"]),
        "accepted_structural": bool(row["accepted_structural"]),
        "structural_status": str(row["structural_status"]),
        "rejection_reason": str(row["rejection_reason"]),
        "candidate_id": str(row.get("candidate_id", "")),
        "five_bar_event_id": str(row.get("five_bar_event_id", "")),
        "episode_id": str(row.get("episode_id", "")),
        "episode_candidate_count": int(row.get("episode_candidate_count", 0)),
        "is_episode_representative": bool(row.get("is_episode_representative", False)),
        "window_len": window_len,
        "window_start_i": window_start_i,
        "window_end_i": decision_i,
        "window_start_time": utc(times.iloc[window_start_i]).isoformat(),
        "window_end_time": detect_utc.isoformat(),
        "box_start_i": box_start_i,
        "box_end_i": box_end_i,
        "box_start_time": utc(times.iloc[box_start_i]).isoformat(),
        "box_end_time": utc(times.iloc[box_end_i]).isoformat(),
        "core_start_i": int(row["core_start_i"]) if "core_start_i" in row else None,
        "core_end_i": int(row["core_end_i"]) if "core_end_i" in row else None,
        "core_start_time": str(row.get("core_start_time", "")),
        "core_end_time": str(row.get("core_end_time", "")),
        "core_length_bars": int(row["core_length_bars"]) if "core_length_bars" in row else None,
        "confirmation_bars": int(row["confirmation_bars"]) if "confirmation_bars" in row else None,
        "context_start_i": context_start_i,
        "context_end_i": context_end_i,
        "context_start_time": utc(context_times.iloc[0]).isoformat(),
        "context_end_time": utc(context_times.iloc[-1]).isoformat(),
        "context_bars": CONTEXT_BARS,
        "post_detection_review_bars": context_end_i - decision_i,
        "prediction_cx_norm": float(row["prediction_cx_norm"]),
        "prediction_cy_norm": float(row["prediction_cy_norm"]),
        "prediction_w_norm": float(row["prediction_w_norm"]),
        "prediction_h_norm": float(row["prediction_h_norm"]),
        "raw_x0_px": raw_x0,
        "raw_y0_px": raw_y0,
        "raw_x1_px": raw_x1,
        "raw_y1_px": raw_y1,
        "model_input_pixel_sha256": str(row["input_pixel_sha256"]),
        "boxes_per_document": 1,
        "canvas_width": CANVAS_WIDTH,
        "canvas_height": CANVAS_HEIGHT,
    }
    return canvas, metadata


def match_count_summary(raw_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize raw, structural, rejection and lineage levels."""

    raw = len(raw_rows)
    accepted = sum(bool(row["accepted_structural"]) for row in raw_rows)
    rejection_counts = Counter(
        str(row["rejection_reason"]) for row in raw_rows if not bool(row["accepted_structural"])
    )
    event_representatives = sum(bool(row.get("five_bar_event_id")) for row in raw_rows)
    episodes = {str(row["episode_id"]) for row in raw_rows if str(row.get("episode_id", ""))}
    return {
        "raw_detections": raw,
        "structurally_accepted_raw_detections": accepted,
        "structurally_rejected_raw_detections": raw - accepted,
        "structural_rejection_breakdown": dict(sorted(rejection_counts.items())),
        "five_bar_event_representatives": event_representatives,
        "episode_ids_seen_on_accepted_rows": len(episodes),
    }


def export(
    *,
    out: Path,
    device: str | None,
    batch_size: int,
) -> dict[str, Any]:
    """Run the frozen replay, render all raw boxes, and atomically publish output."""

    if out.exists():
        raise ExportError(f"refusing to overwrite existing output: {out}")
    building = out.with_name(f"{out.name}.building")
    if building.exists():
        raise ExportError(f"unfinished building output already exists: {building}")
    if batch_size != EXPECTED_BATCH_SIZE:
        raise ExportError(f"source replay requires batch-size={EXPECTED_BATCH_SIZE}")

    prereg, frame, candidates, events, episodes, source = load_source_data()
    detector = prereg["detector"]
    source_device = str(source["scan_receipt"].get("device", "mps"))
    run_device = device or source_device
    immutable = source["scan_receipt"].get("immutable_inputs", {})
    weights_path = ROOT / str(detector["weights"])
    renderer_path = ROOT / str(detector["renderer"])
    if sha256_file(weights_path) != str(immutable["weights"]["sha256"]):
        raise ExportError("weight SHA drifted from source scan receipt")
    if sha256_file(renderer_path) != str(immutable["renderer"]["sha256"]):
        raise ExportError("renderer SHA drifted from source scan receipt")

    from ultralytics import YOLO

    model = YOLO(str(weights_path))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != common.CLASS_NAMES:
        raise ExportError(f"weight class names drifted: {names}")

    started = time.perf_counter()
    building.mkdir(parents=True)
    image_dir = building / "images"
    image_dir.mkdir()
    all_raw: list[dict[str, Any]] = []
    scan_stats: Counter[str] = Counter()
    enriched = add_mas(frame)
    day_rows = pd.read_csv(SOURCE_OUT / "eth_daily_returns.csv").to_dict("records")
    for day_number, day_row in enumerate(day_rows, 1):
        tasks, build_stats = build_tasks(enriched, day_row=day_row, detector=detector)
        scan_stats.update(build_stats)
        day_stats: Counter[str] = Counter(build_stats)
        day_raw = predict_all_raw(
            model,
            tasks,
            batch_size=batch_size,
            detector=detector,
            device=run_device,
            day=utc(day_row["day"]),
            frame=enriched,
            stats=day_stats,
        )
        scan_stats.update({key: value for key, value in day_stats.items() if key not in build_stats})
        all_raw.extend(day_raw)
        print(
            f"raw-export [{day_number:02d}/30] {utc(day_row['day']):%m-%d} "
            f"raw={sum(int(row['day'] == day_row['day']) for row in day_raw):>4} "
            f"accepted={sum(bool(row['accepted_structural']) for row in day_raw):>4}",
            flush=True,
        )

    if len(all_raw) != EXPECTED_RAW_BOXES:
        raise ExportError(f"replayed raw count differs from source: {len(all_raw)}")
    lineage = attach_existing_lineage(all_raw, candidates, events, episodes)
    summary = match_count_summary(all_raw)
    if summary["structurally_accepted_raw_detections"] != EXPECTED_ACCEPTED:
        raise ExportError("replayed structural accepted count differs from source")
    if summary["five_bar_event_representatives"] != EXPECTED_FIVE_BAR_EVENTS:
        raise ExportError("replayed five-bar event count differs from source")

    manifest_rows: list[dict[str, Any]] = []
    render_failures = 0
    for order, row in enumerate(all_raw, 1):
        try:
            image, metadata = render_raw_detection(
                row,
                order=order,
                total=len(all_raw),
                enriched=enriched,
            )
        except Exception:
            render_failures += 1
            raise
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        timestamp = utc(row["window_end_time"])
        path = image_dir / (
            f"{order:04d}_ETHUSDT_P_{direction}_{timestamp:%Y%m%dT%H%MZ}_"
            f"{str(row['source_prediction_id']).split('raw_', 1)[-1]}.png"
        )
        write_png(path, image)
        metadata.update(
            {
                # The image is written below the atomic ``.building`` tree
                # and that tree is renamed after all manifests are complete.
                # Store the final absolute path now so the published manifest
                # never leaks a temporary path.
                "image_path": str((out / "images" / path.name).resolve()),
                "image_sha256": sha256_file(path),
                "image_size_bytes": path.stat().st_size,
            }
        )
        manifest_rows.append(metadata)
        if order == 1 or order % 100 == 0 or order == len(all_raw):
            print(
                f"render [{order:04d}/{len(all_raw):04d}] {direction:<5} "
                f"conf={float(row['confidence']):.3f} {timestamp:%m-%d %H:%M}Z",
                flush=True,
            )

    if len(manifest_rows) != len(all_raw) or any(
        int(row["boxes_per_document"]) != 1 for row in manifest_rows
    ):
        raise ExportError("manifest/image one-box invariant failed")
    csv_path = building / "raw_detections_manifest.csv"
    jsonl_path = building / "raw_detections_manifest.jsonl"
    write_csv(csv_path, manifest_rows)
    write_jsonl(jsonl_path, manifest_rows)

    summary.update(
        {
            "sliding_window_duplicate_boxes_removed_by_five_bar_dedup": int(
                source["scan_receipt"]["scan_totals"]["dedup_removed"]
            ),
            "five_bar_events": EXPECTED_FIVE_BAR_EVENTS,
            "overlap_episodes": EXPECTED_EPISODES,
            "windows_scored": int(scan_stats["windows_scored"]),
            "windows_with_any_box": int(scan_stats["windows_with_any_box"]),
            "windows_skipped_insufficient_rows": int(scan_stats["skip_insufficient_rows"]),
            "windows_skipped_ma_warmup": int(scan_stats["skip_ma_warmup"]),
            "render_failures": render_failures,
            "render_skips": 0,
            "images_written": len(manifest_rows),
            "images_with_exactly_one_box": sum(
                int(row["boxes_per_document"]) == 1 for row in manifest_rows
            ),
        }
    )
    receipt = {
        "protocol": "ma6_owner_yolo_neg30000_eth30d_raw_detection_export_v1_20260828",
        "export_type": "raw_detection_visualization_only",
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "scan_receipt": repo_relative(SOURCE_RESULTS / "scan_receipt.json"),
            "scan_receipt_sha256": source["scan_receipt_sha256"],
            "fetch_receipt": repo_relative(SOURCE_RESULTS / "fetch_receipt.json"),
            "fetch_receipt_sha256": source["fetch_receipt_sha256"],
            "snapshot": repo_relative(source["snapshot_path"]),
            "snapshot_sha256": source["snapshot_sha256"],
            "accepted_candidates": repo_relative(source["candidates_path"]),
            "five_bar_events": repo_relative(source["events_path"]),
            "episodes": repo_relative(source["episodes_path"]),
        },
        "replay": {
            "recomputed_missing_raw_coordinates": True,
            "network_reads": 0,
            "device": run_device,
            "batch_size": batch_size,
            "weights": repo_relative(weights_path),
            "weights_sha256": sha256_file(weights_path),
            "renderer": repo_relative(renderer_path),
            "renderer_sha256": sha256_file(renderer_path),
            "confidence": float(detector["confidence"]),
            "nms_iou": float(detector["nms_iou"]),
            "imgsz": int(detector["imgsz"]),
            "window_lengths": list(map(int, detector["window_lengths"])),
            "endpoint_extension_bars": int(detector["scan_endpoint_extension_after_day_bars"]),
            "structural_core_lengths": list(map(int, detector["mapped_core_length_bars_allowed"])),
            "structural_confirmation_bars": list(map(int, detector["mapped_confirmation_bars_allowed"])),
            "same_symbol_event_gap_bars": int(detector["same_symbol_event_gap_bars"]),
        },
        "count_summary": summary,
        "lineage_match": lineage,
        "output": {
            "directory": str(out.resolve()),
            "images_directory": str((out / "images").resolve()),
            "manifest_csv": str((out / "raw_detections_manifest.csv").resolve()),
            "manifest_jsonl": str((out / "raw_detections_manifest.jsonl").resolve()),
            "manifest_csv_sha256": sha256_file(csv_path),
            "manifest_jsonl_sha256": sha256_file(jsonl_path),
        },
        "safety": {
            "training_or_tuning": False,
            "threshold_or_weight_changed": False,
            "active_or_frozen_changed": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "canonical_data_written": False,
            "telegram_sent": False,
            "production_eligible": False,
            "training_eligible": False,
        },
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    (building / "export_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    building.rename(out)
    print(
        f"raw-export complete: raw={summary['raw_detections']} accepted="
        f"{summary['structurally_accepted_raw_detections']} events={EXPECTED_FIVE_BAR_EVENTS} "
        f"episodes={EXPECTED_EPISODES} images={len(manifest_rows)} "
        f"wall={receipt['wall_seconds'] / 60:.1f}m",
        flush=True,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=EXPECTED_BATCH_SIZE)
    args = parser.parse_args()
    receipt = export(
        out=args.out.resolve(),
        device=args.device,
        batch_size=int(args.batch_size),
    )
    print(json.dumps(receipt["count_summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
