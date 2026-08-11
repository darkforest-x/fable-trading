from __future__ import annotations

from scripts.build_owner_short_gold_center_hardneg_r2 import (
    select_replacement_hard_negatives,
)


def _old(sample_id: str, window: int, kind: str, score: float = 0.0) -> dict:
    return {
        "sample_id": sample_id,
        "symbol": "ETH_USDT_SWAP",
        "win_len": window,
        "win_start": int(sample_id.strip("obc")) * 10,
        "win_end": int(sample_id.strip("obc")) * 10 + window - 1,
        "start_time": f"2026-01-{int(sample_id.strip('obc')):02d}T00:00:00Z",
        "end_time": f"2026-01-{int(sample_id.strip('obc')):02d}T03:00:00Z",
        "selected_hard_kind": kind,
        "max_confidence": score,
    }


def _confirmed(event_id: str, window: int, score: float, day: int) -> dict:
    return {
        "event_id": event_id,
        "sample_id": event_id,
        "symbol": "BTC_USDT_SWAP",
        "window_len": window,
        "win_len": window,
        "window_start_time": f"2026-02-{day:02d}T00:00:00Z",
        "decision_time": f"2026-02-{day:02d}T03:00:00Z",
        "event_conf_max": score,
        "selected_hard_kind": "owner_confirmed_false_fire",
    }


def test_replacement_keeps_exact_total_and_w_histogram() -> None:
    old = [
        _old("o1", 12, "owner_long"),
        _old("b2", 12, "model_ranked_background", 0.9),
        _old("b3", 12, "model_ranked_background", 0.8),
        _old("o4", 13, "owner_long"),
    ]
    confirmed = [
        _confirmed("c1", 12, 0.7, 1),
        _confirmed("c2", 13, 0.6, 2),
    ]

    selected, deferred, profile = select_replacement_hard_negatives(old, confirmed)

    assert len(selected) == len(old)
    assert not deferred
    assert profile["selected_by_w"] == {12: 3, 13: 1}
    assert profile["owner_confirmed_selected"] == 2
    assert profile["retained_r1_by_kind"] == {
        "model_ranked_background": 1,
        "owner_long": 1,
    }


def test_bucket_overflow_defers_lowest_confidence_without_distribution_drift() -> None:
    old = [_old("o1", 12, "owner_long"), _old("b2", 12, "model_ranked_background")]
    confirmed = [
        _confirmed("c1", 12, 0.9, 1),
        _confirmed("c2", 12, 0.8, 2),
        _confirmed("c3", 12, 0.1, 3),
    ]

    selected, deferred, profile = select_replacement_hard_negatives(old, confirmed)

    assert {row["event_id"] for row in selected} == {"c1", "c2"}
    assert [row["event_id"] for row in deferred] == ["c3"]
    assert profile["selected_by_w"] == {12: 2}
    assert profile["confirmed_deferred_by_w"] == {12: 1}


def test_semantically_identical_old_interval_is_excluded_even_if_pixels_differ() -> None:
    duplicate = _old("b1", 14, "model_ranked_background", 0.99)
    duplicate.update(
        {
            "symbol": "BTC_USDT_SWAP",
            "start_time": "2026-02-01T00:00:00Z",
            "end_time": "2026-02-01T03:00:00Z",
        }
    )
    fallback = _old("o2", 14, "owner_long")
    confirmed = [_confirmed("c1", 14, 0.7, 1)]

    selected, _deferred, profile = select_replacement_hard_negatives(
        [duplicate, fallback], confirmed
    )

    assert profile["semantic_old_collisions_excluded"] == 1
    assert {row["sample_id"] for row in selected if "event_id" not in row} == {"o2"}
