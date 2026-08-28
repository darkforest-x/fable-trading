from __future__ import annotations

import pandas as pd

from yoyo.datasets.ma_launch_owner_perfect_filter import PerfectFilterError
from yoyo.datasets.ma_launch_owner_grade_a8000 import (
    allocate_variants,
    cap_and_order_events,
    cross_venue_event_nms,
    extract_scorable_candidate_profiles,
)


def _row(sample: str, *, stamp: str, score: float, venue: str = "okx", direction: str = "LONG") -> dict:
    return {
        "sample_id": sample,
        "symbol": "BTC_USDT_SWAP",
        "direction": direction,
        "core_end_time": stamp,
        "quality_score": score,
        "venue": venue,
    }


def test_cross_venue_nms_keeps_best_within_four_hours() -> None:
    rows = [
        _row("okx", stamp="2025-01-01T00:00:00Z", score=0.8),
        _row("binance", stamp="2025-01-01T01:00:00Z", score=0.9, venue="binance_um"),
        _row("later", stamp="2025-01-01T06:00:00Z", score=0.7),
    ]
    kept = cross_venue_event_nms(rows, gap_minutes=240)
    assert {row["sample_id"] for row in kept} == {"binance", "later"}


def test_cap_and_order_events_applies_block_and_symbol_caps() -> None:
    rows = [
        _row(f"r{i}", stamp=f"2025-01-{i+1:02d}T00:00:00Z", score=1 - i / 100)
        for i in range(5)
    ]
    kept = cap_and_order_events(
        rows,
        per_symbol_direction=3,
        per_symbol_direction_time_block=2,
    )
    assert [row["sample_id"] for row in kept] == ["r0", "r1"]


def test_allocate_variants_uses_five_or_six_and_exact_target() -> None:
    events = []
    for index in range(4):
        events.append(
            {
                **_row(
                    f"event{index}",
                    stamp=f"2025-01-{index+1:02d}T00:00:00Z",
                    score=1 - index / 10,
                ),
                "valid_variants": [
                    {"variant_index": variant, "pre_bars": 5 + variant, "post_bars": 9 - variant}
                    for variant in range(1, 7)
                ],
            }
        )
    plans = allocate_variants(
        events,
        target_images=22,
        minimum_unique_events=4,
        preferred_unique_events=4,
    )
    assert len(plans) == 22
    counts = {}
    for row in plans:
        counts[row["sample_id"]] = counts.get(row["sample_id"], 0) + 1
    assert sorted(counts.values()) == [5, 5, 6, 6]
    assert len({row["dataset_sample_id"] for row in plans}) == 22


def test_invalid_candidate_profile_is_audited_not_fatal(monkeypatch) -> None:
    marker = object()

    def fake_extract(_frame, row):
        if row["sample_id"] == "bad":
            raise PerfectFilterError("profile contains non-finite OHLC")
        return marker

    monkeypatch.setattr(
        "yoyo.datasets.ma_launch_owner_grade_a8000.extract_profile", fake_extract
    )
    profiles = {}
    scorable, rejected = extract_scorable_candidate_profiles(
        pd.DataFrame(),
        [{"sample_id": "good"}, {"sample_id": "bad"}],
        profiles,
    )

    assert [row["sample_id"] for row in scorable] == ["good"]
    assert profiles == {"good": marker}
    assert rejected == [
        {
            "sample_id": "bad",
            "profile_reject_reason": "profile contains non-finite OHLC",
            "training_eligible": False,
            "production_eligible": False,
        }
    ]
