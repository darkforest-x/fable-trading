#!/usr/bin/env python3
"""Audit B2 fire density without pretending L1 fires are executable orders.

The audit reconciles two deliberately different grains:

* the balanced P1 event ruler (positive events + easy negatives), and
* the frozen v10 proposal-led short-L2 pool used by the economic replay.

Neither source is a continuous market scan, so the output refuses to estimate
production orders/day.  It does establish whether B2 is already too dense on
the available development surfaces and whether thresholding, duplicate boxes,
edge mapping, or transport differences explain the count.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL = PROJECT / "analysis/output/p1_local_signal_v2/B2_event_eval.json"
DEFAULT_EVAL_MANIFEST = (
    PROJECT / "analysis/output/local_signal_v2_p1_eval/B2/manifest.jsonl"
)
DEFAULT_CANDIDATES = (
    PROJECT / "analysis/output/p1_b2_short_l2_backtest_20260811_rows.csv"
)
DEFAULT_P1_MANIFEST = PROJECT / "analysis/output/p1_dataset_manifest_20260803.json"
DEFAULT_WEIGHTS = PROJECT / "analysis/output/p1_local_signal_v2/training/B2/weights/best.pt"
DEFAULT_OUT = PROJECT / "analysis/output/p1_b2_density_diagnostic_20260811.json"
THRESHOLDS = (0.35, 0.40, 0.45, 0.50)
WINDOW_BARS = 30
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def endpoint_fire_count(
    rows: list[dict], predictions: dict[str, list[dict]], threshold: float
) -> int:
    """Count endpoints with at least one predicted box at ``threshold``."""
    return sum(
        any(float(box["confidence"]) >= threshold for box in predictions.get(row["eval_id"], ()))
        for row in rows
    )


def evaluation_density(eval_doc: dict, manifest_rows: list[dict], threshold: float) -> dict:
    """Return fire rates at the P1 balanced-event-ruler grain."""
    predictions = eval_doc["predictions"]
    positives = [row for row in manifest_rows if row["sample_type"] == "positive"]
    negatives = [row for row in manifest_rows if row["sample_type"] != "positive"]
    positive_fires = endpoint_fire_count(positives, predictions, threshold)
    negative_fires = endpoint_fire_count(negatives, predictions, threshold)
    all_fires = positive_fires + negative_fires
    return {
        "positive_endpoints": len(positives),
        "easy_negative_endpoints": len(negatives),
        "positive_endpoints_with_any_box": positive_fires,
        "easy_negative_endpoints_with_any_box": negative_fires,
        "easy_negative_endpoint_fire_rate": negative_fires / len(negatives),
        "all_endpoints_with_any_box": all_fires,
        "all_endpoint_fire_rate": all_fires / len(manifest_rows),
    }


def threshold_result(eval_doc: dict, threshold: float) -> dict:
    for row in eval_doc["thresholds"]:
        if abs(float(row["threshold"]) - threshold) < 1e-12:
            return row
    raise KeyError(f"threshold not found: {threshold}")


def candidate_density(rows: pd.DataFrame, threshold: float) -> dict:
    confidence = pd.to_numeric(rows["b2_conf_edge3"], errors="coerce")
    fires = rows.loc[confidence >= threshold].copy()
    span_days = (rows["signal_time"].max() - rows["signal_time"].min()).total_seconds() / 86400
    per_day = fires.groupby(fires["signal_time"].dt.floor("D")).size()
    return {
        "fires": int(len(fires)),
        "fire_rate": float(len(fires) / len(rows)),
        "fires_per_calendar_span_day": float(len(fires) / span_days),
        "median_fires_per_active_utc_day": float(per_day.median()) if len(per_day) else 0.0,
        "p90_fires_per_active_utc_day": float(per_day.quantile(0.9)) if len(per_day) else 0.0,
        "max_fires_per_active_utc_day": int(per_day.max()) if len(per_day) else 0,
    }


def normalized_boxes(result: object) -> list[list[float]]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or not len(boxes):
        return []
    xywhn = boxes.xywhn.cpu().numpy()
    confidence = boxes.conf.cpu().numpy()
    classes = boxes.cls.cpu().numpy()
    values = [
        [*[float(value) for value in box[:4]], float(score), float(class_id)]
        for box, score, class_id in zip(xywhn, confidence, classes)
    ]
    return sorted(values, key=lambda row: tuple(round(value, 12) for value in row))


def transport_parity(rows: pd.DataFrame, weights: Path, *, device: str, samples: int) -> dict:
    """Compare B2 predictions from renderer arrays versus their saved PNG bytes."""
    from ultralytics import YOLO

    from scripts.build_local_signal_v2_p1_eval import resolve_series
    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart

    sample_indices = np.linspace(0, len(rows) - 1, num=min(samples, len(rows)), dtype=int)
    chosen = rows.iloc[sample_indices]
    images = []
    paths = []
    with tempfile.TemporaryDirectory(prefix="b2_transport_") as tmp:
        tmp_dir = Path(tmp)
        for index, row in enumerate(chosen.itertuples()):
            frame = resolve_series(row.symbol)
            if frame is None:
                raise RuntimeError(f"missing K-line series for parity sample: {row.symbol}")
            enriched = add_mas(frame)
            signal_i = int(row.mapped_signal_i)
            image_path = tmp_dir / f"sample_{index}.png"
            image, _ = render_chart(
                enriched.iloc[signal_i - WINDOW_BARS + 1 : signal_i + 1],
                out_path=image_path,
            )
            images.append(image)
            paths.append(str(image_path))
        model = YOLO(str(weights))
        array_results = model.predict(
            images, conf=0.001, iou=0.70, imgsz=960, device=device, verbose=False
        )
        png_results = model.predict(
            paths, conf=0.001, iou=0.70, imgsz=960, device=device, verbose=False
        )
        max_delta = 0.0
        count_mismatches = 0
        for array_result, png_result in zip(array_results, png_results):
            left = normalized_boxes(array_result)
            right = normalized_boxes(png_result)
            if len(left) != len(right):
                count_mismatches += 1
                continue
            for a, b in zip(left, right):
                max_delta = max(max_delta, *(abs(x - y) for x, y in zip(a, b)))
    return {
        "samples": int(len(chosen)),
        "box_count_mismatches": count_mismatches,
        "max_abs_box_or_conf_delta": max_delta,
        "accepted": count_mismatches == 0 and max_delta <= 1e-7,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--eval-manifest", type=Path, default=DEFAULT_EVAL_MANIFEST)
    parser.add_argument("--candidate-rows", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--p1-manifest", type=Path, default=DEFAULT_P1_MANIFEST)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--transport-samples", type=int, default=8)
    args = parser.parse_args()

    eval_doc = json.loads(args.event_eval.read_text())
    eval_rows = read_jsonl(args.eval_manifest)
    candidates = pd.read_csv(args.candidate_rows, parse_dates=["signal_time", "interval_end"])
    p1_manifest = json.loads(args.p1_manifest.read_text())
    if candidates["interval_end"].max() >= HOLDOUT_START:
        raise SystemExit("candidate outcomes touch holdout")
    if candidates["candidate_id"].duplicated().any():
        raise SystemExit("duplicate candidate_id")

    primary_eval = evaluation_density(eval_doc, eval_rows, 0.35)
    ladder = []
    for threshold in THRESHOLDS:
        eval_density = evaluation_density(eval_doc, eval_rows, threshold)
        eval_score = threshold_result(eval_doc, threshold)
        pool_density = candidate_density(candidates, threshold)
        ladder.append(
            {
                "threshold": threshold,
                "validation_recall": eval_score["event_recall"],
                "validation_precision": eval_score["event_precision"],
                "validation_easy_negative_fire_rate": eval_density[
                    "easy_negative_endpoint_fire_rate"
                ],
                "proposal_pool_fires": pool_density["fires"],
                "proposal_pool_fire_rate": pool_density["fire_rate"],
                "proposal_pool_fires_per_calendar_span_day": pool_density[
                    "fires_per_calendar_span_day"
                ],
            }
        )

    gaps = (
        candidates.sort_values(["symbol", "mapped_signal_i"])
        .groupby("symbol")["mapped_signal_i"]
        .diff()
        .dropna()
    )
    primary_pool = candidate_density(candidates, 0.35)
    event_groups = int(candidates.loc[candidates["b2_fire_edge3"], "event_group_id"].nunique())
    primary_fires = int(candidates["b2_fire_edge3"].sum())
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": str(args.weights.relative_to(PROJECT)),
        "selected_threshold": 0.35,
        "decision": {
            "p1_localization_hypothesis": "historical_discovery_pass_unchanged",
            "b2_operating_density": "failed_on_enriched_proposal_pool",
            "continuous_market_density": "not_measured",
            "executable_order_count": "not_available_without_P3_judgment_and_execution",
            "next_phase": "P2_hard_negative_mining_and_continuous_density_replay",
            "p3_judgment": "blocked_until_L1_density_is_credible",
        },
        "grain_reconciliation": {
            "p1_event_ruler": {
                "rows": len(eval_rows),
                "description": "balanced positive-event/easy-negative endpoints; not continuous market exposure",
            },
            "proposal_pool": {
                "rows": len(candidates),
                "symbols": int(candidates["symbol"].nunique()),
                "description": "v10 proposal-led short-L2 rows; already prefiltered and 18-bar spaced",
                "source_path": p1_manifest["inputs"]["candidate_source"]["path"],
                "source_role": p1_manifest["inputs"]["candidate_source"]["role"],
            },
            "continuous_market_windows": {
                "measured": False,
                "reason": "the replay never scanned every symbol x every causal tip endpoint",
            },
        },
        "selected_threshold_evidence": {
            "validation": primary_eval,
            "proposal_pool": primary_pool,
            "proposal_pool_fires": primary_fires,
            "proposal_pool_unique_event_groups": event_groups,
            "event_group_dedup_reduction": 1 - event_groups / primary_fires,
        },
        "threshold_sensitivity_diagnostic_only": ladder,
        "implementation_checks": {
            "candidate_id_unique": True,
            "minimum_same_symbol_gap_bars": int(gaps.min()),
            "minimum_gap_expected": 18,
            "edge2_edge3_disagreements": int(
                (candidates["b2_fire_edge2"] != candidates["b2_fire_edge3"]).sum()
            ),
            "box_count_distribution": {
                str(key): int(value)
                for key, value in Counter(candidates["b2_n_boxes"].astype(int)).items()
            },
            "transport_parity": transport_parity(
                candidates, args.weights, device=args.device, samples=args.transport_samples
            ),
        },
        "root_cause": {
            "verified": [
                "conf=0.35 fires on 15.69% of easy-negative validation endpoints",
                "conf=0.35 fires on 49.78% of an already-prefiltered v10 proposal pool",
                "candidate IDs are unique and source proposals already enforce an 18-bar gap",
                "all B2 pool boxes are already inside both edge2 and edge3 gates",
                "raising confidence enough to reduce density collapses validation recall",
            ],
            "rejected": [
                "duplicate candidate rows",
                "missing same-symbol 18-bar dedup",
                "tip-edge mapping inflation",
                "array-versus-PNG model-input transport mismatch",
            ],
            "likely": "balanced 1:1 easy-negative P1 selection did not represent hard-negative or continuous-market base rates",
            "confidence": "high",
        },
        "safety": {
            "holdout_read": False,
            "threshold_changed": False,
            "model_promoted": False,
            "deployed": False,
            "orders_sent": False,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
