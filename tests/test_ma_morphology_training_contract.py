"""Pure contract tests for the morphology-only Windows training preflight."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.windows.run_ma_morphology_redo import (
    MorphologyTrainingError,
    _morphology_summary,
    expected_training_args,
    validate_preflight_contract,
    validate_results_shape,
    write_morphology_yaml,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    (tmp_path / "models").mkdir()
    base = tmp_path / "models" / "yolo11s.pt"
    base.write_bytes(b"frozen base")
    code, input_file = tmp_path / "runner.py", tmp_path / "input.json"
    code.write_text("# frozen runner\n", encoding="utf-8")
    input_file.write_text("{}\n", encoding="utf-8")
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = dataset / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    manifest_sha = sha(manifest)
    write_json(dataset / "summary.json", {
        "pilot": False, "training_eligible": False, "production_eligible": False,
        "manifest_sha256": manifest_sha,
    })
    review = tmp_path / "review.json"
    write_json(review, {"status": "passed_rendering_spot_check", "manifest_sha256": manifest_sha})
    audit = {"status": "passed", "pilot": False, "per_sample_owner_gold": False, "morphology_evidence_verified_from_ledger": True}
    plan = tmp_path / "plan.json"
    write_json(plan, {
        "experiment_id": "exp-ma-morphology-negatives-20260922-v3",
        "owner_authorization": {"training_authorized": True},
        "training_eligible": False, "production_eligible": False,
        "training_args": expected_training_args(),
        "inputs": {"base_model": {"path": "models/yolo11s.pt", "sha256": sha(base)}},
    })
    contract = tmp_path / "launch.json"
    write_json(contract, {
        "experiment_id": "exp-ma-morphology-negatives-20260922-v3",
        "plan_sha256": sha(plan), "base_model_sha256": sha(base), "dataset_audit": audit,
        "review_path": "review.json", "review_sha256": sha(review),
        "review_manifest_sha256": manifest_sha,
        "files": {"runner.py": sha(code), "input.json": sha(input_file)},
    })
    return plan, dataset, contract, audit


def test_preflight_accepts_exact_offline_contract(tmp_path: Path) -> None:
    plan, dataset, contract, audit = fixture(tmp_path)
    result = validate_preflight_contract(plan_path=plan, dataset=dataset, launch_contract_path=contract, audit_result=audit, root=tmp_path)
    assert result["training_args"] == expected_training_args()
    assert result["dataset_audit"] == audit
    assert set(result["launch_files"]) == {"input.json", "runner.py"}


@pytest.mark.parametrize("mutation", ["pilot", "quarantine", "semantics"])
def test_preflight_rejects_pilot_quarantine_or_semantics_drift(tmp_path: Path, mutation: str) -> None:
    plan, dataset, contract, audit = fixture(tmp_path)
    if mutation == "pilot":
        write_json(dataset / "summary.json", {
            "pilot": True, "training_eligible": False, "production_eligible": False,
            "manifest_sha256": sha(dataset / "manifest.jsonl"),
        })
    elif mutation == "quarantine":
        write_json(dataset / "DO_NOT_TRAIN_LABEL_SEMANTICS.json", {"reason": "unresolved"})
    else:
        audit = {**audit, "per_sample_owner_gold": True}
    with pytest.raises(MorphologyTrainingError):
        validate_preflight_contract(plan_path=plan, dataset=dataset, launch_contract_path=contract, audit_result=audit, root=tmp_path)


def test_results_shape_requires_exactly_40_finite_epochs(tmp_path: Path) -> None:
    complete = tmp_path / "complete.csv"
    pd.DataFrame({"epoch": range(1,41), "metric": [0.1] * 40}).to_csv(complete, index=False)
    assert validate_results_shape(complete) == {"epochs": 40, "columns": ["epoch", "metric"], "sha256": sha(complete)}

    short = tmp_path / "short.csv"
    pd.DataFrame({"epoch": range(39), "metric": [0.1] * 39}).to_csv(short, index=False)
    with pytest.raises(MorphologyTrainingError, match="expected 40"):
        validate_results_shape(short)

    nonfinite = tmp_path / "nonfinite.csv"
    pd.DataFrame({"epoch": range(1,41), "metric": [float("nan")] * 40}).to_csv(nonfinite, index=False)
    with pytest.raises(MorphologyTrainingError, match="finite"):
        validate_results_shape(nonfinite)

    duplicate = tmp_path / "duplicate.csv"
    pd.DataFrame({"epoch": [1] * 40, "metric": [0.1] * 40}).to_csv(duplicate, index=False)
    with pytest.raises(MorphologyTrainingError, match="epoch sequence"):
        validate_results_shape(duplicate)


def test_windows_yaml_freezes_morphology_class_names(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    text = write_morphology_yaml(dataset, "A").read_text(encoding="utf-8")
    assert "names: [dense_launch_long, dense_launch_short]" in text
    assert "profitable_dense" not in text


def test_morphology_metrics_do_not_pool_validation_and_test() -> None:
    records = [
        {"event_id": "v-pos", "binary_label": 1, "low_conf_max_event_score_all_classes": 0.9,
         "background_false_positive_conf025_iou05": False, "matched_class_spatial_hit_iou_ge_05": True},
        {"event_id": "v-bg", "binary_label": 0, "low_conf_max_event_score_all_classes": 0.1,
         "background_false_positive_conf025_iou05": False, "matched_class_spatial_hit_iou_ge_05": False},
    ]
    validation = _morphology_summary(records)
    testing = _morphology_summary([{**records[1], "event_id": "t-bg", "background_false_positive_conf025_iou05": True}])
    assert validation["background_false_positive_rate_conf025_iou05"] == 0.0
    assert validation["positive_matched_class_spatial_hit_rate_conf025_iou05"] == 1.0
    assert validation["low_conf_all_class_event_score_roc_auc"] == 1.0
    assert testing["background_false_positive_rate_conf025_iou05"] == 1.0
    assert testing["positive_matched_class_spatial_hit_rate_conf025_iou05"] is None
    assert testing["low_conf_all_class_event_score_roc_auc"] is None
