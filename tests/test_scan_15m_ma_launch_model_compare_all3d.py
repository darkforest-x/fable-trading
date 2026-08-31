"""Contracts for the frozen five-checkpoint all-universe comparison."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.scan_15m_ma_launch_model_compare_all3d import (
    EXPECTED_DAYS,
    EXPECTED_MODEL_KEYS,
    _pairwise_overlap,
    build_task_batches,
    cluster_episodes,
    load_preregistration,
    utc,
)
from scripts.build_15m_ma_launch_model_compare_all3d_report import alignment_null


def _candidate(
    *,
    day: str = "2026-08-28",
    symbol: str = "AAA_USDT_SWAP",
    core_start: int,
    core_end: int,
    window_end: int,
    confidence: float,
    class_id: int = 0,
) -> dict[str, object]:
    return {
        "day": f"{day}T00:00:00+00:00",
        "symbol": symbol,
        "core_start_i": core_start,
        "core_end_i": core_end,
        "window_end_i": window_end,
        "window_len": 18,
        "confidence": confidence,
        "class_id": class_id,
        "class_name": "dense_long" if class_id == 0 else "dense_short",
    }


def test_preregistration_freezes_exact_five_models_and_authorized_complete_days() -> None:
    prereg = load_preregistration()
    assert tuple(spec["key"] for spec in prereg["models"]) == EXPECTED_MODEL_KEYS
    assert [str(day.date()) for day in EXPECTED_DAYS] == [
        "2026-08-28",
        "2026-08-29",
        "2026-08-30",
    ]
    assert prereg["owner_authorization"]["new_inference_authorized"] is True
    assert prereg["universe"]["ranking"] is None
    assert all(value is False for value in prereg["safety"].values())
    assert [spec["holdout_consumption_number_for_this_configuration"] for spec in prereg["models"]] == [
        2,
        1,
        6,
        3,
        2,
    ]


def test_each_model_contract_stays_inside_its_own_positive_training_geometry() -> None:
    prereg = load_preregistration()
    contracts = {spec["key"]: spec["detector"] for spec in prereg["models"]}
    assert contracts["legacy_t3_10k_960"]["window_lengths"] == [14, 18, 22]
    assert contracts["legacy_t3_10k_1280"]["imgsz"] == 1280
    assert contracts["legacy_owner_10k_neg30k_960"]["window_lengths"] == list(range(18, 26))
    assert contracts["grade_a8k_neg24k_epoch6_960"]["mapped_confirmation_bars_allowed"] == list(
        range(2, 10)
    )
    assert contracts["grade_a8k_neg24k_full40_1280"]["imgsz"] == 1280
    for detector in contracts.values():
        assert detector["scan_endpoint_extension_after_day_bars"] == max(
            detector["mapped_confirmation_bars_allowed"]
        )
        assert detector["confidence"] == 0.25
        assert detector["nms_iou"] == 0.7


def test_episode_merge_only_combines_overlapping_candidates_in_one_symbol_day() -> None:
    candidates = [
        _candidate(core_start=100, core_end=104, window_end=109, confidence=0.70),
        _candidate(core_start=105, core_end=109, window_end=112, confidence=0.95, class_id=1),
        _candidate(core_start=140, core_end=144, window_end=149, confidence=0.80),
        _candidate(day="2026-08-29", core_start=105, core_end=109, window_end=112, confidence=0.99),
    ]
    annotated, episodes = cluster_episodes("test", candidates)
    assert len(annotated) == 4
    assert len(episodes) == 3
    assert episodes[0]["episode_candidate_count"] == 2
    assert episodes[0]["window_end_i"] == 109
    assert episodes[0]["episode_long_candidates"] == 1
    assert episodes[0]["episode_short_candidates"] == 1
    assert episodes[-1]["day"].startswith("2026-08-29")


def test_pairwise_overlap_reports_direction_flips_instead_of_suppressing_them() -> None:
    left = [
        {**_candidate(core_start=100, core_end=104, window_end=109, confidence=0.70), "episode_sequence": 1},
        {**_candidate(core_start=200, core_end=204, window_end=209, confidence=0.80), "episode_sequence": 2},
    ]
    right = [
        {**_candidate(core_start=99, core_end=103, window_end=108, confidence=0.90, class_id=1), "episode_sequence": 1},
        {**_candidate(core_start=260, core_end=264, window_end=269, confidence=0.90), "episode_sequence": 2},
    ]
    comparison = _pairwise_overlap(left, right)
    assert comparison["time_matched_within_one_bar"] == 1
    assert comparison["same_direction_matches"] == 0
    assert comparison["direction_flip_matches"] == 1
    assert comparison["proposal_jaccard"] == 1 / 3


def test_task_generation_is_bounded_per_batch_while_preserving_every_window() -> None:
    """The old W18..25 contract must not retain a multi-GiB symbol-day list."""
    day = utc("2026-08-28T00:00:00Z")
    times = pd.date_range(day - pd.Timedelta(minutes=15 * 160), periods=257, freq="15min")
    close = np.linspace(100.0, 102.0, len(times))
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": np.ones(len(times)),
        }
    )
    detector = {
        "window_lengths": list(range(18, 26)),
        "scan_endpoint_extension_after_day_bars": 1,
    }
    _enriched, batches, stats = build_task_batches(
        frame,
        day=day,
        symbol="AAA_USDT_SWAP",
        inst_id="AAA-USDT-SWAP",
        detector=detector,
        batch_size=8,
    )
    sizes = [len(batch) for batch in batches]
    assert sum(sizes) == 97 * 8
    assert max(sizes) == 8
    assert stats == {}


def test_alignment_null_rotates_within_day_without_changing_episode_identity_fields() -> None:
    day = "2026-08-28"
    row = {
        **_candidate(core_start=106, core_end=110, window_end=115, confidence=0.8),
        "episode_sequence": 1,
        "core_end_time": f"{day}T02:30:00+00:00",
    }
    result = alignment_null([row], [row])
    assert result["actual_matches"] == 1
    assert result["actual_jaccard"] == 1.0
    assert result["null_max_matches"] == 1
    assert result["alignment_p_ge_actual"] == 3 / 96
