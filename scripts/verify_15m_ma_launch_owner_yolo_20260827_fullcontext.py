#!/usr/bin/env python3
"""Reproduce and verify every 2026-08-27 full-context review document."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from scripts.render_15m_ma_launch_owner_yolo_20260827_fullcontext import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    EXPECTED_CONTEXT_BARS,
    EXPECTED_EVENTS,
    EXPERIMENT_ID,
    load_contract,
    load_enriched_snapshot,
    load_events,
    read_json,
    render_event,
    resolve_repo_path,
    sha256_file,
    utc,
)


class FullContextVerificationError(RuntimeError):
    """Fail closed when a source, pixel, geometry, or safety contract drifts."""


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (None if isinstance(value, float) and np.isnan(value) else value)
        for key, value in row.items()
    }


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def verify(
    *,
    prereg_path: Path = DEFAULT_PREREG,
    results: Path = DEFAULT_RESULTS,
    output: Path | None = None,
) -> dict[str, Any]:
    prereg = load_contract(prereg_path)
    events, _events_path = load_events(prereg)
    snapshot_dir = resolve_repo_path(prereg["source_contract"]["snapshot_dir"])
    receipt_path = results / "render_receipt.json"
    manifest_path = results / "manifest.jsonl"
    receipt = read_json(receipt_path)
    manifest = load_manifest(manifest_path)

    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise FullContextVerificationError("render receipt experiment identity drifted")
    if int(receipt.get("holdout_consumption_number_for_this_configuration", -1)) != 3:
        raise FullContextVerificationError("holdout consumption identity drifted")
    if sha256_file(manifest_path) != str(receipt.get("manifest_sha256")):
        raise FullContextVerificationError("manifest hash drifted")
    if len(events) != EXPECTED_EVENTS or len(manifest) != EXPECTED_EVENTS:
        raise FullContextVerificationError("43-event coverage drifted")
    if int(receipt.get("documents", -1)) != EXPECTED_EVENTS:
        raise FullContextVerificationError("document count drifted")
    if (int(receipt.get("canvas_width", -1)), int(receipt.get("canvas_height", -1))) != (
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
    ):
        raise FullContextVerificationError("canvas contract drifted")
    for flag in (
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
        if receipt.get(flag) is not False:
            raise FullContextVerificationError(f"unsafe receipt flag: {flag}")
    if int(receipt.get("network_reads", -1)) != 0:
        raise FullContextVerificationError("render receipt declares a network read")

    frames: dict[str, Any] = {}
    snapshot_hashes: dict[str, str] = {}
    exact_rerenders = exact_png_hashes = exact_event_identities = 0
    after_midnight = long_events = short_events = 0
    for order, (raw_event, manifest_row) in enumerate(
        zip(events.to_dict("records"), manifest), 1
    ):
        event = clean_row(raw_event)
        if int(manifest_row.get("event_order", -1)) != order:
            raise FullContextVerificationError("event order drifted")
        for key in ("symbol", "rank", "class_id", "window_start_i", "window_end_i"):
            if str(manifest_row.get(key)) != str(event.get(key)):
                raise FullContextVerificationError(f"event identity drifted at {order}: {key}")
        if utc(manifest_row["window_end_time"]) != utc(event["window_end_time"]):
            raise FullContextVerificationError(f"event time drifted at {order}")
        exact_event_identities += 1

        symbol = str(event["symbol"])
        if symbol not in frames:
            frames[symbol], snapshot_hashes[symbol] = load_enriched_snapshot(snapshot_dir, symbol)
        expected, expected_meta = render_event(event, event_order=order, enriched=frames[symbol])
        image_path = resolve_repo_path(manifest_row["image_path"])
        actual = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if actual is None or actual.shape != (CANVAS_HEIGHT, CANVAS_WIDTH, 3):
            raise FullContextVerificationError(f"PNG dimensions drifted: {image_path}")
        if not np.array_equal(actual, expected):
            raise FullContextVerificationError(f"exact rerender mismatch: {image_path}")
        exact_rerenders += 1
        if sha256_file(image_path) != str(manifest_row["image_sha256"]):
            raise FullContextVerificationError(f"PNG hash drifted: {image_path}")
        exact_png_hashes += 1

        for key in (
            "model_input_pixel_sha256",
            "raw_x0_px",
            "raw_y0_px",
            "raw_x1_px",
            "raw_y1_px",
            "context_x0_px",
            "context_y0_px",
            "context_x1_px",
            "context_y1_px",
        ):
            if str(manifest_row.get(key)) != str(expected_meta.get(key)):
                raise FullContextVerificationError(f"geometry metadata drifted at {order}: {key}")
        if int(manifest_row.get("boxes_per_document", -1)) != 1:
            raise FullContextVerificationError("a document does not declare exactly one box")
        if int(manifest_row.get("context_bars", -1)) != EXPECTED_CONTEXT_BARS:
            raise FullContextVerificationError("full-context bar count drifted")
        after_midnight += int(bool(manifest_row["after_board_midnight"]))
        long_events += int(int(manifest_row["class_id"]) == 0)
        short_events += int(int(manifest_row["class_id"]) == 1)

    if (len(frames), long_events, short_events, after_midnight) != (19, 37, 6, 2):
        raise FullContextVerificationError("symbol/direction/day totals drifted")
    if snapshot_hashes != receipt.get("source_snapshot_sha256"):
        raise FullContextVerificationError("snapshot hashes drifted")

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "protocol": prereg["protocol"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": 3,
        "events": EXPECTED_EVENTS,
        "symbols": len(frames),
        "long_events": long_events,
        "short_events": short_events,
        "detections_completed_after_midnight": after_midnight,
        "documents_with_exactly_one_box": EXPECTED_EVENTS,
        "context_bars_per_document": EXPECTED_CONTEXT_BARS,
        "exact_event_identity_matches": exact_event_identities,
        "exact_pixel_rerenders": exact_rerenders,
        "exact_png_hash_matches": exact_png_hashes,
        "exact_model_input_pixel_matches": EXPECTED_EVENTS,
        "raw_box_projection_roundtrip_matches": EXPECTED_EVENTS,
        "network_reads_during_verification": 0,
        "safety": {
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
        },
        "passed": True,
    }
    output_path = output or (results / "qa_receipt.json")
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"full-context QA passed: events={EXPECTED_EVENTS} symbols={len(frames)} "
        f"long={long_events} short={short_events} exact_rerenders={exact_rerenders}",
        flush=True,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    verify(
        prereg_path=args.prereg.resolve(),
        results=args.results.resolve(),
        output=args.output.resolve() if args.output else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
