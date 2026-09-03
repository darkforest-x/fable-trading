#!/usr/bin/env python3
"""Offline verifier for the October 2025 Grade-A daily-mover mining run.

The verifier performs no network request and no model inference.  It rebuilds
the daily boards from the frozen Binance monthly archives, replays every saved
model-input pixel from OHLCV, recomputes both actual-direction and flipped-
direction semantic gates, rechecks training-set overlap, rebuilds event
deduplication, and pixel-compares every exact-input and full-day review image.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from scripts import scan_15m_ma_launch_grade_a_daily_movers as scan
from yoyo.layers.l1_detection.render import render_chart
from yoyo.layers.l1_detection.semantic_gate import (
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)


DEFAULT_OUT = scan.DEFAULT_OUT


class VerificationError(RuntimeError):
    """Raised when any frozen artifact fails independent replay."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read one JSONL artifact with stable row order."""

    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _normalized_frame(path: Path, sort: Sequence[str]) -> pd.DataFrame:
    """Load a CSV and sort it by its deterministic identity columns."""

    frame = pd.read_csv(path)
    return frame.sort_values(list(sort)).reset_index(drop=True)


def _compare_csv_rebuild(
    actual_path: Path,
    rebuilt_rows: Sequence[Mapping[str, Any]],
    *,
    sort: Sequence[str],
) -> None:
    """Compare a stored CSV with a fresh source-derived table."""

    actual = _normalized_frame(actual_path, sort)
    rebuilt = scan._csv_ready(rebuilt_rows).sort_values(list(sort)).reset_index(drop=True)
    rebuilt = rebuilt.loc[:, actual.columns]
    try:
        pd.testing.assert_frame_equal(
            actual,
            rebuilt,
            check_dtype=False,
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )
    except AssertionError as exc:
        raise VerificationError(f"CSV rebuild mismatch: {actual_path.name}: {exc}") from exc


def verify_committed_sources(prereg_path: Path, prereg: Mapping[str, Any]) -> str:
    """Require the committed scanner binding and a committed verifier."""

    source_commit = scan.verify_immutable_sources(prereg_path, prereg)
    verifier = Path(__file__).resolve().relative_to(scan.ROOT)
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", str(verifier)],
        cwd=scan.ROOT,
        text=True,
    ).strip()
    if dirty:
        raise VerificationError(f"verifier must be committed before use: {dirty}")
    verifier_commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", str(verifier)],
        cwd=scan.ROOT,
        text=True,
    ).strip()
    if len(verifier_commit) != 40:
        raise VerificationError("could not resolve verifier commit")
    return source_commit


def verify_semantic_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    frames: Mapping[str, pd.DataFrame],
    gates: Mapping[str, Any],
) -> int:
    """Replay every input pixel and both directions of the causal gate."""

    checks = 0
    for row in rows:
        frame = frames[str(row["exchange_symbol"])]
        start = int(row["window_start_i"])
        end = int(row["window_end_i"])
        core_start = int(row["core_start_i"])
        core_end = int(row["core_end_i"])
        if not 0 <= start <= core_start <= core_end + 2 <= end < len(frame):
            raise VerificationError(f"invalid causal indices: {row['candidate_id']}")
        image, _ = render_chart(frame.iloc[start : end + 1], out_path=None)
        if scan.pixel_sha256(image) != str(row["input_pixel_sha256"]):
            raise VerificationError(f"input pixel mismatch: {row['candidate_id']}")
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        flipped = "SHORT" if direction == "LONG" else "LONG"
        causal = frame.iloc[: end + 1]
        features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=end,
            direction=direction,
        )
        decision = evaluate_causal_semantic_gate(features, gates)
        flipped_features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=end,
            direction=flipped,
        )
        flipped_decision = evaluate_causal_semantic_gate(flipped_features, gates)
        expected = {
            "semantic_features": features.to_dict(),
            "semantic_checks": decision.checks,
            "semantic_failed_checks": list(decision.failed_checks),
            "semantic_gate_pass": bool(decision.passed),
            "flipped_semantic_features": flipped_features.to_dict(),
            "flipped_semantic_checks": flipped_decision.checks,
            "flipped_semantic_failed_checks": list(flipped_decision.failed_checks),
            "flipped_semantic_gate_pass": bool(flipped_decision.passed),
        }
        for key, value in expected.items():
            if scan.stable_json(row[key]) != scan.stable_json(value):
                raise VerificationError(f"semantic replay mismatch: {row['candidate_id']} {key}")
        checks += 1
    return checks


