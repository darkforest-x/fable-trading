from __future__ import annotations

from src.judgment.p1_build import (
    LocalBoxDetection,
    select_live_parity_observations,
    select_proposal_led_observations,
)


def _box(*, signal_i: int, window_end_i: int, confidence: float = 0.5) -> LocalBoxDetection:
    return LocalBoxDetection(
        source="okx",
        symbol="BTC_USDT_SWAP",
        window_start_i=window_end_i - 199,
        window_end_i=window_end_i,
        mapped_signal_i=signal_i,
        box_x_center=0.98,
        box_y_center=0.5,
        box_width=0.02,
        box_height=0.1,
        box_confidence=confidence,
        box_class_id=0,
    )


def test_replay_uses_live_min_gap_before_global_age():
    # This preserves the live operator order: tip-3 wins the left-biased gap
    # dedupe first, then is rejected by the final global age gate. Tip is not
    # resurrected after that final assertion.
    detections = [_box(signal_i=497, window_end_i=498), _box(signal_i=500, window_end_i=500)]
    observations, stats = select_live_parity_observations(
        detections,
        pulse_latest_indices=[500],
        min_gap=18,
        max_global_tip_age_bars=2,
    )
    assert observations == []
    assert stats["pulse_raw_signal_count"] == 2
    assert stats["pulse_after_min_gap_count"] == 1
    assert stats["pulse_after_global_age_count"] == 0


def test_replay_accepts_tip_tip1_tip2_and_dedupes_same_signal():
    detections = [
        _box(signal_i=498, window_end_i=498, confidence=0.6),
        _box(signal_i=499, window_end_i=499, confidence=0.7),
        _box(signal_i=500, window_end_i=500, confidence=0.8),
    ]
    observations, stats = select_live_parity_observations(
        detections,
        pulse_latest_indices=[498, 499, 500],
        min_gap=1,
        max_global_tip_age_bars=2,
    )
    assert [item.mapped_signal_i for item in observations] == [498, 499, 500]
    # Each event keeps its first causal appearance even if the same historical
    # window is revisited as tip-1/tip-2 on later pulses.
    assert [item.global_tip_age_bars for item in observations] == [0, 0, 0]
    assert stats["unique_candidate_count"] == 3


def test_replay_uses_confidence_only_for_representative_evidence():
    detections = [
        _box(signal_i=499, window_end_i=499, confidence=0.4),
        _box(signal_i=499, window_end_i=500, confidence=0.9),
    ]
    observations, _ = select_live_parity_observations(
        detections,
        pulse_latest_indices=[500],
        min_gap=18,
    )
    assert len(observations) == 1
    assert observations[0].mapped_signal_i == 499
    assert observations[0].box_confidence == 0.9


def test_proposal_led_replay_uses_only_exact_frozen_windows():
    detections = [
        _box(signal_i=498, window_end_i=498, confidence=0.99),
        _box(signal_i=499, window_end_i=500, confidence=0.80),
        _box(signal_i=500, window_end_i=500, confidence=0.70),
    ]
    observations, stats = select_proposal_led_observations(
        detections,
        proposal_indices=[500],
        min_gap=18,
    )
    assert [item.mapped_signal_i for item in observations] == [499]
    assert stats["source_proposal_count"] == 1
    assert stats["source_proposals_with_candidate"] == 1
    assert stats["source_proposals_without_candidate"] == 0


def test_proposal_led_replay_accounts_for_detector_empty_proposal():
    observations, stats = select_proposal_led_observations([], proposal_indices=[500, 518])
    assert observations == []
    assert stats["source_proposal_count"] == 2
    assert stats["source_proposals_without_candidate"] == 2
    assert stats["source_proposal_missing_indices"] == [500, 518]
