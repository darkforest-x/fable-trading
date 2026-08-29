"""Contract tests for the frozen Grade-A three-day daily-mover scan."""
from __future__ import annotations

from scripts.scan_15m_ma_launch_owner_grade_a8000_hot3d import (
    EXPECTED_CONFIRMATIONS,
    EXPECTED_DAYS,
    EXPECTED_WINDOWS,
    cluster_symbol_episodes,
    load_preregistration,
)


def _candidate(
    *, day: str, start: int, decision: int, confidence: float, class_name: str
) -> dict[str, object]:
    return {
        "symbol": "AAA_USDT_SWAP",
        "core_start_i": start,
        "core_end_i": start + 3,
        "window_end_i": decision,
        "window_end_time": f"{day}T12:00:00+00:00",
        "confidence": confidence,
        "window_len": 18,
        "class_name": class_name,
        "day": f"{day}T00:00:00+00:00",
    }


def test_preregistration_freezes_latest_three_complete_days_and_training_geometry() -> None:
    prereg = load_preregistration()
    detector = prereg["detector"]
    assert [str(day.date()) for day in EXPECTED_DAYS] == [
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
    ]
    assert tuple(detector["window_lengths"]) == EXPECTED_WINDOWS
    assert tuple(detector["mapped_confirmation_bars_allowed"]) == EXPECTED_CONFIRMATIONS
    assert prereg["ranking"]["top_per_day"] == 20
    assert prereg["owner_authorization"]["telegram_delivery_authorized"] is False


def test_episode_merge_crosses_day_boundary_but_not_a_real_gap() -> None:
    rows = [
        _candidate(
            day="2026-08-26",
            start=10,
            decision=20,
            confidence=0.70,
            class_name="dense_long",
        ),
        _candidate(
            day="2026-08-27",
            start=19,
            decision=28,
            confidence=0.80,
            class_name="dense_short",
        ),
        _candidate(
            day="2026-08-28",
            start=40,
            decision=50,
            confidence=0.90,
            class_name="dense_long",
        ),
    ]
    annotated, episodes = cluster_symbol_episodes(rows)
    assert len(annotated) == 3
    assert len(episodes) == 2
    assert episodes[0]["episode_candidate_count"] == 2
    assert episodes[0]["episode_ranked_day_count"] == 2
    assert episodes[0]["day"].startswith("2026-08-26")
    assert episodes[1]["episode_candidate_count"] == 1
