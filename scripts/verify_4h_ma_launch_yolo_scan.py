#!/usr/bin/env python3
"""Verify one frozen 4h YOLO scan without network access or model inference.

The verifier checks the checkpoint identity, every frozen candle snapshot,
every accepted W18/W19 model-input pixel array, the deterministic event
deduplication fields, and every delivered event chart.  It deliberately does
not claim semantic accuracy or trading value: those require 4h Owner Gold and
future-outcome evaluation that this out-of-distribution scan does not have.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

from scripts import scan_4h_ma_launch_yolo_latest as scan
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart


class VerificationError(RuntimeError):
    """Fail closed when any frozen input or delivery artifact drifts."""


def load_frame(path: Path) -> pd.DataFrame:
    """Load one frozen 4h snapshot using the scanner's timestamp semantics."""

    frame = pd.read_csv(path)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    return frame


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    """Raise one contextual verification failure."""

    if actual != expected:
        raise VerificationError(f"{label}: expected={expected!r}, actual={actual!r}")


def verify(out: Path) -> dict[str, Any]:
    """Run the complete offline replay and return a machine-readable receipt."""

    summary_path = out / "summary.json"
    candidates_path = out / "accepted_candidates.csv"
    if not summary_path.is_file() or not candidates_path.is_file():
        raise VerificationError(f"incomplete scan output: {out}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    actual_weight_sha = scan.sha256_file(scan.WEIGHTS)
    assert_equal(actual_weight_sha, scan.EXPECTED_WEIGHT_SHA256, "weight SHA")
    assert_equal(summary["weights_sha256"], actual_weight_sha, "declared weight SHA")
    assert_equal(summary["inference_timeframe"], "4h", "inference timeframe")
    assert_equal(summary["source_timeframe"], "15m", "training timeframe")
    assert_equal(summary["out_of_distribution"], True, "OOD flag")
    assert_equal(summary["research_only"], True, "research-only flag")
    assert_equal(summary["production_eligible"], False, "production eligibility")

    frames: dict[str, pd.DataFrame] = {}
    source_hash_passes = 0
    for audit in summary["fetch_audits"]:
        symbol = str(audit["symbol"])
        candle_path = out / "candles" / f"{symbol}.csv"
        if not candle_path.is_file():
            raise VerificationError(f"missing candle snapshot: {candle_path}")
        assert_equal(scan.sha256_file(candle_path), audit["sha256"], f"{symbol} candle SHA")
        frame = load_frame(candle_path)
        assert_equal(len(frame), int(audit["rows"]), f"{symbol} row count")
        assert_equal(scan.utc(frame.iloc[0]["open_time"]).isoformat(), audit["first"], f"{symbol} first bar")
        assert_equal(scan.utc(frame.iloc[-1]["open_time"]).isoformat(), audit["last"], f"{symbol} last bar")
        gaps = frame["open_time"].diff().iloc[1:]
        if not (gaps == scan.BAR_DELTA).all():
            raise VerificationError(f"{symbol} contains a non-4h gap")
        frames[symbol] = add_mas(frame)
        source_hash_passes += 1
    assert_equal(len(frames), int(summary["usable_symbols"]), "usable symbol count")

    candidates = pd.read_csv(candidates_path)
    assert_equal(len(candidates), int(summary["accepted_structural_boxes"]), "candidate count")
    candidate_pixel_passes = 0
    candidate_records: list[dict[str, Any]] = []
    for record in candidates.to_dict(orient="records"):
        symbol = str(record["symbol"])
        frame = frames[symbol]
        start_i = int(record["window_start_i"])
        end_i = int(record["window_end_i"])
        window_len = int(record["window_len"])
        assert_equal(end_i - start_i + 1, window_len, f"{symbol} window length")
        if window_len not in scan.WINDOW_LENGTHS:
            raise VerificationError(f"{symbol} has disallowed window length {window_len}")
        if int(record["core_length_bars"]) not in scan.ALLOWED_CORES:
            raise VerificationError(f"{symbol} has disallowed core length")
        if int(record["confirmation_bars"]) not in scan.ALLOWED_CONFIRMATIONS:
            raise VerificationError(f"{symbol} has disallowed confirmation length")
        if float(record["confidence"]) < scan.CONFIDENCE:
            raise VerificationError(f"{symbol} is below frozen confidence threshold")
        assert_equal(
            scan.utc(frame.iloc[end_i]["open_time"]).isoformat(),
            str(record["window_end_time"]),
            f"{symbol} endpoint time",
        )
        image, _ = render_chart(frame.iloc[start_i : end_i + 1], out_path=None)
        assert_equal(
            scan.pixel_sha256(image),
            str(record["input_pixel_sha256"]),
            f"{symbol} model-input pixels",
        )
        candidate_pixel_passes += 1
        candidate_records.append(record)

    replay_events = scan.deduplicate(candidate_records)
    declared_events = list(summary["signals"])
    assert_equal(len(replay_events), len(declared_events), "event count")
    event_fields = (
        "event_id",
        "symbol",
        "class_id",
        "class_name",
        "window_len",
        "window_start_i",
        "window_end_i",
        "window_end_time",
        "first_available_at",
        "last_available_at",
        "event_peak_available_at",
        "candidate_count",
    )
    event_chart_pixel_passes = 0
    for order, (replay, declared) in enumerate(zip(replay_events, declared_events), 1):
        for field in event_fields:
            assert_equal(replay[field], declared[field], f"event {order} {field}")
        for field in ("confidence", "event_peak_confidence"):
            if not np.isclose(float(replay[field]), float(declared[field]), rtol=0.0, atol=1e-12):
                raise VerificationError(f"event {order} {field} drifted")
        symbol = str(declared["symbol"])
        latest_open = scan.utc(frames[symbol].iloc[-1]["open_time"])
        expected_current = scan.utc(declared["window_end_time"]) == latest_open
        assert_equal(bool(declared["is_current_latest_bar"]), expected_current, f"event {order} current flag")
        assert_equal(
            declared["latest_market_bar_available_at"],
            (latest_open + scan.BAR_DELTA).isoformat(),
            f"event {order} latest market availability",
        )
        replay_for_chart = dict(declared)
        image = scan.render_event(
            replay_for_chart,
            frame=frames[symbol],
            order=order,
            total=len(declared_events),
        )
        chart_path = out / str(declared["chart"])
        stored = cv2.imread(str(chart_path), cv2.IMREAD_COLOR)
        if stored is None or stored.shape != image.shape or not np.array_equal(stored, image):
            raise VerificationError(f"event {order} chart pixels drifted: {chart_path}")
        assert_equal(scan.sha256_file(chart_path), declared["chart_sha256"], f"event {order} chart SHA")
        event_chart_pixel_passes += 1

    current_count = sum(bool(event["is_current_latest_bar"]) for event in declared_events)
    assert_equal(current_count, int(summary["current_latest_bar_events"]), "current event count")
    overview = cv2.imread(str(out / "overview.png"), cv2.IMREAD_COLOR)
    if overview is None:
        raise VerificationError("overview image is unreadable")

    return {
        "verdict": "PASS",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "network_reads_during_verification": 0,
        "model_inference_during_verification": 0,
        "weight_sha256": actual_weight_sha,
        "scanner_script_sha256": scan.sha256_file(Path(scan.__file__)),
        "verifier_script_sha256": scan.sha256_file(Path(__file__)),
        "source_snapshot_hashes_passed": source_hash_passes,
        "candidate_input_pixel_replays_passed": candidate_pixel_passes,
        "event_chart_pixel_replays_passed": event_chart_pixel_passes,
        "events": len(declared_events),
        "current_latest_bar_events": current_count,
        "overview_shape": list(overview.shape),
        "contract": {
            "confidence": scan.CONFIDENCE,
            "nms_iou": scan.NMS_IOU,
            "imgsz": scan.IMAGE_SIZE,
            "windows": list(scan.WINDOW_LENGTHS),
            "cores": sorted(scan.ALLOWED_CORES),
            "confirmations": sorted(scan.ALLOWED_CONFIRMATIONS),
            "event_gap_bars": scan.EVENT_GAP_BARS,
            "lookback_endpoints": int(summary["lookback_confirmed_4h_bars"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    receipt = verify(out)
    scan.write_json(out / "verification.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
