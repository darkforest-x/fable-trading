#!/usr/bin/env python3
"""Run one preregistered frozen Owner YOLO on 30 complete ETH UTC days.

The model sees only causal preregistered windows ending at each scored closed bar.
Every structurally accepted raw ``cx/cy/w/h`` prediction is retained.  For the
Owner-facing surface, overlapping decision intervals are merged across the
whole month and the first available prediction represents each episode.  Each
1920x1400 document contains one preserved raw box on a 128-bar context chart
plus the exact model input inset.  Context bars after detection are review-only.

Columns used by inference are open/high/low/close plus causal SMA/EMA
20/60/120 computed through each window endpoint.  No feature or rendered input
uses a bar after that endpoint.  The script never trains, tunes, promotes,
deploys, writes canonical data, mutates forward state, or places orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
import zipfile
from collections import Counter
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
from scripts.scan_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    draw_raw_prediction,
    normalized_box_corners,
    pixel_sha256,
    render_exact_input,
    scan_symbol_day_candidates,
)
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import IMG_HEIGHT, IMG_WIDTH, render_chart


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-eth30d-20260828-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_owner_yolo_eth30d_20260828_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
TARGET_START = pd.Timestamp("2026-07-29T00:00:00Z")
TARGET_END = pd.Timestamp("2026-08-28T00:00:00Z")
WARMUP_START = pd.Timestamp("2026-07-27T00:00:00Z")
SNAPSHOT_END = pd.Timestamp("2026-08-28T01:30:00Z")
EXPECTED_DAYS = tuple(pd.date_range(TARGET_START, TARGET_END, inclusive="left", freq="1D"))
EXPECTED_WINDOWS = tuple(range(18, 26))
EXPECTED_CORES = (4, 5)
EXPECTED_CONFIRMATIONS = (4, 5, 6)
SYMBOL = "ETH_USDT_SWAP"
INST_ID = "ETH-USDT-SWAP"
CONTEXT_BARS = 128
PREFERRED_DETECTION_LOCAL = 90
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1400
MAIN_X = 20
MAIN_Y = 118
MAIN_WIDTH = 1880
MAIN_HEIGHT = 780
INSET_WIDTH = 700
INSET_HEIGHT = 406


class Eth30dError(RuntimeError):
    """Fail-closed preregistration, data, inference, render, or delivery error."""


def utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def resolve_repo_path(value: object) -> Path:
    path = (ROOT / str(value).replace("\\", "/")).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise Eth30dError(f"path escapes repository: {value}") from exc
    return path


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise Eth30dError(f"could not write PNG: {path}")


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = sorted({key for row in rows for key in row})
    common.write_csv(path, rows, columns)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    payload = read_json(path)
    configure_profile(payload)
    auth = payload["owner_authorization"]
    if int(auth["holdout_consumption_number_for_this_configuration"]) < 1:
        raise Eth30dError("holdout consumption identity must be positive")
    if auth.get("new_inference_authorized") is not True:
        raise Eth30dError("new inference is not authorized")
    if auth.get("telegram_delivery_authorized") is not True:
        raise Eth30dError("Telegram delivery is not authorized")
    for key in (
        "training_or_tuning_authorized",
        "threshold_or_weight_change_authorized",
        "production_or_promotion_authorized",
    ):
        if auth.get(key) is not False:
            raise Eth30dError(f"unsafe authorization flag: {key}")

    calendar = payload["calendar"]
    if int(calendar["complete_days"]) != 30 or len(EXPECTED_DAYS) != 30:
        raise Eth30dError("calendar must contain exactly 30 complete days")
    if TARGET_END - TARGET_START != pd.Timedelta(days=30):
        raise Eth30dError("target interval must be exactly 30 complete UTC days")

    instrument = payload["instrument"]
    if (instrument["inst_id"], instrument["symbol"], instrument["bar"]) != (
        INST_ID,
        SYMBOL,
        "15m",
    ):
        raise Eth30dError("instrument contract drifted")

    detector = payload["detector"]
    if not EXPECTED_WINDOWS or tuple(sorted(set(EXPECTED_WINDOWS))) != EXPECTED_WINDOWS:
        raise Eth30dError("window support must be non-empty, unique and sorted")
    if not EXPECTED_CORES or tuple(sorted(set(EXPECTED_CORES))) != EXPECTED_CORES:
        raise Eth30dError("core support must be non-empty, unique and sorted")
    if not EXPECTED_CONFIRMATIONS or tuple(sorted(set(EXPECTED_CONFIRMATIONS))) != EXPECTED_CONFIRMATIONS:
        raise Eth30dError("confirmation support must be non-empty, unique and sorted")
    extension = int(detector["scan_endpoint_extension_after_day_bars"])
    if extension != max(EXPECTED_CONFIRMATIONS):
        raise Eth30dError("day extension must equal maximum supported confirmation bars")
    if SNAPSHOT_END != TARGET_END + extension * pd.Timedelta(minutes=15):
        raise Eth30dError("snapshot end must complete the final day's confirmation support")
    if float(detector["confidence"]) != 0.25 or float(detector["nms_iou"]) != 0.7:
        raise Eth30dError("threshold or NMS drifted")
    if int(detector["imgsz"]) != 960:
        raise Eth30dError("image size drifted")
    if detector.get("future_bars_rendered_into_inference") != 0:
        raise Eth30dError("inference must remain causal")
    if detector.get("threshold_or_window_retuning_after_results") is not False:
        raise Eth30dError("post-result tuning switch drifted")

    review = payload["review_contract"]
    if int(review["full_context_bars"]) != CONTEXT_BARS:
        raise Eth30dError("context length drifted")
    if int(review["preferred_detection_local_index"]) != PREFERRED_DETECTION_LOCAL:
        raise Eth30dError("context placement drifted")
    if int(review["boxes_per_document"]) != 1:
        raise Eth30dError("each document must contain one box")
    if any(value is not False for value in payload["safety"].values()):
        raise Eth30dError("one or more safety switches drifted")
    return payload


def configure_profile(payload: Mapping[str, Any]) -> None:
    """Bind one preregistered detector/calendar contract to this process.

    The ETH scan is deliberately reusable across immutable detector weights,
    but every temporal and structural degree of freedom comes from the frozen
    preregistration before any market-data read. Columns used: calendar UTC
    bounds and detector window/core/confirmation lists only.
    """

    global EXPERIMENT_ID, TARGET_START, TARGET_END, WARMUP_START, SNAPSHOT_END
    global EXPECTED_DAYS, EXPECTED_WINDOWS, EXPECTED_CORES, EXPECTED_CONFIRMATIONS
    global SYMBOL, INST_ID

    experiment_id = str(payload.get("experiment_id", ""))
    if not experiment_id.startswith("exp-15m-ma-launch-"):
        raise Eth30dError("unexpected experiment id")
    calendar = payload["calendar"]
    instrument = payload["instrument"]
    detector = payload["detector"]
    EXPERIMENT_ID = experiment_id
    TARGET_START = utc(calendar["target_start"])
    TARGET_END = utc(calendar["target_end_exclusive"])
    WARMUP_START = utc(calendar["warmup_start"])
    SNAPSHOT_END = utc(calendar["snapshot_end_exclusive"])
    EXPECTED_DAYS = tuple(
        pd.date_range(TARGET_START, TARGET_END, inclusive="left", freq="1D")
    )
    EXPECTED_WINDOWS = tuple(map(int, detector["window_lengths"]))
    EXPECTED_CORES = tuple(map(int, detector["mapped_core_length_bars_allowed"]))
    EXPECTED_CONFIRMATIONS = tuple(
        map(int, detector["mapped_confirmation_bars_allowed"])
    )
    SYMBOL = str(instrument["symbol"])
    INST_ID = str(instrument["inst_id"])


def holdout_number(prereg: Mapping[str, Any]) -> int:
    return int(
        prereg["owner_authorization"][
            "holdout_consumption_number_for_this_configuration"
        ]
    )


def verify_training_geometry(
    manifest_path: Path, detector: Mapping[str, Any]
) -> dict[str, Any]:
    """Prove inference support is exactly present in positive training labels."""

    positives = 0
    window_counts: Counter[int] = Counter()
    core_counts: Counter[int] = Counter()
    confirmation_counts: Counter[int] = Counter()
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("sample_kind") != "positive":
                continue
            positives += 1
            window = row.get("window_bars")
            if window is None:
                window = int(row["window_end_i"]) - int(row["window_start_i"]) + 1
            confirmation = row.get("post_bars")
            if confirmation is None:
                confirmation = row["post_core_context_bars"]
            window_counts[int(window)] += 1
            core_counts[int(row["core_bars"])] += 1
            confirmation_counts[int(confirmation)] += 1
    expected_positives = detector.get("training_positive_rows")
    if positives < 1 or (
        expected_positives is not None and positives != int(expected_positives)
    ):
        raise Eth30dError(f"positive training rows drifted: {positives}")
    expected = (
        (tuple(sorted(window_counts)), EXPECTED_WINDOWS, "window"),
        (tuple(sorted(core_counts)), EXPECTED_CORES, "core"),
        (
            tuple(sorted(confirmation_counts)),
            EXPECTED_CONFIRMATIONS,
            "confirmation",
        ),
    )
    for actual, frozen, label in expected:
        if actual != frozen:
            raise Eth30dError(f"training {label} support drifted: {actual} != {frozen}")
    return {
        "positive_rows": positives,
        "window_counts": dict(sorted(window_counts.items())),
        "core_counts": dict(sorted(core_counts.items())),
        "confirmation_counts": dict(sorted(confirmation_counts.items())),
    }


def verify_committed_sources(prereg_path: Path) -> str:
    paths = [
        Path(__file__).resolve().relative_to(ROOT),
        prereg_path.resolve().relative_to(ROOT),
    ]
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise Eth30dError("official scan must run on main")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise Eth30dError(f"scan sources must be committed before market-data reads:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if len(commit) != 40:
        raise Eth30dError("could not resolve source commit")
    return commit


def verify_immutable_inputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    detector = prereg["detector"]
    verified: dict[str, Any] = {}
    for key, hash_key in (
        ("weights", "weights_sha256"),
        ("training_manifest", "training_manifest_sha256"),
        ("renderer", "renderer_sha256"),
    ):
        path = resolve_repo_path(detector[key])
        expected = str(detector[hash_key])
        if not path.is_file() or sha256_file(path) != expected:
            raise Eth30dError(f"immutable input drifted: {key}")
        verified[key] = {"path": repo_relative(path), "sha256": expected}
    verified["training_geometry"] = verify_training_geometry(
        resolve_repo_path(detector["training_manifest"]), detector
    )
    return verified


def validate_snapshot(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        raise Eth30dError("ETH snapshot is empty")
    frame = frame.sort_values("open_time", kind="stable").reset_index(drop=True)
    times = pd.to_datetime(frame["open_time"], utc=True)
    if times.duplicated().any() or not times.is_monotonic_increasing:
        raise Eth30dError("snapshot has duplicate or unsorted timestamps")
    gaps = int((times.diff().iloc[1:] != pd.Timedelta(minutes=15)).sum())
    if gaps:
        raise Eth30dError(f"snapshot contains {gaps} gaps")
    if times.iloc[0] != WARMUP_START or times.iloc[-1] != SNAPSHOT_END - pd.Timedelta(minutes=15):
        raise Eth30dError(
            f"snapshot bounds drifted: {times.iloc[0]}..{times.iloc[-1]}"
        )
    day_counts = {
        day.isoformat(): int(((times >= day) & (times < day + pd.Timedelta(days=1))).sum())
        for day in EXPECTED_DAYS
    }
    if set(day_counts.values()) != {96}:
        raise Eth30dError("one or more target days are not exactly 96 bars")
    return {
        "rows": len(frame),
        "first_open_time": times.iloc[0].isoformat(),
        "last_open_time": times.iloc[-1].isoformat(),
        "gaps": gaps,
        "duplicates": int(times.duplicated().sum()),
        "exact_96_bar_days": sum(value == 96 for value in day_counts.values()),
        "day_counts": day_counts,
    }


def build_day_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    times = pd.to_datetime(frame["open_time"], utc=True)
    rows: list[dict[str, Any]] = []
    for day in EXPECTED_DAYS:
        subset = frame.loc[(times >= day) & (times < day + pd.Timedelta(days=1))]
        if len(subset) != 96:
            raise Eth30dError(f"{day:%Y-%m-%d} is not a complete 96-bar day")
        open_px = float(subset.iloc[0]["open"])
        close_px = float(subset.iloc[-1]["close"])
        rows.append(
            {
                "day": day.isoformat(),
                "rank": 1,
                "symbol": SYMBOL,
                "inst_id": INST_ID,
                "daily_return": close_px / open_px - 1.0,
                "open": open_px,
                "close": close_px,
                "bars": 96,
            }
        )
    return rows


def verify_comparison_prefix(
    prereg: Mapping[str, Any], frame: pd.DataFrame
) -> dict[str, Any] | None:
    """Verify the shared yesterday/new snapshot OHLCV prefix exactly.

    Columns used are ``open_time, open, high, low, close, volume``. This is a
    data-parity check only; it does not inspect detections or tune the model.
    """

    baseline = prereg.get("comparison_baseline")
    if not baseline:
        return None
    old_path = resolve_repo_path(baseline["old_snapshot"])
    old_hash = str(baseline["old_snapshot_sha256"])
    if not old_path.is_file() or sha256_file(old_path) != old_hash:
        raise Eth30dError("comparison baseline snapshot drifted")
    prefix_end = utc(baseline["shared_prefix_end_exclusive"])
    columns = ["open_time", "open", "high", "low", "close", "volume"]
    old = pd.read_csv(old_path, usecols=columns)
    new = frame.loc[:, columns].copy()
    for candidate in (old, new):
        candidate["open_time"] = pd.to_datetime(candidate["open_time"], utc=True)
        for column in columns[1:]:
            candidate[column] = pd.to_numeric(candidate[column], errors="raise")
    old = old.loc[old["open_time"] < prefix_end].reset_index(drop=True)
    new = new.loc[new["open_time"] < prefix_end].reset_index(drop=True)
    if len(old) == 0 or len(old) != len(new):
        raise Eth30dError(
            f"comparison prefix row count drifted: old={len(old)} new={len(new)}"
        )
    if not np.array_equal(
        old["open_time"].astype("int64").to_numpy(),
        new["open_time"].astype("int64").to_numpy(),
    ):
        raise Eth30dError("comparison prefix timestamps drifted")
    old_values = old[columns[1:]].to_numpy(dtype=np.float64)
    new_values = new[columns[1:]].to_numpy(dtype=np.float64)
    if not np.array_equal(old_values, new_values):
        differing = int(np.count_nonzero(old_values != new_values))
        raise Eth30dError(f"comparison prefix OHLCV drifted in {differing} cells")
    return {
        "baseline_experiment_id": str(baseline["experiment_id"]),
        "old_snapshot_path": repo_relative(old_path),
        "old_snapshot_sha256": old_hash,
        "prefix_end_exclusive": prefix_end.isoformat(),
        "shared_rows": len(old),
        "ohlcv_exact_match": True,
        "additional_terminal_rows": len(frame) - len(new),
    }


def fetch_phase(
    prereg: Mapping[str, Any], *, out: Path, results: Path, source_commit: str
) -> dict[str, Any]:
    snapshot_path = out / "kline_snapshot" / f"{SYMBOL}.csv"
    receipt_path = results / "fetch_receipt.json"
    if out.exists() or receipt_path.exists():
        raise FileExistsError(f"refusing to overwrite ETH30d fetch: {out}")
    started = time.perf_counter()
    inst_id, frame, raw_count, error = common.fetch_15m_frame(
        INST_ID, start=WARMUP_START, end=SNAPSHOT_END
    )
    if inst_id != INST_ID or error:
        raise Eth30dError(f"OKX fetch failed: {error}")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    integrity = validate_snapshot(frame)
    comparison_prefix = verify_comparison_prefix(prereg, frame)
    day_rows = build_day_rows(frame)
    snapshot_path.parent.mkdir(parents=True)
    frame.to_csv(snapshot_path, index=False)
    results.mkdir(parents=True, exist_ok=True)
    day_path = out / "eth_daily_returns.csv"
    pd.DataFrame(day_rows).to_csv(day_path, index=False)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "holdout_consumption_number_for_this_configuration": holdout_number(prereg),
        "network_reads": True,
        "inst_id": INST_ID,
        "symbol": SYMBOL,
        "target_start": TARGET_START.isoformat(),
        "target_end_exclusive": TARGET_END.isoformat(),
        "complete_days": len(EXPECTED_DAYS),
        "raw_rows_received": raw_count,
        "snapshot": {
            "path": repo_relative(snapshot_path),
            "sha256": sha256_file(snapshot_path),
            "size_bytes": snapshot_path.stat().st_size,
            **integrity,
        },
        "daily_returns": {
            "path": repo_relative(day_path),
            "sha256": sha256_file(day_path),
            "rows": len(day_rows),
        },
        "comparison_prefix": comparison_prefix,
        "canonical_data_written": False,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "training_or_tuning": False,
        "production_eligible": False,
    }
    write_json(receipt_path, payload)
    print(
        f"ETH30d fetch complete: rows={len(frame)} days={len(day_rows)} "
        f"{timespan(frame)} wall={payload['wall_seconds']:.1f}s",
        flush=True,
    )
    return payload


def timespan(frame: pd.DataFrame) -> str:
    times = pd.to_datetime(frame["open_time"], utc=True)
    return f"{times.iloc[0].isoformat()}..{times.iloc[-1].isoformat()}"


def load_frozen_snapshot(out: Path, results: Path) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    receipt = read_json(results / "fetch_receipt.json")
    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise Eth30dError("fetch receipt identity drifted")
    snapshot_path = resolve_repo_path(receipt["snapshot"]["path"])
    if sha256_file(snapshot_path) != str(receipt["snapshot"]["sha256"]):
        raise Eth30dError("snapshot hash drifted")
    frame = pd.read_csv(snapshot_path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    validate_snapshot(frame)
    day_path = resolve_repo_path(receipt["daily_returns"]["path"])
    if sha256_file(day_path) != str(receipt["daily_returns"]["sha256"]):
        raise Eth30dError("daily-return ledger hash drifted")
    day_rows = pd.read_csv(day_path).to_dict("records")
    if len(day_rows) != 30:
        raise Eth30dError("daily-return ledger must contain 30 rows")
    return frame, day_rows, receipt


def cluster_month_episodes(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge overlapping core-start through decision-end intervals for ETH."""

    ordered = sorted(
        (dict(row) for row in candidates),
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

    annotated: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
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
        episode_id = f"ETH30D_{sequence:03d}"
        classes = Counter(str(row["class_name"]) for row in cluster)
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
                "episode_long_candidates": int(classes["dense_long"]),
                "episode_short_candidates": int(classes["dense_short"]),
                "representative_rule": "earliest_window_end_then_highest_confidence",
            }
        )
    annotated.sort(
        key=lambda row: (int(row["window_end_i"]), int(row["window_len"]), -float(row["confidence"]))
    )
    for number, row in enumerate(annotated, 1):
        row["candidate_id"] = f"candidate_{number:06d}"
    return annotated, episodes


