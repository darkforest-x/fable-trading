"""Pure metric tests for the frozen Grade-A val evaluator."""
from __future__ import annotations

import pytest

from scripts.evaluate_15m_ma_launch_owner_grade_a8000_val import (
    normalized_iou,
    score_prediction_row,
    summarize_direction_flip_null,
    summarize_event_surface,
    summarize_negative_fires,
    summarize_positive_rows,
)


def prediction(class_id: int, confidence: float, box: list[float]) -> dict:
    return {
        "class_id": class_id,
        "class_name": "dense_long" if class_id == 0 else "dense_short",
        "confidence": confidence,
        "xyxy_norm": box,
    }


def positive_row(sample: str, event: str, post: int, direction: str = "LONG") -> dict:
    return {
        "dataset_sample_id": sample,
        "sample_kind": "positive",
        "event_id": event,
        "direction": direction,
        "post_bars": post,
        "ground_truth_class": 0 if direction == "LONG" else 1,
        "ground_truth_xyxy": [0.2, 0.2, 0.6, 0.6],
    }


def test_normalized_iou_handles_identity_disjoint_and_partial_overlap() -> None:
    assert normalized_iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0
    assert normalized_iou([0, 0, 0.2, 0.2], [0.8, 0.8, 1, 1]) == 0.0
    assert normalized_iou([0, 0, 1, 1], [0.5, 0, 1, 1]) == pytest.approx(0.5)


def test_score_requires_same_class_and_iou_and_counts_extra_boxes() -> None:
    row = positive_row("p", "event", 2)
    scored = score_prediction_row(
        row,
        [
            prediction(1, 0.99, [0.2, 0.2, 0.6, 0.6]),
            prediction(0, 0.80, [0.2, 0.2, 0.6, 0.6]),
        ],
        match_iou=0.5,
    )
    assert scored["true_hit"] is True
    assert scored["wrong_direction_overlap"] is True
    assert scored["best_hit_confidence"] == pytest.approx(0.8)
    assert scored["extra_prediction_boxes"] == 1


def test_negative_and_positive_summaries_keep_box_multiplicity() -> None:
    negatives = [
        {
            "dataset_sample_id": "n0",
            "sample_kind": "negative",
            "negative_event_id": "ne0",
            "negative_kind": "easy",
            "boxes": 0,
            "predictions": [],
        },
        {
            "dataset_sample_id": "n1",
            "sample_kind": "negative",
            "negative_event_id": "ne1",
            "negative_kind": "hard",
            "boxes": 2,
            "predictions": [
                prediction(0, 0.9, [0, 0, 1, 1]),
                prediction(1, 0.4, [0, 0, 1, 1]),
            ],
        },
    ]
    summary = summarize_negative_fires(negatives)
    assert summary["fire_rate"] == pytest.approx(0.5)
    assert summary["false_boxes_per_1000_images"] == pytest.approx(1000.0)
    assert summary["class_box_counts"] == {"dense_long": 1, "dense_short": 1}

    positives = [
        score_prediction_row(
            positive_row("p0", "e0", 2),
            [prediction(0, 0.8, [0.2, 0.2, 0.6, 0.6])],
            match_iou=0.5,
        ),
        score_prediction_row(
            positive_row("p1", "e1", 2),
            [
                prediction(0, 0.7, [0.2, 0.2, 0.6, 0.6]),
                prediction(0, 0.5, [0.0, 0.0, 0.1, 0.1]),
            ],
            match_iou=0.5,
        ),
    ]
    positive_summary = summarize_positive_rows(positives)
    assert positive_summary["fixed_threshold_image_recall"] == 1.0
    assert positive_summary["multiple_box_image_rate"] == pytest.approx(0.5)
    assert positive_summary["extra_prediction_boxes"] == 1


def test_event_surface_counts_events_not_position_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.evaluate_15m_ma_launch_owner_grade_a8000_val as module

    monkeypatch.setitem(module.EXPECTED_VAL_COUNTS, "positive_events", 3)
    rows = []
    for sample, event, post, hit in (
        ("a2", "a", 2, False),
        ("a3", "a", 3, True),
        ("b2", "b", 2, True),
        ("b4", "b", 4, True),
        ("c3", "c", 3, False),
    ):
        scored = score_prediction_row(
            positive_row(sample, event, post),
            [prediction(0, 0.8, [0.2, 0.2, 0.6, 0.6])] if hit else [],
            match_iou=0.5,
        )
        rows.append(scored)

    summary = summarize_event_surface(rows)
    assert summary["events"] == 3
    assert summary["events_with_post2_variant"] == 2
    assert summary["post2_true_hit_rate"] == pytest.approx(0.5)
    assert summary["earliest_available_true_hit_rate"] == pytest.approx(1 / 3)
    assert summary["any_hit_event_recall"] == pytest.approx(2 / 3)
    assert summary["first_hit_post_bars"]["histogram"] == {"2": 1, "3": 1}


def test_direction_flip_null_is_paired_at_image_and_event_level() -> None:
    rows = [
        score_prediction_row(
            positive_row("a2", "a", 2),
            [prediction(0, 0.8, [0.2, 0.2, 0.6, 0.6])],
            match_iou=0.5,
        ),
        score_prediction_row(
            positive_row("a3", "a", 3),
            [prediction(1, 0.7, [0.2, 0.2, 0.6, 0.6])],
            match_iou=0.5,
        ),
        score_prediction_row(
            positive_row("b2", "b", 2),
            [prediction(0, 0.9, [0.2, 0.2, 0.6, 0.6])],
            match_iou=0.5,
        ),
    ]
    null = summarize_direction_flip_null(rows)
    assert null["image_level"]["actual_correct_class_hits"] == 2
    assert null["image_level"]["flipped_class_hits"] == 1
    assert null["event_level"]["actual_correct_class_any_hit_events"] == 2
    assert null["event_level"]["flipped_class_any_hit_events"] == 1
    assert null["image_level"]["paired_exact_two_sided_p"] == 1.0
