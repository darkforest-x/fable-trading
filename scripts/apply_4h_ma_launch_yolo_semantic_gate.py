#!/usr/bin/env python3
"""Apply the frozen causal MA semantic gate to the frozen 4h YOLO scan.

The source is the immutable 2026-09-01 all-universe 15-day scan.  This script
does not run YOLO, fetch market data, tune thresholds, or change event spacing.
It replays each of the 1,764 structurally legal boxes on its exact W18/W19
input, computes ATR14 plus SMA/EMA 20/60/120 only through that input endpoint,
and retains the box only when every already-visible predicate from the frozen
15m positive-generation contract passes.

Columns read by the treatment are ``open/high/low/close`` and the derived
``atr/sma20/ema20/sma60/ema60/sma120/ema120`` values.  Core predicates read
``core_start_i..core_end_i``; ATR and mandatory confirmation stop at
``core_end_i + 2``; optional post3/post5 are evaluated only when no later than
``window_end_i``.  Future candles remain available solely to the physically
separate delivery gallery and never enter the gate decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts.scan_4h_ma_launch_yolo_half_month import daily_event_counts  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import (  # noqa: E402
    add_candidate_features,
)
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402
from yoyo.layers.l1_detection.semantic_gate import (  # noqa: E402
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)


EXPERIMENT_ID = "exp-4h-ma-launch-yolo-halfmonth-semantic-gate-20260902-v1"
DEFAULT_SOURCE = ROOT / "analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1"
DEFAULT_PREREG = ROOT / "experiments/active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = DEFAULT_PREREG.parent / "results/semantic_gate"
PARENT_GATE_PREREG = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1"
    / "preregistration.json"
)
HOLDOUT_CONSUMPTION_NUMBER = 7
SOURCE_HOLDOUT_CONSUMPTION_NUMBER = 6
EXPECTED_SOURCE_EXPERIMENT = "p1_4h_yolo_alluniverse_halfmonth_20260901_v1"


class FourHourSemanticGateError(RuntimeError):
    """Fail closed on preregistration, source, pairing, or causality drift."""


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 identity."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write deterministic UTF-8 JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write one stable JSON object per line."""

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _python_value(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def _candidate_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_row_i, raw in enumerate(frame.to_dict(orient="records")):
        row = {key: _python_value(value) for key, value in raw.items()}
        row["source_row_i"] = source_row_i
        row["candidate_id"] = f"structural_{source_row_i + 1:04d}"
        rows.append(row)
    return rows


def _two_sided_exact_sign_p(left_only: int, right_only: int) -> float:
    discordant = int(left_only) + int(right_only)
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, index)
        for index in range(min(int(left_only), int(right_only)) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def paired_binary_summary(
    actual: Sequence[bool], flipped: Sequence[bool]
) -> dict[str, Any]:
    """Summarize the preregistered same-candidate direction-flip null."""

    if len(actual) != len(flipped) or not actual:
        raise FourHourSemanticGateError("paired arrays must be equal and non-empty")
    actual_only = sum(bool(a) and not bool(b) for a, b in zip(actual, flipped))
    flipped_only = sum(bool(b) and not bool(a) for a, b in zip(actual, flipped))
    both = sum(bool(a) and bool(b) for a, b in zip(actual, flipped))
    neither = len(actual) - actual_only - flipped_only - both
    return {
        "pairs": len(actual),
        "actual_direction_positive": actual_only + both,
        "actual_direction_rate": (actual_only + both) / len(actual),
        "flipped_direction_positive": flipped_only + both,
        "flipped_direction_rate": (flipped_only + both) / len(actual),
        "actual_only": actual_only,
        "flipped_only": flipped_only,
        "both": both,
        "neither": neither,
        "paired_exact_two_sided_p": _two_sided_exact_sign_p(actual_only, flipped_only),
    }


def _verify_preregistration(
    prereg_path: Path, source: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    prereg = read_json(prereg_path)
    parent = read_json(PARENT_GATE_PREREG)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise FourHourSemanticGateError("experiment ID drifted")
    authorization = prereg.get("owner_authorization") or {}
    if not bool(authorization.get("holdout_read_authorized")):
        raise FourHourSemanticGateError("4h holdout access is not authorized")
    if int(prereg.get("holdout_consumption_number_for_checkpoint", -1)) != HOLDOUT_CONSUMPTION_NUMBER:
        raise FourHourSemanticGateError("holdout consumption number drifted")
    declared_source = ROOT / str(prereg["frozen_source"]["path"])
    if declared_source.resolve() != source.resolve():
        raise FourHourSemanticGateError("source path differs from preregistration")
    gates = dict(prereg["treatment"]["frozen_morphology_gate"])
    if gates != dict(parent["treatment"]["frozen_morphology_gate"]):
        raise FourHourSemanticGateError("semantic thresholds differ from accepted parent")
    return prereg, gates


def _verify_frozen_files(
    source: Path, prereg: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    for name, expected in prereg["frozen_source"]["artifacts"].items():
        path = source / str(expected["path"])
        if not path.is_file():
            raise FourHourSemanticGateError(f"missing frozen source artifact: {name}")
        actual = sha256_file(path)
        if actual != str(expected["sha256"]):
            raise FourHourSemanticGateError(
                f"frozen source hash drift for {name}: {actual}"
            )
    summary = read_json(source / "summary.json")
    verification = read_json(source / "verification.json")
    if summary.get("experiment_id") != EXPECTED_SOURCE_EXPERIMENT:
        raise FourHourSemanticGateError("source experiment identity drifted")
    if int(summary.get("holdout_consumption_number_for_checkpoint", -1)) != SOURCE_HOLDOUT_CONSUMPTION_NUMBER:
        raise FourHourSemanticGateError("source holdout number drifted")
    if summary.get("weights_sha256") != base.EXPECTED_WEIGHT_SHA256:
        raise FourHourSemanticGateError("source checkpoint drifted")
    if verification.get("verdict") != "PASS":
        raise FourHourSemanticGateError("source offline verification is not PASS")
    if int(verification.get("candidate_input_pixel_replays_passed", -1)) != int(
        summary["accepted_structural_boxes"]
    ):
        raise FourHourSemanticGateError("source candidate replay count drifted")
    return summary, verification


def _load_candidates(source: Path, summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    table = pd.read_csv(source / "accepted_candidates.csv")
    required = {
        "symbol",
        "window_len",
        "window_start_i",
        "window_end_i",
        "window_end_time",
        "input_pixel_sha256",
        "core_start_i",
        "core_end_i",
        "core_length_bars",
        "confirmation_bars",
        "class_id",
        "class_name",
        "confidence",
    }
    missing = sorted(required - set(table.columns))
    if missing:
        raise FourHourSemanticGateError(f"candidate table missing columns: {missing}")
    if len(table) != int(summary["accepted_structural_boxes"]):
        raise FourHourSemanticGateError("candidate count differs from source summary")
    numeric = table[
        [
            "window_len",
            "window_start_i",
            "window_end_i",
            "core_start_i",
            "core_end_i",
            "core_length_bars",
            "confirmation_bars",
            "class_id",
            "confidence",
        ]
    ].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise FourHourSemanticGateError("candidate table contains non-finite values")
    return _candidate_records(table)


def _load_and_audit_candles(
    source: Path,
    summary: Mapping[str, Any],
    candidate_symbols: set[str],
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    frames: dict[str, pd.DataFrame] = {}
    audits: list[dict[str, Any]] = []
    fetch_audits = {str(row["symbol"]): row for row in summary["fetch_audits"]}
    if len(fetch_audits) != int(summary["usable_symbols"]):
        raise FourHourSemanticGateError("source fetch-audit symbol count drifted")
    for symbol, source_audit in sorted(fetch_audits.items()):
        path = source / "candles" / f"{symbol}.csv"
        actual_hash = sha256_file(path)
        if actual_hash != str(source_audit["sha256"]):
            raise FourHourSemanticGateError(f"candle hash drifted: {symbol}")
        audit = {
            "symbol": symbol,
            "path": str(path.relative_to(ROOT)),
            "sha256": actual_hash,
            "rows": int(source_audit["rows"]),
            "ohlcv_materialized_for_gate": symbol in candidate_symbols,
        }
        if symbol in candidate_symbols:
            frame = pd.read_csv(path)
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            for column in ("open", "high", "low", "close", "volume"):
                frame[column] = pd.to_numeric(frame[column], errors="raise")
            values = frame[["open", "high", "low", "close", "volume"]].to_numpy(
                dtype=float
            )
            if not np.isfinite(values).all():
                raise FourHourSemanticGateError(f"non-finite candle value: {symbol}")
            if bool((frame[["open", "high", "low", "close"]] <= 0.0).any().any()):
                raise FourHourSemanticGateError(f"non-positive OHLC: {symbol}")
            gaps = frame["open_time"].diff().iloc[1:]
            if not bool((gaps == base.BAR_DELTA).all()):
                raise FourHourSemanticGateError(f"non-4h gap: {symbol}")
            if len(frame) != int(source_audit["rows"]):
                raise FourHourSemanticGateError(f"candle row count drifted: {symbol}")
            frames[symbol] = add_candidate_features(frame)
            audit["first_open_time"] = base.utc(frame.iloc[0]["open_time"]).isoformat()
            audit["last_open_time"] = base.utc(frame.iloc[-1]["open_time"]).isoformat()
        audits.append(audit)
    if set(frames) != candidate_symbols:
        raise FourHourSemanticGateError("candidate symbols are missing frozen candle files")
    return frames, audits


def _verify_control_replay(
    candidates: Sequence[Mapping[str, Any]], source_summary: Mapping[str, Any]
) -> list[dict[str, Any]]:
    replayed = base.deduplicate(candidates)
    source_events = [dict(row) for row in source_summary["signals"]]
    if len(replayed) != len(source_events):
        raise FourHourSemanticGateError("control event count failed to replay")
    keys = (
        "event_id",
        "symbol",
        "first_detection_bar_open_time",
        "last_detection_bar_open_time",
        "window_end_time",
        "core_start_i",
        "core_end_i",
        "class_id",
        "candidate_count",
    )
    for replay, source in zip(replayed, source_events):
        for key in keys:
            if str(replay[key]) != str(source[key]):
                raise FourHourSemanticGateError(
                    f"control event replay drift: {source['event_id']} {key}"
                )
        if not math.isclose(
            float(replay["confidence"]), float(source["confidence"]), abs_tol=1e-12
        ):
            raise FourHourSemanticGateError("control event confidence drifted")
        if not math.isclose(
            float(replay["event_peak_confidence"]),
            float(source["event_peak_confidence"]),
            abs_tol=1e-12,
        ):
            raise FourHourSemanticGateError("control event peak confidence drifted")
    return replayed


def _control_event_memberships(
    candidates: Sequence[Mapping[str, Any]], control_events: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, list[str]], dict[str, str]]:
    by_symbol: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_symbol[str(row["symbol"])].append(row)
    memberships: dict[str, list[str]] = {}
    owner: dict[str, str] = {}
    for event in control_events:
        symbol = str(event["symbol"])
        peak_time = base.utc(event["event_peak_bar_open_time"])
        peak_confidence = float(event["event_peak_confidence"])
        peak_rows = [
            row
            for row in by_symbol[symbol]
            if base.utc(row["window_end_time"]) == peak_time
            and math.isclose(float(row["confidence"]), peak_confidence, abs_tol=1e-12)
        ]
        if len(peak_rows) != 1:
            raise FourHourSemanticGateError(
                f"event peak is not uniquely recoverable: {event['event_id']}"
            )
        peak_end = int(peak_rows[0]["core_end_i"])
        members = [
            str(row["candidate_id"])
            for row in by_symbol[symbol]
            if abs(int(row["core_end_i"]) - peak_end) < base.EVENT_GAP_BARS
        ]
        memberships[str(event["event_id"])] = sorted(members)
        for candidate_id in members:
            if candidate_id in owner:
                raise FourHourSemanticGateError(
                    f"candidate belongs to two control events: {candidate_id}"
                )
            owner[candidate_id] = str(event["event_id"])
    expected = {str(row["candidate_id"]) for row in candidates}
    if set(owner) != expected:
        raise FourHourSemanticGateError(
            f"control event membership is not exhaustive: {len(owner)}/{len(expected)}"
        )
    return memberships, owner


def _evaluate_candidates(
    candidates: Sequence[Mapping[str, Any]],
    frames: Mapping[str, pd.DataFrame],
    gates: Mapping[str, Any],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for number, source in enumerate(candidates, 1):
        row = dict(source)
        symbol = str(row["symbol"])
        frame = frames[symbol]
        start = int(row["window_start_i"])
        observed = int(row["window_end_i"])
        core_start = int(row["core_start_i"])
        core_end = int(row["core_end_i"])
        if not 0 <= start <= core_start <= core_end + 2 <= observed < len(frame):
            raise FourHourSemanticGateError(f"invalid causal indices: {row['candidate_id']}")
        if base.utc(frame.iloc[observed]["open_time"]) != base.utc(row["window_end_time"]):
            raise FourHourSemanticGateError(f"endpoint time drift: {row['candidate_id']}")
        exact_input = frame.iloc[start : observed + 1]
        replay, _ = render_chart(exact_input, out_path=None)
        replay_hash = base.pixel_sha256(replay)
        if replay_hash != str(row["input_pixel_sha256"]):
            raise FourHourSemanticGateError(f"input pixel drift: {row['candidate_id']}")

        # Physically slice away review-only future before semantic calculation.
        causal = frame.iloc[: observed + 1]
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        flipped_direction = "SHORT" if direction == "LONG" else "LONG"
        features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=observed,
            direction=direction,
        )
        decision = evaluate_causal_semantic_gate(features, gates)
        flipped_features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=observed,
            direction=flipped_direction,
        )
        flipped_decision = evaluate_causal_semantic_gate(flipped_features, gates)
        feature_values = features.to_dict()
        flipped_values = flipped_features.to_dict()
        row.update(
            {
                "semantic_gate_pass": bool(decision.passed),
                "semantic_checks": decision.checks,
                "semantic_failed_checks": list(decision.failed_checks),
                "semantic_features": feature_values,
                "flipped_semantic_gate_pass": bool(flipped_decision.passed),
                "flipped_semantic_checks": flipped_decision.checks,
                "flipped_semantic_failed_checks": list(flipped_decision.failed_checks),
                "flipped_semantic_features": flipped_values,
                "causal_feature_last_i": observed,
                "review_future_bars_excluded_from_gate": len(frame) - observed - 1,
                "input_pixel_replay_sha256": replay_hash,
            }
        )
        output.append(row)
        if number % 200 == 0 or number == len(candidates):
            print(f"semantic gate {number}/{len(candidates)}", flush=True)
    return output


def _flatten_passed_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    flat = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "semantic_checks",
            "semantic_failed_checks",
            "semantic_features",
            "flipped_semantic_checks",
            "flipped_semantic_failed_checks",
            "flipped_semantic_features",
        }
    }
    for key, value in dict(row["semantic_features"]).items():
        flat[f"semantic_{key}"] = value
    flat["semantic_failed_checks"] = "|".join(row["semantic_failed_checks"])
    return flat


def _attach_market_tip(events: list[dict[str, Any]], frames: Mapping[str, pd.DataFrame]) -> None:
    for event in events:
        latest_open = base.utc(frames[str(event["symbol"])].iloc[-1]["open_time"])
        event["latest_market_bar_open_time"] = latest_open.isoformat()
        event["latest_market_bar_available_at"] = (latest_open + base.BAR_DELTA).isoformat()
        event["is_current_latest_bar"] = base.utc(event["window_end_time"]) == latest_open
        event["semantic_gate_applied"] = True
        event["semantic_gate_pass"] = True


def _build_event_pairing(
    control_events: Sequence[Mapping[str, Any]],
    memberships: Mapping[str, Sequence[str]],
    decisions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in control_events:
        event_id = str(event["event_id"])
        members = list(memberships[event_id])
        actual = [
            candidate_id
            for candidate_id in members
            if bool(decisions[candidate_id]["semantic_gate_pass"])
        ]
        flipped = [
            candidate_id
            for candidate_id in members
            if bool(decisions[candidate_id]["flipped_semantic_gate_pass"])
        ]
        rows.append(
            {
                "control_event_id": event_id,
                "symbol": str(event["symbol"]),
                "control_class_name": str(event["class_name"]),
                "first_available_at": str(event["first_available_at"]),
                "last_available_at": str(event["last_available_at"]),
                "control_candidates": len(members),
                "actual_pass_candidates": len(actual),
                "flipped_pass_candidates": len(flipped),
                "actual_event_survives": bool(actual),
                "flipped_event_survives": bool(flipped),
                "actual_candidate_ids": actual,
                "flipped_candidate_ids": flipped,
            }
        )
    return rows


def _qstats(values: Iterable[float]) -> dict[str, float | None]:
    data = np.asarray(list(values), dtype=float)
    if not len(data):
        return {key: None for key in ("min", "p25", "median", "mean", "p75", "max")}
    if not np.isfinite(data).all():
        raise FourHourSemanticGateError("non-finite summary metric")
    return {
        "min": float(data.min()),
        "p25": float(np.quantile(data, 0.25)),
        "median": float(np.median(data)),
        "mean": float(data.mean()),
        "p75": float(np.quantile(data, 0.75)),
        "max": float(data.max()),
    }


def _semantic_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    passed = [row for row in rows if bool(row["semantic_gate_pass"])]
    failures: Counter[str] = Counter()
    for row in rows:
        failures.update(str(value) for value in row["semantic_failed_checks"])
    feature_names = (
        "ma_envelope_atr",
        "ma_spread_end_atr",
        "minimum_close_to_ma_atr",
        "max_close_to_ma_envelope_atr",
        "max_body_to_ma_envelope_atr",
        "post2_progress_atr",
    )
    return {
        "structural_boxes": len(rows),
        "semantic_pass_boxes": len(passed),
        "semantic_rejected_boxes": len(rows) - len(passed),
        "semantic_pass_rate": len(passed) / len(rows),
        "candidate_reduction": 1.0 - len(passed) / len(rows),
        "gate_failure_counts_overlapping": dict(failures.most_common()),
        "pass_rate_by_class": {
            class_name: {
                "boxes": len(group),
                "passed": sum(bool(row["semantic_gate_pass"]) for row in group),
                "pass_rate": sum(bool(row["semantic_gate_pass"]) for row in group)
                / len(group),
            }
            for class_name in sorted({str(row["class_name"]) for row in rows})
            if (group := [row for row in rows if str(row["class_name"]) == class_name])
        },
        "pass_rate_by_confirmation_bars": {
            str(post): {
                "boxes": len(group),
                "passed": sum(bool(row["semantic_gate_pass"]) for row in group),
                "pass_rate": sum(bool(row["semantic_gate_pass"]) for row in group)
                / len(group),
            }
            for post in sorted({int(row["confirmation_bars"]) for row in rows})
            if (group := [row for row in rows if int(row["confirmation_bars"]) == post])
        },
        "feature_distributions": {
            name: {
                "all": _qstats(float(row["semantic_features"][name]) for row in rows),
                "passed": _qstats(
                    float(row["semantic_features"][name]) for row in passed
                ),
            }
            for name in feature_names
        },
    }


def _build_overview(
    path: Path,
    *,
    box_summary: Mapping[str, Any],
    event_summary: Mapping[str, Any],
    null_box: Mapping[str, Any],
    failures: Mapping[str, int],
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.2))
    colors = ("#6b7280", "#0f766e", "#b45309")
    axes[0].bar(
        ["Structural", "Semantic", "Flip null"],
        [
            int(box_summary["structural_boxes"]),
            int(box_summary["semantic_pass_boxes"]),
            int(null_box["flipped_direction_positive"]),
        ],
        color=colors,
    )
    axes[0].set_title("Box survival")
    axes[0].set_ylabel("boxes")
    axes[0].grid(axis="y", alpha=0.2)

    axes[1].bar(
        ["Control", "Survived", "New dedup"],
        [
            int(event_summary["control_events"]),
            int(event_summary["control_events_surviving"]),
            int(event_summary["treatment_deduplicated_events"]),
        ],
        color=colors,
    )
    axes[1].set_title("Event-level effect")
    axes[1].set_ylabel("events")
    axes[1].grid(axis="y", alpha=0.2)

    top = list(failures.items())[:8]
    labels = [item[0] for item in reversed(top)]
    values = [item[1] for item in reversed(top)]
    axes[2].barh(labels, values, color="#c2410c")
    axes[2].set_title("Rejected predicates (overlap)")
    axes[2].set_xlabel("failed boxes")
    axes[2].grid(axis="x", alpha=0.2)
    fig.suptitle(
        "4h YOLO + frozen causal semantic gate | holdout use #7 | OOD research only",
        fontsize=14,
        fontweight="bold",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    source = args.source.resolve()
    prereg_path = args.prereg.resolve()
    out = args.out.resolve()
    building = out.with_name(out.name + ".building")
    if out.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite semantic-gate output: {out}")
    started = time.perf_counter()

    prereg, gates = _verify_preregistration(prereg_path, source)
    source_summary, source_verification = _verify_frozen_files(source, prereg)
    candidates = _load_candidates(source, source_summary)
    control_events = _verify_control_replay(candidates, source_summary)
    memberships, control_owner = _control_event_memberships(candidates, control_events)
    candidate_symbols = {str(row["symbol"]) for row in candidates}
    frames, candle_audits = _load_and_audit_candles(
        source, source_summary, candidate_symbols
    )
    decisions = _evaluate_candidates(candidates, frames, gates)
    decision_by_id = {str(row["candidate_id"]): row for row in decisions}
    event_pairing = _build_event_pairing(
        control_events, memberships, decision_by_id
    )

    passed = [row for row in decisions if bool(row["semantic_gate_pass"])]
    passed_flat = [_flatten_passed_candidate(row) for row in passed]
    treatment_events = base.deduplicate(passed_flat)
    _attach_market_tip(treatment_events, frames)
    for event in treatment_events:
        event["control_event_id"] = control_owner[str(event["candidate_id"])]

    null_box = paired_binary_summary(
        [bool(row["semantic_gate_pass"]) for row in decisions],
        [bool(row["flipped_semantic_gate_pass"]) for row in decisions],
    )
    null_event = paired_binary_summary(
        [bool(row["actual_event_survives"]) for row in event_pairing],
        [bool(row["flipped_event_survives"]) for row in event_pairing],
    )
    box_summary = _semantic_summary(decisions)
    event_sides = Counter(str(event["class_name"]) for event in treatment_events)
    symbol_counts = Counter(str(event["symbol"]) for event in treatment_events)
    surviving_control = sum(bool(row["actual_event_survives"]) for row in event_pairing)
    event_summary = {
        "control_events": len(control_events),
        "control_events_surviving": surviving_control,
        "control_event_retention": surviving_control / len(control_events),
        "treatment_deduplicated_events": len(treatment_events),
        "long_events": int(event_sides["dense_long"]),
        "short_events": int(event_sides["dense_short"]),
        "symbols_with_events": len(symbol_counts),
        "current_latest_bar_events": sum(
            bool(event["is_current_latest_bar"]) for event in treatment_events
        ),
        "daily_event_onsets_cst": daily_event_counts(treatment_events),
        "events_per_symbol": dict(sorted(symbol_counts.items())),
    }
    direction_null_pass = (
        int(null_box["actual_direction_positive"])
        > int(null_box["flipped_direction_positive"])
        and float(null_box["paired_exact_two_sided_p"])
        < float(prereg["decision_rule"]["direction_flip_p_max"])
    )

    building.mkdir(parents=True)
    try:
        shutil.copy2(prereg_path, building / "preregistration.json")
        shutil.copytree(source / "candles", building / "candles")
        write_jsonl(building / "semantic_boxes.jsonl", decisions)
        write_jsonl(building / "candle_audits.jsonl", candle_audits)
        write_jsonl(building / "event_pairing.jsonl", event_pairing)
        pd.DataFrame(passed_flat).to_csv(building / "accepted_candidates.csv", index=False)
        pd.DataFrame(treatment_events).to_csv(building / "signals.csv", index=False)
        overview = building / "paired_gate_overview.png"
        _build_overview(
            overview,
            box_summary=box_summary,
            event_summary=event_summary,
            null_box=null_box,
            failures=box_summary["gate_failure_counts_overlapping"],
        )

        summary = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "experiment_id": EXPERIMENT_ID,
            "verdict": "PASS_EXECUTION",
            "semantic_direction_coherence_verdict": (
                "PASS" if direction_null_pass else "FAIL"
            ),
            "research_only": True,
            "out_of_distribution": True,
            "source_timeframe": "15m",
            "inference_timeframe": "4h",
            "semantic_gate_applied": True,
            "single_changed_variable": prereg["single_variable"],
            "source_scan": str(source.relative_to(ROOT)),
            "source_experiment_id": str(source_summary["experiment_id"]),
            "source_holdout_consumption_number_for_checkpoint": SOURCE_HOLDOUT_CONSUMPTION_NUMBER,
            "holdout_consumed": True,
            "holdout_consumption_number_for_checkpoint": HOLDOUT_CONSUMPTION_NUMBER,
            "owner_authorization": prereg["owner_authorization"],
            "weights_sha256": str(source_summary["weights_sha256"]),
            "confidence": float(source_summary["detector_contract"]["confidence"]),
            "nms_iou": float(source_summary["detector_contract"]["nms_iou"]),
            "window_lengths": source_summary["detector_contract"]["window_lengths"],
            "core_lengths": source_summary["detector_contract"]["core_lengths"],
            "confirmation_bars": source_summary["detector_contract"]["confirmation_bars"],
            "same_symbol_gap_bars": int(
                source_summary["detector_contract"]["same_symbol_gap_bars"]
            ),
            "lookback_confirmed_4h_bars": int(
                source_summary["lookback_confirmed_4h_bars"]
            ),
            "scan_first_endpoint_available_at": str(
                source_summary["scan_first_endpoint_available_at"]
            ),
            "scan_last_endpoint_available_at": str(
                source_summary["scan_last_endpoint_available_at"]
            ),
            "universe_symbols": int(source_summary["universe_symbols"]),
            "usable_symbols": int(source_summary["usable_symbols"]),
            "candidate_symbols_materialized": len(candidate_symbols),
            "candle_files_hash_verified": len(candle_audits),
            "holdout_ohlcv_rows_materialized": sum(
                int(row["rows"])
                for row in candle_audits
                if bool(row["ohlcv_materialized_for_gate"])
            ),
            "model_inference": 0,
            "network_reads": 0,
            "threshold_grid_runs": 0,
            "threshold_or_weight_changed": False,
            "future_candles_used_by_gate": 0,
            "input_pixel_replays_passed": len(decisions),
            "source_verification": source_verification,
            "frozen_morphology_gate": gates,
            "box_summary": box_summary,
            "event_summary": event_summary,
            "direction_flip_null": {
                "hypothesis": (
                    "Keep every 4h candidate, box geometry, confidence and class fixed; "
                    "invert only LONG/SHORT inside the semantic calculation."
                ),
                "box_level": null_box,
                "control_event_level": null_event,
                "preregistered_p_max": float(
                    prereg["decision_rule"]["direction_flip_p_max"]
                ),
            },
            "signals": treatment_events,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "trained": False,
            "promoted": False,
            "deployed": False,
            "active_or_frozen_changed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "training_eligible": False,
            "production_eligible": False,
        }
        write_json(building / "summary.json", summary)
        receipt_paths = (
            "summary.json",
            "semantic_boxes.jsonl",
            "candle_audits.jsonl",
            "event_pairing.jsonl",
            "accepted_candidates.csv",
            "signals.csv",
            "paired_gate_overview.png",
            "preregistration.json",
        )
        receipt = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "verdict": "PASS",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": {
                name: {
                    "sha256": sha256_file(building / name),
                    "size_bytes": (building / name).stat().st_size,
                }
                for name in receipt_paths
            },
        }
        write_json(building / "gate_receipt.json", receipt)
        building.replace(out)
    except Exception:
        if building.exists():
            shutil.rmtree(building)
        raise

    print(
        json.dumps(
            {
                "output": str(out),
                "control_boxes": len(decisions),
                "semantic_boxes": len(passed),
                "control_events": len(control_events),
                "surviving_control_events": surviving_control,
                "treatment_events": len(treatment_events),
                "direction_flip_null": null_box,
                "holdout_consumption_number_for_checkpoint": HOLDOUT_CONSUMPTION_NUMBER,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