def context_bounds(frame_len: int, decision_i: int) -> tuple[int, int]:
    if frame_len < CONTEXT_BARS:
        raise Eth30dError("snapshot is shorter than the full-context contract")
    preferred_start = int(decision_i) - PREFERRED_DETECTION_LOCAL
    start = max(0, min(frame_len - CONTEXT_BARS, preferred_start))
    end = start + CONTEXT_BARS - 1
    if not start <= decision_i <= end:
        raise Eth30dError("decision bar falls outside review context")
    return start, end


def render_episode(
    row: Mapping[str, Any], *, order: int, total: int, enriched: pd.DataFrame
) -> tuple[np.ndarray, dict[str, Any]]:
    times = pd.to_datetime(enriched["open_time"], utc=True)
    clean = render_exact_input(enriched, row)
    if pixel_sha256(clean) != str(row["input_pixel_sha256"]):
        raise Eth30dError("exact inference input pixel identity drifted")
    raw_overlay = draw_raw_prediction(clean, row)

    decision_i = int(row["window_end_i"])
    context_start_i, context_end_i = context_bounds(len(enriched), decision_i)
    context = enriched.iloc[context_start_i : context_end_i + 1]
    context_times = pd.to_datetime(context["open_time"], utc=True)
    if len(context) != CONTEXT_BARS or not (
        context_times.diff().dropna() == pd.Timedelta(minutes=15)
    ).all():
        raise Eth30dError("full context is not 128 contiguous bars")
    main, context_tf = render_chart(
        context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None
    )
    model_window = enriched.iloc[int(row["window_start_i"]) : decision_i + 1]
    _model_input, input_tf = render_chart(model_window, out_path=None)
    projection = project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=context_start_i,
    )
    color = common.CLASS_COLORS[int(row["class_id"])]
    x0 = max(0, min(MAIN_WIDTH - 1, int(projection["context_x0_px"])))
    x1 = max(0, min(MAIN_WIDTH - 1, int(projection["context_x1_px"])))
    y0 = max(0, min(MAIN_HEIGHT - 1, int(projection["context_y0_px"])))
    y1 = max(0, min(MAIN_HEIGHT - 1, int(projection["context_y1_px"])))
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    cv2.rectangle(main, (x0, y0), (x1, y1), color, 5, cv2.LINE_AA)

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
    direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
    detect_utc = utc(row["window_end_time"])
    detect_cst = detect_utc.tz_convert("Asia/Shanghai")
    core_start = utc(row["core_start_time"])
    core_end = utc(row["core_end_time"])
    put_text(
        canvas,
        f"ETHUSDT.P 15m | {order:03d}/{total:03d} | {direction} conf {float(row['confidence']):.3f} | {row['episode_id']}",
        (24, 40),
        scale=0.78,
        thickness=2,
    )
    put_text(
        canvas,
        f"core {core_start:%Y-%m-%d %H:%M}..{core_end:%H:%M} UTC | detection {detect_utc:%Y-%m-%d %H:%M} UTC / {detect_cst:%m-%d %H:%M} CST | W{int(row['window_len'])}",
        (24, 76),
        scale=0.60,
        color=(60, 60, 60),
    )
    put_text(
        canvas,
        f"128-bar context | colored box = preserved raw YOLO box | grey after DETECT = review-only future | episode candidates {int(row['episode_candidate_count'])}",
        (24, 106),
        scale=0.50,
        color=(85, 85, 85),
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main

    label_positions = [0, 24, 48, 72, 96, 127]
    for local_i in label_positions:
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
        put_text(
            canvas,
            price_text(price),
            (CANVAS_WIDTH - 118, py),
            scale=0.42,
            color=(75, 75, 75),
        )

    footer_y = 944
    put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.66, thickness=2)
    put_text(
        canvas,
        "Top: 128 consecutive ETH 15m bars. The rectangle is this episode's first preserved raw YOLO box.",
        (28, footer_y + 35),
        scale=0.50,
    )
    put_text(
        canvas,
        f"Dashed DETECT is when the model input and {min(EXPECTED_CONFIRMATIONS)}-{max(EXPECTED_CONFIRMATIONS)} confirmation bars were fully known.",
        (28, footer_y + 65),
        scale=0.50,
    )
    put_text(
        canvas,
        f"Right: exact 1280x742 W{min(EXPECTED_WINDOWS)}-{max(EXPECTED_WINDOWS)} detector input. Later grey bars never entered inference.",
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
        "event_order": order,
        "events_total": total,
        "episode_id": str(row["episode_id"]),
        "episode_candidate_count": int(row["episode_candidate_count"]),
        "symbol": SYMBOL,
        "class_id": int(row["class_id"]),
        "class_name": str(row["class_name"]),
        "confidence": float(row["confidence"]),
        "window_len": int(row["window_len"]),
        "window_start_i": int(row["window_start_i"]),
        "window_end_i": decision_i,
        "window_end_time": detect_utc.isoformat(),
        "core_start_i": int(row["core_start_i"]),
        "core_end_i": int(row["core_end_i"]),
        "core_start_time": core_start.isoformat(),
        "core_end_time": core_end.isoformat(),
        "core_length_bars": int(row["core_length_bars"]),
        "confirmation_bars": int(row["confirmation_bars"]),
        "context_start_i": context_start_i,
        "context_end_i": context_end_i,
        "context_start_time": utc(context_times.iloc[0]).isoformat(),
        "context_end_time": utc(context_times.iloc[-1]).isoformat(),
        "context_bars": CONTEXT_BARS,
        "post_detection_review_bars": context_end_i - decision_i,
        "model_input_pixel_sha256": pixel_sha256(clean),
        "boxes_per_document": 1,
        "canvas_width": CANVAS_WIDTH,
        "canvas_height": CANVAS_HEIGHT,
    }
    return canvas, metadata


