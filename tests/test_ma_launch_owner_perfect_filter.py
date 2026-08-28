from __future__ import annotations

import numpy as np

from yoyo.datasets.ma_launch_owner_perfect_filter import (
    DEFAULT_PREREG,
    _deduplicate,
    _load_pinned_rows,
    constrained_dtw_cost,
    constrained_dtw_distance,
    derivative_transform,
    hard_gate_failures,
    segmented_lockstep_distance,
    segmented_sequence_distance,
    sequence_distance,
    verify_aeon_parity_fixture,
    z_normalize,
    read_json,
)


GATES = {
    "max_six_ma_end_bandwidth_atr": 0.95,
    "max_six_ma_core_envelope_atr": 1.50,
    "min_core_directional_progress_atr": -0.60,
    "max_core_directional_progress_atr": 1.00,
    "min_aligned_ma_slope_atr_per_bar": 0.02,
    "contracting_max_end_start_ratio": 0.90,
    "contracting_min_decrease_steps": 2,
    "crossing_max_end_start_ratio": 1.15,
    "crossing_min_pairwise_order_flips": 3,
    "min_candle_bundle_touch_rate": 0.40,
    "max_close_to_bundle_q75_atr": 1.50,
    "max_pre_body_q90_atr": 1.10,
    "max_pre_abs_path_atr": 5.50,
    "max_pre_last3_directional_progress_atr": 1.00,
    "max_pre_favourable_excursion_atr": 3.00,
    "min_post1_progress_atr": 0.00,
    "min_post2_progress_atr": 1.00,
    "min_post3_progress_atr": 1.25,
    "min_post5_progress_atr": 1.75,
    "min_post_progress_floor_atr": 0.00,
    "min_positive_post_steps_out_of_5": 3,
    "max_post_retrace_atr": 0.75,
    "max_post_reverse_body_count": 1,
    "max_opposite_post_body_atr": 0.80,
    "max_core_wick_q90_atr": 2.00,
    "max_core_reverse_body_count": 1,
    "max_core_body_atr": 1.20,
    "max_box_height_norm": 0.55,
}


def _passing_metrics() -> dict[str, float]:
    return {
        "six_ma_end_bandwidth_atr": 0.70,
        "six_ma_core_envelope_atr": 1.10,
        "core_width_end_start_ratio": 0.80,
        "core_width_decrease_steps": 3.0,
        "pairwise_order_flips": 3.0,
        "aligned_ma_slope_atr_per_bar": 0.04,
        "candle_bundle_touch_rate": 0.8,
        "close_to_bundle_q75_atr": 0.5,
        "pre_body_q90_atr": 0.5,
        "pre_abs_path_atr": 3.0,
        "pre_last3_directional_progress_atr": 0.5,
        "pre_max_favourable_excursion_atr": 1.0,
        "core_wick_q90_atr": 0.8,
        "core_reverse_body_count": 0.0,
        "core_max_body_atr": 0.7,
        "post1_progress_atr": 0.9,
        "post2_progress_atr": 1.2,
        "post3_progress_atr": 1.7,
        "post5_progress_atr": 2.5,
        "post_min_progress_atr": 0.0,
        "core_directional_progress_atr": 0.5,
        "positive_post_steps": 4.0,
        "post_retrace_atr": 0.2,
        "post_reverse_body_count": 0.0,
        "max_opposite_post_body_atr": 0.2,
        "box_height_norm": 0.3,
    }


def test_pinned_positive_and_owner_reference_lineage() -> None:
    positives, references, accepted_family, _ = _load_pinned_rows(
        read_json(DEFAULT_PREREG)
    )
    assert len(positives) == 10_000
    assert sum(row["training_data_yaml_exposed"] for row in positives) == 9_976
    assert len(references) == 19
    assert len(accepted_family) == 50
    roles = {row["reference_role"] for row in references}
    assert roles == {
        "perfect",
        "good",
        "standard_late",
        "semantic_reject",
        "boundary_wrong",
        "boundary_reboxed",
    }
    perfect = next(row for row in references if row["reference_role"] == "perfect")
    good = next(row for row in references if row["reference_role"] == "good")
    assert perfect["sample_id"] == "a20a0a4e50a94b1a017d38a0"
    assert good["sample_id"] == "4e86ddc32a5401c49bf4aeb3"