def verify_training_overlap(
    rows: Sequence[Mapping[str, Any]], training: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Recompute every persisted training-overlap classification."""

    replayed = scan.annotate_training_overlap(rows, training)
    fields = (
        "exact_training_coordinate_matches",
        "exact_training_input_matches",
        "exact_training_sample_kinds",
        "exact_training_splits",
        "exact_training_sample_ids",
        "near_training_positive_event",
        "nearest_training_positive_event_id",
        "nearest_training_positive_core_end_distance_bars",
        "novelty_status",
    )
    for stored, rebuilt in zip(rows, replayed):
        for field in fields:
            if scan.stable_json(stored[field]) != scan.stable_json(rebuilt[field]):
                raise VerificationError(f"training-overlap mismatch: {stored['candidate_id']} {field}")
    return replayed


def verify_events_and_images(
    out: Path,
    *,
    stored_events: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    rankings: Sequence[Mapping[str, Any]],
    frames: Mapping[str, pd.DataFrame],
    gap_bars: int,
) -> tuple[int, int]:
    """Rebuild event order and pixel-compare every delivered review image."""

    rebuilt = scan.deduplicate_review_events(decisions, gap_bars=gap_bars)
    if len(rebuilt) != len(stored_events):
        raise VerificationError("review event count mismatch")
    event_fields = (
        "event_id",
        "review_rank",
        "day",
        "exchange_symbol",
        "core_start_time",
        "core_end_time",
        "window_end_time",
        "class_id",
        "confidence",
        "semantic_gate_pass",
        "candidate_count",
        "semantic_candidate_count",
        "novelty_status",
        "direction_matches_completed_day",
    )
    for stored, expected in zip(stored_events, rebuilt):
        for field in event_fields:
            if scan.stable_json(stored[field]) != scan.stable_json(expected[field]):
                raise VerificationError(f"event rebuild mismatch: {stored['event_id']} {field}")
        rendered = scan.render_exact_model_input(
            stored, frames[str(stored["exchange_symbol"])]
        )
        delivered = cv2.imread(str(out / str(stored["model_input_chart"])), cv2.IMREAD_COLOR)
        if delivered is None or not np.array_equal(rendered, delivered):
            raise VerificationError(f"exact-input chart mismatch: {stored['event_id']}")
        if scan.sha256_file(out / str(stored["model_input_chart"])) != str(
            stored["model_input_chart_sha256"]
        ):
            raise VerificationError(f"exact-input chart SHA mismatch: {stored['event_id']}")

    day_checks = 0
    for day in sorted({scan.utc(row["day"]) for row in rankings}):
        relative = Path("day_context") / f"day_{day:%Y%m%d}_top5_up_down.png"
        delivered = cv2.imread(str(out / relative), cv2.IMREAD_COLOR)
        rendered = scan.build_day_sheet(day, rankings, stored_events, frames)
        if delivered is None or not np.array_equal(rendered, delivered):
            raise VerificationError(f"day-context chart mismatch: {day:%Y-%m-%d}")
        day_checks += 1
    return len(stored_events), day_checks


def verify(out: Path, *, prereg_path: Path) -> dict[str, Any]:
    """Run the complete network-free, inference-free artifact replay."""

    out = out.resolve()
    if not out.is_dir():
        raise FileNotFoundError(out)
    verification_path = out / "verification.json"
    if verification_path.exists():
        raise FileExistsError(f"refusing to overwrite {verification_path}")
    prereg, gates = scan.load_preregistration(prereg_path)
    source_commit = verify_committed_sources(prereg_path, prereg)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    if summary.get("experiment_id") != scan.EXPERIMENT_ID:
        raise VerificationError("summary experiment identity drifted")
    if summary.get("source_commit") != source_commit:
        raise VerificationError("summary source commit drifted")
    if summary.get("holdout_consumed") is not False:
        raise VerificationError("summary incorrectly marks holdout consumption")
    if summary.get("network_reads") != 0 or summary.get("holdout_ohlcv_rows_materialized") != 0:
        raise VerificationError("summary safety counters drifted")

    source_manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    archive_checks = 0
    for row in source_manifest["archives"]:
        path = scan.ROOT / str(row["path"])
        if scan.sha256_file(path) != str(row["sha256"]):
            raise VerificationError(f"source archive SHA mismatch: {row['path']}")
        archive_checks += 1

    archive_root = scan.ROOT / prereg["data"]["archive_root"]
    universe, rankings, _ = scan.build_daily_rankings(prereg, archive_root=archive_root)
    _compare_csv_rebuild(
        out / "universe_daily_returns.csv",
        universe,
        sort=("day", "exchange_symbol"),
    )
    _compare_csv_rebuild(
        out / "daily_rankings.csv",
        rankings,
        sort=("day", "board_order"),
    )
    symbols = sorted({str(row["exchange_symbol"]) for row in rankings})
    frames, _ = scan.load_selected_frames(prereg, archive_root=archive_root, symbols=symbols)

    decisions = read_jsonl(out / "semantic_decisions.jsonl")
    semantic_checks = verify_semantic_rows(decisions, frames=frames, gates=gates)
    training = scan.load_training_index(prereg)
    replayed = verify_training_overlap(decisions, training)
    stored_events = read_jsonl(out / "review_queue.jsonl")
    event_checks, day_checks = verify_events_and_images(
        out,
        stored_events=stored_events,
        decisions=replayed,
        rankings=rankings,
        frames=frames,
        gap_bars=int(prereg["detector"]["same_symbol_day_event_gap_bars"]),
    )
    if int(summary["counts"]["structural_boxes"]) != semantic_checks:
        raise VerificationError("summary structural count mismatch")
    if int(summary["counts"]["review_events"]) != event_checks:
        raise VerificationError("summary review-event count mismatch")

    receipt = {
        "schema_version": 1,
        "experiment_id": scan.EXPERIMENT_ID,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS",
        "network_reads": 0,
        "model_inference_calls": 0,
        "holdout_ohlcv_rows_materialized": 0,
        "source_archive_sha_checks": archive_checks,
        "universe_symbol_days_rebuilt": len(universe),
        "ranked_symbol_days_rebuilt": len(rankings),
        "model_input_pixel_and_semantic_checks": semantic_checks,
        "training_overlap_checks": len(replayed),
        "review_events_rebuilt_and_chart_checked": event_checks,
        "day_context_pixel_checks": day_checks,
    }
    scan.write_json(verification_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--prereg", type=Path, default=scan.DEFAULT_PREREG)
    args = parser.parse_args()
    verify(args.out, prereg_path=args.prereg.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
