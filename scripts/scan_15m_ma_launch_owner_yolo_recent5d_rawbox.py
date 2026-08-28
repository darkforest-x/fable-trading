#!/usr/bin/env python3
"""Repair the five-day Owner YOLO review surface without tuning the detector.

The v1 daily boards mixed three different geometries: 1280x742 W18--25 model
inputs, normalized YOLO rectangles, and a compressed 96-bar day chart.  Worse,
the day renderer discarded predicted ``cy/h`` and rebuilt a candle-only box.

This bounded holdout reuse keeps the delivered weight, thresholds, window
support and structural filters byte-for-byte frozen.  It records every accepted
four-coordinate prediction, clusters overlapping decision intervals into one
continuous episode, and renders the earliest episode per symbol-day on the
exact 1280x742 input that the model saw.  Every review overlay therefore has at
most one raw YOLO rectangle.  All candidates and episodes remain in CSV; the
review selection never pretends that suppressed later episodes did not exist.

No network read, training, threshold search, promotion, deployment, forward
mutation or order action is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from scripts.scan_15m_ma_launch_owner_yolo_recent5d import verify_training_geometry
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas
from yoyo.layers.l1_detection.render import IMG_HEIGHT, IMG_WIDTH, render_chart


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-recent5d-rawbox-v2"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_SOURCE_OUT = ROOT / "analysis" / "output" / "ma_launch_owner_yolo_recent5d_v1"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_owner_yolo_recent5d_rawbox_v2"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
EXPECTED_DAYS = tuple(pd.Timestamp(f"2026-08-{day:02d}T00:00:00Z") for day in range(23, 28))
EXPECTED_WINDOWS = tuple(range(18, 26))
EXPECTED_CORES = (4, 5)
EXPECTED_CONFIRMATIONS = (4, 5, 6)


class RawBoxRepairError(RuntimeError):
    """Fail-closed lineage, geometry, inference or rendering error."""


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def final_relative(path: Path, building: Path, final: Path) -> str:
    return repo_relative(final / path.relative_to(building))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4])
    if not ok:
        raise OSError(f"OpenCV failed to write {path}")


def _validate_sha(value: str, field: str) -> str:
    text = str(value)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise RawBoxRepairError(f"invalid SHA-256 in {field}")
    return text


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    """Enforce the Owner-authorized display repair and frozen detector contract."""

    payload = read_json(path)
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise RawBoxRepairError("unexpected experiment_id")
    authorization = payload["owner_authorization"]
    if int(authorization["holdout_consumption_number_for_this_configuration"]) != 2:
        raise RawBoxRepairError("this repair must be recorded as holdout use #2")
    if authorization.get("rerender_and_geometry_audit_authorized") is not True:
        raise RawBoxRepairError("raw-box repair is not authorized")
    if authorization.get("threshold_or_weight_change_authorized") is not False:
        raise RawBoxRepairError("threshold/weight changes must remain unauthorized")

    days = tuple(utc(value) for value in payload["calendar"]["complete_days"])
    if days != EXPECTED_DAYS:
        raise RawBoxRepairError("calendar drifted from the frozen five days")
    source = payload["source_snapshot"]
    for field in ("fetch_receipt_sha256", "daily_rankings_sha256", "v1_signals_sha256"):
        _validate_sha(str(source[field]), f"source_snapshot.{field}")

    detector = payload["detector"]
    if tuple(map(int, detector["window_lengths"])) != EXPECTED_WINDOWS:
        raise RawBoxRepairError("window support drifted")
    if tuple(map(int, detector["mapped_core_length_bars_allowed"])) != EXPECTED_CORES:
        raise RawBoxRepairError("core support drifted")
    if tuple(map(int, detector["mapped_confirmation_bars_allowed"])) != EXPECTED_CONFIRMATIONS:
        raise RawBoxRepairError("confirmation support drifted")
    if float(detector["confidence"]) != 0.25 or float(detector["nms_iou"]) != 0.7:
        raise RawBoxRepairError("threshold or NMS drifted")
    if int(detector["imgsz"]) != 960 or int(detector["same_symbol_event_gap_bars"]) != 5:
        raise RawBoxRepairError("inference geometry drifted")
    if int(detector["input_width"]) != IMG_WIDTH or int(detector["input_height"]) != IMG_HEIGHT:
        raise RawBoxRepairError("model-input dimensions drifted")

    repair = payload["repair_contract"]
    if repair["preserved_prediction_coordinates"] != ["cx", "cy", "w", "h"]:
        raise RawBoxRepairError("four-coordinate preservation drifted")
    if repair["episode_interval"] != "mapped_core_start_i..window_end_i_inclusive":
        raise RawBoxRepairError("episode interval definition drifted")
    if repair["episode_merge"] != "merge_overlapping_intervals_same_symbol_day_class_agnostic":
        raise RawBoxRepairError("episode merge definition drifted")
    if repair["episode_representative"] != "earliest_window_end_then_highest_confidence":
        raise RawBoxRepairError("episode representative definition drifted")
    if repair["daily_review_selection"] != "earliest_episode_per_symbol_day":
        raise RawBoxRepairError("daily review selection drifted")
    if int(repair["maximum_boxes_per_review_panel"]) != 1:
        raise RawBoxRepairError("review panels must remain one-box maximum")
    if any(value is not False for value in payload["safety"].values()):
        raise RawBoxRepairError("one or more safety switches drifted")
    return payload


def verify_committed_sources(prereg_path: Path) -> str:
    paths = [
        Path(__file__).resolve().relative_to(ROOT),
        (ROOT / "scripts" / "scan_15m_ma_launch_t3_daily_movers.py").relative_to(ROOT),
        prereg_path.resolve().relative_to(ROOT),
    ]
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise RawBoxRepairError("official repair must run on main")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise RawBoxRepairError(f"repair sources must be committed:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if len(commit) != 40:
        raise RawBoxRepairError("could not resolve source commit")
    return commit


def verify_immutable_inputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    detector = prereg["detector"]
    verified: dict[str, Any] = {}
    for key, hash_key in (
        ("weights", "weights_sha256"),
        ("training_manifest", "training_manifest_sha256"),
        ("renderer", "renderer_sha256"),
    ):
        path = ROOT / str(detector[key])
        expected = str(detector[hash_key])
        if not path.is_file() or sha256_file(path) != expected:
            raise RawBoxRepairError(f"immutable input drifted: {key}")
        verified[key] = {"path": repo_relative(path), "sha256": expected}
    verified["training_geometry"] = verify_training_geometry(
        ROOT / str(detector["training_manifest"])
    )
    return verified


def verify_source_snapshot(
    prereg: Mapping[str, Any], source_out: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    source = prereg["source_snapshot"]
    fetch_receipt_path = ROOT / str(source["fetch_receipt"])
    if sha256_file(fetch_receipt_path) != str(source["fetch_receipt_sha256"]):
        raise RawBoxRepairError("v1 fetch receipt drifted")
    receipt = read_json(fetch_receipt_path)
    rankings_path = source_out / "daily_rankings.csv"
    if sha256_file(rankings_path) != str(source["daily_rankings_sha256"]):
        raise RawBoxRepairError("daily rankings drifted")
    if receipt.get("daily_rankings_sha256") != str(source["daily_rankings_sha256"]):
        raise RawBoxRepairError("fetch receipt ranking identity drifted")
    snapshots = {str(row["symbol"]): row for row in receipt["snapshot_files"]}
    if len(snapshots) != 75:
        raise RawBoxRepairError("source snapshot count drifted")
    for symbol, row in snapshots.items():
        path = source_out / "kline_snapshot" / f"{symbol}.csv"
        if not path.is_file() or sha256_file(path) != str(row["sha256"]):
            raise RawBoxRepairError(f"snapshot drifted: {symbol}")
    v1_signals = source_out / "signals.csv"
    if sha256_file(v1_signals) != str(source["v1_signals_sha256"]):
        raise RawBoxRepairError("v1 signal baseline drifted")
    rankings = pd.read_csv(rankings_path)
    rankings["day"] = pd.to_datetime(rankings["day"], utc=True)
    if len(rankings) != 100:
        raise RawBoxRepairError("daily ranking row count drifted")
    return rankings, receipt


def scan_symbol_day_candidates(
    frame: pd.DataFrame,
    *,
    day_row: Mapping[str, Any],
    model: Any,
    detector: Mapping[str, Any],
    device: str,
    batch_size: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Return all structurally accepted raw boxes plus the unchanged v1 events."""

    enriched = add_mas(frame)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    day = utc(day_row["day"])
    endpoint_end = day + pd.Timedelta(days=1) + int(
        detector["scan_endpoint_extension_after_day_bars"]
    ) * common.BAR_DELTA
    endpoint_indices = np.flatnonzero((times >= day) & (times < endpoint_end))
    tasks: list[tuple[np.ndarray, Any, dict[str, Any]]] = []
    candidates: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()

    def flush() -> None:
        if not tasks:
            return
        candidates.extend(
            common._predict_batches(  # noqa: SLF001 - same audited scan adapter
                model,
                tasks,
                batch_size=batch_size,
                conf=float(detector["confidence"]),
                iou=float(detector["nms_iou"]),
                imgsz=int(detector["imgsz"]),
                device=device,
                day=day,
                frame=enriched,
                allowed_cores=set(map(int, detector["mapped_core_length_bars_allowed"])),
                allowed_confirmations=set(
                    map(int, detector["mapped_confirmation_bars_allowed"])
                ),
                stats=stats,
            )
        )
        tasks.clear()

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
            if len(tasks) >= batch_size:
                flush()
    flush()
    events = common.deduplicate_hits(
        candidates, gap_bars=int(detector["same_symbol_event_gap_bars"])
    )
    stats["accepted_before_dedup"] = len(candidates)
    stats["deduplicated_events"] = len(events)
    stats["dedup_removed"] = len(candidates) - len(events)
    return candidates, events, dict(stats)


