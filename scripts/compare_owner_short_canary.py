#!/usr/bin/env python3
"""Compare two Owner-short scans on one frozen causal canary snapshot.

The comparator refuses to score scans whose time range, symbols, exposure
count, inference thresholds, window lengths, deduplication rule, or evaluation
scope differ.  It reads detections only; no OHLCV, future outcome, holdout, or
trading configuration is opened here.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


CONTRACT_FIELDS = (
    "protocol",
    "symbols",
    "scanned_symbols",
    "stale_symbols",
    "latest_bar",
    "replay_start_exclusive",
    "hours",
    "window_lengths",
    "confidence",
    "nms_iou",
    "event_gap_bars",
    "bar_endpoints",
    "window_exposures",
    "evaluation_scope",
    "holdout_use_number",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def distribution(values: Iterable[float]) -> dict[str, float | None]:
    array = np.asarray(list(values), dtype=float)
    if not len(array):
        return {"min": None, "median": None, "p90": None, "max": None}
    return {
        "min": float(array.min()),
        "median": float(np.median(array)),
        "p90": float(np.quantile(array, 0.90)),
        "max": float(array.max()),
    }


def validate_contract(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    mismatches = {
        field: {"r1": left.get(field), "r2": right.get(field)}
        for field in CONTRACT_FIELDS
        if left.get(field) != right.get(field)
    }
    if mismatches:
        raise ValueError(f"canary contract mismatch: {json.dumps(mismatches, ensure_ascii=False)}")
    if left.get("evaluation_scope") != "preholdout_postval_canary":
        raise ValueError("comparison is not a pre-holdout post-val canary")
    if int(left.get("holdout_use_number", -1)) != 0:
        raise ValueError("canary reports a holdout read")
    if left.get("stale_symbols"):
        raise ValueError("canary contains stale symbols")
    return {field: left.get(field) for field in CONTRACT_FIELDS}


def scan_metrics(summary: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    event_counts = Counter(str(row["symbol"]) for row in events)
    hours = float(summary["hours"])
    exposures = int(summary["window_exposures"])
    endpoints = int(summary["bar_endpoints"])
    raw = int(summary["raw_detections"])
    dedup = int(summary["deduplicated_events"])
    if dedup != len(events):
        raise ValueError(f"event file/count mismatch: summary={dedup} file={len(events)}")
    return {
        "weights_sha256": summary["weights_sha256"],
        "raw_detections": raw,
        "raw_per_1000_window_exposures": raw / exposures * 1000,
        "deduplicated_events": dedup,
        "events_per_1000_bar_endpoints": dedup / endpoints * 1000,
        "events_per_day_all_symbols": dedup / hours * 24,
        "triggered_symbols": len(event_counts),
        "triggered_symbol_share": len(event_counts) / int(summary["symbols"]),
        "events_per_triggered_symbol": distribution(event_counts.values()),
        "predicted_core_4_to_7_share": (
            sum(4 <= int(row["predicted_core_bars"]) <= 7 for row in events) / dedup
            if dedup
            else None
        ),
        "decision_delay_3_to_5_share": (
            sum(3 <= int(row["decision_delay_bars"]) <= 5 for row in events) / dedup
            if dedup
            else None
        ),
        "first_confidence": distribution(float(row["conf"]) for row in events),
        "peak_confidence": distribution(float(row["event_conf_max"]) for row in events),
    }


def relative_change(new: float, old: float) -> float | None:
    return (new - old) / old if old else None


def cross_model_event_overlap(
    r1_events: list[dict[str, Any]],
    r2_events: list[dict[str, Any]],
    *,
    gap_bars: int,
) -> dict[str, Any]:
    """Pair same-symbol events by the frozen core-midpoint dedupe tolerance."""

    candidates: list[tuple[float, int, str, str, int, int]] = []
    for r1_index, r1 in enumerate(r1_events):
        for r2_index, r2 in enumerate(r2_events):
            if str(r1["symbol"]) != str(r2["symbol"]):
                continue
            core_distance = abs(float(r1["core_mid_i"]) - float(r2["core_mid_i"]))
            if core_distance > gap_bars:
                continue
            decision_distance = abs(int(r1["decision_i"]) - int(r2["decision_i"]))
            candidates.append(
                (
                    core_distance,
                    decision_distance,
                    str(r1.get("event_id", r1_index)),
                    str(r2.get("event_id", r2_index)),
                    r1_index,
                    r2_index,
                )
            )
    used_r1: set[int] = set()
    used_r2: set[int] = set()
    matched_core_distances: list[float] = []
    matched_decision_distances: list[float] = []
    for core_distance, decision_distance, _r1_id, _r2_id, r1_index, r2_index in sorted(
        candidates
    ):
        if r1_index in used_r1 or r2_index in used_r2:
            continue
        used_r1.add(r1_index)
        used_r2.add(r2_index)
        matched_core_distances.append(core_distance)
        matched_decision_distances.append(float(decision_distance))
    matched = len(used_r1)
    r1_only = len(r1_events) - matched
    r2_only = len(r2_events) - matched
    return {
        "match_rule": f"same symbol and abs(core_mid_i delta) <= {gap_bars} bars",
        "matched_events": matched,
        "r1_only_events": r1_only,
        "r2_only_events": r2_only,
        "r2_retained_share": matched / len(r2_events) if r2_events else None,
        "union_jaccard": matched / (matched + r1_only + r2_only)
        if matched + r1_only + r2_only
        else None,
        "matched_core_mid_distance_bars": distribution(matched_core_distances),
        "matched_decision_distance_bars": distribution(matched_decision_distances),
    }


def build_comparison(r1_dir: Path, r2_dir: Path, snapshot_summary: Path) -> dict[str, Any]:
    r1_summary = read_json(r1_dir / "scan_summary.json")
    r2_summary = read_json(r2_dir / "scan_summary.json")
    contract = validate_contract(r1_summary, r2_summary)
    snapshot = read_json(snapshot_summary)
    if snapshot.get("evaluation_scope") != "preholdout_postval_canary":
        raise ValueError("snapshot evaluation scope drift")
    if int(snapshot.get("holdout_rows_materialized", -1)) != 0:
        raise ValueError("snapshot materialized holdout rows")
    if snapshot.get("max_materialized_time") != contract["latest_bar"]:
        raise ValueError("snapshot and scan endpoint drift")
    r1_events = read_jsonl(r1_dir / "events.jsonl")
    r2_events = read_jsonl(r2_dir / "events.jsonl")
    r1 = scan_metrics(r1_summary, r1_events)
    r2 = scan_metrics(r2_summary, r2_events)
    delta_keys = (
        "raw_detections",
        "raw_per_1000_window_exposures",
        "deduplicated_events",
        "events_per_1000_bar_endpoints",
        "events_per_day_all_symbols",
        "triggered_symbols",
    )
    delta = {
        key: {
            "absolute": float(r2[key]) - float(r1[key]),
            "relative": relative_change(float(r2[key]), float(r1[key])),
        }
        for key in delta_keys
    }
    return {
        "protocol": "owner_short_gold_center_r1_r2_canary_comparison_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": "preholdout_postval_canary",
        "holdout_read": False,
        "production_eligible": False,
        "contract": contract,
        "snapshot": {
            key: snapshot.get(key)
            for key in (
                "manifest_sha256",
                "requested_symbols",
                "usable_symbols",
                "snapshot_end",
                "max_materialized_time",
                "holdout_start",
                "holdout_rows_materialized",
                "canonical_data_written",
            )
        },
        "r1": r1,
        "r2": r2,
        "r2_minus_r1": delta,
        "cross_model_event_overlap": cross_model_event_overlap(
            r1_events,
            r2_events,
            gap_bars=int(contract["event_gap_bars"]),
        ),
        "verdict_note": (
            "Density change is a necessary diagnostic, not an event-precision or "
            "production verdict; no confidence threshold was tuned on this block."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r1-dir", type=Path, required=True)
    parser.add_argument("--r2-dir", type=Path, required=True)
    parser.add_argument("--snapshot-summary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    receipt = build_comparison(args.r1_dir, args.r2_dir, args.snapshot_summary)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