def test_hard_gate_reports_every_failed_axis() -> None:
    metrics = _passing_metrics()
    assert hard_gate_failures(metrics, GATES) == []
    metrics.update(
        {
            "six_ma_end_bandwidth_atr": 1.0,
            "six_ma_core_envelope_atr": 1.6,
            "core_width_end_start_ratio": 1.2,
            "core_width_decrease_steps": 1.0,
            "pairwise_order_flips": 2.0,
            "aligned_ma_slope_atr_per_bar": 0.01,
            "candle_bundle_touch_rate": 0.2,
            "close_to_bundle_q75_atr": 1.6,
            "pre_body_q90_atr": 1.2,
            "pre_abs_path_atr": 5.6,
            "pre_last3_directional_progress_atr": 1.1,
            "pre_max_favourable_excursion_atr": 3.1,
            "core_wick_q90_atr": 2.1,
            "core_reverse_body_count": 2.0,
            "core_max_body_atr": 1.3,
            "post1_progress_atr": -0.1,
            "post2_progress_atr": 0.9,
            "post3_progress_atr": 1.2,
            "post5_progress_atr": 1.7,
            "post_min_progress_atr": -0.1,
            "core_directional_progress_atr": 1.1,
            "positive_post_steps": 2.0,
            "post_retrace_atr": 0.8,
            "post_reverse_body_count": 2.0,
            "max_opposite_post_body_atr": 0.9,
            "box_height_norm": 0.6,
        }
    )
    assert hard_gate_failures(metrics, GATES) == [
        "six_ma_end_bandwidth_atr",
        "six_ma_core_envelope_atr",
        "core_directional_progress_too_large",
        "aligned_ma_slope_atr_per_bar",
        "ma_bundle_topology",
        "candle_bundle_touch_rate",
        "close_to_bundle_q75_atr",
        "pre_body_q90_atr",
        "pre_abs_path_atr",
        "pre_last3_directional_progress_atr",
        "pre_max_favourable_excursion_atr",
        "core_wick_q90_atr",
        "core_reverse_body_count",
        "core_max_body_atr",
        "post1_progress_atr",
        "post2_progress_atr",
        "post3_progress_atr",
        "post5_progress_atr",
        "post_min_progress_atr",
        "positive_post_steps",
        "post_retrace_atr",
        "post_reverse_body_count",
        "max_opposite_post_body_atr",
        "box_height_norm",
    ]
    assert hard_gate_failures(metrics, GATES, include_box=False)[-1] == (
        "max_opposite_post_body_atr"
    )


def test_constrained_dtw_identity_and_local_warp() -> None:
    base = np.vstack(
        [
            np.linspace(-1.0, 1.0, 13),
            np.sin(np.linspace(0.0, np.pi, 13)),
        ]
    )
    assert constrained_dtw_distance(base, base, radius=2) == 0.0
    delayed = np.c_[base[:, :1], base[:, :-1]]
    lockstep_cost = float(np.sum((base - delayed) ** 2))
    assert constrained_dtw_cost(base, delayed, radius=2) < lockstep_cost


def test_sequence_distance_is_zero_for_identity() -> None:
    sequence = np.vstack(
        [
            np.linspace(-2.0, 3.0, 13),
            np.cos(np.linspace(0.0, np.pi, 13)),
            np.linspace(1.0, 2.0, 13) ** 2,
        ]
    )
    result = sequence_distance(
        sequence,
        sequence,
        radius=2,
        weights={"lockstep": 0.35, "dtw": 0.40, "ddtw": 0.25},
    )
    assert result == {
        "lockstep_distance": 0.0,
        "dtw_distance": 0.0,
        "ddtw_distance": 0.0,
        "combined_distance": 0.0,
    }


