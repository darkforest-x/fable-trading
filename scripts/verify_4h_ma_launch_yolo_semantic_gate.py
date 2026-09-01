#!/usr/bin/env python3
"""Independently replay the frozen 4h semantic-gate result and image gallery.

This verifier performs no network access and no model inference.  It validates
the preregistered source hashes, recomputes every saved causal feature and gate
decision from the frozen 4h candle bytes, replays all exact W18/W19 input pixel
hashes, proves the accepted table is the exact passing subset, re-deduplicates
the treatment events, and verifies every global-future chart hash.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts.apply_4h_ma_launch_yolo_semantic_gate import (  # noqa: E402
    DEFAULT_OUT,
    DEFAULT_SOURCE,
    EXPERIMENT_ID,
    HOLDOUT_CONSUMPTION_NUMBER,
    PARENT_GATE_PREREG,
    paired_binary_summary,
    sha256_file,
    write_json,
)
from yoyo.datasets.fifteen_minute_launch_candidates import (  # noqa: E402
    add_candidate_features,
)
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402
from yoyo.layers.l1_detection.semantic_gate import (  # noqa: E402
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)


class FourHourSemanticVerificationError(RuntimeError):
    """Raised when a saved result does not replay byte-for-byte or numerically."""


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_feature_equal(
    actual: Mapping[str, Any], expected: Mapping[str, Any], candidate_id: str
) -> None:
    if set(actual) != set(expected):
        raise FourHourSemanticVerificationError(
            f"feature keys drifted: {candidate_id}"
        )
    for key in actual:
        left, right = actual[key], expected[key]
        if left is None or right is None:
            if left is not None or right is not None:
                raise FourHourSemanticVerificationError(
                    f"conditional feature drift: {candidate_id} {key}"
                )
        elif not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12):
            raise FourHourSemanticVerificationError(
                f"feature value drift: {candidate_id} {key}"
            )


def _load_frames(source: Path, symbols: set[str]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for symbol in sorted(symbols):
        frame = pd.read_csv(source / "candles" / f"{symbol}.csv")
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        frames[symbol] = add_candidate_features(frame)
    return frames


def _verify_receipt(out: Path) -> int:
    receipt = read_json(out / "gate_receipt.json")
    if receipt.get("verdict") != "PASS":
        raise FourHourSemanticVerificationError("gate receipt is not PASS")
    for name, expected in receipt["files"].items():
        path = out / name
        if sha256_file(path) != str(expected["sha256"]):
            raise FourHourSemanticVerificationError(f"gate artifact hash drifted: {name}")
        if path.stat().st_size != int(expected["size_bytes"]):
            raise FourHourSemanticVerificationError(f"gate artifact size drifted: {name}")
    return len(receipt["files"])


def _verify_gallery(out: Path, summary: Mapping[str, Any]) -> int:
    receipt_path = out / "global_future_gallery_receipt.json"
    if not receipt_path.is_file():
        raise FourHourSemanticVerificationError("global-future receipt is missing")
    receipt = read_json(receipt_path)
    if receipt.get("verdict") != "PASS":
        raise FourHourSemanticVerificationError("global-future receipt is not PASS")
    if int(receipt["charts"]) != int(summary["event_summary"]["treatment_deduplicated_events"]):
        raise FourHourSemanticVerificationError("global-future chart count drifted")
    if sha256_file(out / "summary.json") != str(receipt["source_summary_sha256"]):
        raise FourHourSemanticVerificationError("gallery source summary drifted")
    for event in receipt["events"]:
        path = out / str(event["chart"])
        if sha256_file(path) != str(event["chart_sha256"]):
            raise FourHourSemanticVerificationError(f"gallery chart drifted: {path.name}")
    gallery = out / str(receipt["gallery"])
    if not gallery.is_file():
        raise FourHourSemanticVerificationError("global-future HTML is missing")
    return int(receipt["charts"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verification-out", type=Path, default=None)
    args = parser.parse_args()
    source = args.source.resolve()
    out = args.out.resolve()
    verification_out = (
        args.verification_out.resolve()
        if args.verification_out is not None
        else out / "verification.json"
    )

    summary = read_json(out / "summary.json")
    prereg = read_json(out / "preregistration.json")
    parent = read_json(PARENT_GATE_PREREG)
    if summary.get("experiment_id") != EXPERIMENT_ID:
        raise FourHourSemanticVerificationError("experiment ID drifted")
    if int(summary.get("holdout_consumption_number_for_checkpoint", -1)) != HOLDOUT_CONSUMPTION_NUMBER:
        raise FourHourSemanticVerificationError("holdout consumption number drifted")
    if not bool(summary.get("holdout_consumed")):
        raise FourHourSemanticVerificationError("holdout consumption is not declared")
    gates = dict(prereg["treatment"]["frozen_morphology_gate"])
    if gates != dict(parent["treatment"]["frozen_morphology_gate"]):
        raise FourHourSemanticVerificationError("gate thresholds differ from parent")

    for _, expected in prereg["frozen_source"]["artifacts"].items():
        path = source / str(expected["path"])
        if sha256_file(path) != str(expected["sha256"]):
            raise FourHourSemanticVerificationError(f"source artifact drifted: {path.name}")

    receipt_files = _verify_receipt(out)
    saved = read_jsonl(out / "semantic_boxes.jsonl")
    if len(saved) != int(summary["box_summary"]["structural_boxes"]):
        raise FourHourSemanticVerificationError("semantic box count drifted")
    ids = [str(row["candidate_id"]) for row in saved]
    if len(ids) != len(set(ids)):
        raise FourHourSemanticVerificationError("duplicate semantic candidate IDs")
    frames = _load_frames(source, {str(row["symbol"]) for row in saved})

    replayed = 0
    pixel_replays = 0
    for row in saved:
        candidate_id = str(row["candidate_id"])
        frame = frames[str(row["symbol"])]
        start = int(row["window_start_i"])
        observed = int(row["window_end_i"])
        core_start = int(row["core_start_i"])
        core_end = int(row["core_end_i"])
        causal = frame.iloc[: observed + 1]
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        flipped = "SHORT" if direction == "LONG" else "LONG"
        features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=observed,
            direction=direction,
        )
        flipped_features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=observed,
            direction=flipped,
        )
        _assert_feature_equal(features.to_dict(), row["semantic_features"], candidate_id)
        _assert_feature_equal(
            flipped_features.to_dict(), row["flipped_semantic_features"], candidate_id
        )
        decision = evaluate_causal_semantic_gate(features, gates)
        flipped_decision = evaluate_causal_semantic_gate(flipped_features, gates)
        if bool(decision.passed) != bool(row["semantic_gate_pass"]):
            raise FourHourSemanticVerificationError(f"gate decision drift: {candidate_id}")
        if list(decision.failed_checks) != list(row["semantic_failed_checks"]):
            raise FourHourSemanticVerificationError(f"gate failures drift: {candidate_id}")
        if bool(flipped_decision.passed) != bool(row["flipped_semantic_gate_pass"]):
            raise FourHourSemanticVerificationError(
                f"flipped decision drift: {candidate_id}"
            )
        image, _ = render_chart(frame.iloc[start : observed + 1], out_path=None)
        if base.pixel_sha256(image) != str(row["input_pixel_sha256"]):
            raise FourHourSemanticVerificationError(f"input pixel drift: {candidate_id}")
        replayed += 1
        pixel_replays += 1

    passing_ids = {str(row["candidate_id"]) for row in saved if row["semantic_gate_pass"]}
    accepted = pd.read_csv(out / "accepted_candidates.csv", keep_default_na=False)
    accepted_ids = set(accepted["candidate_id"].astype(str)) if len(accepted) else set()
    if passing_ids != accepted_ids:
        raise FourHourSemanticVerificationError("accepted table is not exact passing subset")

    treatment_events = base.deduplicate(accepted.to_dict(orient="records")) if len(accepted) else []
    saved_events = [dict(row) for row in summary["signals"]]
    if len(treatment_events) != len(saved_events):
        raise FourHourSemanticVerificationError("treatment event count failed to replay")
    for actual, expected in zip(treatment_events, saved_events):
        for key in (
            "event_id",
            "symbol",
            "first_detection_bar_open_time",
            "last_detection_bar_open_time",
            "class_id",
            "candidate_count",
        ):
            if str(actual[key]) != str(expected[key]):
                raise FourHourSemanticVerificationError(
                    f"treatment event replay drift: {expected['event_id']} {key}"
                )

    pairing = read_jsonl(out / "event_pairing.jsonl")
    if len(pairing) != int(summary["event_summary"]["control_events"]):
        raise FourHourSemanticVerificationError("event pairing count drifted")
    box_null = paired_binary_summary(
        [bool(row["semantic_gate_pass"]) for row in saved],
        [bool(row["flipped_semantic_gate_pass"]) for row in saved],
    )
    event_null = paired_binary_summary(
        [bool(row["actual_event_survives"]) for row in pairing],
        [bool(row["flipped_event_survives"]) for row in pairing],
    )
    if box_null != summary["direction_flip_null"]["box_level"]:
        raise FourHourSemanticVerificationError("box direction-null summary drifted")
    if event_null != summary["direction_flip_null"]["control_event_level"]:
        raise FourHourSemanticVerificationError("event direction-null summary drifted")

    gallery_charts = _verify_gallery(out, summary)
    result = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "verdict": "PASS",
        "holdout_consumption_number_for_checkpoint": HOLDOUT_CONSUMPTION_NUMBER,
        "source_hashes_verified": len(prereg["frozen_source"]["artifacts"]),
        "gate_receipt_files_verified": receipt_files,
        "semantic_boxes_recomputed": replayed,
        "input_pixel_replays_passed": pixel_replays,
        "accepted_subset_verified": len(passing_ids),
        "treatment_events_replayed": len(treatment_events),
        "control_event_pairs_verified": len(pairing),
        "direction_flip_null_verified": True,
        "future_candles_used_by_gate": 0,
        "global_future_charts_verified": gallery_charts,
        "model_inference": 0,
        "network_reads": 0,
    }
    write_json(verification_out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
