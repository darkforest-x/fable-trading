#!/usr/bin/env python3
"""Scan three completed OKX daily absolute-mover Top20 boards with Grade-A YOLO.

The daily board is retrospective: instruments are ranked by the absolute
confirmed ``1Dutc`` open-to-close return of the same day.  For each selected
symbol-day, the frozen detector sees only causal 15m windows ending at the
scored bar.  Every accepted raw YOLO rectangle is retained, overlapping
same-symbol intervals are merged across all three days, and the earliest
model-available rectangle represents each review episode.

Columns used by inference are open/high/low/close plus causal SMA/EMA
20/60/120 computed through each window endpoint.  No feature or detector input
uses a future bar.  The 128-bar review chart may show later grey bars, which are
visually separated and never enter inference.  This script never trains,
tunes, promotes, deploys, writes canonical data, sends Telegram messages,
mutates forward state, or places orders.
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
from scripts.render_15m_ma_launch_owner_yolo_20260827_fullcontext import (
    dashed_vertical,
    price_text,
    project_raw_box,
    put_text,
    x_at_float,
)
from scripts.scan_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    draw_raw_prediction,
    pixel_sha256,
    render_exact_input,
    scan_symbol_day_candidates,
)
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-grade-a8000-hot3d-20260829-v1"
FULL40_1280_EXPERIMENT_ID = "exp-15m-ma-launch-owner-grade-a8000-hot3d-1280-20260830-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_owner_grade_a8000_hot3d_20260829_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
EXPECTED_DAYS = tuple(pd.date_range("2026-08-26", "2026-08-29", inclusive="left", freq="1D", tz="UTC"))
EXPECTED_WINDOWS = (18, 19)
EXPECTED_CORES = (4, 5)
EXPECTED_CONFIRMATIONS = tuple(range(2, 10))
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

# A new replay must be deliberately added here with its expected holdout-use
# counter and native model inference size.  This prevents an arbitrary
# preregistration file from silently changing image-size semantics or treating
# a prior model's frozen market snapshot as its own new network fetch.
SUPPORTED_EXPERIMENTS: Mapping[str, Mapping[str, int]] = {
    EXPERIMENT_ID: {
        "holdout_consumption_number": 2,
        "imgsz": 960,
    },
    FULL40_1280_EXPERIMENT_ID: {
        "holdout_consumption_number": 1,
        "imgsz": 1280,
    },
}


class Hot3dError(RuntimeError):
    """Fail-closed preregistration, data, inference, render, or QA error."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

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


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_rows(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    empty_columns: Sequence[str] = (),
) -> None:
    columns = sorted({key for row in rows for key in row}) or list(empty_columns)
    common.write_csv(path, rows, columns)


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
        raise Hot3dError(f"path escapes repository: {value}") from exc
    return path


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise Hot3dError(f"could not write PNG: {path}")