def cluster_candidates_into_episodes(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge overlapping ``core_start..decision_end`` intervals per symbol-day.

    The representative is the first model-available hit, not the highest score
    seen later in the completed episode.  Confidence only breaks equal decision
    times.  This is a review aggregation rule, never a threshold or label.
    """

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[(str(row["day"]), str(row["symbol"]))].append(dict(row))

    annotated: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for (day, symbol), rows in sorted(grouped.items()):
        ordered = sorted(
            rows,
            key=lambda row: (
                int(row["core_start_i"]),
                int(row["window_end_i"]),
                -float(row["confidence"]),
                int(row["window_len"]),
            ),
        )
        clusters: list[list[dict[str, Any]]] = []
        active: list[dict[str, Any]] = []
        active_end: int | None = None
        for row in ordered:
            start_i = int(row["core_start_i"])
            decision_end_i = int(row["window_end_i"])
            if active and active_end is not None and start_i > active_end:
                clusters.append(active)
                active = []
                active_end = None
            active.append(row)
            active_end = decision_end_i if active_end is None else max(active_end, decision_end_i)
        if active:
            clusters.append(active)

        for sequence, cluster in enumerate(clusters, 1):
            representative = min(
                cluster,
                key=lambda row: (
                    int(row["window_end_i"]),
                    -float(row["confidence"]),
                    int(row["core_end_i"]),
                    int(row["window_len"]),
                ),
            )
            episode_id = (
                f"{day[:10].replace('-', '')}_{symbol.replace('_USDT_SWAP', '')}_{sequence:02d}"
            )
            class_counts = Counter(str(row["class_name"]) for row in cluster)
            for row in cluster:
                annotated.append({**row, "episode_id": episode_id})
            episodes.append(
                {
                    **representative,
                    "episode_id": episode_id,
                    "episode_sequence": sequence,
                    "episode_candidate_count": len(cluster),
                    "episode_interval_start_i": min(int(row["core_start_i"]) for row in cluster),
                    "episode_interval_end_i": max(int(row["window_end_i"]) for row in cluster),
                    "episode_max_confidence": max(float(row["confidence"]) for row in cluster),
                    "episode_long_candidates": int(class_counts["dense_long"]),
                    "episode_short_candidates": int(class_counts["dense_short"]),
                    "representative_rule": "earliest_window_end_then_highest_confidence",
                }
            )

    annotated.sort(
        key=lambda row: (
            str(row["day"]),
            int(row["rank"]),
            str(row["symbol"]),
            int(row["window_end_i"]),
            int(row["window_len"]),
            -float(row["confidence"]),
        )
    )
    for number, row in enumerate(annotated, 1):
        row["candidate_id"] = f"candidate_{number:05d}"
    episodes.sort(
        key=lambda row: (
            str(row["day"]),
            int(row["rank"]),
            str(row["symbol"]),
            int(row["window_end_i"]),
        )
    )
    return annotated, episodes


def normalized_box_corners(
    row: Mapping[str, Any], *, width: int = IMG_WIDTH, height: int = IMG_HEIGHT
) -> tuple[int, int, int, int]:
    cx = float(row["prediction_cx_norm"])
    cy = float(row["prediction_cy_norm"])
    box_w = float(row["prediction_w_norm"])
    box_h = float(row["prediction_h_norm"])
    values = (cx, cy, box_w, box_h)
    if not all(np.isfinite(values)) or not all(0.0 < value <= 1.0 for value in values):
        raise RawBoxRepairError(f"invalid raw prediction: {values}")
    x0 = int(round((cx - box_w / 2.0) * width))
    x1 = int(round((cx + box_w / 2.0) * width))
    y0 = int(round((cy - box_h / 2.0) * height))
    y1 = int(round((cy + box_h / 2.0) * height))
    x0, x1 = sorted((max(0, min(width - 1, x0)), max(0, min(width - 1, x1))))
    y0, y1 = sorted((max(0, min(height - 1, y0)), max(0, min(height - 1, y1))))
    if x1 <= x0 or y1 <= y0:
        raise RawBoxRepairError("raw prediction collapses after clipping")
    return x0, y0, x1, y1


def draw_raw_prediction(image: np.ndarray, row: Mapping[str, Any]) -> np.ndarray:
    """Draw exactly one preserved YOLO rectangle on an input copy."""

    if image.shape != (IMG_HEIGHT, IMG_WIDTH, 3):
        raise RawBoxRepairError(f"unexpected input shape: {image.shape}")
    overlay = image.copy()
    x0, y0, x1, y1 = normalized_box_corners(row)
    cv2.rectangle(
        overlay,
        (x0, y0),
        (x1, y1),
        common.CLASS_COLORS[int(row["class_id"])],
        4,
        cv2.LINE_AA,
    )
    return overlay


def render_exact_input(enriched: pd.DataFrame, row: Mapping[str, Any]) -> np.ndarray:
    start_i, end_i = int(row["window_start_i"]), int(row["window_end_i"])
    window = enriched.iloc[start_i : end_i + 1]
    if len(window) != int(row["window_len"]):
        raise RawBoxRepairError("review input window length drifted")
    image, _ = render_chart(window, out_path=None)
    expected = row.get("input_pixel_sha256")
    if expected is not None and str(expected) and pixel_sha256(image) != str(expected):
        raise RawBoxRepairError("review re-render differs from actual inference pixels")
    return image


def _put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    color: tuple[int, int, int] = (25, 25, 25),
    scale: float = 0.55,
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


def review_card(
    overlay: np.ndarray,
    *,
    day_row: Mapping[str, Any],
    review_row: Mapping[str, Any],
) -> np.ndarray:
    width, image_height, header_height = 760, 441, 74
    card = np.full((header_height + image_height, width, 3), 247, dtype=np.uint8)
    resized = cv2.resize(overlay, (width, image_height), interpolation=cv2.INTER_AREA)
    card[header_height:] = resized
    symbol = str(day_row["symbol"]).replace("_USDT_SWAP", "")
    _put_text(
        card,
        f"#{int(day_row['rank']):02d} {symbol}  {float(day_row['daily_return']) * 100:+.2f}%",
        (10, 24),
        scale=0.65,
        thickness=2,
    )
    if bool(review_row["has_detection"]):
        direction = "LONG" if int(review_row["class_id"]) == 0 else "SHORT"
        stamp = utc(review_row["window_end_time"])
        detail = (
            f"FIRST episode | {direction} {float(review_row['confidence']):.2f} | "
            f"decision {stamp:%H:%M} UTC | W{int(review_row['window_len'])} | "
            f"raw xywh"
        )
        color = common.CLASS_COLORS[int(review_row["class_id"])]
    else:
        detail = "NO accepted episode | final W25 model input reference | zero boxes"
        color = (105, 105, 105)
    _put_text(card, detail, (10, 54), color=color, scale=0.5, thickness=1)
    return card


def daily_sheet(
    cards: Sequence[np.ndarray],
    *,
    day: pd.Timestamp,
    accepted_candidates: int,
    episodes: int,
    review_boxes: int,
) -> np.ndarray:
    if len(cards) != 20:
        raise RawBoxRepairError("daily review sheet must contain Top20")
    columns = 2
    rows = math.ceil(len(cards) / columns)
    banner_h = 112
    cell_h, cell_w = cards[0].shape[:2]
    canvas = np.full((banner_h + rows * cell_h, columns * cell_w, 3), 243, dtype=np.uint8)
    _put_text(
        canvas,
        f"{day:%Y-%m-%d} UTC | corrected raw-box review | Top20 | review boxes {review_boxes}/20",
        (16, 31),
        scale=0.79,
        thickness=2,
    )
    _put_text(
        canvas,
        f"accepted candidates {accepted_candidates} -> overlap episodes {episodes}; first episode only in each panel",
        (16, 62),
        color=(35, 70, 150),
        scale=0.56,
        thickness=2,
    )
    _put_text(
        canvas,
        "ONE PANEL = ONE ACTUAL 1280x742 W18-25 INPUT; green LONG / red SHORT; raw YOLO cx/cy/w/h",
        (16, 91),
        color=(35, 70, 150),
        scale=0.52,
        thickness=1,
    )
    for index, card in enumerate(cards):
        row, column = divmod(index, columns)
        y, x = banner_h + row * cell_h, column * cell_w
        canvas[y : y + cell_h, x : x + cell_w] = card
    return canvas


def render_overview(
    rankings: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
) -> np.ndarray:
    canvas = np.full((2100, 1800, 3), 248, dtype=np.uint8)
    _put_text(
        canvas,
        "15m Owner YOLO | corrected raw-box review | 2026-08-23..27 UTC",
        (24, 42),
        scale=0.92,
        thickness=2,
    )
    _put_text(
        canvas,
        "Frozen detector. Raw cx/cy/w/h preserved. One earliest episode max per review panel; full ledger retained.",
        (24, 76),
        color=(35, 70, 150),
        scale=0.59,
        thickness=2,
    )
    candidate_count = Counter((str(row["day"]), str(row["symbol"])) for row in candidates)
    episode_count = Counter((str(row["day"]), str(row["symbol"])) for row in episodes)
    review_by_key = {
        (str(row["day"]), str(row["symbol"])): row for row in reviews
    }
    columns, card_w = 3, 570
    for day_index, day in enumerate(EXPECTED_DAYS):
        grid_row, column = divmod(day_index, columns)
        x0, y0 = 20 + column * 590, 105 + grid_row * 990
        board = sorted(
            (row for row in rankings if utc(row["day"]) == day),
            key=lambda row: int(row["rank"]),
        )
        day_key = day.isoformat()
        c_total = sum(candidate_count[(day_key, str(row["symbol"]))] for row in board)
        e_total = sum(episode_count[(day_key, str(row["symbol"]))] for row in board)
        cv2.rectangle(canvas, (x0, y0), (x0 + card_w, y0 + 970), (225, 229, 234), 2)
        _put_text(
            canvas,
            f"{day:%Y-%m-%d} | candidates {c_total} | episodes {e_total}",
            (x0 + 12, y0 + 35),
            scale=0.64,
            thickness=2,
        )
        _put_text(canvas, "# SYMBOL       RETURN  CAND/EP  REVIEW", (x0 + 12, y0 + 69), scale=0.48)
        for line, row in enumerate(board):
            key = (day_key, str(row["symbol"]))
            review = review_by_key[key]
            symbol = str(row["symbol"]).replace("_USDT_SWAP", "")[:12]
            if bool(review["has_detection"]):
                mark = "L" if int(review["class_id"]) == 0 else "S"
                mark += f" {float(review['confidence']):.2f}"
            else:
                mark = "NONE"
            text = (
                f"{int(row['rank']):02d} {symbol:<12} {float(row['daily_return']) * 100:+6.1f}%  "
                f"{candidate_count[key]:>3}/{episode_count[key]:<2}  {mark}"
            )
            color = (20, 125, 35) if float(row["daily_return"]) >= 0 else (45, 45, 190)
            _put_text(canvas, text, (x0 + 12, y0 + 105 + line * 41), color=color, scale=0.5)
    return canvas


def legacy_parity(
    events: Sequence[Mapping[str, Any]], baseline_path: Path
) -> dict[str, Any]:
    old = pd.read_csv(baseline_path)
    new = pd.DataFrame(events)
    identity = [
        "day",
        "rank",
        "symbol",
        "class_id",
        "core_start_i",
        "core_end_i",
        "window_len",
        "window_start_i",
        "window_end_i",
    ]
    old_keys = old.loc[:, identity].astype(str).agg("|".join, axis=1).tolist()
    new_keys = new.loc[:, identity].astype(str).agg("|".join, axis=1).tolist()
    if old_keys != new_keys:
        raise RawBoxRepairError("frozen v1 event identities changed during display repair")
    confidence_delta = np.abs(
        old["confidence"].to_numpy(dtype=float) - new["confidence"].to_numpy(dtype=float)
    )
    max_delta = float(confidence_delta.max(initial=0.0))
    if max_delta > 1e-5:
        raise RawBoxRepairError(f"v1 confidence parity drifted: {max_delta}")
    return {
        "baseline_events": len(old),
        "reproduced_events": len(new),
        "identity_matches": len(old),
        "maximum_confidence_abs_delta": max_delta,
        "passed": True,
    }


def _csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = sorted({key for row in rows for key in row})
    common.write_csv(path, rows, columns)


def scan_and_render(
    prereg: Mapping[str, Any],
    *,
    source_out: Path,
    out: Path,
    results: Path,
    device: str,
    batch_size: int,
    source_commit: str,
) -> dict[str, Any]:
    """Run the frozen detector and build corrected one-box review artifacts."""

    if out.exists() or results.exists():
        raise FileExistsError(f"refusing to overwrite repaired outputs: {out} / {results}")
    building_out = out.with_name(f"{out.name}.building")
    building_results = results.with_name(f"{results.name}.building")
    if building_out.exists() or building_results.exists():
        raise FileExistsError("unfinished raw-box building directory already exists")
    building_out.mkdir(parents=True)
    building_results.mkdir(parents=True)

    rankings_frame, fetch_receipt = verify_source_snapshot(prereg, source_out)
    immutable_inputs = verify_immutable_inputs(prereg)
    ranked = rankings_frame.to_dict("records")
    detector = prereg["detector"]

    from ultralytics import YOLO

    model = YOLO(str(ROOT / str(detector["weights"])))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != common.CLASS_NAMES:
        raise RawBoxRepairError(f"weight classes drifted: {names}")

    started = time.perf_counter()
    frames: dict[str, pd.DataFrame] = {}
    all_candidates: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    scan_rows: list[dict[str, Any]] = []
    for number, day_row in enumerate(ranked, 1):
        symbol = str(day_row["symbol"])
        if symbol not in frames:
            frame = pd.read_csv(source_out / "kline_snapshot" / f"{symbol}.csv")
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            for column in ("open", "high", "low", "close", "volume"):
                frame[column] = pd.to_numeric(frame[column], errors="raise")
            frames[symbol] = frame
        candidates, events, stats = scan_symbol_day_candidates(
            frames[symbol],
            day_row=day_row,
            model=model,
            detector=detector,
            device=device,
            batch_size=batch_size,
        )
        all_candidates.extend(candidates)
        all_events.extend(events)
        scan_rows.append(
            {
                "day": utc(day_row["day"]).isoformat(),
                "rank": int(day_row["rank"]),
                "symbol": symbol,
                "daily_return": float(day_row["daily_return"]),
                **stats,
            }
        )
        print(
            f"rawbox [{number:03d}/100] {utc(day_row['day']):%m-%d} "
            f"#{int(day_row['rank']):02d} {symbol:<22} "
            f"accepted={len(candidates):>3} v1events={len(events):>2}",
            flush=True,
        )

    parity = legacy_parity(all_events, source_out / "signals.csv")
    annotated_candidates, episodes = cluster_candidates_into_episodes(all_candidates)
    _csv(building_out / "accepted_candidates.csv", annotated_candidates)
    _csv(building_out / "legacy_events.csv", all_events)
    _csv(building_out / "episodes.csv", episodes)
    _csv(building_out / "scan_stats.csv", scan_rows)

    episodes_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in episodes:
        episodes_by_key[(str(row["day"]), str(row["symbol"]))].append(row)
    candidates_by_key: Counter[tuple[str, str]] = Counter(
        (str(row["day"]), str(row["symbol"])) for row in annotated_candidates
    )

    input_dir = building_out / "review" / "model_inputs"
    overlay_dir = building_out / "review" / "overlays"
    input_dir.mkdir(parents=True)
    overlay_dir.mkdir(parents=True)
    enriched_frames = {symbol: add_mas(frame) for symbol, frame in frames.items()}
    review_rows: list[dict[str, Any]] = []
    daily_images: list[dict[str, Any]] = []

    for day in EXPECTED_DAYS:
        board = sorted(
            (row for row in ranked if utc(row["day"]) == day),
            key=lambda row: int(row["rank"]),
        )
        cards: list[np.ndarray] = []
        day_episode_count = 0
        day_candidate_count = 0
        day_review_boxes = 0
        for day_row in board:
            symbol = str(day_row["symbol"])
            key = (day.isoformat(), symbol)
            symbol_episodes = sorted(
                episodes_by_key.get(key, []),
                key=lambda row: (
                    int(row["window_end_i"]),
                    -float(row["confidence"]),
                ),
            )
            day_episode_count += len(symbol_episodes)
            day_candidate_count += candidates_by_key[key]
            enriched = enriched_frames[symbol]
            if symbol_episodes:
                selected = dict(symbol_episodes[0])
                selected["has_detection"] = True
                clean = render_exact_input(enriched, selected)
                overlay = draw_raw_prediction(clean, selected)
                day_review_boxes += 1
            else:
                times = pd.to_datetime(enriched["open_time"], utc=True)
                indices = np.flatnonzero((times >= day) & (times < day + pd.Timedelta(days=1)))
                if len(indices) != 96:
                    raise RawBoxRepairError(f"non-96-bar review day: {day} {symbol}")
                end_i = int(indices[-1])
                window_len = max(EXPECTED_WINDOWS)
                start_i = end_i - window_len + 1
                selected = {
                    "day": day.isoformat(),
                    "rank": int(day_row["rank"]),
                    "symbol": symbol,
                    "daily_return": float(day_row["daily_return"]),
                    "has_detection": False,
                    "class_id": None,
                    "class_name": None,
                    "confidence": None,
                    "episode_id": None,
                    "episode_candidate_count": 0,
                    "window_start_i": start_i,
                    "window_end_i": end_i,
                    "window_len": window_len,
                    "window_end_time": utc(times.iloc[end_i]).isoformat(),
                    "prediction_cx_norm": None,
                    "prediction_cy_norm": None,
                    "prediction_w_norm": None,
                    "prediction_h_norm": None,
                }
                clean = render_exact_input(enriched, selected)
                overlay = clean.copy()

            stem = f"{day:%Y%m%d}_{int(day_row['rank']):02d}_{symbol}"
            input_path = input_dir / f"{stem}_input.png"
            overlay_path = overlay_dir / f"{stem}_rawbox.png"
            write_png(input_path, clean)
            write_png(overlay_path, overlay)
            review_row = {
                **selected,
                "review_selection": "earliest_episode_per_symbol_day",
                "all_episodes_on_symbol_day": len(symbol_episodes),
                "all_accepted_candidates_on_symbol_day": candidates_by_key[key],
                "boxes_per_overlay": int(bool(selected["has_detection"])),
                "model_input_path": final_relative(input_path, building_out, out),
                "model_input_png_sha256": sha256_file(input_path),
                "model_input_pixel_sha256": pixel_sha256(clean),
                "overlay_path": final_relative(overlay_path, building_out, out),
                "overlay_png_sha256": sha256_file(overlay_path),
                "input_width": IMG_WIDTH,
                "input_height": IMG_HEIGHT,
                "training_or_tuning": False,
                "production_eligible": False,
            }
            if review_row["has_detection"]:
                x0, y0, x1, y1 = normalized_box_corners(review_row)
                review_row.update(
                    {
                        "prediction_x0_px": x0,
                        "prediction_y0_px": y0,
                        "prediction_x1_px": x1,
                        "prediction_y1_px": y1,
                    }
                )
            review_rows.append(review_row)
            cards.append(review_card(overlay, day_row=day_row, review_row=review_row))

        sheet = daily_sheet(
            cards,
            day=day,
            accepted_candidates=day_candidate_count,
            episodes=day_episode_count,
            review_boxes=day_review_boxes,
        )
        path = building_results / f"day_{day:%Y%m%d}_top20_rawbox.png"
        write_png(path, sheet)
        daily_images.append(
            {
                "day": day.isoformat(),
                "accepted_candidates": day_candidate_count,
                "episodes": day_episode_count,
                "review_boxes": day_review_boxes,
                "path": final_relative(path, building_results, results),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "width": int(sheet.shape[1]),
                "height": int(sheet.shape[0]),
            }
        )

    _csv(building_out / "review_manifest.csv", review_rows)
    write_jsonl(building_out / "review_manifest.jsonl", review_rows)
    overview = render_overview(ranked, annotated_candidates, episodes, review_rows)
    overview_path = building_results / "overview_rawbox.png"
    write_png(overview_path, overview)

    archive_path = building_results / "actual_model_inputs_and_rawbox_overlays.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(building_out / "review_manifest.csv", "review_manifest.csv")
        for path in sorted(input_dir.glob("*.png")):
            archive.write(path, f"model_inputs/{path.name}")
        for path in sorted(overlay_dir.glob("*.png")):
            archive.write(path, f"rawbox_overlays/{path.name}")

    candidate_counts = Counter(str(row["class_name"]) for row in annotated_candidates)
    episode_counts = Counter(str(row["class_name"]) for row in episodes)
    review_counts = Counter(
        str(row["class_name"]) for row in review_rows if bool(row["has_detection"])
    )
    totals = Counter()
    for row in scan_rows:
        for key, value in row.items():
            if key not in {"day", "rank", "symbol", "daily_return"}:
                totals[key] += int(value)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "source_commit": source_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": 2,
        "network_reads": 0,
        "device": device,
        "immutable_inputs": immutable_inputs,
        "source_fetch_receipt_sha256": str(
            prereg["source_snapshot"]["fetch_receipt_sha256"]
        ),
        "source_snapshot_files": len(fetch_receipt["snapshot_files"]),
        "selected_symbol_days": len(ranked),
        "scan_totals": dict(sorted(totals.items())),
        "accepted_candidates": len(annotated_candidates),
        "accepted_candidate_classes": dict(sorted(candidate_counts.items())),
        "legacy_five_bar_events": len(all_events),
        "legacy_event_parity": parity,
        "overlap_episodes": len(episodes),
        "episode_classes": dict(sorted(episode_counts.items())),
        "review_panels": len(review_rows),
        "review_panels_with_one_box": sum(
            int(row["boxes_per_overlay"] == 1) for row in review_rows
        ),
        "review_panels_with_zero_boxes": sum(
            int(row["boxes_per_overlay"] == 0) for row in review_rows
        ),
        "review_classes": dict(sorted(review_counts.items())),
        "review_selection": "earliest_episode_per_symbol_day",
        "maximum_boxes_per_review_panel": 1,
        "four_coordinate_prediction_fields_preserved": [
            "prediction_cx_norm",
            "prediction_cy_norm",
            "prediction_w_norm",
            "prediction_h_norm",
        ],
        "accepted_candidates_path": repo_relative(out / "accepted_candidates.csv"),
        "legacy_events_path": repo_relative(out / "legacy_events.csv"),
        "episodes_path": repo_relative(out / "episodes.csv"),
        "review_manifest_path": repo_relative(out / "review_manifest.csv"),
        "overview": {
            "path": final_relative(overview_path, building_results, results),
            "sha256": sha256_file(overview_path),
            "size_bytes": overview_path.stat().st_size,
            "width": int(overview.shape[1]),
            "height": int(overview.shape[0]),
        },
        "daily_images": daily_images,
        "review_archive": {
            "path": final_relative(archive_path, building_results, results),
            "sha256": sha256_file(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "model_inputs": len(review_rows),
            "rawbox_overlays": len(review_rows),
        },
        "wall_seconds": round(time.perf_counter() - started, 3),
        "threshold_or_weight_changed": False,
        "training_or_tuning": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "production_eligible": False,
    }
    write_json(building_results / "scan_receipt.json", payload)
    building_out.rename(out)
    building_results.rename(results)
    print(
        f"raw-box repair complete: candidates={len(annotated_candidates)} "
        f"legacy={len(all_events)} episodes={len(episodes)} "
        f"review_boxes={payload['review_panels_with_one_box']}/100 "
        f"wall={payload['wall_seconds'] / 60:.1f}m",
        flush=True,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--source-out", type=Path, default=DEFAULT_SOURCE_OUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--source-commit",
        help="exact committed source identity for a disposable worker without .git",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    prereg_path = args.prereg.resolve()
    prereg = load_preregistration(prereg_path)
    if args.source_commit is None:
        source_commit = verify_committed_sources(prereg_path)
    else:
        source_commit = str(args.source_commit)
        if len(source_commit) != 40 or any(
            char not in "0123456789abcdef" for char in source_commit
        ):
            parser.error("--source-commit must be an exact lowercase 40-char SHA")
    scan_and_render(
        prereg,
        source_out=args.source_out.resolve(),
        out=args.out.resolve(),
        results=args.results.resolve(),
        device=common.choose_device(args.device),
        batch_size=args.batch_size,
        source_commit=source_commit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
