"""Validation tests for the frozen 331-event Owner review receipt."""

import pytest

from scripts.ingest_owner_short_canary_review import validate_review


def fixtures() -> tuple[dict, list[dict], dict]:
    manifest = [
        {"review_id": "C001", "event_id": "e1"},
        {"review_id": "C002", "event_id": "e2"},
    ]
    summary = {
        "protocol": "review-v1",
        "events_source_sha256": "source-hash",
        "owner_decisions_preselected": 0,
        "training_eligible": 0,
    }
    payload = {
        "protocol": "review-v1",
        "source_sha256": "source-hash",
        "total": 2,
        "counts": {"pending": 0, "target": 1, "rebox": 0, "hard_negative": 1},
        "decisions": {"C001": "target", "C002": "hard_negative"},
    }
    return payload, manifest, summary


def test_validate_review_accepts_complete_one_to_one_payload() -> None:
    payload, manifest, summary = fixtures()

    counts = validate_review(payload, manifest, summary)

    assert counts == payload["counts"]


def test_validate_review_rejects_missing_decision() -> None:
    payload, manifest, summary = fixtures()
    del payload["decisions"]["C002"]

    with pytest.raises(ValueError, match="decision IDs mismatch"):
        validate_review(payload, manifest, summary)


def test_validate_review_rejects_declared_count_mismatch() -> None:
    payload, manifest, summary = fixtures()
    payload["counts"]["target"] = 2

    with pytest.raises(ValueError, match="declared counts mismatch"):
        validate_review(payload, manifest, summary)
