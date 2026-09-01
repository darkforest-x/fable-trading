"""Contract tests for the owner-approved 4h semantic-gate holdout replay."""

from __future__ import annotations

from scripts import scan_4h_ma_launch_yolo_latest as base
from scripts.apply_4h_ma_launch_yolo_semantic_gate import (
    HOLDOUT_CONSUMPTION_NUMBER,
    SOURCE_HOLDOUT_CONSUMPTION_NUMBER,
    _control_event_memberships,
    paired_binary_summary,
)


def _candidate(index: int, core_end: int, confidence: float) -> dict[str, object]:
    return {
        "candidate_id": f"c{index}",
        "symbol": "TEST_USDT_SWAP",
        "core_end_i": core_end,
        "class_name": "dense_long",
        "class_id": 0,
        "confidence": confidence,
        "window_end_time": f"2026-09-01T{index * 4:02d}:00:00+00:00",
    }


def test_owner_approved_replay_is_checkpoint_holdout_use_seven() -> None:
    assert SOURCE_HOLDOUT_CONSUMPTION_NUMBER == 6
    assert HOLDOUT_CONSUMPTION_NUMBER == 7


def test_direction_flip_null_uses_exact_same_candidate_pairs() -> None:
    summary = paired_binary_summary(
        [True, True, False, False],
        [False, True, True, False],
    )

    assert summary == {
        "pairs": 4,
        "actual_direction_positive": 2,
        "actual_direction_rate": 0.5,
        "flipped_direction_positive": 2,
        "flipped_direction_rate": 0.5,
        "actual_only": 1,
        "flipped_only": 1,
        "both": 1,
        "neither": 1,
        "paired_exact_two_sided_p": 1.0,
    }


def test_control_membership_is_exhaustive_and_uses_frozen_gap() -> None:
    candidates = [
        _candidate(0, 10, 0.9),
        _candidate(1, 11, 0.7),
        _candidate(2, 20, 0.8),
    ]
    events = base.deduplicate(candidates)

    memberships, owner = _control_event_memberships(candidates, events)

    assert len(events) == 2
    assert sorted(len(value) for value in memberships.values()) == [1, 2]
    assert set(owner) == {"c0", "c1", "c2"}