def holdout_number(prereg: Mapping[str, Any]) -> int:
    return int(
        prereg["owner_authorization"][
            "holdout_consumption_number_for_this_configuration"
        ]
    )


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    """Load and enforce the exact owner-authorized no-tuning contract."""

    payload = read_json(path)
    experiment_id = str(payload.get("experiment_id", ""))
    spec = SUPPORTED_EXPERIMENTS.get(experiment_id)
    if spec is None:
        raise Hot3dError("unexpected experiment_id")
    auth = payload["owner_authorization"]
    if (
        holdout_number(payload) != int(spec["holdout_consumption_number"])
        or auth.get("new_inference_authorized") is not True
    ):
        raise Hot3dError("holdout authorization identity drifted")
    if auth.get("telegram_delivery_authorized") is not False:
        raise Hot3dError("Telegram must remain unauthorized")
    for key in (
        "training_or_tuning_authorized",
        "threshold_or_weight_change_authorized",
        "production_or_promotion_authorized",
    ):
        if auth.get(key) is not False:
            raise Hot3dError(f"unsafe authorization flag: {key}")

    days = tuple(utc(value) for value in payload["calendar"]["complete_days"])
    if days != EXPECTED_DAYS or payload["calendar"].get("current_partial_day_excluded") is not True:
        raise Hot3dError("three-day calendar drifted")
    ranking = payload["ranking"]
    if int(ranking["top_per_day"]) != 20:
        raise Hot3dError("daily Top20 size drifted")
    if ranking["sort"] != "descending absolute return; symbol ascending is the deterministic tie break":
        raise Hot3dError("daily mover ordering drifted")
    if ranking["causality"] != "post_hoc_same_day_ranking_not_live_selection":
        raise Hot3dError("ranking causality disclosure drifted")
    if ranking.get("minimum_volume_filter") is not None:
        raise Hot3dError("unexpected volume filter")

    detector = payload["detector"]
    if tuple(map(int, detector["window_lengths"])) != EXPECTED_WINDOWS:
        raise Hot3dError("window support drifted")
    if tuple(map(int, detector["mapped_core_length_bars_allowed"])) != EXPECTED_CORES:
        raise Hot3dError("core support drifted")
    if tuple(map(int, detector["mapped_confirmation_bars_allowed"])) != EXPECTED_CONFIRMATIONS:
        raise Hot3dError("confirmation support drifted")
    if int(detector["scan_endpoint_extension_after_day_bars"]) != max(EXPECTED_CONFIRMATIONS):
        raise Hot3dError("endpoint extension drifted")
    if float(detector["confidence"]) != 0.25 or float(detector["nms_iou"]) != 0.7:
        raise Hot3dError("threshold or NMS drifted")
    if (
        int(detector["imgsz"]) != int(spec["imgsz"])
        or int(detector["same_symbol_event_gap_bars"]) != 5
    ):
        raise Hot3dError("inference geometry drifted")
    if detector.get("future_bars_rendered_into_inference") != 0:
        raise Hot3dError("inference must remain causal")
    if detector.get("threshold_or_window_retuning_after_results") is not False:
        raise Hot3dError("post-result tuning switch drifted")

    review = payload["review_contract"]
    if review["episode_merge"] != "merge_overlapping_intervals_same_symbol_across_all_three_days_class_agnostic":
        raise Hot3dError("episode merge contract drifted")
    if review["episode_representative"] != "earliest_window_end_then_highest_confidence":
        raise Hot3dError("episode representative drifted")
    if int(review["boxes_per_document"]) != 1:
        raise Hot3dError("review documents must remain one-box")
    if int(review["full_context_bars"]) != CONTEXT_BARS:
        raise Hot3dError("full-context length drifted")
    if int(review["preferred_detection_local_index"]) != PREFERRED_DETECTION_LOCAL:
        raise Hot3dError("detection placement drifted")
    if review.get("telegram_delivery") is not False:
        raise Hot3dError("Telegram review switch drifted")
    if any(value is not False for value in payload["safety"].values()):
        raise Hot3dError("one or more safety switches drifted")
    replay = payload.get("data", {}).get("replay_source")
    if experiment_id == FULL40_1280_EXPERIMENT_ID:
        if not isinstance(replay, Mapping):
            raise Hot3dError("1280 replay must identify the frozen source snapshot")
        if replay.get("experiment_id") != EXPERIMENT_ID:
            raise Hot3dError("1280 replay may only reuse the declared 960 snapshot")
        if int(replay.get("holdout_consumption_number", -1)) != int(
            SUPPORTED_EXPERIMENTS[EXPERIMENT_ID]["holdout_consumption_number"]
        ):
            raise Hot3dError("1280 replay source holdout identity drifted")
        for key in ("fetch_receipt", "daily_rankings", "snapshot_location"):
            if not isinstance(replay.get(key), str) or not str(replay[key]):
                raise Hot3dError(f"1280 replay source missing {key}")
    elif replay is not None:
        raise Hot3dError("base hot3d run must not declare a replay source")
    return payload


def verify_committed_sources(prereg_path: Path) -> str:
    """Require main and committed builder bytes before market-data reads."""

    paths = [
        Path(__file__).resolve().relative_to(ROOT),
        (ROOT / "scripts" / "scan_15m_ma_launch_t3_daily_movers.py").relative_to(ROOT),
        (ROOT / "scripts" / "scan_15m_ma_launch_owner_yolo_recent5d_rawbox.py").relative_to(ROOT),
        (ROOT / "scripts" / "render_15m_ma_launch_owner_yolo_20260827_fullcontext.py").relative_to(ROOT),
        prereg_path.resolve().relative_to(ROOT),
    ]
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise Hot3dError("official scan must run on main")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise Hot3dError(f"scan sources must be committed before market-data reads:\n{dirty}")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if len(commit) != 40:
        raise Hot3dError("could not resolve source commit")
    return commit


