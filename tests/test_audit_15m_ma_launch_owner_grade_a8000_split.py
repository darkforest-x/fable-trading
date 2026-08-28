from datetime import datetime, timezone

from scripts.audit_15m_ma_launch_owner_grade_a8000_split import (
    cross_split_interval_overlaps,
    dependency_interval,
    row_event_identity,
)


def positive(sample: str, start: int, render_end: int, core_end: int) -> dict:
    return {
        "sample_kind": "positive",
        "event_id": f"event-{sample}",
        "dataset_sample_id": sample,
        "source_path": "source.csv",
        "window_start_i": start,
        "window_end_i": render_end,
        "source_core_end_i": core_end,
        "core_end_time": "2025-01-01T00:00:00+00:00",
    }


def negative(sample: str, start: int, render_end: int, dependency_end: int) -> dict:
    return {
        "sample_kind": "negative",
        "negative_event_id": f"event-{sample}",
        "dataset_sample_id": sample,
        "source_path": "source.csv",
        "window_start_i": start,
        "window_end_i": render_end,
        "dependency_end_i": dependency_end,
        "dependency_end_time": "2025-01-02T00:00:00+00:00",
    }


def test_positive_dependency_uses_later_of_render_and_core_plus_five() -> None:
    start, end, end_time = dependency_interval(positive("p", 80, 103, 100))
    assert (start, end) == (80, 105)
    assert end_time == datetime(2025, 1, 1, 1, 15, tzinfo=timezone.utc)

    assert dependency_interval(positive("wide", 80, 109, 100))[1] == 109


def test_negative_dependency_uses_manifest_pinned_end() -> None:
    start, end, end_time = dependency_interval(negative("n", 20, 37, 41))
    assert (start, end) == (20, 41)
    assert end_time == datetime(2025, 1, 2, tzinfo=timezone.utc)


def test_cross_split_overlap_is_source_interval_aware() -> None:
    train = [positive("train", 10, 27, 24)]
    separated = [negative("val-safe", 40, 57, 61)]
    overlapping = [negative("val-overlap", 28, 45, 49)]

    assert cross_split_interval_overlaps(train, separated) == []
    found = cross_split_interval_overlaps(train, overlapping)
    assert found[0]["train_interval"] == [10, 29]
    assert found[0]["val_interval"] == [28, 49]


def test_event_identity_separates_positive_and_negative_namespaces() -> None:
    assert row_event_identity(positive("same", 1, 3, 2)) == "positive:event-same"
    assert row_event_identity(negative("same", 4, 6, 8)) == "negative:event-same"
