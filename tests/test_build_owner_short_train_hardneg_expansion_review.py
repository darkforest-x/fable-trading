"""Unit tests for the second train hard-negative expansion review."""

from __future__ import annotations

import pytest

from scripts.build_owner_short_train_hardneg_expansion_review import (
    select_hard_negative_diverse,
)


def event(block: str, index: int, *, symbol: str | None = None) -> dict:
    return {
        "candidate_block": block,
        "symbol": symbol or f"S{index}",
        "hard_negative_affinity_v2": 10.0 - index,
        "event_conf_max": 0.5,
        "decision_time": f"2026-01-01T00:{index:02d}:00Z",
        "event_id": f"{block}-{index}",
    }


def test_select_hard_negative_diverse_respects_quotas() -> None:
    rows = [event("A", index) for index in range(5)] + [event("B", index) for index in range(4)]

    selected = select_hard_negative_diverse(rows, {"A": 3, "B": 2})

    assert [row["event_id"] for row in selected] == ["A-0", "A-1", "A-2", "B-0", "B-1"]


def test_select_hard_negative_diverse_relaxes_symbol_cap() -> None:
    rows = [event("A", index, symbol="ONLY") for index in range(5)]

    assert len(select_hard_negative_diverse(rows, {"A": 5})) == 5


def test_select_hard_negative_diverse_rejects_underfilled_block() -> None:
    with pytest.raises(ValueError, match="need 2"):
        select_hard_negative_diverse([event("A", 0)], {"A": 2})
