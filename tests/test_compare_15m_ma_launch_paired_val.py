"""Tests for the pre-result paired Grade-A validation comparison."""

from __future__ import annotations

import pytest

from scripts.compare_15m_ma_launch_paired_val import (
    PairedValidationError,
    compare_rows,
    holm_adjust,
    paired_binary_summary,
)


def positive(
    sample: str, event: str, post: int, hit: bool, direction: str = "LONG"
) -> dict:
    return {
        "dataset_sample_id": sample,
        "sample_kind": "positive",
        "event_id": event,
        "direction": direction,
        "post_bars": post,
        "true_hit": hit,
        "boxes": int(hit),
    }


def negative(sample: str, event: str, boxes: int, kind: str = "hard") -> dict:
    return {
        "dataset_sample_id": sample,
        "sample_kind": "negative",
        "negative_event_id": event,
        "negative_kind": kind,
        "boxes": boxes,
    }


def test_paired_binary_summary_uses_only_discordant_pairs() -> None:
    result = paired_binary_summary(
        [True, True, False, False], [True, False, True, False]
    )
    assert result["control_only"] == 1
    assert result["treatment_only"] == 1
    assert result["paired_exact_two_sided_p"] == 1.0
    assert result["rate_delta_treatment_minus_control"] == 0.0


def test_holm_adjust_is_monotone_in_sorted_p_order() -> None:
    adjusted = holm_adjust({"a": 0.01, "b": 0.03, "c": 0.04})
    assert adjusted == pytest.approx({"a": 0.03, "b": 0.06, "c": 0.06})


def test_compare_rows_collapses_variants_and_is_seed_deterministic() -> None:
    control = [
        positive("a2", "a", 2, False),
        positive("a3", "a", 3, True),
        positive("b2", "b", 2, False, "SHORT"),
        positive("b4", "b", 4, False, "SHORT"),
        negative("n1", "n", 1),
        negative("n2", "n", 0),
        negative("n3", "m", 0, "easy"),
    ]
    treatment = [
        positive("a2", "a", 2, True),
        positive("a3", "a", 3, True),
        positive("b2", "b", 2, True, "SHORT"),
        positive("b4", "b", 4, True, "SHORT"),
        negative("n1", "n", 0),
        negative("n2", "n", 0),
        negative("n3", "m", 0, "easy"),
    ]
    first = compare_rows(
        control, treatment, bootstrap_reps=200, permutation_reps=500, seed=7
    )
    second = compare_rows(
        control, treatment, bootstrap_reps=200, permutation_reps=500, seed=7
    )
    assert first == second
    assert first["counts"] == {
        "paired_images": 7,
        "positive_images": 4,
        "negative_images": 3,
        "positive_events": 2,
        "negative_events": 2,
    }
    earliest = first["primary_surfaces"]["earliest"]
    assert earliest["control_positive"] == 0
    assert earliest["treatment_positive"] == 2
    any_event = first["secondary_surfaces"]["any_variant_event_recall"]
    assert any_event["control_positive"] == 1
    assert any_event["treatment_positive"] == 2
    negative_fire = first["primary_surfaces"]["negative_fired_image_rate"]
    assert negative_fire["rate_delta_treatment_minus_control"] == pytest.approx(-1 / 3)
    assert negative_fire["event_block_bootstrap"]["blocks"] == 2


def test_compare_rows_rejects_identity_drift() -> None:
    control = [positive("p", "event-a", 2, True), negative("n", "neg", 0)]
    treatment = [positive("p", "event-b", 2, True), negative("n", "neg", 0)]
    with pytest.raises(PairedValidationError, match="event_id"):
        compare_rows(
            control,
            treatment,
            bootstrap_reps=10,
            permutation_reps=10,
        )
