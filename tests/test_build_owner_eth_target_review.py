from collections import Counter

import pytest

from scripts.build_owner_eth_target_review import (
    REVIEW_QUOTAS,
    _round_robin_strata,
    delay_group,
    position_band,
    select_review_rows,
)


def _row(index: int, group: str) -> dict:
    delay = {
        "delay_3": 3,
        "delay_4": 4,
        "delay_5": 5,
    }[group]
    center = (0.42, 0.56, 0.71, 0.86)[index % 4]
    return {
        "event_id": f"{group}-{index:04d}",
        "symbol": f"SYM{index % 17}",
        "anchor_time": f"2025-01-{1 + index % 28:02d} 00:00:00+00:00",
        "post_bars": delay,
        "box_bars": 5 if index % 2 == 0 else 7,
        "box_center_ratio": center,
        "position_band": position_band(center),
        "delay_group": group,
    }


def test_delay_group_uses_five_as_a_hard_ceiling():
    assert delay_group(3) == "delay_3"
    assert delay_group(4) == "delay_4"
    assert delay_group(5) == "delay_5"
    with pytest.raises(ValueError):
        delay_group(2)
    with pytest.raises(ValueError):
        delay_group(6)


def test_position_band_does_not_freeze_exact_middle_or_right():
    assert position_band(0.42) == "left_of_center"
    assert position_band(0.55) == "middle_band"
    assert position_band(0.70) == "right_band"
    assert position_band(0.85) == "far_right_band"


def test_round_robin_covers_width_and_position_strata():
    rows = [_row(index, "delay_3") for index in range(120)]
    selected = _round_robin_strata(rows, 40)
    assert len(selected) == 40
    assert {row["box_bars"] for row in selected} == {5, 7}
    assert len({row["position_band"] for row in selected}) >= 3


def test_review_selection_has_frozen_counts_without_semantic_scoring():
    rows = []
    for group in REVIEW_QUOTAS:
        rows.extend(_row(index, group) for index in range(140))
    selected = select_review_rows(rows)
    assert len(selected) == 200
    assert len({row["event_id"] for row in selected}) == 200
    assert Counter(row["review_group"] for row in selected) == Counter(REVIEW_QUOTAS)
    assert {row["selection_method"] for row in selected} == {
        "deterministic_delay_width_position_stratified"
    }
    assert all("review_priority_score" not in row for row in selected)
