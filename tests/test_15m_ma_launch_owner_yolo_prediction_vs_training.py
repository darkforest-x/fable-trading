"""Contract tests for the Owner-YOLO prediction/training semantic audit."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.audit_15m_ma_launch_owner_yolo_prediction_vs_training import (
    DEFAULT_PREREG,
    EXPERIMENT_ID,
    box_corners_from_normalized,
    feature_gate_checks,
    gate_failures,
    representative_orders,
    verify_preregistration,
    vertical_iou,
)


ROOT = Path(__file__).resolve().parents[1]
AUTOFILL_PREREG = (
    ROOT / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
)


def _passing_features() -> dict[str, float]:
    return {
        "ma_envelope_atr": 1.0,
        "ma_spread_end_atr": 0.8,
        "candle_envelope_atr": 1.8,
        "max_body_atr": 0.7,
        "core_progress_atr": 0.2,
        "post1_progress_atr": 0.2,
        "post2_progress_atr": 1.2,
        "post3_progress_atr": 1.5,
        "post5_progress_atr": 2.0,
        "aligned_ma_slope_atr": 0.1,
        "ma_slope_std_atr": 0.1,
        "minimum_close_to_ma_atr": 0.5,
        "max_close_to_ma_envelope_atr": 1.0,
        "max_body_to_ma_envelope_atr": 0.8,
    }


def test_preregistration_pins_the_authorized_fourth_holdout_inspection() -> None:
    payload = verify_preregistration(DEFAULT_PREREG)
    assert payload["experiment_id"] == EXPERIMENT_ID
    assert payload["owner_authorization"]["holdout_consumption_number_for_this_configuration"] == 4
    assert payload["safety"]["new_model_inference"] is False
    assert payload["safety"]["training"] is False
    assert payload["safety"]["label_change"] is False


def test_exact_training_gate_reports_density_failures() -> None:
    gates = json.loads(AUTOFILL_PREREG.read_text(encoding="utf-8"))["morphology_gate"]
    passing = _passing_features()
    assert all(feature_gate_checks(passing, gates).values())
    assert gate_failures(passing, gates) == []

    failing = dict(passing, ma_envelope_atr=1.50001, ma_spread_end_atr=1.10001)
    assert gate_failures(failing, gates) == ["ma_envelope", "ma_spread_end"]


def test_vertical_iou_and_normalized_box_mapping_are_explicit() -> None:
    assert vertical_iou(0.0, 10.0, 2.0, 8.0) == 0.6
    assert vertical_iou(0.0, 1.0, 2.0, 3.0) == 0.0
    assert box_corners_from_normalized(
        0.5, 0.5, 0.25, 0.5, image_width=1280, image_height=742
    ) == (480, 186, 800, 556)


def test_representative_selection_keeps_matches_and_hard_failures() -> None:
    rows = []
    for order in range(1, 9):
        rows.append(
            {
                "event_order": order,
                "strict_training_spec_match": order in {2, 7},
                "similarity_distance_to_owner50": float(order) / 10,
                "ma_envelope_atr": float(9 - order),
                "distance_ok": order in {2, 3, 7},
                "confidence": float(order) / 10,
            }
        )
    selected = representative_orders(pd.DataFrame(rows))
    assert len(selected) == 6
    assert {2, 7}.issubset(selected)
    assert len(selected) == len(set(selected))
