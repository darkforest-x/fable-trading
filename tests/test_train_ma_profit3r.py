"""Focused safety checks for the Windows MA-profit training entrypoint."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import cv2
import numpy as np
from yoyo.datasets.ma_profit_training_contract import EXPERIMENT_ID, OWNER_V2_REQUEST


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "windows" / "train_ma_profit3r.py"
SPEC = importlib.util.spec_from_file_location("train_ma_profit3r", PATH)
assert SPEC is not None and SPEC.loader is not None
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_windows_yaml_rewrites_only_root_and_keeps_portable_list_references(tmp_path):
    written = subject.write_windows_yaml(tmp_path, "A", r"C:\fable\datasets\ma_profit3r")
    text = written.read_text(encoding="utf-8")
    assert "path: C:/fable/datasets/ma_profit3r" in text
    assert "train: train_A.txt" in text and "val: val.txt" in text and "test: test.txt" in text


def test_all_semantic_and_spatial_augmentations_are_disabled():
    disabled = ("fliplr", "flipud", "mosaic", "mixup", "copy_paste", "hsv_h", "hsv_s", "hsv_v", "translate", "scale", "degrees", "shear", "perspective", "erasing")
    assert {key: subject.SAFE_AUG[key] for key in disabled} == {key: 0.0 for key in disabled}
    assert subject.SAFE_AUG["auto_augment"] is None


def test_selection_receipt_rejects_cluster_leakage():
    with pytest.raises(subject.ProfitTrainingError, match="cluster contains multiple"):
        subject._receipt_events({"events": [
            {"event_id": "one", "cluster_id": "same", "split": "train", "canonical_asset": "one", "direction": "LONG", "core_end_time": "2025-01-01T00:00:00Z"},
            {"event_id": "two", "cluster_id": "same", "split": "train", "canonical_asset": "two", "direction": "LONG", "core_end_time": "2025-01-01T00:00:00Z"},
        ]})


def test_dataset_validation_rejects_subquota_training_cohort(tmp_path):
    plan = tmp_path / "plan.json"
    _json(plan, {"experiment_id": "profit", "owner_authorization": {"training_authorized": True}, "safety": {"training_eligible": False}})
    contract = tmp_path / "contract.json"
    _json(contract, {"experiment_id": "profit", "original_plan_sha256": _sha(plan), "minimum_train_winners": 3000, "maximum_train_winners": 5000})
    cohort = tmp_path / "cohort.json"
    _json(cohort, {"experiment_id": "profit", "capacity_gate": True, "dataset_ledger_sha256": "e" * 64, "selected_events_sha256": "e" * 64, "training_contract_sha256": _sha(contract), "events": [{"event_id": "event", "cluster_id": "cluster", "split": "train", "canonical_asset": "asset", "direction": "LONG", "core_end_time": "2025-01-01T00:00:00+00:00"}]})
    rows = []
    for variant, arms in (("A", ["A"]), ("B1", ["B"]), ("B2", ["B"])):
        image = tmp_path / "images" / "train" / f"event_{variant}.png"
        label = tmp_path / "labels" / "train" / f"event_{variant}.txt"
        image.parent.mkdir(parents=True, exist_ok=True); label.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(cv2.imencode(".png", np.full((742,1280,3), 255, np.uint8))[1].tobytes()); label.write_text("0 0.5 0.5 0.1 0.1\n")
        rows.append({"event_id": "event", "cluster_id": "cluster", "canonical_asset": "asset", "direction": "LONG", "core_end_time": "2025-01-01T00:00:00+00:00", "class_id": 0, "box": {"cx_norm":.5,"cy_norm":.5,"w_norm":.1,"h_norm":.1}, "split": "train", "arms": arms, "variant": variant,
                     "image_path": image.relative_to(tmp_path).as_posix(), "image_sha256": _sha(image),
                     "label_path": label.relative_to(tmp_path).as_posix(), "label_sha256": _sha(label),
                     "visible_end_close_time_utc": "2025-01-01T00:00:00+00:00", "decision_at_utc": "2025-01-01T00:00:00+00:00"})
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    for name, paths in (("train_A.txt", ["./images/train/event_A.png"]), ("train_B.txt", ["./images/train/event_B1.png", "./images/train/event_B2.png"]), ("val.txt", []), ("test.txt", [])):
        (tmp_path / name).write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")
    summary = tmp_path / "summary.json"
    events_sha = "e" * 64
    _json(summary, {"manifest_sha256": _sha(manifest), "events_sha256": events_sha, "selection_receipt_sha256": _sha(cohort), "training_contract_sha256": _sha(contract)})
    dataset_plan = tmp_path / "dataset_plan.json"
    _json(dataset_plan, {"experiment_id": "profit", "original_plan_sha256": _sha(plan), "training_contract_sha256": _sha(contract), "selection_receipt_sha256": _sha(cohort), "events_sha256": events_sha})
    summary_payload = json.loads(summary.read_text())
    summary_payload["plan_sha256"] = _sha(dataset_plan)
    _json(summary, summary_payload)

    with pytest.raises(subject.ProfitTrainingError, match="outside 3000..5000"):
        subject.validate_dataset(tmp_path, plan, dataset_plan, contract, cohort)


def test_windows_runner_accepts_only_bound_owner_v2_capacity(tmp_path, monkeypatch):
    root = tmp_path / "repo"; exp = root / "experiment"; exp.mkdir(parents=True)
    monkeypatch.setattr(subject, "ROOT", root)
    plan = exp / "plan.json"
    _json(plan, {"experiment_id": EXPERIMENT_ID})
    original = exp / "training_contract.json"
    _json(original, {"experiment_id": EXPERIMENT_ID, "quota_scope": "train_independent_retained_events", "original_plan_sha256": _sha(plan), "minimum_train_winners": 3000, "maximum_train_winners": 5000})
    amendment = exp / "owner_amendment_1500_v2.json"
    _json(amendment, {"schema_version": 1, "experiment_id": EXPERIMENT_ID, "owner_request": OWNER_V2_REQUEST, "authorized_minimum_train_winners": 1500, "maximum_train_winners": 5000, "quota_scope": "train_independent_retained_events", "original_plan_sha256": _sha(plan), "original_training_contract_sha256": _sha(original), "only_capacity_changed": True, "training_authorized": True, "production_eligible": False})
    owner_v2 = exp / "training_contract_owner1500_v2.json"
    _json(owner_v2, {"schema_version": 2, "experiment_id": EXPERIMENT_ID, "quota_scope": "train_independent_retained_events", "original_plan_sha256": _sha(plan), "minimum_train_winners": 1500, "maximum_train_winners": 5000, "original_training_contract_sha256": _sha(original), "owner_amendment_path": str(amendment.relative_to(root)), "owner_amendment_sha256": _sha(amendment)})
    assert subject.validate_capacity_contract(plan, owner_v2) == (1500, 5000)
    payload = json.loads(owner_v2.read_text()); payload["minimum_train_winners"] = 1499; _json(owner_v2, payload)
    with pytest.raises(subject.ProfitTrainingError, match="exactly 1500/5000"):
        subject.validate_capacity_contract(plan, owner_v2)


@pytest.mark.parametrize("image_bytes,label,match", [
    (b"not-a-png", "0 0.5 0.5 0.1 0.1\n", "unreadable image"),
    (cv2.imencode(".png", np.full((742, 1280, 3), 255, np.uint8))[1].tobytes(), "0 1.1 0.5 0.1 0.1\n", "YOLO box leaves"),
])
def test_pixel_and_label_validator_rejects_bad_image_or_box(tmp_path, image_bytes, label, match):
    image, label_path = tmp_path / "images" / "train" / "x.png", tmp_path / "labels" / "train" / "x.txt"
    image.parent.mkdir(parents=True); label_path.parent.mkdir(parents=True)
    image.write_bytes(image_bytes); label_path.write_text(label)
    row = {"image_path": "images/train/x.png", "label_path": "labels/train/x.txt", "class_id": 0, "box": {"cx_norm": .5, "cy_norm": .5, "w_norm": .1, "h_norm": .1}, "split": "train"}
    with pytest.raises(subject.ProfitTrainingError, match=match):
        subject._validate_pixels_and_label(tmp_path, row)


def test_remote_dataset_root_must_match_audited_root(tmp_path):
    with pytest.raises(subject.ProfitTrainingError, match="must be the dataset"):
        subject.resolve_audited_remote_root(tmp_path.resolve(), str(tmp_path / "other"))