def render_overview(
    episodes: Sequence[Mapping[str, Any]], *, scan_totals: Mapping[str, int]
) -> np.ndarray:
    rows = max(1, math.ceil(len(episodes) / 3))
    height = 250 + rows * 58
    canvas = np.full((height, 1800, 3), 248, dtype=np.uint8)
    put_text(
        canvas,
        f"ETHUSDT.P 15m | frozen Owner YOLO | {TARGET_START:%Y-%m-%d}..{TARGET_END - pd.Timedelta(days=1):%m-%d} UTC",
        (24, 42),
        scale=0.88,
        thickness=2,
    )
    put_text(
        canvas,
        f"windows {int(scan_totals.get('windows_scored', 0)):,} | raw boxes {int(scan_totals.get('raw_boxes', 0)):,} | structural {int(scan_totals.get('accepted_structural_boxes', 0)):,} | overlap episodes {len(episodes)}",
        (24, 80),
        scale=0.61,
        color=(35, 70, 150),
        thickness=2,
    )
    put_text(
        canvas,
        "One row per episode representative: earliest model-available box; all raw candidates remain in CSV.",
        (24, 114),
        scale=0.54,
        color=(65, 65, 65),
    )
    for index, row in enumerate(episodes):
        grid_row, column = divmod(index, 3)
        x = 24 + column * 590
        y = 170 + grid_row * 58
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        stamp = utc(row["window_end_time"])
        color = common.CLASS_COLORS[int(row["class_id"])]
        put_text(
            canvas,
            f"{index + 1:03d} {stamp:%m-%d %H:%M} {direction:<5} conf {float(row['confidence']):.3f} cand {int(row['episode_candidate_count']):>3}",
            (x, y),
            scale=0.49,
            color=color,
            thickness=1,
        )
    return canvas


