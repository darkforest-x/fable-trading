from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.evaluate_eth3m_short_pilot_v2_cls import (
    assert_fixed_threshold,
    evaluate_predictions,
    select_manifest_rows,
    summarize_training_results,
    verify_remote_training_evidence,
)


def test_fixed_threshold_rejects_any_sweep_value() -> None:
    assert_fixed_threshold(0.50)
    with pytest.raises(ValueError, match="frozen"):
        assert_fixed_threshold(0.49)


def test_evaluate_predictions_computes_confusion_and_auc() -> None:
    rows = [
        {"split": "val", "class_name": "short_start", "target": 1, "pred_target": 1, "p_short_start": 0.9},
        {"split": "val", "class_name": "short_start", "target": 1, "pred_target": 0, "p_short_start": 0.4},
        {"split": "val", "class_name": "no_start", "target": 0, "pred_target": 1, "p_short_start": 0.8},
        {"split": "val", "class_name": "no_start", "target": 0, "pred_target": 0, "p_short_start": 0.1},
    ]
    metrics = evaluate_predictions(rows)["val"]
    assert metrics["tp"] == 1
    assert metrics["fp"] == 1
    assert metrics["tn"] == 1
    assert metrics["fn"] == 1
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["specificity"] == 0.5
    assert metrics["balanced_accuracy"] == 0.5
    assert metrics["roc_auc"] == 0.75


def test_training_summary_uses_first_max_top1_epoch(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "results.csv").write_text(
        "epoch,metrics/accuracy_top1,lr/pg2\n"
        "1,0.80952,0.077023\n"
        "2,0.80952,0.0530465\n"
        "3,0.78571,0.0290696\n",
        encoding="utf-8",
    )
    summary = summarize_training_results(run_dir, val_n=42)
    assert summary["best_epoch_by_first_max_top1"] == 1
    assert summary["best_top1_count"] == 34
    assert summary["first_epoch_lr_pg2"] == 0.077023


def test_select_manifest_rows_rejects_forbidden_eval_paths(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = [
        {"sample_id": "bad", "split": "val", "class_name": "no_start", "target": "0", "anchor_time": "2026-01-01", "image_rel": "weak_or_review/no_start/bad.png"},
    ]
    with (dataset / "manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="forbidden"):
        select_manifest_rows(dataset, ("val",))


def test_remote_training_evidence_requires_zero_and_completion_markers(tmp_path: Path) -> None:
    log = tmp_path / "remote.log"
    receipt = tmp_path / "remote.exit"
    remote_best = tmp_path / "remote_best.pt"
    local_best = tmp_path / "local_best.pt"
    log.write_text(
        '{"status": "preflight_passed"}\n'
        "NVIDIA GeForce RTX 3060\n"
        "Best results observed at epoch 1\n"
        "[launcher] exit_code=0\n",
        encoding="utf-8",
    )
    receipt.write_text("0\r\n", encoding="utf-8")
    # The production helper also pins the frozen model hash.  Patch the module
    # constant to this fixture's digest so this test stays tiny and offline.
    remote_best.write_bytes(b"same-frozen-weights")
    local_best.write_bytes(remote_best.read_bytes())
    from scripts import evaluate_eth3m_short_pilot_v2_cls as evaluator

    expected = evaluator.EXPECTED_REMOTE_WEIGHTS_SHA256
    evaluator.EXPECTED_REMOTE_WEIGHTS_SHA256 = evaluator.sha256(remote_best)
    try:
        evidence = verify_remote_training_evidence(log, receipt, remote_best, local_best)
    finally:
        evaluator.EXPECTED_REMOTE_WEIGHTS_SHA256 = expected
    assert evidence["exit_code"] == 0
    assert evidence["log_bytes"] > 0
    assert evidence["remote_local_best_match"] is True

    receipt.write_text("1\r\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not zero"):
        verify_remote_training_evidence(log, receipt, remote_best, local_best)
