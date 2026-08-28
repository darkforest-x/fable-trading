#!/usr/bin/env python3
"""Offline verifier for the corrected five-day raw-box review artifacts."""
from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd

from scripts.scan_15m_ma_launch_owner_yolo_recent5d_rawbox import (
    DEFAULT_OUT,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    DEFAULT_SOURCE_OUT,
    EXPERIMENT_ID,
    IMG_HEIGHT,
    IMG_WIDTH,
    cluster_candidates_into_episodes,
    draw_raw_prediction,
    load_preregistration,
    normalized_box_corners,
    pixel_sha256,
    sha256_file,
    utc,
)
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart


ROOT = Path(__file__).resolve().parents[1]


class RawBoxVerificationError(RuntimeError):
    """Fail-closed artifact identity, geometry or parity error."""


def resolve_repo_path(value: object) -> Path:
    """Resolve a receipt path from POSIX or Windows without allowing escape."""

    text = str(value)
    windows = PureWindowsPath(text)
    if windows.is_absolute() or windows.drive:
        raise RawBoxVerificationError(f"absolute path forbidden: {text}")
    normalized = text.replace("\\", "/")
    path = (ROOT / normalized).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise RawBoxVerificationError(f"path escapes repository: {text}") from exc
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def png_info(path: Path) -> tuple[np.ndarray, int, int]:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RawBoxVerificationError(f"could not decode PNG: {path}")
    height, width = image.shape[:2]
    return image, width, height


def _clean_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: (None if isinstance(value, float) and np.isnan(value) else value)
        for key, value in dict(row).items()
    }


