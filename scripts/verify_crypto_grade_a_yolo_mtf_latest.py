#!/usr/bin/env python3
"""Offline verifier for the frozen four-timeframe Grade-A YOLO scan.

The verifier performs zero network reads and zero model inference.  It checks
the frozen candle bytes and clocks, re-renders every structural model input,
recomputes the causal semantic gate in both actual and direction-flipped arms,
rebuilds five-bar events and the deterministic review order, and pixel-replays
every delivered event chart.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_crypto_grade_a_yolo_mtf_latest as scan  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402
from yoyo.layers.l1_detection.semantic_gate import (  # noqa: E402
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)


DEFAULT_OUT = scan.DEFAULT_OUT


class MultiTimeframeVerificationError(RuntimeError):
    """Raised when any frozen source, decision, rank, or chart fails replay."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MultiTimeframeVerificationError(message)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _same_float(left: Any, right: Any, *, atol: float = 1e-12) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=atol)
    except (TypeError, ValueError):
        return False


def _same_mapping_floats(
    actual: Mapping[str, Any], expected: Mapping[str, Any], *, label: str
) -> None:
    require(set(actual) == set(expected), f"{label} keys differ")
    for key in actual:
        left, right = actual[key], expected[key]
        if isinstance(left, bool) or isinstance(right, bool):
            require(bool(left) == bool(right), f"{label}.{key} differs")
        elif left is None or right is None:
            require(left is right, f"{label}.{key} None mismatch")
        else:
            require(_same_float(left, right), f"{label}.{key} float differs")


def verify_candles(
    out: Path, summary: Mapping[str, Any]
) -> tuple[dict[str, dict[str, pd.DataFrame]], int]:
    """Verify every declared candle file, row clock, and frozen endpoint."""

    frames: dict[str, dict[str, pd.DataFrame]] = {}
    checked = 0
    for spec in scan.TIMEFRAMES:
        tf_frames: dict[str, pd.DataFrame] = {}
        audits = list(summary["fetch_audits"][spec.key])
        require(
            len(audits) == int(summary["scan_stats"][spec.key]["usable_symbols"]),
            f"{spec.key} audit count differs from usable symbols",
        )
        expected_latest = scan.latest_closed_open(summary["frozen_at"], spec)
        for audit in audits:
            symbol = str(audit["symbol"])
            path = out / "candles" / spec.key / f"{symbol}.csv"
            require(path.is_file(), f"missing candle file: {path}")
            require(scan.sha256_file(path) == str(audit["sha256"]), f"candle SHA drift: {symbol} {spec.key}")
            frame = pd.read_csv(path)
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            require(len(frame) == int(audit["rows"]), f"candle row count drift: {symbol} {spec.key}")
            require(len(frame) >= scan.MIN_HISTORY_ROWS, f"short candle history: {symbol} {spec.key}")
            require(scan.utc(frame.iloc[-1]["open_time"]) == expected_latest, f"latest candle drift: {symbol} {spec.key}")
            diffs = frame["open_time"].diff().iloc[1:]
            require(bool((diffs == spec.delta).all()), f"candle continuity drift: {symbol} {spec.key}")
            numeric = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
            require(bool(np.isfinite(numeric).all()), f"non-finite candle: {symbol} {spec.key}")
            tf_frames[symbol] = frame
            checked += 1
        frames[spec.key] = scan.enrich_model_frames(tf_frames)
    return frames, checked


