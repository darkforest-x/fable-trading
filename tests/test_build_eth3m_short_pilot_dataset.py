from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_eth3m_short_pilot_dataset import (
    HOLDOUT_START,
    assign_event_split,
    compact_segment,
    prepare_samples,
)


def _row(task_id: int, candidate: str, target: int, *, lag: int, start: str, conf: float = 0.5):
    return {
        "task_id": task_id,
        "candidate_time": candidate,
        "v10_conf": conf,
        "owner_is_target": target,
        "owner_label": "是" if target else "不是",
        "box_start_time": start,
        "first_below_all_mas_lag_bars": lag,
        # A future field may exist in the source, but prepare_samples must not select it.
        "outcome_return_3h": -0.1,
    }


def test_positive_anchor_dedup_keeps_widest_then_highest_confidence() -> None:
    detail = pd.DataFrame(
        [
            _row(1, "2026-03-01T00:30:00Z", 1, lag=2, start="2026-03-01T00:18:00Z", conf=0.4),
            _row(2, "2026-03-01T00:33:00Z", 1, lag=3, start="2026-03-01T00:15:00Z", conf=0.3),
            _row(3, "2026-03-01T03:00:00Z", 0, lag=0, start="2026-03-01T02:30:00Z"),
        ]
    )
    samples, audit = prepare_samples(detail)
    positive = samples[samples["target"] == 1]
    assert len(positive) == 1
    assert int(positive.iloc[0]["task_id"]) == 2
    assert audit["positive_exact_anchor_duplicates_removed"] == 1


def test_positive_negative_exact_anchor_conflict_is_rejected() -> None:
    detail = pd.DataFrame(
        [
            _row(1, "2026-03-01T00:30:00Z", 1, lag=2, start="2026-03-01T00:18:00Z"),
            _row(2, "2026-03-01T00:24:00Z", 0, lag=0, start="2026-03-01T00:00:00Z"),
        ]
    )
    with pytest.raises(ValueError, match="exact anchor conflicts"):
        prepare_samples(detail)


@pytest.mark.parametrize(
    ("box_start", "anchor", "expected"),
    [(99, 100, 5), (96, 100, 5), (94, 100, 7), (0, 100, 12)],
)
def test_compact_segment_is_between_five_and_twelve_bars(
    box_start: int, anchor: int, expected: int
) -> None:
    start, end = compact_segment(box_start, anchor)
    assert end - start + 1 == expected
    assert 5 <= end - start + 1 <= 12
    assert end == anchor


def test_event_split_is_chronological_and_never_crosses_event() -> None:
    times = pd.to_datetime(
        [
            "2026-03-01T00:00:00Z",
            "2026-03-01T00:30:00Z",  # same event
            "2026-03-01T02:00:00Z",
            "2026-03-01T04:00:00Z",
            "2026-03-01T06:00:00Z",
        ],
        utc=True,
    )
    samples = pd.DataFrame({"anchor_time": times, "target": [1, 0, 1, 0, 1], "task_id": range(5)})
    split, cutoff, n_train_events = assign_event_split(samples)
    assert split.groupby("event_id")["split"].nunique().max() == 1
    assert split.loc[split.split == "train", "anchor_time"].max() < cutoff
    assert split.loc[split.split == "val", "anchor_time"].min() > cutoff
    assert n_train_events == 3


def test_holdout_boundary_constant_is_strict() -> None:
    assert HOLDOUT_START == pd.Timestamp("2026-05-04", tz="UTC")
