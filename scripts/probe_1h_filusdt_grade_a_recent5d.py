#!/usr/bin/env python3
"""Run the frozen Grade-A detector on FIL-USDT-SWAP 1h for five closed days.

This is an Owner-authorized, research-only holdout probe.  It fetches one
official OKX 1H candle page, removes the still-forming bar, and scores exactly
the latest 120 fully closed endpoints.  Each endpoint produces the unchanged
W18 and W19 inputs.  Raw YOLO counts, structurally accepted boxes, and the
frozen causal semantic-gate subset are reported separately.

All model and semantic features at endpoint ``t`` use only OHLC rows with
``open_time <= t``.  Review charts may show later frozen bars, but they are
written under ``results/review`` while exact causal inputs are written under
``results/model_inputs``.  The two surfaces never share an input path.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ma_launch_t3_daily_movers as common  # noqa: E402
from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts import scan_crypto_grade_a_yolo_mtf_latest as latest  # noqa: E402
from scripts.scan_15m_ma_launch_model_compare_all3d import x_at_float  # noqa: E402
from src.scout_mtf.tf_scan import fetch_candles  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402


EXPERIMENT_ID = "exp-1h-filusdt-grade-a-recent5d-probe-20260903-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
SYMBOL = "FIL_USDT_SWAP"
INST_ID = "FIL-USDT-SWAP"
BAR = "1H"
BAR_DELTA = pd.Timedelta(hours=1)
ENDPOINTS = 120
FETCH_LIMIT = 300
REVIEW_BARS = 240
HOLDOUT_NUMBER = 17
SCREENSHOT = Path(
    "/var/folders/fr/fq9wwmwx3xn63_cdk_v8nvt00000gn/T/"
    "codex-clipboard-0bdf6467-6faf-4059-8faa-22267879f420.png"
)


class FilProbeError(RuntimeError):
    """Fail closed on preregistration, source, model, causality, or artifact drift."""


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
    """Hash decoded BGR pixels instead of PNG container bytes."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write stable, readable UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic JSON Lines."""

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_png(path: Path, image: np.ndarray) -> None:
    """Write a PNG and fail if OpenCV cannot persist it."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
        raise OSError(f"OpenCV failed to write {path}")


def latest_closed_open(frozen_at: object) -> pd.Timestamp:
    """Latest 1h bar open whose close is available at ``frozen_at``."""

    return utc(frozen_at).floor(BAR_DELTA) - BAR_DELTA


def load_preregistration(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and enforce the exact Owner-authorized, no-tuning run contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise FilProbeError("unexpected experiment_id")
    auth = payload.get("owner_authorization") or {}
    if auth.get("holdout_read_authorized") is not True:
        raise FilProbeError("holdout read is not authorized")
    if int(auth.get("holdout_consumption_number_for_checkpoint", -1)) != HOLDOUT_NUMBER:
        raise FilProbeError("checkpoint holdout number drifted")
    if any(
        bool(auth.get(key))
        for key in (
            "training_or_tuning_authorized",
            "threshold_or_weight_change_authorized",
            "production_or_promotion_authorized",
            "orders_authorized",
        )
    ):
        raise FilProbeError("an unauthorized mutation is enabled")
    scope = payload.get("scope") or {}
    expected_scope = {
        "venue": "OKX",
        "instrument": INST_ID,
        "bar": BAR,
        "endpoint_count": ENDPOINTS,
        "fetch_limit": FETCH_LIMIT,
        "review_history_bars": REVIEW_BARS,
    }
    for key, expected in expected_scope.items():
        if scope.get(key) != expected:
            raise FilProbeError(f"scope.{key} drifted")
    detector = payload.get("detector") or {}
    expected_detector = {
        "weights_sha256": base.EXPECTED_WEIGHT_SHA256,
        "imgsz": base.IMAGE_SIZE,
        "confidence": base.CONFIDENCE,
        "nms_iou": base.NMS_IOU,
        "window_lengths": list(base.WINDOW_LENGTHS),
        "mapped_core_length_bars_allowed": sorted(base.ALLOWED_CORES),
        "mapped_confirmation_bars_allowed": sorted(base.ALLOWED_CONFIRMATIONS),
        "same_symbol_event_gap_bars": base.EVENT_GAP_BARS,
    }
    for key, expected in expected_detector.items():
        if detector.get(key) != expected:
            raise FilProbeError(f"detector.{key} drifted")
    for item in (payload.get("implementation_dependencies") or {}).values():
        dep = ROOT / str(item["path"])
        if not dep.is_file() or sha256_file(dep) != str(item["sha256"]):
            raise FilProbeError(f"implementation dependency drifted: {dep}")
    gate_path = ROOT / str(payload["semantic_gate"]["source"])
    if sha256_file(gate_path) != str(payload["semantic_gate"]["source_sha256"]):
        raise FilProbeError("semantic-gate preregistration drifted")
    gate_payload = json.loads(gate_path.read_text(encoding="utf-8"))
    gates = dict(gate_payload["treatment"]["frozen_morphology_gate"])
    if any(bool(value) for value in (payload.get("safety") or {}).values()):
        raise FilProbeError("one or more safety switches are enabled")
    return payload, gates


def verify_committed_sources(prereg_path: Path) -> str:
    """Require main and committed experiment code/config before the market read."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise FilProbeError("official probe must run on main")
    paths = [Path(__file__).resolve().relative_to(ROOT), prereg_path.relative_to(ROOT)]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise FilProbeError(f"probe sources must be committed:\n{dirty}")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def normalize_closed_frame(frame: pd.DataFrame, frozen_at: object) -> pd.DataFrame:
    """Return a contiguous numeric 1h prefix ending at the frozen closed tip."""

    out = frame.copy()
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True)
    out = out.loc[out["open_time"] <= latest_closed_open(frozen_at)].copy()
    out = out.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    if len(out) < max(140, ENDPOINTS + max(base.WINDOW_LENGTHS)):
        raise FilProbeError(f"insufficient closed rows: {len(out)}")
    if utc(out.iloc[-1]["open_time"]) != latest_closed_open(frozen_at):
        raise FilProbeError(
            f"stale latest row {out.iloc[-1]['open_time']} expected {latest_closed_open(frozen_at)}"
        )
    if not bool((out["open_time"].diff().iloc[1:] == BAR_DELTA).all()):
        raise FilProbeError("non-contiguous 1h source rows")
    columns = ["open", "high", "low", "close", "volume"]
    for column in columns:
        out[column] = pd.to_numeric(out[column], errors="raise")
    numeric = out[columns].to_numpy(dtype=float)
    if not bool(np.isfinite(numeric).all()):
        raise FilProbeError("non-finite OHLCV")
    if bool((out[["open", "high", "low", "close"]] <= 0).any().any()):
        raise FilProbeError("non-positive OHLC")
    return out


