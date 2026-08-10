"""Unit tests for P2 evaluation-only hard-negative scoring."""
from __future__ import annotations

import pytest

from scripts.eval_local_signal_v2_p2_hardneg import score_negative_predictions


def test_score_negative_predictions_counts_endpoints_boxes_and_duplicates():
    result = score_negative_predictions(
        {
            "a": [],
            "b": [0.40],
            "c": [0.50, 0.60],
            "d": [],
        }
    )
    assert result["endpoints"] == 4
    assert result["fired_endpoints"] == 2
    assert result["endpoint_fire_rate"] == pytest.approx(0.5)
    assert result["total_false_positive_boxes"] == 3
    assert result["false_positive_boxes_per_1000_endpoints"] == pytest.approx(750.0)
    assert result["duplicate_boxes_after_first"] == 1
    assert result["fired_max_confidence_quantiles"]["p50"] == pytest.approx(0.5)


def test_score_negative_predictions_handles_empty_input():
    result = score_negative_predictions({})
    assert result["endpoints"] == 0
    assert result["endpoint_fire_rate"] is None
    assert result["false_positive_boxes_per_1000_endpoints"] is None
    assert result["fired_max_confidence_quantiles"] is None
