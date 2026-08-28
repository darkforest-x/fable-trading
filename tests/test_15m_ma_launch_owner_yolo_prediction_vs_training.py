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
from scripts.verify_15m_ma_launch_owner_yolo_prediction_vs_training import verify
from scripts.send_15m_ma_launch_owner_yolo_prediction_training_parity import (
    ARCHIVE,
    REPORT,
    RECEIPT,
    artifact_contract,
    deliver,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
AUTOFILL_PREREG = (
    ROOT / "experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json"
)
RESULTS = (
    ROOT
    / "experiments/active"
    / "exp-15m-ma-launch-owner-yolo-20260827-training-parity-audit-v1"
    / "results"
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


def test_committed_prediction_training_parity_artifacts_verify() -> None:
    result = verify(RESULTS)
    assert result["passed"] is True
    assert result["strict_training_spec_matches"] == 2
    assert result["out_of_training_spec"] == 41
    assert result["failing_events_with_alternative_core"] == 0
    assert result["comparison_images"] == 43


def test_telegram_contract_builds_complete_lossless_archive(tmp_path: Path) -> None:
    archive = tmp_path / "all_43.zip"
    artifacts, contract_sha = artifact_contract(
        results=RESULTS,
        report=REPORT,
        archive=archive,
    )
    assert [row["id"] for row in artifacts] == [
        "overview",
        "representative_pairs",
        "full_43_archive",
        "html_report",
    ]
    assert len(contract_sha) == 64
    assert archive.is_file()


def test_telegram_delivery_is_resumable_and_needs_no_owner_review(tmp_path: Path) -> None:
    sent_text: list[str] = []
    sent_documents: list[tuple[str, str]] = []

    receipt = deliver(
        results=RESULTS,
        report=REPORT,
        archive=tmp_path / "all_43.zip",
        receipt_path=tmp_path / "telegram_receipt.json",
        sleep_seconds=0,
        send_text=lambda value: sent_text.append(value) is None,
        send_document=lambda path, caption: sent_documents.append((path.name, caption)) is None,
    )
    assert receipt["delivery_complete"] is True
    assert receipt["manual_owner_review_required"] is False
    assert len(sent_text) == 2
    assert len(sent_documents) == 4


def test_committed_telegram_delivery_is_complete_and_hash_bound() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert receipt["delivery_complete"] is True
    assert receipt["manual_owner_review_required"] is False
    assert receipt["expected_documents"] == 4
    assert [row["id"] for row in receipt["document_actions"]] == [
        "overview",
        "representative_pairs",
        "full_43_archive",
        "html_report",
    ]
    for row in receipt["document_actions"]:
        assert sha256_file(Path(row["path"])) == row["sha256"]
    assert sha256_file(ARCHIVE) == receipt["document_actions"][2]["sha256"]