def collapse_episodes(rows: Sequence[Mapping[str, Any]], stage: str) -> list[dict[str, Any]]:
    """Merge overlapping core-to-decision intervals and retain first availability.

    The representative is the earliest complete model endpoint in the episode;
    confidence breaks ties only.  This prevents a later, higher-confidence view
    from being backdated to the first detection.
    """

    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            int(row["core_start_i"]),
            int(row["window_end_i"]),
            -float(row["confidence"]),
        ),
    )
    clusters: list[list[dict[str, Any]]] = []
    active: list[dict[str, Any]] = []
    active_end: int | None = None
    for row in ordered:
        start = int(row["core_start_i"])
        end = int(row["window_end_i"])
        if active and active_end is not None and start > active_end:
            clusters.append(active)
            active = []
            active_end = None
        active.append(row)
        active_end = end if active_end is None else max(active_end, end)
    if active:
        clusters.append(active)

    episodes: list[dict[str, Any]] = []
    for sequence, cluster in enumerate(clusters, 1):
        representative = min(
            cluster,
            key=lambda row: (
                int(row["window_end_i"]),
                -float(row["confidence"]),
                int(row["window_len"]),
            ),
        )
        counts = Counter(str(row["class_name"]) for row in cluster)
        event = dict(representative)
        detected_open = utc(event["window_end_time"])
        event.update(
            {
                "event_id": f"{stage}_{sequence:02d}",
                "stage": stage,
                "first_detection_bar_open": detected_open.isoformat(),
                "first_available_at": (detected_open + BAR_DELTA).isoformat(),
                "episode_candidate_count": len(cluster),
                "episode_max_confidence": max(float(row["confidence"]) for row in cluster),
                "episode_long_candidates": int(counts["dense_long"]),
                "episode_short_candidates": int(counts["dense_short"]),
                "representative_rule": "earliest_window_end_then_highest_confidence",
            }
        )
        episodes.append(event)
    return sorted(episodes, key=lambda row: utc(row["first_available_at"]))


