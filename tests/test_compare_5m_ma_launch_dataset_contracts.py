"""Unit tests for historical-vs-causal 5-minute manifest comparison."""
from scripts.compare_5m_ma_launch_dataset_contracts import summarize


def test_summary_distinguishes_future_views_from_event_duplication() -> None:
    rows = [
        {
            "event_id": "e1",
            "image_sha256": "a",
            "post_bars": 2,
            "split": "train",
            "sample_kind": "positive",
        },
        {
            "event_id": "e1",
            "image_sha256": "b",
            "post_bars": 5,
            "split": "train",
            "sample_kind": "positive",
        },
        {
            "event_id": "e2",
            "image_sha256": "c",
            "post_bars": 9,
            "split": "val",
            "sample_kind": "negative",
        },
    ]

    summary = summarize(rows)

    assert summary["rows"] == 3
    assert summary["unique_events"] == 2
    assert summary["events_with_multiple_rows"] == 1
    assert summary["visible_future_rows"] == 2
    assert summary["event_split_overlap"] == 0
