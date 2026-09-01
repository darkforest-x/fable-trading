from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from scripts.research_15m_ma_launch_l2_reference_augmentation import (
    ReferenceAugmentationError,
    assign_augmented_dependency_blocks,
    eligible_reference_records,
    reference_side,
    sha256_csv_prefix,
)


def _prereg() -> dict:
    return {
        "inputs": {
            "reference_manifest": {
                "rows": 4,
                "positive_rows": 2,
                "negative_rows": 2,
            }
        },
        "reference_contract": {
            "eligible_manifest_splits": ["train", "val"],
        },
        "frozen_contract": {
            "train_available_at_end_exclusive": "2026-02-26T12:00:00Z",
        },
    }


def _record(
    sample_id: str,
    *,
    kind: str,
    split: str,
    end: str,
    end_i: int,
) -> dict:
    return {
        "sample_id": sample_id,
        "source_sample_id": sample_id,
        "sample_kind": kind,
        "split": split,
        "direction": "LONG" if kind == "positive" else None,
        "paired_direction": "SHORT" if kind == "negative" else None,
        "window_end_time": end,
        "window_end_i": end_i,
        "source_path": "data/example.csv",
        "symbol": "BTC_USDT_SWAP",
    }


def test_reference_side_uses_direction_and_paired_direction() -> None:
    assert reference_side({"sample_kind": "positive", "direction": "LONG"}) == "long"
    assert (
        reference_side({"sample_kind": "negative", "paired_direction": "SHORT"})
        == "short"
    )


def test_reference_selection_is_time_based_and_excludes_declared_rows() -> None:
    records = [
        _record(
            "positive_keep",
            kind="positive",
            split="train",
            end="2026-02-01T00:00:00Z",
            end_i=1000,
        ),
        _record(
            "positive_late",
            kind="positive",
            split="val",
            end="2026-02-26T11:45:00Z",
            end_i=2000,
        ),
        _record(
            "negative_keep",
            kind="negative",
            split="val",
            end="2026-01-01T00:00:00Z",
            end_i=3000,
        ),
        _record(
            "negative_excluded",
            kind="negative",
            split="excluded",
            end="2025-01-01T00:00:00Z",
            end_i=4000,
        ),
    ]
    selected, stats = eligible_reference_records(records, _prereg())
    assert {row["sample_id"] for row in selected} == {
        "positive_keep",
        "negative_keep",
    }
    assert stats["excluded_at_or_after_train_cutoff"] == 1
    assert stats["excluded_manifest_split:excluded"] == 1


def test_reference_selection_rejects_duplicate_economic_event_key() -> None:
    records = [
        _record(
            "p1",
            kind="positive",
            split="train",
            end="2026-01-01T00:00:00Z",
            end_i=1000,
        ),
        _record(
            "p2",
            kind="positive",
            split="train",
            end="2026-01-01T00:00:00Z",
            end_i=1000,
        ),
        _record(
            "n1",
            kind="negative",
            split="val",
            end="2026-01-02T00:00:00Z",
            end_i=1100,
        ),
        _record(
            "n2",
            kind="negative",
            split="val",
            end="2026-01-03T00:00:00Z",
            end_i=1200,
        ),
    ]
    with pytest.raises(ReferenceAugmentationError, match="duplicate economic event"):
        eligible_reference_records(records, _prereg())


def test_dependency_blocks_are_transitive_and_prefer_real_l1() -> None:
    frame = pd.DataFrame(
        [
            {
                "episode_id": "reference:first",
                "symbol": "BTC_USDT_SWAP",
                "available_at": "2026-01-01T02:00:00Z",
                "exposure_start_time": "2026-01-01T00:00:00Z",
                "exposure_end_exclusive": "2026-01-01T04:00:00Z",
                "event_source": "reference_window",
                "source_priority": 1,
            },
            {
                "episode_id": "reference:bridge",
                "symbol": "BTC_USDT_SWAP",
                "available_at": "2026-01-01T04:00:00Z",
                "exposure_start_time": "2026-01-01T03:00:00Z",
                "exposure_end_exclusive": "2026-01-01T07:00:00Z",
                "event_source": "reference_window",
                "source_priority": 1,
            },
            {
                "episode_id": "real:l1",
                "symbol": "BTC_USDT_SWAP",
                "available_at": "2026-01-01T06:00:00Z",
                "exposure_start_time": "2026-01-01T06:00:00Z",
                "exposure_end_exclusive": "2026-01-01T09:00:00Z",
                "event_source": "real_l1",
                "source_priority": 0,
            },
        ]
    )
    out = assign_augmented_dependency_blocks(frame)
    assert out["dependency_block_id"].nunique() == 1
    representatives = out.loc[out["dependency_representative"], "episode_id"].tolist()
    assert representatives == ["real:l1"]


def test_prefix_hash_does_not_include_later_rows(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_bytes(b"a,b\n1,2\n3,4\n5,6\n")
    observed = sha256_csv_prefix(path, 2)
    expected = hashlib.sha256(b"a,b\n1,2\n3,4\n").hexdigest()
    assert observed == expected
