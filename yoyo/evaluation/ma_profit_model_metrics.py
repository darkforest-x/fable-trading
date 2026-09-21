"""Leakage-resistant event metrics for the two MA-profit YOLO arms.

Scores use only the maximum confidence of the ledger-known direction in that
event's causal A image.  IoU is reserved for the fixed deployment detection
metric; it never gates scores or economic ranking.  Future outcome fields are
labels/reporting targets, never model inputs.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import numpy as np


DEPLOY_CONF, HIT_IOU, TOP_FRACTION, PERMUTATIONS, PERMUTATION_SEED = .25, .5, .10, 1999, 0


class ProfitMetricError(ValueError):
    """Raised when full-pool event lineage or predictions are incomplete."""


def iou_xywh(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    """Return clipped IoU for normalized centre-width-height boxes."""

    def edges(box: Mapping[str, Any]) -> tuple[float, float, float, float]:
        cx, cy, width, height = (float(box[key]) for key in ("cx_norm", "cy_norm", "w_norm", "h_norm"))
        return cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2
    ax0, ay0, ax1, ay1 = edges(left); bx0, by0, bx1, by1 = edges(right)
    if not all(math.isfinite(value) for value in (ax0, ay0, ax1, ay1, bx0, by0, bx1, by1)) or ax1 <= ax0 or ay1 <= ay0 or bx1 <= bx0 or by1 <= by0:
        raise ProfitMetricError("invalid normalized box")
    overlap = max(0., min(ax1, bx1) - max(ax0, bx0)) * max(0., min(ay1, by1) - max(ay0, by0))
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - overlap
    return overlap / union if union else 0.


def _class_id(direction: str) -> int:
    if direction not in {"LONG", "SHORT"}:
        raise ProfitMetricError(f"unsupported direction: {direction!r}")
    return 0 if direction == "LONG" else 1


def _finite(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProfitMetricError(f"invalid {label}") from exc
    if not math.isfinite(number):
        raise ProfitMetricError(f"non-finite {label}")
    return number


def join_events(
    ledger_rows: Sequence[Mapping[str, Any]], manifest_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]], *, split: str,
) -> list[dict[str, Any]]:
    """Require one ledger, manifest A image and prediction record per event."""

    if split not in {"val", "test"}:
        raise ProfitMetricError("evaluation split must be val or test")
    ledger: dict[str, Mapping[str, Any]] = {}
    for row in ledger_rows:
        if str(row.get("split")) != split:
            continue
        event_id = str(row.get("event_id", ""))
        if not event_id or event_id in ledger:
            raise ProfitMetricError(f"duplicate/blank ledger event: {event_id!r}")
        if str(row.get("profit", {}).get("outcome")) not in {"TP", "SL", "TIMEOUT"}:
            raise ProfitMetricError(f"unresolved evaluation ledger event: {event_id}")
        ledger[event_id] = row
    manifest: dict[str, Mapping[str, Any]] = {}
    for row in manifest_rows:
        if str(row.get("split")) != split:
            continue
        arms, variant, event_id = row.get("arms"), str(row.get("variant")), str(row.get("event_id", ""))
        if not isinstance(arms, list) or not {"A", "B"}.issubset(set(arms)) or variant != "A" or not event_id or event_id in manifest:
            raise ProfitMetricError(f"invalid/duplicate common evaluation manifest event: {event_id!r}")
        manifest[event_id] = row
    if set(ledger) != set(manifest):
        raise ProfitMetricError("ledger and common evaluation manifest event sets differ")
    predictions: dict[str, Mapping[str, Any]] = {}
    manifest_by_image = {str(row["image_path"]): (event_id, row) for event_id, row in manifest.items()}
    for row in prediction_rows:
        image_path = str(row.get("image_path", ""))
        if image_path not in manifest_by_image:
            raise ProfitMetricError(f"prediction image outside common manifest: {image_path}")
        event_id, expected = manifest_by_image[image_path]
        if event_id in predictions or row.get("image_sha256") != expected.get("image_sha256") or row.get("event_id") != event_id or row.get("split") != split:
            raise ProfitMetricError(f"duplicate/drifting prediction: {event_id}")
        boxes = row.get("boxes")
        if not isinstance(boxes, list):
            raise ProfitMetricError(f"prediction boxes list required: {event_id}")
        predictions[event_id] = row
    if set(predictions) != set(manifest):
        raise ProfitMetricError("missing prediction image in full evaluation pool")
    events: list[dict[str, Any]] = []
    for event_id in sorted(ledger):
        source, rendered, predicted = ledger[event_id], manifest[event_id], predictions[event_id]
        direction, wanted = str(source.get("direction")), _class_id(str(source.get("direction")))
        for key in ("direction", "bar_minutes", "canonical_asset", "core_end_time"):
            if key not in source or key not in rendered or source[key] != rendered[key]:
                raise ProfitMetricError(f"manifest/ledger {key} mismatch: {event_id}")
        profit = source["profit"]
        expected_box = rendered.get("box")
        if bool(profit.get("retained")) != (expected_box is not None) or rendered.get("class_id") != (wanted if profit.get("retained") else None):
            raise ProfitMetricError(f"manifest retained/box mismatch: {event_id}")
        boxes: list[dict[str, Any]] = []
        for box in predicted["boxes"]:
            if not isinstance(box, Mapping) or box.get("class_id") not in {0, 1}:
                raise ProfitMetricError(f"invalid predicted class: {event_id}")
            confidence = _finite(box.get("confidence"), "confidence")
            if not 0. <= confidence <= 1.:
                raise ProfitMetricError(f"out-of-range confidence: {event_id}")
            iou_xywh(box, box)  # Validate even false/low-confidence boxes.
            cx, cy, width, height = (float(box[key]) for key in ("cx_norm", "cy_norm", "w_norm", "h_norm"))
            if min(cx-width/2, cy-height/2) < -1e-5 or max(cx+width/2, cy+height/2) > 1+1e-5:
                raise ProfitMetricError(f"predicted box leaves image: {event_id}")
            boxes.append(dict(box, confidence=confidence))
        same_direction = [box for box in boxes if int(box["class_id"]) == wanted]
        score = max((float(box["confidence"]) for box in same_direction), default=0.)
        deployed = [box for box in boxes if float(box["confidence"]) >= DEPLOY_CONF]
        correct_deployed = [box for box in deployed if int(box["class_id"]) == wanted]
        hit = bool(expected_box is not None and any(iou_xywh(box, expected_box) >= HIT_IOU for box in correct_deployed))
        gross_r, net_r, risk, entry = (_finite(profit[key], key) for key in ("gross_r", "net_r", "risk_price", "entry_price"))
        if risk <= 0 or entry <= 0:
            raise ProfitMetricError(f"non-positive price-risk/entry: {event_id}")
        events.append({"event_id": event_id, "split": split, "direction": direction, "bar_minutes": int(source["bar_minutes"]), "retained": bool(profit["retained"]), "outcome": str(profit["outcome"]), "net_profitable": net_r > 0., "score": score, "quality_score": source.get("quality_score"), "hit": hit, "deployed_any": bool(deployed), "wrong_direction_deployed": any(int(box["class_id"]) != wanted for box in deployed), "wrong_direction_box_count": sum(int(box["class_id"]) != wanted for box in deployed), "gross_r": gross_r, "net_r": net_r, "gross_bp": gross_r * risk / entry * 1e4, "net_bp": net_r * risk / entry * 1e4})
        events[-1].update({"same_direction_deployed": bool(correct_deployed), "false_positive_box_count": len(deployed)-int(hit)})
    return events


def _auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    if len(np.unique(labels)) != 2 or len(np.unique(scores)) < 2:
        return None
    positives, negatives = scores[labels == 1], scores[labels == 0]
    return float(((positives[:, None] > negatives).mean() + .5 * (positives[:, None] == negatives).mean()))


def _summary(events: Sequence[Mapping[str, Any]], score_key: str, *, allow_missing: bool = False, deployment_metrics: bool = True) -> dict[str, Any]:
    missing = [str(event["event_id"]) for event in events if event.get(score_key) is None]
    if missing:
        return {"status": "not_computable_missing_score" if allow_missing else "invalid", "missing_event_ids": missing}
    ordered = sorted(events, key=lambda event: (-float(event[score_key]), str(event["event_id"])))
    labels = np.array([int(event["retained"]) for event in ordered], dtype=int)
    scores = np.array([_finite(event[score_key], score_key) for event in ordered], dtype=float)
    positives = int(labels.sum()); negatives = len(labels) - positives
    tp = sum(bool(event["hit"]) for event in ordered)
    fn = positives - tp
    fp = sum(int(event["false_positive_box_count"]) > 0 for event in ordered)
    fp_boxes = sum(int(event["false_positive_box_count"]) for event in ordered)
    denominator = max(1, math.ceil(len(ordered) * TOP_FRACTION))
    top = ordered[:denominator]
    result: dict[str, Any] = {
        "status": "ok", "events": len(ordered), "retained_events": positives, "failed_events": negatives,
        "detection": {"tp": tp, "fp": fp, "fn": fn, "event_precision": None if tp + fp == 0 else tp / (tp + fp), "event_recall": None if positives == 0 else tp / positives, "failure_trigger_rate": None if negatives == 0 else sum((not event["retained"] and event["deployed_any"]) for event in ordered) / negatives, "wrong_direction_false_positive_events": sum(bool(event["wrong_direction_deployed"]) for event in ordered), "wrong_direction_false_positive_boxes": sum(int(event["wrong_direction_box_count"]) for event in ordered)} if deployment_metrics else {"status": "not_applicable_no_preregistered_quality_threshold"},
        "roc_auc": _auc(labels, scores), "top10": _economics(top), "all": _economics(ordered),
    }
    result["top10"]["selection_status"] = "constant_score_arbitrary_id_tiebreak" if len(np.unique(scores)) < 2 else "score_ranked_with_id_tiebreak"
    if deployment_metrics:
        result["detection"].update({"false_positive_boxes": fp_boxes, "box_precision": None if tp+fp_boxes == 0 else tp/(tp+fp_boxes), "failure_same_direction_trigger_rate": None if negatives == 0 else sum(not event["retained"] and event["same_direction_deployed"] for event in ordered)/negatives})
        result["detection"]["counting_note"] = "tp/fn count candidate events; fp counts events with at least one unmatched box. An event may contribute both tp and fp. box_precision counts every unmatched box."
        result["triggered_any"] = _economics([event for event in ordered if event["deployed_any"]])
        result["triggered_candidate_direction"] = _economics([event for event in ordered if event["same_direction_deployed"]])
        result["economics_direction_note"] = "Returns always follow the original candidate direction; wrong-direction-only fires are not claimed as executable trades."
    if result["roc_auc"] is None or len(np.unique(scores)) < 2:
        result["ranking_permutation"] = {"status": "not_identifiable", "reason": "single_class_labels_or_constant_score"}
    else:
        rng, observed, values = np.random.default_rng(PERMUTATION_SEED), result["top10"]["mean_net_bp"], np.array([float(event["net_bp"]) for event in ordered])
        null = np.empty(PERMUTATIONS)
        for index in range(PERMUTATIONS):
            null[index] = values[rng.permutation(len(values))[:denominator]].mean()
        result["ranking_permutation"] = {"status": "diagnostic_only", "draws": PERMUTATIONS, "seed": PERMUTATION_SEED, "p_greater_or_equal": float((1 + (null >= observed).sum()) / (PERMUTATIONS + 1))}
    return result


def _economics(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"events": 0, "mean_gross_bp": None, "mean_net_bp": None, "mean_gross_r": None, "mean_net_r": None, "tp_rate": None, "net_profitable_rate": None}
    return {"events": len(events), "mean_gross_bp": float(np.mean([event["gross_bp"] for event in events])), "mean_net_bp": float(np.mean([event["net_bp"] for event in events])), "mean_gross_r": float(np.mean([event["gross_r"] for event in events])), "mean_net_r": float(np.mean([event["net_r"] for event in events])), "tp_rate": float(np.mean([event["outcome"] == "TP" for event in events])), "net_profitable_rate": float(np.mean([event["net_profitable"] for event in events]))}


def evaluate_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Report model and quality-score baselines overall and by required strata."""

    if len({event["split"] for event in events}) > 1:
        raise ProfitMetricError("val and test must be evaluated separately")
    result = {"model": _summary(events, "score"), "quality_score_baseline": _summary(events, "quality_score", allow_missing=True, deployment_metrics=False), "strata": {}, "matched_random_control": {"status": "pending_separate_frozen_controls"}}
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        for label in (f"split={event['split']}", f"direction={event['direction']}", f"timeframe={event['bar_minutes']}m"):
            groups[label].append(event)
    result["strata"] = {name: {"model": _summary(group, "score"), "quality_score_baseline": _summary(group, "quality_score", allow_missing=True, deployment_metrics=False)} for name, group in sorted(groups.items())}
    return result
