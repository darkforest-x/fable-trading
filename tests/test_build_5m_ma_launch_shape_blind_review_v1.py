"""Selection tests for the 5-minute outcome-blinded Owner review pack."""
from __future__ import annotations

from collections import Counter

from scripts.build_5m_ma_launch_shape_blind_review_v1 import (
    select_stratified,
    stratum_key,
)


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for split in ("train", "val"):
        for kind in ("positive", "negative"):
            for direction in ("LONG", "SHORT"):
                for index in range(5):
                    rows.append(
                        {
                            "split": split,
                            "sample_kind": kind,
                            "trade_direction": direction,
                            "event_id": f"{split}-{kind}-{direction}-{index}",
                        }
                    )
    return rows


def test_selection_is_deterministic_balanced_and_carries_weights() -> None:
    first = select_stratified(_rows(), per_stratum=2, seed=7)
    second = select_stratified(_rows(), per_stratum=2, seed=7)

    assert [row["event_id"] for row in first] == [row["event_id"] for row in second]
    assert Counter(stratum_key(row) for row in first) == Counter(
        {
            (split, kind, direction): 2
            for split in ("train", "val")
            for kind in ("positive", "negative")
            for direction in ("LONG", "SHORT")
        }
    )
    assert all(row["selection_probability"] == 0.4 for row in first)
    assert all(row["estimation_weight"] == 2.5 for row in first)
