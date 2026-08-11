"""Validation tests for the train-time hard-negative review receipt."""

import pytest

from scripts.ingest_owner_short_train_hardneg_review import (
    distribution,
    selection_is_causal,
    validate_review,
)


def fixtures() -> tuple[dict, list[dict], dict]:
    manifest = [
        {"review_id": "T001", "event_id": "e1"},
        {"review_id": "T002", "event_id": "e2"},
    ]
    summary = {
        "protocol": "train-review-v1",
        "selected_candidates_sha256": "source-hash",
        "owner_decisions_preselected": 0,
        "training_eligible": 0,
        "quality_gates": {"safe": True},
    }
    payload = {
        "protocol": "train-review-v1",
        "source_sha256": "source-hash",
        "total": 2,
        "counts": {"pending": 0, "target": 1, "rebox": 0, "hard_negative": 1},
        "decisions": {"T001": "target", "T002": "hard_negative"},
    }
    return payload, manifest, summary


def test_validate_review_accepts_complete_one_to_one_payload() -> None:
    payload, manifest, summary = fixtures()

    assert validate_review(payload, manifest, summary) == payload["counts"]


def test_validate_review_rejects_source_hash_drift() -> None:
    payload, manifest, summary = fixtures()
    payload["source_sha256"] = "wrong"

    with pytest.raises(ValueError, match="source hash"):
        validate_review(payload, manifest, summary)


def test_validate_review_rejects_pending_decision() -> None:
    payload, manifest, summary = fixtures()
    payload["decisions"]["T002"] = "pending"
    payload["counts"] = {"pending": 1, "target": 1, "rebox": 0, "hard_negative": 0}

    with pytest.raises(ValueError, match="incomplete"):
        validate_review(payload, manifest, summary)


def test_validate_review_rejects_red_build_gate() -> None:
    payload, manifest, summary = fixtures()
    summary["quality_gates"]["safe"] = False

    with pytest.raises(ValueError, match="quality gates"):
        validate_review(payload, manifest, summary)


def test_distribution_accepts_empty_rebox_bucket() -> None:
    assert distribution([]) == {"median": None, "p90": None, "mean": None}


def test_selection_is_causal_accepts_explicit_builder_field() -> None:
    rows = [
        {"review_id": "M001", "hard_negative_newblocks_future_used": False},
        {"review_id": "M002", "hard_negative_newblocks_future_used": False},
    ]

    assert selection_is_causal(rows, "hard_negative_newblocks_future_used")


def test_selection_is_causal_rejects_missing_field() -> None:
    with pytest.raises(ValueError, match="causal proof field"):
        selection_is_causal([{"review_id": "M001"}], "hard_negative_newblocks_future_used")


def test_selection_is_causal_rejects_future_use() -> None:
    rows = [{"review_id": "M001", "hard_negative_newblocks_future_used": True}]

    assert not selection_is_causal(rows, "hard_negative_newblocks_future_used")