def scan_phase(
    prereg: Mapping[str, Any],
    *,
    out: Path,
    results: Path,
    device: str,
    batch_size: int,
    source_commit: str,
) -> dict[str, Any]:
    if (results / "scan_receipt.json").exists() or (results / "charts").exists():
        raise FileExistsError("refusing to overwrite ETH30d scan outputs")
    frame, day_rows, fetch_receipt = load_frozen_snapshot(out, results)
    immutable = verify_immutable_inputs(prereg)
    detector = prereg["detector"]
    from ultralytics import YOLO

    model = YOLO(str(resolve_repo_path(detector["weights"])))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != common.CLASS_NAMES:
        raise Eth30dError(f"weight classes drifted: {names}")

    started = time.perf_counter()
    all_candidates: list[dict[str, Any]] = []
    scan_rows: list[dict[str, Any]] = []
    for number, day_row in enumerate(day_rows, 1):
        candidates, _legacy_day_events, stats = scan_symbol_day_candidates(
            frame,
            day_row=day_row,
            model=model,
            detector=detector,
            device=device,
            batch_size=batch_size,
        )
        all_candidates.extend(candidates)
        scan_rows.append({"day": str(day_row["day"]), **stats})
        print(
            f"ETH30d [{number:02d}/30] {utc(day_row['day']):%m-%d} "
            f"raw={int(stats.get('raw_boxes', 0)):>4} accepted={len(candidates):>4}",
            flush=True,
        )

    annotated, episodes = cluster_month_episodes(all_candidates)
    legacy_events = common.deduplicate_hits(
        all_candidates, gap_bars=int(detector["same_symbol_event_gap_bars"])
    )
    write_rows(out / "accepted_candidates.csv", annotated)
    write_rows(out / "five_bar_events.csv", legacy_events)
    write_rows(out / "episodes.csv", episodes)
    write_rows(out / "scan_stats.csv", scan_rows)

    enriched = add_mas(frame)
    charts_building = results / "charts.building"
    charts_building.mkdir(parents=True)
    manifest_rows: list[dict[str, Any]] = []
    for order, row in enumerate(episodes, 1):
        image, metadata = render_episode(row, order=order, total=len(episodes), enriched=enriched)
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        path = charts_building / f"{order:03d}_ETHUSDT_P_{direction}_{utc(row['window_end_time']):%Y%m%dT%H%MZ}.png"
        write_png(path, image)
        metadata.update(
            {
                "image_path": repo_relative(results / "charts" / path.name),
                "image_sha256": sha256_file(path),
                "image_size_bytes": path.stat().st_size,
            }
        )
        manifest_rows.append(metadata)
        print(
            f"render [{order:03d}/{len(episodes):03d}] {direction:<5} "
            f"conf={float(row['confidence']):.3f} {utc(row['window_end_time']):%m-%d %H:%M}Z",
            flush=True,
        )
    os.replace(charts_building, results / "charts")
    write_jsonl(results / "manifest.jsonl", manifest_rows)
    pd.DataFrame(manifest_rows).to_csv(results / "manifest.csv", index=False)

    totals = Counter()
    for row in scan_rows:
        for key, value in row.items():
            if key != "day":
                totals[key] += int(value)
    overview = render_overview(episodes, scan_totals=totals)
    overview_path = results / "overview.png"
    write_png(overview_path, overview)
    archive_path = results / "ethusdt_p_30d_all_signal_charts.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(results / "manifest.csv", "manifest.csv")
        archive.write(out / "accepted_candidates.csv", "accepted_candidates.csv")
        archive.write(out / "episodes.csv", "episodes.csv")
        archive.write(overview_path, overview_path.name)
        for path in sorted((results / "charts").glob("*.png")):
            archive.write(path, f"charts/{path.name}")

    classes = Counter(str(row["class_name"]) for row in episodes)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "holdout_consumption_number_for_this_configuration": holdout_number(prereg),
        "detector_display_name": str(detector.get("display_name", "Owner YOLO")),
        "confidence": float(detector["confidence"]),
        "nms_iou": float(detector["nms_iou"]),
        "imgsz": int(detector["imgsz"]),
        "window_lengths": list(EXPECTED_WINDOWS),
        "mapped_core_length_bars_allowed": list(EXPECTED_CORES),
        "mapped_confirmation_bars_allowed": list(EXPECTED_CONFIRMATIONS),
        "device": device,
        "fetch_receipt_sha256": sha256_file(results / "fetch_receipt.json"),
        "snapshot_sha256": str(fetch_receipt["snapshot"]["sha256"]),
        "immutable_inputs": immutable,
        "target_start": TARGET_START.isoformat(),
        "target_end_exclusive": TARGET_END.isoformat(),
        "complete_days": 30,
        "scan_totals": dict(sorted(totals.items())),
        "accepted_candidates": len(annotated),
        "five_bar_events": len(legacy_events),
        "overlap_episodes": len(episodes),
        "episode_classes": dict(sorted(classes.items())),
        "documents": len(manifest_rows),
        "documents_with_exactly_one_box": len(manifest_rows),
        "manifest_path": repo_relative(results / "manifest.jsonl"),
        "manifest_sha256": sha256_file(results / "manifest.jsonl"),
        "overview": {
            "path": repo_relative(overview_path),
            "sha256": sha256_file(overview_path),
            "size_bytes": overview_path.stat().st_size,
            "width": int(overview.shape[1]),
            "height": int(overview.shape[0]),
        },
        "archive": {
            "path": repo_relative(archive_path),
            "sha256": sha256_file(archive_path),
            "size_bytes": archive_path.stat().st_size,
            "charts": len(manifest_rows),
        },
        "wall_seconds": round(time.perf_counter() - started, 3),
        "training_or_tuning": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(results / "scan_receipt.json", payload)
    print(
        f"ETH30d scan complete: candidates={len(annotated)} events={len(legacy_events)} "
        f"episodes={len(episodes)} wall={payload['wall_seconds'] / 60:.1f}m",
        flush=True,
    )
    return payload


