import pandas as pd

from scripts.build_owner_short_gold_center_dataset import (
    assert_unique_training_examples,
    assign_time_splits,
    deduplicate_positive_plans,
    dependency_blocks,
    overlaps,
)


def _row(sample_id: str, symbol: str, start: int, end: int, when: str) -> dict:
    stamp = pd.Timestamp(when, tz="UTC")
    return {
        "sample_id": sample_id,
        "symbol": symbol,
        "win_start": start,
        "win_end": end,
        "start_time": stamp.isoformat(),
        "end_time": (stamp + pd.Timedelta(minutes=(end - start) * 15)).isoformat(),
    }


def test_dependency_blocks_keep_overlapping_same_symbol_together() -> None:
    plans = [
        _row("a", "ETH", 100, 115, "2026-01-01"),
        _row("b", "ETH", 110, 125, "2026-01-01 02:30"),
        _row("c", "ETH", 200, 215, "2026-01-02"),
        _row("d", "BTC", 110, 125, "2026-01-01 02:30"),
    ]
    blocks = dependency_blocks(plans)
    assert sorted(len(block) for block in blocks) == [1, 1, 2]


def test_time_split_never_breaks_dependency_and_has_purge() -> None:
    plans = [
        _row(str(i), "ETH", i * 100, i * 100 + 14, f"2025-{i + 1:02d}-01")
        for i in range(10)
    ]
    profile = assign_time_splits(plans, val_frac=0.2, purge_bars=4)
    assert profile["actual_gap_bars"] >= 4
    by_dependency = {}
    for row in plans:
        by_dependency.setdefault(row["dependency_id"], set()).add(row["split"])
    assert all(len(splits) == 1 for splits in by_dependency.values())


def test_overlap_is_inclusive() -> None:
    assert overlaps((10, 20), [(20, 30)])
    assert not overlaps((10, 19), [(20, 30)])


def test_duplicate_owner_targets_collapse_before_split_and_keep_lineage() -> None:
    base = {
        "symbol": "ETH",
        "win_start": 100,
        "win_end": 113,
        "core_global": [105, 110],
    }
    plans = [
        {**base, "sample_id": "alias", "exact_star_box": False},
        {**base, "sample_id": "star", "exact_star_box": True},
        {
            **base,
            "sample_id": "other",
            "win_start": 200,
            "win_end": 213,
            "core_global": [205, 210],
            "exact_star_box": False,
        },
    ]
    unique, profile = deduplicate_positive_plans(plans)
    assert [row["sample_id"] for row in unique] == ["star", "other"]
    assert unique[0]["owner_annotation_ids"] == ["alias", "star"]
    assert unique[0]["owner_annotation_count"] == 2
    assert profile == {
        "duplicate_target_groups": 1,
        "duplicate_annotation_rows_removed": 1,
        "unique_positive_targets": 2,
    }


def test_byte_identical_training_examples_are_rejected() -> None:
    rows = [
        {"sample_id": "a", "image_sha256": "image", "label_sha256": "label"},
        {"sample_id": "b", "image_sha256": "image", "label_sha256": "label"},
    ]
    try:
        assert_unique_training_examples(rows)
    except ValueError as exc:
        assert "a and b" in str(exc)
    else:
        raise AssertionError("duplicate image/label pair was accepted")
