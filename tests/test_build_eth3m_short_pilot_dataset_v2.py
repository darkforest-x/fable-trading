from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.build_eth3m_short_pilot_dataset_v2 import (
    BAR_DELTA,
    FUTURE_BARS,
    WINDOW,
    SourceInterval,
    choose_purged_split,
    load_pre_holdout_ohlc,
    merge_calibration_events,
    merge_source_intervals,
)


def test_pre_holdout_loader_reads_exact_continuous_prefix(tmp_path: Path) -> None:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    cutoff = start + 10 * BAR_DELTA
    times = pd.date_range(start, periods=12, freq=BAR_DELTA)
    frame = pd.DataFrame(
        {
            "ts": times.view("int64") // 1_000_000,
            "open": range(12),
            "high": range(1, 13),
            "low": range(12),
            "close": range(12),
            "volume": [1] * 12,
            "open_time": times,
        }
    )
    path = tmp_path / "ohlc.csv"
    frame.to_csv(path, index=False)
    loaded = load_pre_holdout_ohlc(path, holdout_start=cutoff)
    assert len(loaded) == 10
    assert loaded.open_time.max() == cutoff - BAR_DELTA
    assert not (loaded.open_time >= cutoff).any()


def test_calibration_events_merge_overlapping_future_horizons() -> None:
    detail = pd.DataFrame(
        [
            {
                "task_id": 1,
                "candidate_time": pd.Timestamp("2026-03-01T00:30:00Z"),
                "v10_conf": 0.8,
                "owner_is_target": 1,
                "owner_label": "是",
                "box_start_time": pd.Timestamp("2026-03-01T00:00:00Z"),
                "first_below_all_mas_lag_bars": 10,
            },
            {
                "task_id": 2,
                "candidate_time": pd.Timestamp("2026-03-01T02:30:00Z"),
                "v10_conf": 0.9,
                "owner_is_target": 1,
                "owner_label": "是",
                "box_start_time": pd.Timestamp("2026-03-01T01:45:00Z"),
                "first_below_all_mas_lag_bars": 10,
            },
        ]
    )
    calibration = pd.DataFrame(
        [
            {
                "task_id": 1,
                "source_task_id": 1,
                "entry_candidate_time": pd.Timestamp("2026-03-01T00:00:00Z"),
                "original_v10_time": pd.Timestamp("2026-03-01T00:30:00Z"),
                "causal_image_rel": "causal_images/task_01.png",
                "review_image_rel": "review_images/task_01.jpg",
            },
            {
                "task_id": 2,
                "source_task_id": 2,
                "entry_candidate_time": pd.Timestamp("2026-03-01T02:00:00Z"),
                "original_v10_time": pd.Timestamp("2026-03-01T02:30:00Z"),
                "causal_image_rel": "causal_images/task_02.png",
                "review_image_rel": "review_images/task_02.jpg",
            },
        ]
    )
    reps, audit = merge_calibration_events(detail, calibration)
    assert len(reps) == 2
    assert reps["positive_event_id"].nunique() == 1
    assert set(reps.source_task_id) == {1, 2}
    assert audit["overlapping_calibration_rows_grouped"] == 1


def _interval(group: str, anchor: str, target: int, *, positive: str | None = None):
    timestamp = pd.Timestamp(anchor)
    return SourceInterval(
        source_group=group,
        start=timestamp - BAR_DELTA,
        label_end=timestamp + FUTURE_BARS * BAR_DELTA,
        positive_event_id=positive,
        samples=[
            {
                "anchor_time": timestamp,
                "target": target,
                "sample_kind": "confirmed_current_tip" if target else "owner_no_tip_negative",
                "tip_offset": 0,
                "source_task_id": int(group.rsplit("_", 1)[-1]),
                "calibration_task_id": "",
                "label_provenance": "test",
            }
        ],
    )


def test_source_intervals_merge_and_keep_event_on_one_split() -> None:
    intervals = [
        _interval("positive_1", "2026-03-01T00:00:00Z", 1, positive="p1"),
        _interval("negative_2", "2026-03-01T01:00:00Z", 0),
        _interval("positive_3", "2026-03-03T00:00:00Z", 1, positive="p2"),
    ]
    samples, events = merge_source_intervals(intervals)
    assert len(events) == 2
    assert samples.loc[samples.source_group == "positive_1", "event_id"].item() == samples.loc[
        samples.source_group == "negative_2", "event_id"
    ].item()


def test_purged_split_requires_input_start_after_training_label_end() -> None:
    # Three positive events separated by more than the 200+60 bar requirement.
    base = pd.Timestamp("2026-03-01T00:00:00Z")
    intervals = [
        _interval("positive_1", str(base), 1, positive="p1"),
        _interval("positive_2", str(base + 300 * BAR_DELTA), 1, positive="p2"),
        _interval("positive_3", str(base + 600 * BAR_DELTA), 1, positive="p3"),
    ]
    samples, events = merge_source_intervals(intervals)
    split, _, audit = choose_purged_split(samples, events, target_train_fraction=2 / 3)
    assert split.groupby("event_id").split.nunique().max() == 1
    assert pd.Timestamp(audit["first_val_input_start"]) > pd.Timestamp(
        audit["last_train_label_end"]
    )
    assert audit["anchor_embargo_bars"] >= WINDOW + FUTURE_BARS
