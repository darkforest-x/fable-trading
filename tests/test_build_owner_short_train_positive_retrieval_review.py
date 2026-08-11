"""Unit tests for causal positive-retrieval review construction."""

from scripts.build_owner_short_train_positive_retrieval_review import (
    owner_positive_event,
    select_positive_diverse,
)


def test_owner_positive_event_uses_frozen_decision_boundary() -> None:
    event = owner_positive_event(
        {
            "sample_id": "gold-1",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-01T03:45:00Z",
            "win_len": 16,
            "core_bars": 7,
            "post_bars": 4,
            "yolo_box": [0.50, 0.40, 0.30, 0.20],
        }
    )

    assert event["decision_time"] == "2026-01-01T03:45:00Z"
    assert event["window_len"] == 16
    assert event["predicted_core_bars"] == 7
    assert event["decision_delay_bars"] == 4
    assert event["x1n"] == 0.35
    assert event["x2n"] == 0.65


def test_select_positive_diverse_respects_block_quotas() -> None:
    rows = []
    for block, quota in {"A": 2, "B": 1}.items():
        for index in range(quota + 2):
            rows.append(
                {
                    "candidate_block": block,
                    "symbol": f"S{index}",
                    "positive_affinity": 10.0 - index,
                    "event_conf_max": 0.5,
                    "decision_time": f"2026-01-01T00:0{index}:00Z",
                    "event_id": f"{block}-{index}",
                }
            )

    selected = select_positive_diverse(rows, {"A": 2, "B": 1})

    assert [row["event_id"] for row in selected] == ["A-0", "A-1", "B-0"]


def test_select_positive_diverse_rejects_underfilled_block() -> None:
    rows = [
        {
            "candidate_block": "A",
            "symbol": "S0",
            "positive_affinity": 1.0,
            "event_conf_max": 0.5,
            "decision_time": "2026-01-01T00:00:00Z",
            "event_id": "A-0",
        }
    ]

    try:
        select_positive_diverse(rows, {"A": 2})
    except ValueError as exc:
        assert "need 2" in str(exc)
    else:
        raise AssertionError("underfilled block must fail")