def verify_training_geometry(
    manifest_path: Path, detector: Mapping[str, Any]
) -> dict[str, Any]:
    """Prove inference window/core/post support equals positive training support."""

    positives = 0
    windows: Counter[int] = Counter()
    cores: Counter[int] = Counter()
    confirmations: Counter[int] = Counter()
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("sample_kind") != "positive":
                continue
            positives += 1
            window = row.get("window_bars")
            if window is None:
                window = int(row["window_end_i"]) - int(row["window_start_i"]) + 1
            confirmation = row.get("post_bars", row.get("post_core_context_bars"))
            windows[int(window)] += 1
            cores[int(row["core_bars"])] += 1
            confirmations[int(confirmation)] += 1
    if positives != int(detector["training_positive_rows"]):
        raise Hot3dError(f"positive training rows drifted: {positives}")
    for actual, expected, label in (
        (tuple(sorted(windows)), EXPECTED_WINDOWS, "window"),
        (tuple(sorted(cores)), EXPECTED_CORES, "core"),
        (tuple(sorted(confirmations)), EXPECTED_CONFIRMATIONS, "confirmation"),
    ):
        if actual != expected:
            raise Hot3dError(f"training {label} support drifted: {actual} != {expected}")
    return {
        "positive_rows": positives,
        "window_counts": dict(sorted(windows.items())),
        "core_counts": dict(sorted(cores.items())),
        "confirmation_counts": dict(sorted(confirmations.items())),
    }


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
            raise Hot3dError(f"immutable input drifted: {key}")
        verified[key] = {"path": repo_relative(path), "sha256": expected}
    verified["training_geometry"] = verify_training_geometry(
        resolve_repo_path(detector["training_manifest"]), detector
    )
    return verified


def validate_rankings(frame: pd.DataFrame) -> None:
    if len(frame) != len(EXPECTED_DAYS) * 20:
        raise Hot3dError("daily ranking row count drifted")
    for day in EXPECTED_DAYS:
        board = frame.loc[frame["day"] == day].copy()
        if sorted(board["rank"].astype(int).tolist()) != list(range(1, 21)):
            raise Hot3dError(f"rank identity drifted for {day:%Y-%m-%d}")
        actual = board.sort_values("rank", kind="stable")["symbol"].astype(str).tolist()
        expected = sorted(
            board.to_dict("records"),
            key=lambda row: (-abs(float(row["daily_return"])), str(row["symbol"])),
        )
        if actual != [str(row["symbol"]) for row in expected]:
            raise Hot3dError(f"absolute-return ordering drifted for {day:%Y-%m-%d}")


