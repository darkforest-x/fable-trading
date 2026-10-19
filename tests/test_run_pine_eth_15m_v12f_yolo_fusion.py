"""Contracts for the delayed V12F/Grade-A YOLO interval fusion."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.run_pine_eth_15m_v12f_yolo_fusion import (
    BAR,
    GATE_PREREG_PATH,
    GATE_PREREG_SHA256,
    MODEL_PATH,
    MODEL_SHA256,
    RENDERER_PATH,
    RENDERER_SHA256,
    SEMANTIC_GATE_PATH,
    SEMANTIC_GATE_SHA256,
    FusionError,
    candidate_endpoint_indices,
    file_sha256,
    fuse_signal_intervals,
    map_prediction_to_core,
    validate_frozen_detector,
    validate_fusion_ledgers,
)
from yoyo.layers.l1_detection.render import ChartTransform


def test_detector_and_semantic_gate_are_bound_to_frozen_bytes() -> None:
    gates = validate_frozen_detector()
    expected = {
        MODEL_PATH: MODEL_SHA256,
        RENDERER_PATH: RENDERER_SHA256,
        SEMANTIC_GATE_PATH: SEMANTIC_GATE_SHA256,
        GATE_PREREG_PATH: GATE_PREREG_SHA256,
    }
    assert all(Path(path).is_file() for path in expected)
    assert {path: file_sha256(path) for path in expected} == expected
    assert gates["core_lengths"] == [4, 5]
    assert gates["min_post2_progress_atr"] == 1.0


def test_conditioned_scan_covers_every_closed_interval_overlap_endpoint() -> None:
    assert candidate_endpoint_indices([10], frame_length=40) == list(range(11, 25))
    assert candidate_endpoint_indices([25], frame_length=30) == [26, 27, 28]
    assert candidate_endpoint_indices([], frame_length=30) == []


def test_prediction_mapping_uses_nearest_renderer_candle_centers() -> None:
    transform = ChartTransform(
        n_bars=19,
        width=1280,
        height=742,
        left=12,
        top=12,
        plot_w=1256,
        plot_h=718,
        price_min=90.0,
        price_max=110.0,
        candle_half_w=20,
    )
    x0 = transform.x_at(5) / transform.width
    x1 = transform.x_at(9) / transform.width
    mapped = map_prediction_to_core(
        cx=(x0 + x1) / 2.0,
        width=x1 - x0,
        transform=transform,
        window_start_i=100,
        window_end_i=118,
    )
    assert mapped == {
        "core_start_i": 105,
        "core_end_i": 109,
        "core_length_bars": 5,
        "confirmation_bars": 9,
    }


def _pine_signal() -> pd.DataFrame:
    start = pd.Timestamp("2025-01-01T00:15:00Z")
    return pd.DataFrame(
        [
            {
                "signal_id": "pine-1",
                "symbol": "ETH_USDT_SWAP",
                "direction": "long",
                "signal_i": 101,
                "interval_start": start,
                "interval_end": start + BAR,
                "available_at": start + BAR,
            }
        ]
    )


def _proposal(
    detection_id: str,
    *,
    direction: str = "long",
    interval_start: str,
    interval_end: str,
    available_at: str,
    confidence: float,
    semantic_pass: bool = True,
    symbol: str = "ETH_USDT_SWAP",
) -> dict[str, object]:
    return {
        "detection_id": detection_id,
        "symbol": symbol,
        "direction": direction,
        "interval_start": pd.Timestamp(interval_start),
        "interval_end": pd.Timestamp(interval_end),
        "available_at": pd.Timestamp(available_at),
        "confidence": confidence,
        "core_start_i": 98,
        "core_end_i": 100,
        "confirmation_bars": 2,
        "window_len": 18,
        "structural_pass": True,
        "semantic_pass": semantic_pass,
    }


def test_fusion_accepts_boundary_overlap_and_uses_later_availability() -> None:
    proposals = pd.DataFrame(
        [
            _proposal(
                "early-lower-confidence",
                interval_start="2025-01-01T00:00:00Z",
                interval_end="2025-01-01T00:15:00Z",
                available_at="2025-01-01T00:45:00Z",
                confidence=0.30,
            ),
            _proposal(
                "later-higher-confidence",
                interval_start="2025-01-01T00:15:00Z",
                interval_end="2025-01-01T00:45:00Z",
                available_at="2025-01-01T01:00:00Z",
                confidence=0.95,
            ),
            _proposal(
                "wrong-side",
                direction="short",
                interval_start="2025-01-01T00:15:00Z",
                interval_end="2025-01-01T00:45:00Z",
                available_at="2025-01-01T00:45:00Z",
                confidence=0.99,
            ),
            _proposal(
                "semantic-reject",
                interval_start="2025-01-01T00:15:00Z",
                interval_end="2025-01-01T00:45:00Z",
                available_at="2025-01-01T00:30:00Z",
                confidence=0.99,
                semantic_pass=False,
            ),
        ]
    )

    fused = fuse_signal_intervals(_pine_signal(), proposals)

    assert len(fused) == 1
    row = fused.iloc[0]
    assert row["model_detection_id"] == "early-lower-confidence"
    assert row["combined_available_at"] == pd.Timestamp("2025-01-01T00:45:00Z")
    assert row["availability_delay_bars_from_pine"] == 1.0
    assert row["exact_same_start_bar"] is False or not row["exact_same_start_bar"]
    assert row["overlap_seconds"] == 0.0
    assert row["eligible_model_matches"] == 2


def test_fusion_ledger_validator_rejects_early_combined_availability() -> None:
    proposals = pd.DataFrame(
        [
            _proposal(
                "delayed-model",
                interval_start="2024-12-31T23:15:00Z",
                interval_end="2025-01-01T00:15:00Z",
                available_at="2025-01-01T00:45:00Z",
                confidence=0.30,
            )
        ]
    )
    proposals = proposals.assign(
        class_id=0,
        core_start_i=98,
        core_end_i=101,
        core_length_bars=4,
        window_start_i=86,
        window_end_i=103,
        window_len=18,
        confirmation_bars=2,
        window_end_bar_open=pd.Timestamp("2025-01-01T00:30:00Z"),
    )
    fused = fuse_signal_intervals(_pine_signal(), proposals)
    fused.loc[:, "combined_available_at"] = pd.Timestamp("2025-01-01T00:30:00Z")

    with pytest.raises(FusionError, match="combined availability"):
        validate_fusion_ledgers(_pine_signal(), proposals, fused)


def test_fusion_rejects_nonoverlap_wrong_symbol_and_wrong_direction() -> None:
    proposals = pd.DataFrame(
        [
            _proposal(
                "non-overlap",
                interval_start="2025-01-01T01:00:00Z",
                interval_end="2025-01-01T01:15:00Z",
                available_at="2025-01-01T01:30:00Z",
                confidence=0.9,
            ),
            _proposal(
                "wrong-symbol",
                symbol="BTC_USDT_SWAP",
                interval_start="2025-01-01T00:15:00Z",
                interval_end="2025-01-01T00:30:00Z",
                available_at="2025-01-01T00:45:00Z",
                confidence=0.9,
            ),
            _proposal(
                "wrong-direction",
                direction="short",
                interval_start="2025-01-01T00:15:00Z",
                interval_end="2025-01-01T00:30:00Z",
                available_at="2025-01-01T00:45:00Z",
                confidence=0.9,
            ),
        ]
    )

    assert fuse_signal_intervals(_pine_signal(), proposals).empty


def test_fusion_stage_can_audit_raw_and_structural_before_semantic_gate() -> None:
    proposals = pd.DataFrame(
        [
            _proposal(
                "structural-only",
                interval_start="2025-01-01T00:15:00Z",
                interval_end="2025-01-01T00:30:00Z",
                available_at="2025-01-01T00:45:00Z",
                confidence=0.70,
                semantic_pass=False,
            ),
            {
                **_proposal(
                    "raw-only",
                    interval_start="2025-01-01T00:15:00Z",
                    interval_end="2025-01-01T00:30:00Z",
                    available_at="2025-01-01T00:30:00Z",
                    confidence=0.80,
                    semantic_pass=False,
                ),
                "structural_pass": False,
            },
        ]
    )

    raw = fuse_signal_intervals(_pine_signal(), proposals, stage="raw")
    structural = fuse_signal_intervals(_pine_signal(), proposals, stage="structural")
    semantic = fuse_signal_intervals(_pine_signal(), proposals, stage="semantic")

    assert raw.iloc[0]["model_detection_id"] == "raw-only"
    assert structural.iloc[0]["model_detection_id"] == "structural-only"
    assert semantic.empty