def verify_phase(
    prereg: Mapping[str, Any], *, out: Path, results: Path
) -> dict[str, Any]:
    frame, _day_rows, fetch_receipt = load_frozen_snapshot(out, results)
    scan = read_json(results / "scan_receipt.json")
    if scan.get("experiment_id") != EXPERIMENT_ID:
        raise Eth30dError("scan receipt identity drifted")
    if sha256_file(results / "manifest.jsonl") != str(scan["manifest_sha256"]):
        raise Eth30dError("manifest hash drifted")
    manifest = [
        json.loads(line)
        for line in (results / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(manifest) != int(scan["overlap_episodes"]):
        raise Eth30dError("episode/manifest count drifted")
    enriched = add_mas(frame)
    episode_rows = pd.read_csv(out / "episodes.csv")
    if len(episode_rows) != len(manifest):
        raise Eth30dError("episode source row count drifted")
    exact_rerenders = exact_hashes = exact_inputs = 0
    image_hashes: set[str] = set()
    for order, row in enumerate(manifest, 1):
        if int(row["event_order"]) != order or int(row["boxes_per_document"]) != 1:
            raise Eth30dError("manifest order or box count drifted")
        # Use the original episode row for exact normalized coordinates; pixel
        # corners alone lose subpixel precision and are not a valid rerender key.
        source = episode_rows.loc[episode_rows["episode_id"] == row["episode_id"]]
        if len(source) != 1:
            raise Eth30dError("episode source identity drifted")
        source_row = source.iloc[0].to_dict()
        source_row["episode_candidate_count"] = int(row["episode_candidate_count"])
        expected, expected_meta = render_episode(
            source_row, order=order, total=len(manifest), enriched=enriched
        )
        path = resolve_repo_path(row["image_path"])
        actual = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if actual is None or actual.shape != (CANVAS_HEIGHT, CANVAS_WIDTH, 3):
            raise Eth30dError(f"chart dimensions drifted: {path}")
        if not np.array_equal(actual, expected):
            raise Eth30dError(f"chart pixel rerender drifted: {path}")
        exact_rerenders += 1
        digest = sha256_file(path)
        if digest != str(row["image_sha256"]):
            raise Eth30dError(f"chart PNG hash drifted: {path}")
        image_hashes.add(digest)
        exact_hashes += 1
        if str(expected_meta["model_input_pixel_sha256"]) != str(row["model_input_pixel_sha256"]):
            raise Eth30dError("exact model input hash drifted")
        exact_inputs += 1
    if len(image_hashes) != len(manifest):
        raise Eth30dError("signal chart PNG hashes are not unique")
    input_hashes = [str(row["model_input_pixel_sha256"]) for row in manifest]
    if len(set(input_hashes)) != len(input_hashes):
        raise Eth30dError("exact model-input hashes are not unique")
    shifted_input_hash_matches = 0
    if len(input_hashes) > 1:
        shifted_input_hash_matches = sum(
            left == right
            for left, right in zip(input_hashes, input_hashes[1:] + input_hashes[:1])
        )
    if shifted_input_hash_matches != 0:
        raise Eth30dError("shifted event/input null unexpectedly matched")
    for key in (
        "training_or_tuning",
        "threshold_or_weight_changed",
        "active_or_frozen_changed",
        "promoted",
        "deployed",
        "forward_state_changed",
        "orders_placed",
        "training_eligible",
        "production_eligible",
    ):
        if scan.get(key) is not False:
            raise Eth30dError(f"unsafe scan flag: {key}")
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": holdout_number(prereg),
        "snapshot_sha256": str(fetch_receipt["snapshot"]["sha256"]),
        "events": len(manifest),
        "documents_with_exactly_one_box": len(manifest),
        "exact_pixel_rerenders": exact_rerenders,
        "exact_png_hash_matches": exact_hashes,
        "exact_model_input_pixel_matches": exact_inputs,
        "unique_model_input_hashes": len(set(input_hashes)),
        "shifted_event_input_hash_matches": shifted_input_hash_matches,
        "unique_chart_hashes": len(image_hashes),
        "network_reads_during_verification": 0,
        "training_or_tuning": False,
        "production_eligible": False,
        "passed": True,
    }
    write_json(results / "qa_receipt.json", payload)
    print(
        f"ETH30d QA passed: events={len(manifest)} exact_rerenders={exact_rerenders}",
        flush=True,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--fetch", action="store_true")
    phase.add_argument("--scan", action="store_true")
    phase.add_argument("--verify", action="store_true")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    prereg_path = args.prereg.resolve()
    prereg = load_preregistration(prereg_path)
    out, results = args.out.resolve(), args.results.resolve()
    if args.verify:
        verify_phase(prereg, out=out, results=results)
        return 0
    source_commit = verify_committed_sources(prereg_path)
    if args.fetch:
        fetch_phase(prereg, out=out, results=results, source_commit=source_commit)
    else:
        if args.batch_size < 1:
            parser.error("--batch-size must be positive")
        scan_phase(
            prereg,
            out=out,
            results=results,
            device=common.choose_device(args.device),
            batch_size=args.batch_size,
            source_commit=source_commit,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