def load_frozen_snapshot(
    prereg: Mapping[str, Any], out: Path, results: Path
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, Any], dict[str, str]]:
    """Read either this run's snapshot or an explicitly frozen prior snapshot.

    The full40-1280 replay intentionally consumes no network data.  Its
    ``replay_source`` therefore names the exact prior receipt, rankings and
    OHLCV directory rather than copying or regenerating those bytes.
    """

    replay = prereg.get("data", {}).get("replay_source")
    if isinstance(replay, Mapping):
        receipt_path = resolve_repo_path(replay["fetch_receipt"])
        rankings_path = resolve_repo_path(replay["daily_rankings"])
        snapshot_root = resolve_repo_path(replay["snapshot_location"])
        source_experiment_id = str(replay["experiment_id"])
        source_holdout_number = int(replay["holdout_consumption_number"])
        expected_receipt_sha = str(replay.get("fetch_receipt_sha256", ""))
        expected_rankings_sha = str(replay.get("daily_rankings_sha256", ""))
        source_kind = "frozen_prior_snapshot"
    else:
        receipt_path = results / "fetch_receipt.json"
        rankings_path = out / "daily_rankings.csv"
        snapshot_root = out / "kline_snapshot"
        source_experiment_id = str(prereg["experiment_id"])
        source_holdout_number = holdout_number(prereg)
        expected_receipt_sha = ""
        expected_rankings_sha = ""
        source_kind = "own_snapshot"

    if expected_receipt_sha and sha256_file(receipt_path) != expected_receipt_sha:
        raise Hot3dError("replay fetch receipt bytes drifted")
    receipt = read_json(receipt_path)
    if receipt.get("experiment_id") != source_experiment_id:
        raise Hot3dError("fetch receipt identity drifted")
    if int(receipt["holdout_consumption_number_for_this_configuration"]) != source_holdout_number:
        raise Hot3dError("fetch holdout counter drifted")
    if sha256_file(rankings_path) != str(receipt["daily_rankings_sha256"]):
        raise Hot3dError("daily ranking bytes drifted")
    if expected_rankings_sha and str(receipt["daily_rankings_sha256"]) != expected_rankings_sha:
        raise Hot3dError("replay ranking identity drifted")
    rankings = pd.read_csv(rankings_path)
    rankings["day"] = pd.to_datetime(rankings["day"], utc=True)
    validate_rankings(rankings)
    snapshots = {str(row["symbol"]): row for row in receipt["snapshot_files"]}
    selected = set(rankings["symbol"].astype(str))
    if set(snapshots) != selected:
        raise Hot3dError("snapshot/selected-symbol identity drifted")
    frames: dict[str, pd.DataFrame] = {}
    for symbol, identity in snapshots.items():
        path = snapshot_root / f"{symbol}.csv"
        if not path.is_file() or sha256_file(path) != str(identity["sha256"]):
            raise Hot3dError(f"snapshot hash drifted: {symbol}")
        frame = pd.read_csv(path)
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        if frame["open_time"].duplicated().any() or not frame["open_time"].is_monotonic_increasing:
            raise Hot3dError(f"snapshot time identity invalid: {symbol}")
        gaps = int((frame["open_time"].diff().iloc[1:] != pd.Timedelta(minutes=15)).sum())
        if gaps:
            raise Hot3dError(f"snapshot has {gaps} gaps: {symbol}")
        frames[symbol] = frame
    for row in rankings.to_dict("records"):
        day, symbol = utc(row["day"]), str(row["symbol"])
        times = frames[symbol]["open_time"]
        bars = int(((times >= day) & (times < day + pd.Timedelta(days=1))).sum())
        if bars != 96:
            raise Hot3dError(f"ranked day is not 96 bars: {day} {symbol}")
    source_identity = {
        "kind": source_kind,
        "experiment_id": source_experiment_id,
        "fetch_receipt": repo_relative(receipt_path),
        "fetch_receipt_sha256": sha256_file(receipt_path),
        "daily_rankings": repo_relative(rankings_path),
        "daily_rankings_sha256": str(receipt["daily_rankings_sha256"]),
        "snapshot_location": repo_relative(snapshot_root),
    }
    return rankings, frames, receipt, source_identity