def flatten_semantic(row: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten nested semantic values for spreadsheet review."""

    return latest.flatten_semantic_candidate(row)


def episodes_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Whether two core-to-decision episode intervals overlap."""

    return max(int(left["core_start_i"]), int(right["core_start_i"])) <= min(
        int(left["window_end_i"]), int(right["window_end_i"])
    )


def draw_dashed_vertical(image: np.ndarray, x: int, color: tuple[int, int, int]) -> None:
    """Draw a full-height dashed event marker."""

    for y in range(10, image.shape[0] - 10, 24):
        cv2.line(image, (x, y), (x, min(image.shape[0] - 10, y + 14)), color, 2)


def put_text(
    image: np.ndarray,
    value: str,
    origin: tuple[int, int],
    *,
    color: tuple[int, int, int] = (25, 25, 25),
    scale: float = 0.55,
    thickness: int = 1,
) -> None:
    """Draw one anti-aliased ASCII annotation."""

    cv2.putText(
        image,
        value,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def render_global_review(
    frame: pd.DataFrame,
    structural_episodes: Sequence[Mapping[str, Any]],
    semantic_episodes: Sequence[Mapping[str, Any]],
) -> np.ndarray:
    """Render 240h context with the five-day scan boundary and event markers."""

    context_start = max(0, len(frame) - REVIEW_BARS)
    context = frame.iloc[context_start:].copy()
    chart, transform = render_chart(context, width=1920, height=1040, out_path=None)
    canvas = np.full((1160, 1920, 3), 247, dtype=np.uint8)
    canvas[86:1126] = chart
    scan_start_i = len(frame) - ENDPOINTS
    scan_x = x_at_float(transform, scan_start_i - context_start)
    draw_dashed_vertical(canvas[86:1126], scan_x, (90, 90, 90))
    put_text(canvas, "FILUSDT.P 1h | frozen Grade-A YOLO | 120 closed endpoints", (24, 33), scale=0.78, thickness=2)
    put_text(
        canvas,
        "grey=5d scan start | thin=structural episode | thick=L1+semantic pipeline event | later bars are review-only",
        (24, 67),
        color=(60, 60, 60),
        scale=0.52,
    )
    put_text(canvas, "SCAN START", (max(4, scan_x - 47), 105), color=(75, 75, 75), scale=0.43)

    for number, row in enumerate(structural_episodes, 1):
        local = int(row["window_end_i"]) - context_start
        if not 0 <= local < len(context):
            continue
        x = x_at_float(transform, local)
        is_long = int(row["class_id"]) == 0
        color = (25, 145, 35) if is_long else (45, 45, 195)
        cv2.line(canvas, (x, 86), (x, 1125), color, 2, cv2.LINE_AA)
        available = utc(row["first_available_at"]).tz_convert("Asia/Shanghai")
        label = (
            f"STRUCT {'L' if is_long else 'S'} "
            f"{available:%m-%d %H:%M} {float(row['confidence']):.2f}"
        )
        y = 124 + (number % 6) * 27
        put_text(canvas, label, (min(1640, max(4, x - 72)), y), color=color, scale=0.42)
    for number, row in enumerate(semantic_episodes, 1):
        local = int(row["window_end_i"]) - context_start
        if not 0 <= local < len(context):
            continue
        x = x_at_float(transform, local)
        is_long = int(row["class_id"]) == 0
        color = (10, 115, 20) if is_long else (25, 25, 165)
        cv2.line(canvas, (x, 86), (x, 1125), color, 5, cv2.LINE_AA)
        available = utc(row["first_available_at"]).tz_convert("Asia/Shanghai")
        label = (
            f"PASS {'L' if is_long else 'S'} {available:%m-%d %H:%M} "
            f"{float(row['confidence']):.2f}"
        )
        y = 300 + (number % 6) * 27
        put_text(
            canvas,
            label,
            (min(1640, max(4, x - 72)), y),
            color=color,
            scale=0.44,
            thickness=2,
        )
    return canvas


def render_exact_inputs(
    enriched: pd.DataFrame,
    episodes: Sequence[Mapping[str, Any]],
    directory: Path,
) -> list[dict[str, Any]]:
    """Persist the exact causal model input and its preserved raw box."""

    records: list[dict[str, Any]] = []
    for number, row in enumerate(episodes, 1):
        start = int(row["window_start_i"])
        end = int(row["window_end_i"])
        clean, _ = render_chart(enriched.iloc[start : end + 1], out_path=None)
        digest = pixel_sha256(clean)
        if digest != str(row["input_pixel_sha256"]):
            raise FilProbeError(f"exact model input pixel drift: {row['event_id']}")
        x0, y0, x1, y1 = base.normalized_box_corners(row)
        overlay = clean.copy()
        color = common.CLASS_COLORS[int(row["class_id"])]
        cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 4, cv2.LINE_AA)
        stem = f"{number:02d}_{row['event_id']}_{row['class_name']}"
        clean_path = directory / f"{stem}_input.png"
        overlay_path = directory / f"{stem}_box.png"
        write_png(clean_path, clean)
        write_png(overlay_path, overlay)
        records.append(
            {
                "event_id": row["event_id"],
                "clean_path": clean_path.name,
                "overlay_path": overlay_path.name,
                "pixel_sha256": digest,
                "window_end_time": row["window_end_time"],
                "class_name": row["class_name"],
                "confidence": float(row["confidence"]),
            }
        )
    return records


def build_gallery(
    path: Path,
    *,
    global_chart: Path,
    input_records: Sequence[Mapping[str, Any]],
    structural_episodes: Sequence[Mapping[str, Any]],
    semantic_episodes: Sequence[Mapping[str, Any]],
) -> None:
    """Build a self-contained local navigation surface for Owner review."""

    rows = []
    for record, event in zip(input_records, structural_episodes):
        event_id = str(event["event_id"])
        matching_pipeline = [
            row
            for row in semantic_episodes
            if int(row["class_id"]) == int(event["class_id"])
            and episodes_overlap(event, row)
        ]
        status = "CONTAINS PIPELINE PASS" if matching_pipeline else "STRUCTURAL ONLY"
        available = utc(event["first_available_at"]).tz_convert("Asia/Shanghai")
        rows.append(
            "<article><h3>"
            + html.escape(
                f"{event_id} · {status} · {event['class_name']} · conf {float(event['confidence']):.3f} · available CST {available:%Y-%m-%d %H:%M}"
            )
            + "</h3><img src='../model_inputs/"
            + html.escape(str(record["overlay_path"]))
            + "'></article>"
        )
    body = "\n".join(rows) or "<p>No structurally accepted YOLO episode.</p>"
    document = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>FILUSDT.P 1h recent-5d frozen model probe</title>
<style>body{{font-family:system-ui;margin:24px;background:#f5f6f8;color:#181818}}img{{max-width:100%;border:1px solid #aaa;background:white}}article{{margin:28px 0;padding:16px;background:white}}code{{background:#eee;padding:2px 5px}}</style></head>
<body><h1>FILUSDT.P 1h · 最近5天冻结模型逐小时扫描</h1>
<p>结构 episode：{len(structural_episodes)}；完整流水线 episode：{len(semantic_episodes)}。全局图中的检测线右侧仅供审核，不进入模型。</p>
<img src='{html.escape(global_chart.name)}'><hr>{body}</body></html>"""
    path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    prereg_path = args.prereg.resolve()
    out = args.out.resolve()
    building = out.with_name(out.name + ".building")
    if out.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite output: {out}")
    prereg, gates = load_preregistration(prereg_path)
    source_commit = verify_committed_sources(prereg_path)
    weights = ROOT / str(prereg["detector"]["weights"])
    if sha256_file(weights) != base.EXPECTED_WEIGHT_SHA256:
        raise FilProbeError("frozen checkpoint bytes drifted")
    if not SCREENSHOT.is_file() or sha256_file(SCREENSHOT) != str(
        prereg["reference_screenshot"]["sha256"]
    ):
        raise FilProbeError("reference screenshot is missing or changed")

    started = time.perf_counter()
    frozen_at = utc(datetime.now(timezone.utc))
    building.mkdir(parents=True)
    (building / "candles").mkdir()
    (building / "model_inputs").mkdir()
    (building / "review").mkdir()
    (building / "reference").mkdir()
    shutil.copy2(prereg_path, building / "preregistration.json")
    shutil.copy2(SCREENSHOT, building / "reference" / "owner_screenshot.png")
    write_json(
        building / "holdout_consumption_started.json",
        {
            "experiment_id": EXPERIMENT_ID,
            "started_at": frozen_at.isoformat(),
            "source_commit": source_commit,
            "holdout_consumption_number_for_checkpoint": HOLDOUT_NUMBER,
            "instrument": INST_ID,
            "bar": BAR,
            "latest_closed_bar_open": latest_closed_open(frozen_at).isoformat(),
            "earliest_scored_bar_open": (
                latest_closed_open(frozen_at) - (ENDPOINTS - 1) * BAR_DELTA
            ).isoformat(),
            "failure_still_consumes_holdout": True,
        },
    )

    try:
        fetched = fetch_candles(INST_ID, BAR, FETCH_LIMIT)
        frame = normalize_closed_frame(fetched, frozen_at)
        candle_path = building / "candles" / f"{SYMBOL}.csv"
        frame.to_csv(candle_path, index=False)

        semantic_ready = latest.enrich_model_frames({SYMBOL: frame})
        enriched, tasks = base.build_tasks(
            semantic_ready, lookback_endpoints=ENDPOINTS
        )
        expected_tasks = ENDPOINTS * len(base.WINDOW_LENGTHS)
        if len(tasks) != expected_tasks:
            raise FilProbeError(f"expected {expected_tasks} model tasks, got {len(tasks)}")

        from ultralytics import YOLO

        device = base.choose_device(args.device)
        model = YOLO(str(weights))
        if {int(key): str(value) for key, value in model.names.items()} != common.CLASS_NAMES:
            raise FilProbeError("checkpoint class map drifted")
        structural, stats = base.infer(
            model,
            tasks,
            frames=enriched,
            device=device,
            batch_size=max(1, int(args.batch_size)),
        )
        decisions = latest.evaluate_semantic_candidates(
            structural,
            enriched,
            gates,
            timeframe="1h",
        )
        passed = [row for row in decisions if bool(row["semantic_gate_pass"])]
        structural_flat = [flatten_semantic(row) for row in decisions]
        passed_flat = [flatten_semantic(row) for row in passed]
        structural_episodes = collapse_episodes(structural_flat, "structural")
        semantic_episodes = collapse_episodes(passed_flat, "pipeline")

        latest_close = float(enriched[SYMBOL].iloc[-1]["close"])
        for row in structural_episodes:
            detected_i = int(row["window_end_i"])
            row["detection_close"] = float(enriched[SYMBOL].iloc[detected_i]["close"])
            row["close_to_frozen_tip_return"] = latest_close / row["detection_close"] - 1.0

        pd.DataFrame(structural_flat).to_csv(
            building / "structural_candidates.csv", index=False
        )
        pd.DataFrame(passed_flat).to_csv(
            building / "semantic_candidates.csv", index=False
        )
        pd.DataFrame(structural_episodes).to_csv(
            building / "structural_episodes.csv", index=False
        )
        pd.DataFrame(semantic_episodes).to_csv(
            building / "pipeline_events.csv", index=False
        )
        write_jsonl(building / "semantic_decisions.jsonl", decisions)

        input_records = render_exact_inputs(
            enriched[SYMBOL], structural_episodes, building / "model_inputs"
        )
        global_image = render_global_review(
            enriched[SYMBOL], structural_episodes, semantic_episodes
        )
        global_path = building / "review" / "FILUSDT_P_1h_recent5d_global.png"
        write_png(global_path, global_image)
        build_gallery(
            building / "review" / "gallery.html",
            global_chart=global_path,
            input_records=input_records,
            structural_episodes=structural_episodes,
            semantic_episodes=semantic_episodes,
        )

        null = latest.paired_direction_null(decisions)
        long_pipeline = [
            row for row in semantic_episodes if int(row["class_id"]) == 0
        ]
        summary = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "frozen_at": frozen_at.isoformat(),
            "holdout_consumption_number_for_checkpoint": HOLDOUT_NUMBER,
            "source": {
                "venue": "OKX",
                "instrument": INST_ID,
                "bar": BAR,
                "rows": len(frame),
                "first_open": utc(frame.iloc[0]["open_time"]).isoformat(),
                "last_open": utc(frame.iloc[-1]["open_time"]).isoformat(),
                "last_available_at": (
                    utc(frame.iloc[-1]["open_time"]) + BAR_DELTA
                ).isoformat(),
                "csv_sha256": sha256_file(candle_path),
            },
            "detector": {
                "weights_sha256": sha256_file(weights),
                "device": device,
                "tasks": len(tasks),
                "window_lengths": list(base.WINDOW_LENGTHS),
                "stats": dict(sorted(stats.items())),
            },
            "results": {
                "structural_candidates": len(structural),
                "structural_episodes": len(structural_episodes),
                "semantic_pass_candidates": len(passed),
                "pipeline_events": len(semantic_episodes),
                "pipeline_long_events": len(long_pipeline),
                "pipeline_short_events": len(semantic_episodes) - len(long_pipeline),
                "full_pipeline_recognized_a_long_setup": bool(long_pipeline),
                "exact_owner_trade_identity_resolved": False,
                "identity_limit": "Owner entry timestamp and price are absent from the screenshot.",
                "structural_episode_summaries": structural_episodes,
                "pipeline_event_summaries": semantic_episodes,
            },
            "null_control": null,
            "causality": {
                "future_rows_in_model_inputs": 0,
                "model_input_end_equals_scored_endpoint": True,
                "review_future_physically_separate": True,
                "exact_input_pixel_replays": len(input_records),
                "exact_input_pixel_failures": 0,
            },
            "safety": dict(prereg["safety"]),
            "elapsed_seconds": time.perf_counter() - started,
        }
        write_json(building / "summary.json", summary)
        out.parent.mkdir(parents=True, exist_ok=True)
        building.rename(out)
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        write_json(
            building / "failure_receipt.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "holdout_consumption_number_for_checkpoint": HOLDOUT_NUMBER,
                "failure_still_consumes_holdout": True,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