def verify(
    *,
    prereg_path: Path = DEFAULT_PREREG,
    source_out: Path = DEFAULT_SOURCE_OUT,
    out: Path = DEFAULT_OUT,
    results: Path = DEFAULT_RESULTS,
    output: Path | None = None,
) -> dict[str, Any]:
    """Re-render every review input and reproduce every one-box overlay."""

    prereg = load_preregistration(prereg_path)
    receipt_path = results / "scan_receipt.json"
    receipt = read_json(receipt_path)
    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise RawBoxVerificationError("scan receipt experiment identity drifted")
    if int(receipt["holdout_consumption_number_for_this_configuration"]) != 2:
        raise RawBoxVerificationError("holdout usage number drifted")
    if int(receipt["network_reads"]) != 0:
        raise RawBoxVerificationError("repair unexpectedly performed a network read")
    for flag in (
        "threshold_or_weight_changed",
        "training_or_tuning",
        "active_or_frozen_changed",
        "promoted",
        "deployed",
        "forward_state_changed",
        "orders_placed",
        "production_eligible",
    ):
        if receipt.get(flag) is not False:
            raise RawBoxVerificationError(f"safety flag drifted: {flag}")

    candidates_path = resolve_repo_path(receipt["accepted_candidates_path"])
    events_path = resolve_repo_path(receipt["legacy_events_path"])
    episodes_path = resolve_repo_path(receipt["episodes_path"])
    review_path = resolve_repo_path(receipt["review_manifest_path"])
    candidates = pd.read_csv(candidates_path)
    events = pd.read_csv(events_path)
    episodes = pd.read_csv(episodes_path)
    reviews = pd.read_csv(review_path)
    if len(candidates) != int(receipt["accepted_candidates"]):
        raise RawBoxVerificationError("accepted candidate count drifted")
    if len(events) != int(receipt["legacy_five_bar_events"]):
        raise RawBoxVerificationError("legacy event count drifted")
    if len(episodes) != int(receipt["overlap_episodes"]):
        raise RawBoxVerificationError("episode count drifted")
    if len(reviews) != 100:
        raise RawBoxVerificationError("review manifest must contain 100 symbol-days")

    raw_fields = [
        "prediction_cx_norm",
        "prediction_cy_norm",
        "prediction_w_norm",
        "prediction_h_norm",
    ]
    for field in raw_fields:
        values = pd.to_numeric(candidates[field], errors="raise")
        if values.isna().any() or not ((values > 0) & (values <= 1)).all():
            raise RawBoxVerificationError(f"invalid raw prediction field: {field}")
    if candidates["input_pixel_sha256"].astype(str).str.fullmatch(r"[0-9a-f]{64}").sum() != len(
        candidates
    ):
        raise RawBoxVerificationError("candidate input pixel identity is incomplete")

    recomputed_annotated, recomputed_episodes = cluster_candidates_into_episodes(
        [_clean_dict(row) for row in candidates.to_dict("records")]
    )
    if len(recomputed_annotated) != len(candidates) or len(recomputed_episodes) != len(episodes):
        raise RawBoxVerificationError("episode clustering is not reproducible")
    episode_identity = [
        "episode_id",
        "day",
        "symbol",
        "window_end_i",
        "window_len",
        "class_id",
        "episode_candidate_count",
    ]
    expected_episode_keys = pd.DataFrame(recomputed_episodes)[episode_identity].astype(str).agg(
        "|".join, axis=1
    )
    actual_episode_keys = episodes[episode_identity].astype(str).agg("|".join, axis=1)
    if expected_episode_keys.tolist() != actual_episode_keys.tolist():
        raise RawBoxVerificationError("episode representative identities drifted")

    frames: dict[str, pd.DataFrame] = {}
    one_box = zero_box = input_matches = overlay_matches = 0
    for raw_row in reviews.to_dict("records"):
        row = _clean_dict(raw_row)
        symbol = str(row["symbol"])
        if symbol not in frames:
            frame = pd.read_csv(source_out / "kline_snapshot" / f"{symbol}.csv")
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            for column in ("open", "high", "low", "close", "volume"):
                frame[column] = pd.to_numeric(frame[column], errors="raise")
            frames[symbol] = add_mas(frame)
        enriched = frames[symbol]
        start_i, end_i = int(row["window_start_i"]), int(row["window_end_i"])
        window = enriched.iloc[start_i : end_i + 1]
        if len(window) != int(row["window_len"]):
            raise RawBoxVerificationError("review input window length drifted")
        expected_clean, _ = render_chart(window, out_path=None)
        input_path = resolve_repo_path(row["model_input_path"])
        overlay_path = resolve_repo_path(row["overlay_path"])
        actual_clean, width, height = png_info(input_path)
        actual_overlay, overlay_width, overlay_height = png_info(overlay_path)
        if (width, height, overlay_width, overlay_height) != (
            IMG_WIDTH,
            IMG_HEIGHT,
            IMG_WIDTH,
            IMG_HEIGHT,
        ):
            raise RawBoxVerificationError("review dimensions drifted")
        if not np.array_equal(actual_clean, expected_clean):
            raise RawBoxVerificationError(f"model input pixel drift: {input_path}")
        if pixel_sha256(actual_clean) != str(row["model_input_pixel_sha256"]):
            raise RawBoxVerificationError("model input pixel hash drifted")
        if sha256_file(input_path) != str(row["model_input_png_sha256"]):
            raise RawBoxVerificationError("model input PNG hash drifted")
        input_matches += 1
        has_detection = bool(row["has_detection"])
        if has_detection:
            normalized_box_corners(row)
            expected_overlay = draw_raw_prediction(expected_clean, row)
            one_box += 1
            if int(row["boxes_per_overlay"]) != 1:
                raise RawBoxVerificationError("detected review does not declare one box")
        else:
            expected_overlay = expected_clean
            zero_box += 1
            if int(row["boxes_per_overlay"]) != 0:
                raise RawBoxVerificationError("empty review does not declare zero boxes")
        if not np.array_equal(actual_overlay, expected_overlay):
            raise RawBoxVerificationError(f"raw-box overlay is not reproducible: {overlay_path}")
        if sha256_file(overlay_path) != str(row["overlay_png_sha256"]):
            raise RawBoxVerificationError("overlay PNG hash drifted")
        overlay_matches += 1

    if one_box != int(receipt["review_panels_with_one_box"]):
        raise RawBoxVerificationError("one-box panel count drifted")
    if zero_box != int(receipt["review_panels_with_zero_boxes"]):
        raise RawBoxVerificationError("zero-box panel count drifted")
    if one_box + zero_box != 100:
        raise RawBoxVerificationError("review panel coverage drifted")

    verified_pngs = []
    for item in [receipt["overview"], *receipt["daily_images"]]:
        path = resolve_repo_path(item["path"])
        _image, width, height = png_info(path)
        if sha256_file(path) != str(item["sha256"]):
            raise RawBoxVerificationError(f"summary PNG hash drifted: {path}")
        if (width, height) != (int(item["width"]), int(item["height"])):
            raise RawBoxVerificationError(f"summary PNG dimensions drifted: {path}")
        verified_pngs.append(
            {
                "path": str(item["path"]),
                "sha256": str(item["sha256"]),
                "width": width,
                "height": height,
            }
        )

    archive = receipt["review_archive"]
    archive_path = resolve_repo_path(archive["path"])
    if sha256_file(archive_path) != str(archive["sha256"]):
        raise RawBoxVerificationError("review archive hash drifted")
    with zipfile.ZipFile(archive_path) as handle:
        names = handle.namelist()
        input_names = [name for name in names if name.startswith("model_inputs/")]
        overlay_names = [name for name in names if name.startswith("rawbox_overlays/")]
        if len(input_names) != 100 or len(overlay_names) != 100:
            raise RawBoxVerificationError("review archive member count drifted")
        if names.count("review_manifest.csv") != 1:
            raise RawBoxVerificationError("review archive manifest missing")

    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": 2,
        "network_reads_during_verification": 0,
        "accepted_candidates": len(candidates),
        "legacy_events": len(events),
        "legacy_event_parity": receipt["legacy_event_parity"],
        "overlap_episodes": len(episodes),
        "review_panels": len(reviews),
        "one_raw_box_panels": one_box,
        "zero_box_panels": zero_box,
        "exact_model_input_rerenders": input_matches,
        "exact_raw_box_overlay_reproductions": overlay_matches,
        "preserved_prediction_fields": raw_fields,
        "review_archive_model_inputs": 100,
        "review_archive_overlays": 100,
        "verified_pngs": verified_pngs,
        "safety": {
            "threshold_or_weight_changed": False,
            "training_or_tuning": False,
            "active_or_frozen_changed": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "production_eligible": False,
        },
        "passed": True,
    }
    output_path = output or (results / "qa_receipt.json")
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"raw-box QA passed: candidates={len(candidates)} episodes={len(episodes)} "
        f"one_box={one_box} zero_box={zero_box} exact_inputs={input_matches}",
        flush=True,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--source-out", type=Path, default=DEFAULT_SOURCE_OUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    verify(
        prereg_path=args.prereg.resolve(),
        source_out=args.source_out.resolve(),
        out=args.out.resolve(),
        results=args.results.resolve(),
        output=args.output.resolve() if args.output else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