def cluster_symbol_episodes(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge overlapping core-start through decision-end intervals per symbol."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[str(row["symbol"])].append(dict(row))
    annotated: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for symbol, rows in sorted(grouped.items()):
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
        base = symbol.replace("_USDT_SWAP", "")
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
            episode_id = f"HOT3D_{base}_{sequence:03d}"
            class_counts = Counter(str(row["class_name"]) for row in cluster)
            days = sorted({str(row["day"])[:10] for row in cluster})
            for row in cluster:
                annotated.append({**row, "episode_id": episode_id})
            episodes.append(
                {
                    **representative,
                    "episode_id": episode_id,
                    "episode_sequence_for_symbol": sequence,
                    "episode_candidate_count": len(cluster),
                    "episode_interval_start_i": min(int(row["core_start_i"]) for row in cluster),
                    "episode_interval_end_i": max(int(row["window_end_i"]) for row in cluster),
                    "episode_max_confidence": max(float(row["confidence"]) for row in cluster),
                    "episode_long_candidates": int(class_counts["dense_long"]),
                    "episode_short_candidates": int(class_counts["dense_short"]),
                    "episode_ranked_days": ",".join(days),
                    "episode_ranked_day_count": len(days),
                    "representative_rule": "earliest_window_end_then_highest_confidence",
                }
            )
    annotated.sort(
        key=lambda row: (
            utc(row["window_end_time"]),
            str(row["symbol"]),
            int(row["window_len"]),
            -float(row["confidence"]),
        )
    )
    for number, row in enumerate(annotated, 1):
        row["candidate_id"] = f"candidate_{number:06d}"
    episodes.sort(
        key=lambda row: (utc(row["window_end_time"]), str(row["symbol"]), str(row["episode_id"]))
    )
    return annotated, episodes


def context_bounds(frame_len: int, decision_i: int) -> tuple[int, int]:
    if frame_len < CONTEXT_BARS:
        raise Hot3dError("snapshot is shorter than the full-context contract")
    preferred_start = int(decision_i) - PREFERRED_DETECTION_LOCAL
    start = max(0, min(frame_len - CONTEXT_BARS, preferred_start))
    end = start + CONTEXT_BARS - 1
    if not start <= decision_i <= end:
        raise Hot3dError("decision bar falls outside review context")
    return start, end


def display_symbol(symbol: object) -> str:
    return str(symbol).replace("_USDT_SWAP", "") + "USDT.P"


def render_episode(
    row: Mapping[str, Any],
    *,
    order: int,
    total: int,
    enriched: pd.DataFrame,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Render one 1920x1400 whole-context document with one episode box."""

    times = pd.to_datetime(enriched["open_time"], utc=True)
    clean = render_exact_input(enriched, row)
    if pixel_sha256(clean) != str(row["input_pixel_sha256"]):
        raise Hot3dError("exact inference input pixel identity drifted")
    raw_overlay = draw_raw_prediction(clean, row)
    decision_i = int(row["window_end_i"])
    context_start_i, context_end_i = context_bounds(len(enriched), decision_i)
    context = enriched.iloc[context_start_i : context_end_i + 1]
    context_times = pd.to_datetime(context["open_time"], utc=True)
    if len(context) != CONTEXT_BARS or not (
        context_times.diff().dropna() == pd.Timedelta(minutes=15)
    ).all():
        raise Hot3dError("full context is not 128 contiguous bars")
    main, context_tf = render_chart(context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None)
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
        f"{display_symbol(row['symbol'])} 15m | {order:03d}/{total:03d} | {direction} conf {float(row['confidence']):.3f} | {row['episode_id']}",
        (24, 40),
        scale=0.76,
        thickness=2,
    )
    put_text(
        canvas,
        f"Top20 day {str(row['day'])[:10]} rank #{int(row['rank']):02d} return {float(row['daily_return']) * 100:+.2f}% | core {core_start:%m-%d %H:%M}..{core_end:%H:%M} UTC | detect {detect_utc:%m-%d %H:%M} UTC / {detect_cst:%m-%d %H:%M} CST",
        (24, 76),
        scale=0.54,
        color=(60, 60, 60),
    )
    put_text(
        canvas,
        f"128-bar context | one episode / one preserved raw box | W{int(row['window_len'])}, core{int(row['core_length_bars'])}, post{int(row['confirmation_bars'])} | grey after DETECT = review-only future",
        (24, 106),
        scale=0.49,
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
        "Top: 128 consecutive 15m bars. The colored rectangle is this independent episode's first raw YOLO box.",
        (28, footer_y + 35),
        scale=0.49,
    )
    put_text(
        canvas,
        f"Dashed DETECT is when the W{int(row['window_len'])} input and {int(row['confirmation_bars'])} post-core bars were fully known.",
        (28, footer_y + 65),
        scale=0.49,
    )
    put_text(
        canvas,
        "Right: exact 1280x742 detector input. The board is post-hoc daily movers, not a live tradable universe.",
        (28, footer_y + 95),
        scale=0.49,
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
        "day": str(row["day"]),
        "rank": int(row["rank"]),
        "daily_return": float(row["daily_return"]),
        "symbol": str(row["symbol"]),
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
    rankings: pd.DataFrame,
    episodes: Sequence[Mapping[str, Any]],
    *,
    accepted_candidates: int,
    detector_display_name: str,
) -> np.ndarray:
    """Render the frozen three daily boards with per-symbol episode counts."""

    width, height = 1920, 1160
    canvas = np.full((height, width, 3), 248, dtype=np.uint8)
    put_text(
        canvas,
        f"15m {detector_display_name} | 3 complete UTC days | daily absolute-return Top20 | episodes {len(episodes)}",
        (24, 42),
        scale=0.88,
        thickness=2,
    )
    put_text(
        canvas,
        f"accepted raw candidates {accepted_candidates} -> overlapping same-symbol episodes {len(episodes)} | one high-res document per episode",
        (24, 78),
        scale=0.58,
        color=(35, 70, 150),
        thickness=2,
    )
    put_text(
        canvas,
        "POST-HOC BOARD: 'hot' means |daily return|, not volume/social popularity; today is excluded; not a live trading selection.",
        (24, 111),
        scale=0.53,
        color=(35, 70, 150),
        thickness=1,
    )
    counts: Counter[tuple[str, str]] = Counter(
        (str(row["day"])[:10], str(row["symbol"])) for row in episodes
    )
    class_counts: Counter[tuple[str, str, str]] = Counter(
        (str(row["day"])[:10], str(row["symbol"]), str(row["class_name"]))
        for row in episodes
    )
    for column, day in enumerate(EXPECTED_DAYS):
        x0, y0, card_w = 20 + column * 630, 145, 610
        cv2.rectangle(canvas, (x0, y0), (x0 + card_w, height - 20), (225, 229, 234), 2)
        day_key = f"{day:%Y-%m-%d}"
        board = rankings.loc[rankings["day"] == day].sort_values("rank", kind="stable")
        day_events = sum(counts[(day_key, str(row.symbol))] for row in board.itertuples())
        put_text(canvas, f"{day_key} UTC | episodes {day_events}", (x0 + 12, y0 + 35), scale=0.68, thickness=2)
        put_text(canvas, "#  SYMBOL          RETURN       EP(L/S)", (x0 + 12, y0 + 70), scale=0.50)
        for line, row in enumerate(board.itertuples()):
            symbol = str(row.symbol).replace("_USDT_SWAP", "")[:13]
            key = (day_key, str(row.symbol))
            long_n = class_counts[(day_key, str(row.symbol), "dense_long")]
            short_n = class_counts[(day_key, str(row.symbol), "dense_short")]
            text = (
                f"{int(row.rank):02d} {symbol:<13} {float(row.daily_return) * 100:+8.2f}%   "
                f"{counts[key]:>2}({long_n}/{short_n})"
            )
            color = (20, 125, 35) if float(row.daily_return) >= 0 else (45, 45, 190)
            put_text(canvas, text, (x0 + 12, y0 + 108 + line * 46), scale=0.53, color=color)
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
        raise FileExistsError("refusing to overwrite hot3d scan outputs")
    rankings, frames, fetch_receipt, source_identity = load_frozen_snapshot(prereg, out, results)
    immutable = verify_immutable_inputs(prereg)
    detector = prereg["detector"]
    from ultralytics import YOLO

    model = YOLO(str(resolve_repo_path(detector["weights"])))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != common.CLASS_NAMES:
        raise Hot3dError(f"weight classes drifted: {names}")

    started = time.perf_counter()
    all_candidates: list[dict[str, Any]] = []
    all_five_bar_events: list[dict[str, Any]] = []
    scan_rows: list[dict[str, Any]] = []
    day_rows = rankings.sort_values(["day", "rank"], kind="stable").to_dict("records")
    for number, day_row in enumerate(day_rows, 1):
        symbol = str(day_row["symbol"])
        candidates, five_bar_events, stats = scan_symbol_day_candidates(
            frames[symbol],
            day_row=day_row,
            model=model,
            detector=detector,
            device=device,
            batch_size=batch_size,
        )
        all_candidates.extend(candidates)
        all_five_bar_events.extend(five_bar_events)
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
            f"hot3d [{number:02d}/60] {utc(day_row['day']):%m-%d} #{int(day_row['rank']):02d} "
            f"{symbol:<22} {float(day_row['daily_return']) * 100:+7.2f}% "
            f"raw={int(stats.get('raw_boxes', 0)):>3} accepted={len(candidates):>3}",
            flush=True,
        )

    annotated, episodes = cluster_symbol_episodes(all_candidates)
    write_rows(out / "accepted_candidates.csv", annotated, empty_columns=("candidate_id", "episode_id"))
    write_rows(out / "five_bar_events.csv", all_five_bar_events, empty_columns=("day", "symbol"))
    write_rows(out / "episodes.csv", episodes, empty_columns=("episode_id", "symbol"))
    write_rows(out / "scan_stats.csv", scan_rows)

    enriched = {symbol: add_mas(frame) for symbol, frame in frames.items()}
    charts_building = results / "charts.building"
    charts_building.mkdir(parents=True)
    manifest_rows: list[dict[str, Any]] = []
    for order, row in enumerate(episodes, 1):
        image, metadata = render_episode(
            row,
            order=order,
            total=len(episodes),
            enriched=enriched[str(row["symbol"])],
        )
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        base = str(row["symbol"]).replace("_USDT_SWAP", "")
        path = charts_building / (
            f"{order:03d}_{base}USDT_P_{direction}_{utc(row['window_end_time']):%Y%m%dT%H%MZ}.png"
        )
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
            f"render [{order:03d}/{len(episodes):03d}] {base:<14} {direction:<5} "
            f"conf={float(row['confidence']):.3f} {utc(row['window_end_time']):%m-%d %H:%M}Z",
            flush=True,
        )
    os.replace(charts_building, results / "charts")
    write_jsonl(results / "manifest.jsonl", manifest_rows)
    pd.DataFrame(manifest_rows).to_csv(results / "manifest.csv", index=False)

    overview = render_overview(
        rankings,
        episodes,
        accepted_candidates=len(annotated),
        detector_display_name=str(detector["display_name"]),
    )
    overview_path = results / "overview.png"
    write_png(overview_path, overview)
    archive_path = results / "hot3d_all_signal_charts.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in (
            results / "manifest.csv",
            results / "manifest.jsonl",
            overview_path,
            # Under a frozen-snapshot replay the rankings live in the SOURCE
            # run's output directory, not this one. Packaging them from `out`
            # crashed the 1280 replay after all 41 episodes had been rendered,
            # losing the scan receipt while leaving the charts on disk -- an
            # interrupted run that looked like a completed one.
            resolve_repo_path(source_identity["daily_rankings"]),
            out / "accepted_candidates.csv",
            out / "episodes.csv",
            out / "scan_stats.csv",
        ):
            archive.write(path, path.name)
        for path in sorted((results / "charts").glob("*.png")):
            archive.write(path, f"charts/{path.name}")

    totals: Counter[str] = Counter()
    for row in scan_rows:
        for key, value in row.items():
            if key not in {"day", "rank", "symbol", "daily_return"}:
                totals[key] += int(value)
    classes = Counter(str(row["class_name"]) for row in episodes)
    event_days = Counter(str(row["day"])[:10] for row in episodes)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": str(prereg["experiment_id"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "holdout_consumption_number_for_this_configuration": holdout_number(prereg),
        "detector_display_name": str(detector["display_name"]),
        "weights_sha256": str(detector["weights_sha256"]),
        "confidence": float(detector["confidence"]),
        "nms_iou": float(detector["nms_iou"]),
        "imgsz": int(detector["imgsz"]),
        "window_lengths": list(EXPECTED_WINDOWS),
        "mapped_core_length_bars_allowed": list(EXPECTED_CORES),
        "mapped_confirmation_bars_allowed": list(EXPECTED_CONFIRMATIONS),
        "device": device,
        "snapshot_source": source_identity,
        "fetch_receipt_sha256": str(source_identity["fetch_receipt_sha256"]),
        "daily_rankings_sha256": str(fetch_receipt["daily_rankings_sha256"]),
        "immutable_inputs": immutable,
        "complete_days": [day.isoformat() for day in EXPECTED_DAYS],
        "selected_symbol_days": len(rankings),
        "selected_unique_symbols": len(frames),
        "ranking_causality": prereg["ranking"]["causality"],
        "hot_definition": prereg["ranking"]["hot_definition"],
        "scan_totals": dict(sorted(totals.items())),
        "accepted_candidates": len(annotated),
        "five_bar_events_before_cross_day_episode_merge": len(all_five_bar_events),
        "overlap_episodes": len(episodes),
        "episode_classes": dict(sorted(classes.items())),
        "episode_representative_days": dict(sorted(event_days.items())),
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
        "telegram_sent": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(results / "scan_receipt.json", payload)
    print(
        f"hot3d scan complete: candidates={len(annotated)} five_bar={len(all_five_bar_events)} "
        f"episodes={len(episodes)} wall={payload['wall_seconds'] / 60:.1f}m",
        flush=True,
    )
    return payload


def verify_phase(
    prereg: Mapping[str, Any], *, out: Path, results: Path
) -> dict[str, Any]:
    rankings, frames, fetch_receipt, _source_identity = load_frozen_snapshot(prereg, out, results)
    scan = read_json(results / "scan_receipt.json")
    if scan.get("experiment_id") != str(prereg["experiment_id"]):
        raise Hot3dError("scan receipt identity drifted")
    if sha256_file(results / "manifest.jsonl") != str(scan["manifest_sha256"]):
        raise Hot3dError("manifest hash drifted")
    manifest = [
        json.loads(line)
        for line in (results / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(manifest) != int(scan["overlap_episodes"]):
        raise Hot3dError("episode/manifest count drifted")
    episode_rows = pd.read_csv(out / "episodes.csv") if manifest else pd.DataFrame()
    if len(episode_rows) != len(manifest):
        raise Hot3dError("episode source row count drifted")
    enriched = {symbol: add_mas(frame) for symbol, frame in frames.items()}
    exact_rerenders = exact_hashes = exact_inputs = 0
    image_hashes: set[str] = set()
    input_hashes: list[str] = []
    for order, row in enumerate(manifest, 1):
        if int(row["event_order"]) != order or int(row["boxes_per_document"]) != 1:
            raise Hot3dError("manifest order or box count drifted")
        source = episode_rows.loc[episode_rows["episode_id"] == row["episode_id"]]
        if len(source) != 1:
            raise Hot3dError("episode source identity drifted")
        source_row = source.iloc[0].to_dict()
        source_row["episode_candidate_count"] = int(row["episode_candidate_count"])
        symbol = str(row["symbol"])
        expected, expected_meta = render_episode(
            source_row,
            order=order,
            total=len(manifest),
            enriched=enriched[symbol],
        )
        path = resolve_repo_path(row["image_path"])
        actual = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if actual is None or actual.shape != (CANVAS_HEIGHT, CANVAS_WIDTH, 3):
            raise Hot3dError(f"chart dimensions drifted: {path}")
        if not np.array_equal(actual, expected):
            raise Hot3dError(f"chart pixel rerender drifted: {path}")
        exact_rerenders += 1
        digest = sha256_file(path)
        if digest != str(row["image_sha256"]):
            raise Hot3dError(f"chart PNG hash drifted: {path}")
        image_hashes.add(digest)
        exact_hashes += 1
        if str(expected_meta["model_input_pixel_sha256"]) != str(row["model_input_pixel_sha256"]):
            raise Hot3dError("exact model input hash drifted")
        input_hashes.append(str(row["model_input_pixel_sha256"]))
        exact_inputs += 1
    if len(image_hashes) != len(manifest):
        raise Hot3dError("signal chart PNG hashes are not unique")
    shifted_matches = 0
    if len(input_hashes) > 1:
        shifted_matches = sum(
            left == right
            for left, right in zip(input_hashes, input_hashes[1:] + input_hashes[:1])
        )
    for key in (
        "training_or_tuning",
        "threshold_or_weight_changed",
        "active_or_frozen_changed",
        "promoted",
        "deployed",
        "forward_state_changed",
        "orders_placed",
        "telegram_sent",
        "training_eligible",
        "production_eligible",
    ):
        if scan.get(key) is not False:
            raise Hot3dError(f"unsafe scan flag: {key}")
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": str(prereg["experiment_id"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": holdout_number(prereg),
        "daily_rankings_sha256": str(fetch_receipt["daily_rankings_sha256"]),
        "ranked_symbol_days": len(rankings),
        "events": len(manifest),
        "documents_with_exactly_one_box": len(manifest),
        "exact_pixel_rerenders": exact_rerenders,
        "exact_png_hash_matches": exact_hashes,
        "exact_model_input_pixel_matches": exact_inputs,
        "unique_model_input_hashes": len(set(input_hashes)),
        "shifted_event_input_hash_matches": shifted_matches,
        "unique_chart_hashes": len(image_hashes),
        "network_reads_during_verification": 0,
        "training_or_tuning": False,
        "telegram_sent": False,
        "production_eligible": False,
        "passed": True,
    }
    write_json(results / "qa_receipt.json", payload)
    print(
        f"hot3d QA passed: ranked={len(rankings)} events={len(manifest)} "
        f"exact_rerenders={exact_rerenders}",
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
        if prereg.get("data", {}).get("replay_source") is not None:
            raise Hot3dError("frozen replay forbids --fetch; no new network data may be read")
        common.fetch_and_rank(
            prereg,
            out=out,
            results=results,
            workers=8,
            source_commit=source_commit,
        )
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
