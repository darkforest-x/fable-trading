#!/usr/bin/env python3
"""Independently verify the causal semantic-gate paired A/B artifacts.

The verifier recomputes control/treatment subset relations, image and event
counts, saved-feature gate decisions, conditional future visibility, bounded
source-read claims, and every receipt hash.  It performs no inference and reads
no market source file.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_15m_ma_launch_owner_yolo_semantic_gate import (  # noqa: E402
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    EXPECTED_VAL_COUNTS,
    EXPERIMENT_ID,
    read_json,
    read_jsonl,
    sha256_file,
    verify_preregistration,
)
from yoyo.layers.l1_detection.semantic_gate import (  # noqa: E402
    evaluate_causal_semantic_gate,
)


class SemanticGateVerificationError(RuntimeError):
    """Raised when a persisted result cannot be independently reproduced."""


def _event_any(
    rows: Iterable[Mapping[str, Any]], *, surface: str, event_key: str, outcome: str
) -> int:
    grouped: defaultdict[str, list[bool]] = defaultdict(list)
    for row in rows:
        score = row[surface]
        value = bool(score[outcome]) if outcome != "fired" else int(score["boxes"]) > 0
        grouped[str(score[event_key])].append(value)
    return sum(any(values) for values in grouped.values())


def _verify_receipt(results: Path, receipt: Mapping[str, Any]) -> None:
    paths = {
        "summary_sha256": results / "summary.json",
        "paired_predictions_sha256": results / "paired_predictions.jsonl",
        "semantic_boxes_sha256": results / "semantic_boxes.jsonl",
        "source_audit_sha256": results / "source_and_structural_audit.jsonl",
        "overview_sha256": results / "paired_ab_overview.png",
        "gallery_sha256": results / "rejected_examples/index.html",
    }
    for key, path in paths.items():
        if not path.is_file() or sha256_file(path) != str(receipt[key]):
            raise SemanticGateVerificationError(f"receipt hash drift: {key} -> {path}")


def verify(results: Path = DEFAULT_RESULTS, prereg_path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    """Return a compact independent verification receipt or fail closed."""

    prereg, gates = verify_preregistration(prereg_path)
    summary = read_json(results / "summary.json")
    receipt = read_json(results / "receipt.json")
    _verify_receipt(results, receipt)
    if summary.get("experiment_id") != EXPERIMENT_ID or receipt.get("experiment_id") != EXPERIMENT_ID:
        raise SemanticGateVerificationError("experiment identity drifted")
    if summary.get("status") != "pass" or receipt.get("status") != "pass":
        raise SemanticGateVerificationError("persisted experiment did not pass")

    paired = read_jsonl(results / "paired_predictions.jsonl")
    boxes = read_jsonl(results / "semantic_boxes.jsonl")
    audits = read_jsonl(results / "source_and_structural_audit.jsonl")
    examples = read_jsonl(results / "rejected_examples.jsonl")
    expected_rows = EXPECTED_VAL_COUNTS["positive"] + EXPECTED_VAL_COUNTS["negative"]
    if len(paired) != expected_rows:
        raise SemanticGateVerificationError(f"paired row count drifted: {len(paired)}")
    if len({str(row["dataset_sample_id"]) for row in paired}) != len(paired):
        raise SemanticGateVerificationError("paired sample ids are not unique")

    structural_count = semantic_count = 0
    for row in paired:
        control = row["structural_control"]
        treatment = row["semantic_gate_treatment"]
        control_ids = {str(box["prediction_id"]) for box in control["predictions"]}
        treatment_ids = {str(box["prediction_id"]) for box in treatment["predictions"]}
        if not treatment_ids.issubset(control_ids):
            raise SemanticGateVerificationError("treatment contains a non-control box")
        if any(not bool(box["semantic_pass"]) for box in treatment["predictions"]):
            raise SemanticGateVerificationError("a failed semantic box escaped treatment")
        structural_count += len(control_ids)
        semantic_count += len(treatment_ids)

    if structural_count != len(boxes):
        raise SemanticGateVerificationError("semantic box ledger is not control-complete")
    if len({str(box["prediction_id"]) for box in boxes}) != len(boxes):
        raise SemanticGateVerificationError("semantic prediction ids are not unique")
    recomputed_passes = 0
    for box in boxes:
        result = evaluate_causal_semantic_gate(box["semantic_features"], gates)
        if result.passed != bool(box["semantic_pass"]):
            raise SemanticGateVerificationError("saved semantic decision does not recompute")
        if result.checks != box["semantic_checks"]:
            raise SemanticGateVerificationError("saved semantic predicates do not recompute")
        confirmation = int(box["confirmation_bars"])
        features = box["semantic_features"]
        checks = box["semantic_checks"]
        if confirmation == 2 and (
            features["post3_progress_atr"] is not None
            or features["post5_progress_atr"] is not None
            or "post3" in checks
            or "post5" in checks
        ):
            raise SemanticGateVerificationError("post2 proposal contains unseen future")
        if confirmation in {3, 4} and (
            features["post5_progress_atr"] is not None or "post5" in checks
        ):
            raise SemanticGateVerificationError("post3/4 proposal contains unseen post5")
        recomputed_passes += result.passed
    if recomputed_passes != semantic_count:
        raise SemanticGateVerificationError("treatment box count does not match recomputation")

    positives = [row for row in paired if row["sample_kind"] == "positive"]
    negatives = [row for row in paired if row["sample_kind"] == "negative"]
    if len(positives) != EXPECTED_VAL_COUNTS["positive"] or len(negatives) != EXPECTED_VAL_COUNTS["negative"]:
        raise SemanticGateVerificationError("positive/negative row composition drifted")
    control_positive_hits = sum(bool(row["structural_control"]["true_hit"]) for row in positives)
    treatment_positive_hits = sum(
        bool(row["semantic_gate_treatment"]["true_hit"]) for row in positives
    )
    control_event_hits = _event_any(
        positives, surface="structural_control", event_key="event_id", outcome="true_hit"
    )
    treatment_event_hits = _event_any(
        positives,
        surface="semantic_gate_treatment",
        event_key="event_id",
        outcome="true_hit",
    )
    control_negative_fires = sum(int(row["structural_control"]["boxes"]) > 0 for row in negatives)
    treatment_negative_fires = sum(
        int(row["semantic_gate_treatment"]["boxes"]) > 0 for row in negatives
    )
    control_negative_boxes = sum(int(row["structural_control"]["boxes"]) for row in negatives)
    treatment_negative_boxes = sum(
        int(row["semantic_gate_treatment"]["boxes"]) for row in negatives
    )

    expected = summary["surfaces"]
    comparisons = {
        "control_positive_image_hits": (
            control_positive_hits,
            expected["structural_control"]["positive_images"]["all"]["true_hit_images"],
        ),
        "treatment_positive_image_hits": (
            treatment_positive_hits,
            expected["semantic_gate_treatment"]["positive_images"]["all"]["true_hit_images"],
        ),
        "control_positive_event_hits": (
            control_event_hits,
            expected["structural_control"]["positive_events"]["any_hit_events"],
        ),
        "treatment_positive_event_hits": (
            treatment_event_hits,
            expected["semantic_gate_treatment"]["positive_events"]["any_hit_events"],
        ),
        "control_negative_fires": (
            control_negative_fires,
            expected["structural_control"]["negative_images"]["all"]["fired_images"],
        ),
        "treatment_negative_fires": (
            treatment_negative_fires,
            expected["semantic_gate_treatment"]["negative_images"]["all"]["fired_images"],
        ),
        "control_negative_boxes": (
            control_negative_boxes,
            expected["structural_control"]["negative_images"]["all"]["boxes"],
        ),
        "treatment_negative_boxes": (
            treatment_negative_boxes,
            expected["semantic_gate_treatment"]["negative_images"]["all"]["boxes"],
        ),
    }
    mismatches = {name: values for name, values in comparisons.items() if values[0] != values[1]}
    if mismatches:
        raise SemanticGateVerificationError(f"summary metrics do not recompute: {mismatches}")

    source_audits = [row for row in audits if "rows_materialized" in row]
    if len(source_audits) != int(summary["dataset"]["source_files_read_as_bounded_prefixes"]):
        raise SemanticGateVerificationError("source audit count drifted")
    if any(
        int(row["holdout_ohlcv_rows_materialized"]) != 0
        or row.get("sample_pixel_parity") is not True
        for row in source_audits
    ):
        raise SemanticGateVerificationError("bounded source or pixel parity audit failed")
    for row in examples:
        image = results / "rejected_examples" / str(row["image_path"])
        if not image.is_file() or sha256_file(image) != str(row["image_sha256"]):
            raise SemanticGateVerificationError("rejected example hash drifted")

    event_retention = treatment_event_hits / control_event_hits
    negative_reduction = (control_negative_boxes - treatment_negative_boxes) / control_negative_boxes
    decision = summary["decision"]
    if not math.isclose(event_retention, float(decision["positive_event_recall_retention"]), abs_tol=1e-15):
        raise SemanticGateVerificationError("event recall retention drifted")
    if not math.isclose(negative_reduction, float(decision["negative_box_reduction"]), abs_tol=1e-15):
        raise SemanticGateVerificationError("negative box reduction drifted")
    if not all(bool(value) for value in decision["checks"].values()):
        raise SemanticGateVerificationError("one or more frozen decision checks failed")

    return {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "passed": True,
        "preregistration_sha256": sha256_file(prereg_path),
        "results_receipt_sha256": sha256_file(results / "receipt.json"),
        "paired_rows": len(paired),
        "semantic_boxes": len(boxes),
        "semantic_pass_boxes": recomputed_passes,
        "source_prefix_audits": len(source_audits),
        "holdout_ohlcv_rows_materialized": 0,
        "control_positive_event_hits": control_event_hits,
        "treatment_positive_event_hits": treatment_event_hits,
        "positive_event_recall_retention": event_retention,
        "control_negative_boxes": control_negative_boxes,
        "treatment_negative_boxes": treatment_negative_boxes,
        "negative_box_reduction": negative_reduction,
        "rejected_example_images": len(examples),
        "thresholds_match_training_preregistration": prereg["treatment"]["frozen_morphology_gate"] == gates,
        "treatment_subset_verified": True,
        "conditional_future_visibility_verified": True,
        "receipt_hashes_verified": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = verify(args.results.resolve(), args.prereg.resolve())
    if args.output:
        output = args.output.resolve()
        if output.exists():
            raise FileExistsError(f"refusing to overwrite verification: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        payload["output"] = str(output)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