def verify_semantic_decisions(
    out: Path,
    frames: Mapping[str, Mapping[str, pd.DataFrame]],
    gates: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    """Replay every input pixel and both directions of the frozen semantic gate."""

    decisions_by_tf: dict[str, list[dict[str, Any]]] = {}
    pixel_checks = 0
    semantic_checks = 0
    for spec in scan.TIMEFRAMES:
        decisions = read_jsonl(out / spec.key / "semantic_decisions.jsonl")
        decisions_by_tf[spec.key] = decisions
        for row in decisions:
            require(str(row["timeframe"]) == spec.key, f"candidate timeframe drift: {row['candidate_id']}")
            frame = frames[spec.key][str(row["symbol"])]
            start = int(row["window_start_i"])
            observed = int(row["window_end_i"])
            core_start = int(row["core_start_i"])
            core_end = int(row["core_end_i"])
            require(0 <= start <= core_start <= core_end + 2 <= observed < len(frame), f"candidate indices drift: {row['candidate_id']}")
            replay, _ = render_chart(frame.iloc[start : observed + 1], out_path=None)
            replay_hash = scan.base.pixel_sha256(replay)
            require(replay_hash == str(row["input_pixel_sha256"]), f"candidate pixel drift: {row['candidate_id']}")
            require(replay_hash == str(row["input_pixel_replay_sha256"]), f"stored replay hash drift: {row['candidate_id']}")
            pixel_checks += 1

            causal = frame.iloc[: observed + 1]
            direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
            flipped = "SHORT" if direction == "LONG" else "LONG"
            actual_features = compute_causal_core_semantics(
                causal,
                core_start_i=core_start,
                core_end_i=core_end,
                observed_end_i=observed,
                direction=direction,
            )
            actual_decision = evaluate_causal_semantic_gate(actual_features, gates)
            flipped_features = compute_causal_core_semantics(
                causal,
                core_start_i=core_start,
                core_end_i=core_end,
                observed_end_i=observed,
                direction=flipped,
            )
            flipped_decision = evaluate_causal_semantic_gate(flipped_features, gates)
            _same_mapping_floats(
                actual_features.to_dict(), row["semantic_features"], label=f"{row['candidate_id']}.features"
            )
            _same_mapping_floats(
                flipped_features.to_dict(), row["flipped_semantic_features"], label=f"{row['candidate_id']}.flipped_features"
            )
            require(bool(actual_decision.passed) == bool(row["semantic_gate_pass"]), f"actual decision drift: {row['candidate_id']}")
            require(list(actual_decision.failed_checks) == list(row["semantic_failed_checks"]), f"actual reasons drift: {row['candidate_id']}")
            require(dict(actual_decision.checks) == dict(row["semantic_checks"]), f"actual checks drift: {row['candidate_id']}")
            require(bool(flipped_decision.passed) == bool(row["flipped_semantic_gate_pass"]), f"flipped decision drift: {row['candidate_id']}")
            require(list(flipped_decision.failed_checks) == list(row["flipped_semantic_failed_checks"]), f"flipped reasons drift: {row['candidate_id']}")
            require(dict(flipped_decision.checks) == dict(row["flipped_semantic_checks"]), f"flipped checks drift: {row['candidate_id']}")
            semantic_checks += 1
    return decisions_by_tf, pixel_checks, semantic_checks


EVENT_KEYS = (
    "event_id",
    "symbol",
    "timeframe",
    "window_len",
    "window_start_i",
    "window_end_i",
    "window_end_time",
    "core_start_i",
    "core_end_i",
    "core_length_bars",
    "confirmation_bars",
    "class_id",
    "class_name",
    "first_available_at",
    "last_available_at",
    "event_peak_available_at",
    "candidate_count",
    "is_current_latest_bar",
)


def verify_events_and_ranks(
    out: Path,
    summary: Mapping[str, Any],
    frames: Mapping[str, Mapping[str, pd.DataFrame]],
    decisions_by_tf: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    """Rebuild semantic events and the documented non-numeric review ranking."""

    frozen_at = scan.utc(summary["frozen_at"])
    rebuilt_events: list[dict[str, Any]] = []
    for spec in scan.TIMEFRAMES:
        passed = [
            scan.flatten_semantic_candidate(row)
            for row in decisions_by_tf[spec.key]
            if bool(row["semantic_gate_pass"])
        ]
        events = scan.deduplicate_events(
            passed,
            spec=spec,
            frames=frames[spec.key],
            frozen_at=frozen_at,
        )
        require(len(events) == int(summary["event_counts"][spec.key]["total"]), f"{spec.key} event count drift")
        rebuilt_events.extend(events)

    rebuilt = scan.rank_events(rebuilt_events)
    declared = read_jsonl(out / "ranked_signals.jsonl")
    require(len(rebuilt) == len(declared) == int(summary["ranked_events"]), "ranked event count drift")
    for expected, actual in zip(rebuilt, declared):
        for key in EVENT_KEYS:
            require(str(expected[key]) == str(actual[key]), f"event {actual.get('event_id')} {key} drift")
        for key in (
            "confidence",
            "event_peak_confidence",
            "confidence_percentile_within_timeframe",
        ):
            require(_same_float(expected[key], actual[key]), f"event {actual['event_id']} {key} drift")
        for key in (
            "review_rank",
            "confidence_rank_within_timeframe",
            "events_in_timeframe",
            "symbol_timeframe_count",
            "same_side_timeframe_count",
            "direction_conflict_for_symbol",
        ):
            require(str(expected[key]) == str(actual[key]), f"event {actual['event_id']} {key} drift")
        require(list(expected["symbol_timeframes"]) == list(actual["symbol_timeframes"]), f"event {actual['event_id']} symbol TF drift")
        require(list(expected["same_side_timeframes"]) == list(actual["same_side_timeframes"]), f"event {actual['event_id']} side TF drift")
    return declared, len(declared)


def verify_charts(
    out: Path,
    events: Sequence[Mapping[str, Any]],
    frames: Mapping[str, Mapping[str, pd.DataFrame]],
) -> int:
    """Pixel-replay every delivered event chart and verify its PNG SHA."""

    checked = 0
    total = len(events)
    for row in events:
        path = out / "charts" / str(row["chart"])
        require(path.is_file(), f"missing event chart: {path}")
        require(scan.sha256_file(path) == str(row["chart_sha256"]), f"chart SHA drift: {row['event_id']}")
        saved = cv2.imread(str(path))
        require(saved is not None, f"unreadable event chart: {row['event_id']}")
        replay = scan.render_event(
            row,
            frame=frames[str(row["timeframe"])][str(row["symbol"])],
            total=total,
        )
        require(saved.shape == replay.shape, f"chart shape drift: {row['event_id']}")
        require(bool(np.array_equal(saved, replay)), f"chart pixel drift: {row['event_id']}")
        checked += 1
        if checked % 25 == 0 or checked == total:
            print(f"chart replay {checked}/{total}", flush=True)
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.output.resolve()
    summary = read_json(out / "summary.json")
    require(summary.get("experiment_id") == scan.EXPERIMENT_ID, "experiment identity drift")
    require(summary.get("weights_sha256") == scan.EXPECTED_WEIGHT_SHA256, "summary weight drift")
    require(scan.sha256_file(scan.WEIGHTS) == scan.EXPECTED_WEIGHT_SHA256, "local weight drift")
    require(summary.get("research_only") is True, "research-only flag drift")
    require(summary.get("production_eligible") is False, "production eligibility drift")
    require(summary.get("prior_4h_owner_rejection_preserved") is True, "4h rejection disclosure drift")
    require(summary.get("cross_timeframe_confidence_combined") is False, "cross-TF score was combined")
    for field in (
        "trained",
        "threshold_or_weight_changed",
        "promoted",
        "active_or_frozen_changed",
        "forward_state_changed",
        "deployed",
        "telegram_sent",
        "orders_placed",
    ):
        require(summary.get(field) is False, f"unsafe summary flag: {field}")

    prereg, gates = scan.load_preregistration(out / "preregistration.json")
    if summary.get("resumed_from_receipted_failure") is True:
        require(
            summary.get("original_prereg_source_commit") == prereg.get("source_commit"),
            "original preregistration commit drift",
        )
        require(
            summary.get("additional_market_read_during_recovery") is False,
            "recovery performed an additional market read",
        )
        recovery = read_json(out / "recovery_amendment.json")
        require(
            recovery.get("original_prereg_source_commit") == prereg.get("source_commit"),
            "recovery/preregistration binding drift",
        )
        require(
            recovery.get("corrected_builder_commit") == summary.get("source_commit"),
            "corrected builder commit drift",
        )
        require(
            recovery.get("model_gate_timeframe_universe_ranking_changed") is False,
            "recovery changed an analytical contract",
        )
        original_receipts = {
            "holdout_consumption_started.json": out
            / "recovery"
            / "original_holdout_consumption_started.json",
            "universe.json": out / "recovery" / "original_universe.json",
            "failure_receipt.json": out / "recovery" / "original_failure_receipt.json",
        }
        for name, path in original_receipts.items():
            require(path.is_file(), f"missing original recovery receipt: {name}")
            require(
                scan.sha256_file(path) == str(recovery["frozen_receipts"][name]),
                f"original recovery receipt SHA drift: {name}",
            )
    else:
        require(
            prereg.get("source_commit") == summary.get("source_commit"),
            "source commit drift",
        )
    frames, candle_files = verify_candles(out, summary)
    decisions, pixels, semantic = verify_semantic_decisions(out, frames, gates)
    events, event_count = verify_events_and_ranks(out, summary, frames, decisions)
    charts = verify_charts(out, events, frames)
    require((out / "summary_overview.png").is_file(), "missing summary overview")
    require((out / "gallery.html").is_file(), "missing gallery")
    require((out / "overview_all.png").is_file(), "missing combined overview")

    receipt = {
        "schema_version": 1,
        "experiment_id": scan.EXPERIMENT_ID,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS",
        "network_reads": 0,
        "model_inference_runs": 0,
        "candle_files_verified": candle_files,
        "candidate_input_pixel_replays": pixels,
        "semantic_decisions_recomputed": semantic,
        "events_and_ranks_rebuilt": event_count,
        "event_chart_pixel_replays": charts,
        "summary_sha256": scan.sha256_file(out / "summary.json"),
        "ranked_signals_sha256": scan.sha256_file(out / "ranked_signals.jsonl"),
        "gallery_sha256": scan.sha256_file(out / "gallery.html"),
        "research_only": True,
        "production_eligible": False,
    }
    scan.write_json(out / "verification.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
