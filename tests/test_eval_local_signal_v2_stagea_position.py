"""Pure contract tests for Stage-A position diagnostics."""
from __future__ import annotations

import pytest

from scripts.eval_local_signal_v2_stagea_position import (
    BUCKETS,
    position_diagnostic,
    score_threshold,
    xywhn_iou,
)


def _positive(index: int, bucket: str, anchor_ratio: float) -> dict:
    return {
        "eval_id": f"p{index}",
        "sample_type": "positive",
        "position_bucket": bucket,
        "anchor_x_ratio": anchor_ratio,
        "win_len": 24,
        "gt_xywhn": [anchor_ratio, 0.5, 0.2, 0.3],
    }


def _prediction(row: dict, confidence: float = 0.8) -> dict:
    return {"confidence": confidence, "xywhn": list(row["gt_xywhn"])}


def test_xywhn_iou_contract() -> None:
    assert xywhn_iou([0.5, 0.5, 0.2, 0.2], [0.5, 0.5, 0.2, 0.2]) == pytest.approx(1.0)
    assert xywhn_iou([0.2, 0.5, 0.1, 0.2], [0.8, 0.5, 0.1, 0.2]) == 0.0


def test_position_diagnostic_accepts_balanced_real_candle_detection() -> None:
    ratios = {"left_mid": 0.25, "mid": 0.45, "mid_right": 0.65, "right": 0.80}
    rows = []
    predictions = {}
    index = 0
    for bucket in BUCKETS:
        for _ in range(8):
            row = _positive(index, bucket, ratios[bucket])
            rows.append(row)
            predictions[row["eval_id"]] = [_prediction(row)]
            index += 1
    rows.append({"eval_id": "n0", "sample_type": "easy_negative", "gt_xywhn": None})
    predictions["n0"] = []

    fixed = score_threshold(rows, predictions, 0.05)
    diagnostic = position_diagnostic(rows, predictions, fixed)

    assert fixed["event_recall"] == 1.0
    assert diagnostic["position_invariance_diagnostic_pass"]
    assert diagnostic["bucket_recall_spread"] == 0.0


def test_position_diagnostic_rejects_right_only_shortcut() -> None:
    ratios = {"left_mid": 0.25, "mid": 0.45, "mid_right": 0.65, "right": 0.80}
    rows = []
    predictions = {}
    index = 0
    for bucket in BUCKETS:
        for _ in range(8):
            row = _positive(index, bucket, ratios[bucket])
            rows.append(row)
            predictions[row["eval_id"]] = [_prediction(row)] if bucket == "right" else []
            index += 1

    fixed = score_threshold(rows, predictions, 0.05)
    diagnostic = position_diagnostic(rows, predictions, fixed)

    assert not diagnostic["position_invariance_diagnostic_pass"]
    assert fixed["buckets"]["left_mid"]["event_recall"] == 0.0
    assert fixed["buckets"]["right"]["event_recall"] == 1.0
    assert diagnostic["bucket_recall_spread"] == 1.0