def test_segmented_distance_preserves_prelude_core_release_boundaries() -> None:
    sequence = np.vstack(
        [
            np.linspace(-2.0, 3.0, 22),
            np.cos(np.linspace(0.0, np.pi, 22)),
            np.linspace(1.0, 2.0, 22) ** 2,
        ]
    )
    result = segmented_sequence_distance(
        sequence,
        sequence,
        radius=2,
        component_weights={"lockstep": 0.35, "dtw": 0.40, "ddtw": 0.25},
        segment_slices={"prelude": [0, 12], "core": [12, 17], "release": [17, 22]},
        segment_weights={"prelude": 0.25, "core": 0.35, "release": 0.40},
    )
    assert result["combined_distance"] == 0.0
    assert result["prelude_combined_distance"] == 0.0
    assert result["core_combined_distance"] == 0.0
    assert result["release_combined_distance"] == 0.0
    assert (
        segmented_lockstep_distance(
            sequence,
            sequence,
            segment_slices={
                "prelude": [0, 12],
                "core": [12, 17],
                "release": [17, 22],
            },
            segment_weights={"prelude": 0.25, "core": 0.35, "release": 0.40},
        )
        == 0.0
    )


def test_channel_z_normalization_and_derivative_preserve_shape() -> None:
    sequence = np.vstack(
        [np.arange(13, dtype=float), 10.0 + 3.0 * np.arange(13, dtype=float)]
    )
    normalized = z_normalize(sequence)
    np.testing.assert_allclose(normalized.mean(axis=1), 0.0, atol=1e-12)
    np.testing.assert_allclose(normalized.std(axis=1), 1.0, atol=1e-12)
    derivative = derivative_transform(normalized)
    assert derivative.shape == (normalized.shape[0], normalized.shape[1] - 2)


def test_frozen_aeon_1_5_fixture_matches_raw_costs() -> None:
    receipt = verify_aeon_parity_fixture(
        {
            "aeon_1_5_parity_fixture": {
                "expected_dtw_cost": 1.3172096700811546,
                "expected_ddtw_cost": 0.34713860704043453,
                "absolute_tolerance": 1e-12,
            }
        }
    )
    assert receipt["passed"] is True


def _dedup_row(sample_id: str, timestamp: str, score: float) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "quality_tier": "PERFECT_CANDIDATE",
        "training_data_yaml_exposed": True,
        "quality_score": score,
        "source_path": "data/example.csv",
        "source_core_start_i": int(sample_id),
        "source_core_end_i": int(sample_id) + 4,
        "direction": "LONG",
        "symbol": "EXAMPLE_USDT_SWAP",
        "image_sha256": f"sha-{sample_id}",
        "core_end_time": timestamp,
        "time_block": "2026-01",
    }


def test_event_dedup_includes_exact_four_hour_boundary() -> None:
    rows = [
        _dedup_row("1", "2026-01-01T00:00:00Z", 0.8),
        _dedup_row("2", "2026-01-01T04:00:00Z", 0.9),
        _dedup_row("3", "2026-01-01T08:15:00Z", 0.7),
    ]
    _deduplicate(
        rows,
        {
            "same_symbol_direction_event_gap_minutes": 240,
            "max_shortlist_per_symbol_per_direction_time_block": 10,
            "max_shortlist_per_symbol_per_direction": 10,
        },
    )
    kept = {str(row["sample_id"]) for row in rows if row["event_dedup_kept"]}
    assert kept == {"2", "3"}


def test_event_dedup_prevents_transitive_near_winners() -> None:
    rows = [
        _dedup_row("1", "2026-01-01T00:00:00Z", 0.8),
        _dedup_row("2", "2026-01-01T03:00:00Z", 0.9),
        _dedup_row("3", "2026-01-01T06:00:00Z", 0.7),
    ]
    _deduplicate(
        rows,
        {
            "same_symbol_direction_event_gap_minutes": 240,
            "max_shortlist_per_symbol_per_direction_time_block": 10,
            "max_shortlist_per_symbol_per_direction": 10,
        },
    )
    kept = {str(row["sample_id"]) for row in rows if row["event_dedup_kept"]}
    assert kept == {"2"}
