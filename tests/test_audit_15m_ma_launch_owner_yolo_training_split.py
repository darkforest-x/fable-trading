from scripts.audit_15m_ma_launch_owner_yolo_training_split import (
    cross_split_interval_overlaps,
)


def row(source: str, start: int, end: int, sample_id: str) -> dict:
    return {
        "source_path": source,
        "window_start_i": start,
        "dependency_end_i": end,
        "sample_id": sample_id,
    }


def test_cross_split_interval_audit_allows_shared_source_after_purge() -> None:
    train = [row("a.csv", 10, 20, "train")]
    val = [row("a.csv", 30, 40, "val")]
    assert cross_split_interval_overlaps(train, val) == []


def test_cross_split_interval_audit_reports_touching_intervals() -> None:
    train = [row("a.csv", 10, 30, "train")]
    val = [row("a.csv", 30, 40, "val")]
    overlaps = cross_split_interval_overlaps(train, val)
    assert len(overlaps) == 1
    assert overlaps[0]["train_sample_id"] == "train"
    assert overlaps[0]["val_sample_id"] == "val"
