"""Preserve the historical Stage-A gap snapshot and its later correction.

Inputs are restricted to the pre-holdout Stage-A manifests, the completed 3060
training artifacts, and the already-produced Stage-A validation diagnostic.
The original report temporarily treated center 40%--60% and 8--12 post-core
bars as the target.  Owner later corrected that assumption: box position must
not be fixed, post-core context is 0--10 bars, ten is only the maximum delay,
and semantic platform quality is the label.  The old strict-joint calculation
is retained as a historical diagnostic and is explicitly marked superseded.

The script never opens market holdout data and does not select a production
threshold.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "datasets/local_signal_v2_stagea_randomcrop_v1/w20_manifest.json"
DEFAULT_SUMMARY = ROOT / "datasets/local_signal_v2_stagea_randomcrop_v1/stagea_summary.json"
DEFAULT_POSITION_EVAL = ROOT / "analysis/output/p1_local_signal_v2_stagea_position_eval_20260811.json"
DEFAULT_RESULTS = ROOT / "analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/results.csv"
DEFAULT_TRAIN_LOG = ROOT / "analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/train.log"
DEFAULT_WEIGHTS = ROOT / "analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt"
DEFAULT_OUTPUT = ROOT / "analysis/output/p1_local_signal_v2_stagea_gap_to_owner_target_20260811.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quantile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _count(rows: list[dict[str, Any]], predicate: Any) -> dict[str, Any]:
    matching = [row for row in rows if predicate(row)]
    return {
        "count": len(matching),
        "share": len(matching) / len(rows),
        "train": sum(row["split"] == "train" for row in matching),
        "val": sum(row["split"] == "val" for row in matching),
    }


def _parse_final_validation(train_log: Path) -> dict[str, float]:
    matches = re.findall(r"results_dict: (\{[^\n]+\})", train_log.read_text(errors="replace"))
    if not matches:
        raise ValueError(f"results_dict not found in {train_log}")
    result = ast.literal_eval(matches[-1])
    return {key: float(value) for key, value in result.items()}


def _parse_best_epoch(results_csv: Path) -> dict[str, float | int]:
    with results_csv.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    metric = "metrics/mAP50-95(B)"
    best = max(rows, key=lambda row: float(row[metric]))
    return {
        "epoch": int(best["epoch"]),
        "elapsed_seconds": float(rows[-1]["time"]),
        "precision": float(best["metrics/precision(B)"]),
        "recall": float(best["metrics/recall(B)"]),
        "map50": float(best["metrics/mAP50(B)"]),
        "map50_95": float(best[metric]),
    }


def analyze(
    manifest_path: Path,
    summary_path: Path,
    position_eval_path: Path,
    results_csv: Path,
    train_log: Path,
    weights: Path,
) -> dict[str, Any]:
    raw_rows = json.loads(manifest_path.read_text())
    summary = json.loads(summary_path.read_text())
    position_eval = json.loads(position_eval_path.read_text())

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        first, last = raw["small_local"]
        row = dict(raw)
        row["box_bars"] = last - first + 1
        row["box_center_real_ratio"] = ((first + last) / 2) / (raw["win_len"] - 1)
        row["post_box_real_bars"] = raw["win_len"] - 1 - last
        rows.append(row)

    exact_target = lambda row: (
        20 <= row["win_len"] <= 30
        and 4 <= row["box_bars"] <= 7
        and 0.40 <= row["box_center_real_ratio"] <= 0.60
        and 8 <= row["post_box_real_bars"] <= 12
    )
    exact_target_w25 = lambda row: exact_target(row) and row["win_len"] >= 25

    threshold_by_value = {
        round(float(item["threshold"]), 2): item for item in position_eval["thresholds"]
    }
    selected_thresholds = []
    for threshold in (0.05, 0.10, 0.15, 0.20, 0.35):
        item = threshold_by_value[threshold]
        selected_thresholds.append(
            {
                "threshold": threshold,
                "event_precision": item["event_precision"],
                "event_recall": item["event_recall"],
                "event_f1": item["event_f1"],
                "easy_negative_fire_rate": item["easy_negative_fire_rate"],
                "false_positive_boxes": item["false_positive_boxes"],
            }
        )

    centers = [row["box_center_real_ratio"] for row in rows]
    post_bars = [row["post_box_real_bars"] for row in rows]
    box_lengths = Counter(row["box_bars"] for row in rows)
    counts = summary["counts"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "historical_stage_a_gap_snapshot_superseded",
        "superseded_by": "analysis/p1_owner_eth_perfect_platform_semantic_audit_20260811.md",
        "superseded_assumption": {
            "input_real_bars": [20, 30],
            "core_box_bars": [4, 7],
            "preferred_box_center_real_ratio": [0.40, 0.60],
            "preferred_post_core_real_bars": [8, 12],
            "status": "revoked_by_owner_20260811",
        },
        "current_owner_contract": {
            "semantic_target": "owner-perfect-platform morphology",
            "input_real_bars": [20, 30],
            "core_box_bars": [4, 7],
            "post_core_real_bars": [0, 10],
            "post_core_interpretation": "ten is the maximum tolerated delay, not the target",
            "box_position": "distributed naturally; not fixed to exact-right or exact-middle",
            "later_move_defines_positive": False,
        },
        "sources": {
            "manifest": str(manifest_path.relative_to(ROOT)),
            "dataset_summary": str(summary_path.relative_to(ROOT)),
            "position_eval": str(position_eval_path.relative_to(ROOT)),
            "results_csv": str(results_csv.relative_to(ROOT)),
            "train_log": str(train_log.relative_to(ROOT)),
            "weights": str(weights.relative_to(ROOT)),
            "weights_sha256": _sha256(weights),
        },
        "data_discipline": {
            "holdout_read": False,
            "production_eligible": False,
            "split_rule": summary["split_rule"],
            "purge_bars": summary["purge_bars"],
            "full_window_before_holdout": summary["full_window_before_holdout"],
            "validation_used_for_early_stopping": position_eval["validation_used_for_early_stopping"],
            "independent_acceptance_set": position_eval["independent_acceptance_set"],
        },
        "dataset": {
            "positive": len(rows),
            "easy_negative": summary["n_negative_manifest"],
            "hard_negative": 0,
            "train_total": counts["train_positive"] + counts["train_negative"],
            "val_total": counts["val_positive"] + counts["val_negative"],
            "positive_rate": 0.5,
        },
        "geometry_alignment": {
            "window_20_30": _count(rows, lambda row: 20 <= row["win_len"] <= 30),
            "window_25_30": _count(rows, lambda row: 25 <= row["win_len"] <= 30),
            "box_4_7": _count(rows, lambda row: 4 <= row["box_bars"] <= 7),
            "box_length_counts": {str(key): box_lengths[key] for key in sorted(box_lengths)},
            "center_40_60": _count(rows, lambda row: 0.40 <= row["box_center_real_ratio"] <= 0.60),
            "center_45_55": _count(rows, lambda row: 0.45 <= row["box_center_real_ratio"] <= 0.55),
            "post_core_8_12": _count(rows, lambda row: 8 <= row["post_box_real_bars"] <= 12),
            "post_core_7_13": _count(rows, lambda row: 7 <= row["post_box_real_bars"] <= 13),
            "strict_joint_20_30": _count(rows, exact_target),
            "strict_joint_25_30": _count(rows, exact_target_w25),
            "box_center_real_ratio_quantiles": {
                "min": min(centers),
                "p10": _quantile(centers, 0.10),
                "p25": _quantile(centers, 0.25),
                "p50": _quantile(centers, 0.50),
                "p75": _quantile(centers, 0.75),
                "p90": _quantile(centers, 0.90),
                "max": max(centers),
            },
            "post_core_real_bars_quantiles": {
                "min": min(post_bars),
                "p10": _quantile(post_bars, 0.10),
                "p25": _quantile(post_bars, 0.25),
                "p50": _quantile(post_bars, 0.50),
                "p75": _quantile(post_bars, 0.75),
                "p90": _quantile(post_bars, 0.90),
                "max": max(post_bars),
            },
        },
        "training": {
            "recipe": "YOLO11s, imgsz=960, batch=8, seed=0, epochs=60, patience=15, forbidden augmentations disabled",
            "best_epoch_csv": _parse_best_epoch(results_csv),
            "final_revalidation": _parse_final_validation(train_log),
            "early_stopped_after_epoch": 53,
        },
        "detector_diagnostic": {
            "matching_iou": position_eval["matching_iou"],
            "thresholds": selected_thresholds,
            "position_invariance": position_eval["position_diagnostic"],
            "interpretation": "Position shortcut is substantially reduced, but selectivity is poor and cannot be repaired by threshold raising alone.",
        },
        "gap_diagnosis": [
            {
                "priority": 1,
                "gap": "Positive semantics are inherited from legacy pad200 anchors, not yet owner-reviewed against the new ETH perfect-signal rubric.",
            },
            {
                "priority": 2,
                "gap": "The dataset contains zero hard negatives; easy backgrounds do not teach the detector to reject look-alike morphology.",
            },
            {
                "priority": 3,
                "gap": "Historical only: 25.36% met the now-revoked centered-plus-8-to-12-post-bars assumption; this is not a current target-alignment metric.",
            },
            {
                "priority": 4,
                "gap": "Box lengths cover 4 and 5 bars only, not the full owner-allowed 4-to-7 range.",
            },
        ],
        "asset_decision": {
            "keep": [
                "Stage-A positive/easy-negative manifests and time split",
                "Stage-A best.pt as the fine-tuning initialization",
                "3060 logs, results.csv, and position diagnostic",
                "Existing P2 hard-negative candidate ledgers as mining inputs after re-rendering",
                "Rejected causal/fixed-right arms as historical evidence and comparison artifacts",
            ],
            "do_not_claim": [
                "Do not call the Stage-A validation an independent acceptance result.",
                "Do not promote, deploy, or treat the delayed morphology detector as a fresh live signal.",
                "Do not solve low precision by threshold selection on the same validation set.",
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--position-eval", type=Path, default=DEFAULT_POSITION_EVAL)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--train-log", type=Path, default=DEFAULT_TRAIN_LOG)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result = analyze(
        args.manifest,
        args.summary,
        args.position_eval,
        args.results,
        args.train_log,
        args.weights,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
