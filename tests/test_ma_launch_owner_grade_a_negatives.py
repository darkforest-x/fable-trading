"""Contracts for the Grade-A nuisance-matched negative dataset builder."""

from __future__ import annotations

import json
from pathlib import Path

from yoyo.datasets.ma_launch_owner_grade_a_negatives import (
    DEFAULT_PREREG,
    group_positive_events,
    legacy_negative_compatibility_audit,
    load_positive_rows,
    load_preregistration,
    nuisance_key,
)


ROOT = Path(__file__).resolve().parents[1]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_preregistration_freezes_dataset_only_and_three_to_one() -> None:
    prereg = load_preregistration(DEFAULT_PREREG)
    assert prereg["owner_authorization"]["training_run_authorized"] is False
    assert prereg["safety"]["training_started"] is False
    assert prereg["negative_sampling"]["target_negative_images"] == 24_000
    assert prereg["negative_sampling"]["target_kinds_per_positive_event"] == [
        "hard",
        "hard",
        "easy",
    ]


def test_frozen_positive_manifest_groups_to_1043_events() -> None:
    prereg = load_preregistration(DEFAULT_PREREG)
    rows = load_positive_rows(prereg)
    events = group_positive_events(rows, expected_events=1043)
    assert len(rows) == 8_000
    assert sum(len(event.variants) for event in events) == 8_000
    assert {len(event.variants) for event in events} == {7, 8}
    assert {event.core_bars for event in events} == {4, 5}
    assert all(
        pre + event.core_bars + post in {18, 19}
        for event in events
        for _variant_id, _variant_index, pre, post in event.variants
    )


def test_legacy_30000_negatives_are_not_drop_in_compatible() -> None:
    prereg = load_preregistration(DEFAULT_PREREG)
    positives = load_positive_rows(prereg)
    legacy = _jsonl(
        ROOT
        / "datasets"
        / "ma_launch_owner_autofill10000_yolo_neg30000_v2"
        / "manifest.jsonl"
    )
    audit = legacy_negative_compatibility_audit(positives, legacy)
    assert audit["legacy_negative_rows"] == 30_000
    assert audit["legacy_okx_source_rows"] == 30_000
    assert audit["legacy_18_or_19_rows"] == 3_834
    assert audit["new_positive_venues"] == {"binance_um": 6_809, "okx": 1_191}
    assert audit["common_source_paths"] == 111
    assert audit["drop_in_compatible"] is False


def test_nuisance_key_excludes_the_label() -> None:
    base = {
        "venue": "binance_um",
        "symbol": "BTC_USDT_SWAP",
        "time_block": "2025H1",
        "core_bars": 4,
        "pre_bars": 7,
        "post_bars": 7,
        "window_bars": 18,
        "class_id": 0,
    }
    changed_label = {**base, "class_id": None, "negative_kind": "hard"}
    assert nuisance_key(base) == nuisance_key(changed_label)
