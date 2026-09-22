"""Receipt bindings for the negative-redo collection and delivery gates."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.research import run_ma_profit_negative_redo as runner
from yoyo.evaluation import ma_profit_negative_delivery as delivery
from yoyo.datasets.ma_profit_negative_redo import sha256


def _write(path: Path, content: str = "{}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def bound_receipts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Small on-disk reproduction of the control-metrics receipt input schema."""
    root, old = tmp_path / "repo", tmp_path / "old"
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "OLD", old)
    monkeypatch.setattr(delivery, "ROOT", root)
    monkeypatch.setattr(delivery, "OLD", old)

    ledger = _write(root / "ledger.json", "{\"event_id\": \"e1\"}\n")
    _write(root / runner.CONTROL_METRICS_PATH, "# frozen controls builder\n")
    for relative in (
        "matched_controls_owner1500_v2/receipt.json",
        "matched_controls_owner1500_v2/frozen_events.jsonl",
        "matched_controls_owner1500_v2/frozen_sources.json",
        "matched_control_outcomes_owner1500_v2/summary.json",
        "matched_control_outcomes_owner1500_v2/outcomes.jsonl",
    ):
        _write(old / relative)

    evaluation = root / "evaluation/arm_A"
    events = {}
    for split in ("val", "test"):
        event_path = _write(evaluation / f"events_{split}.jsonl", f"{{\"split\": \"{split}\"}}\n")
        events[f"events_{split}.jsonl"] = sha256(event_path)
    evaluation_receipt = {
        "status": "completed", "arm": "A", "model_sha256": "model", "manifest_sha256": "manifest",
        "ledger_sha256": sha256(ledger), "runner_sha256": "runner", "metrics_code_sha256": "metrics",
        "splits": ["val", "test"], "artifacts": events,
    }
    _write(evaluation / "receipt.json", json.dumps(evaluation_receipt))
    arm_b_receipt = _write(root / "evaluation/arm_B/receipt.json", "{\"arm\": \"B\"}\n")

    plan = {"inputs": {"old_ledger": {"path": "ledger.json", "sha256": sha256(ledger)}}}
    expected_inputs = runner._expected_control_inputs(plan, evaluation, evaluation_receipt)
    controls_receipt = {
        "status": "completed", "arm": "A",
        "inputs": [{"path": path, "sha256": digest} for path, digest in expected_inputs.items()],
        "artifacts": {"matched_metrics_val.json": "v", "matched_metrics_test.json": "t"},
    }
    return plan, evaluation, evaluation_receipt, controls_receipt, arm_b_receipt


def test_receipts_require_frozen_evaluator_and_current_arm_evaluation(bound_receipts):
    """A matching schema passes; evaluator drift and another arm's receipt fail closed."""
    plan, evaluation, evaluation_receipt, controls_receipt, arm_b_receipt = bound_receipts
    launch = {"files": [
        {"path": runner.EVALUATOR_PATH, "sha256": evaluation_receipt["runner_sha256"]},
        {"path": runner.METRICS_PATH, "sha256": evaluation_receipt["metrics_code_sha256"]},
    ]}
    expected_evaluation = {
        "status": "completed", "arm": "A", "model_sha256": "model", "manifest_sha256": "manifest",
        "ledger_sha256": plan["inputs"]["old_ledger"]["sha256"],
        "runner_sha256": runner._launch_file_sha(launch, runner.EVALUATOR_PATH),
        "metrics_code_sha256": runner._launch_file_sha(launch, runner.METRICS_PATH),
    }
    runner._validate_evaluation_receipt(evaluation_receipt, expected_evaluation)
    delivery._validate_evaluation_receipt(evaluation_receipt, expected_evaluation)

    expected_controls = runner._expected_control_inputs(plan, evaluation, evaluation_receipt)
    runner._validate_control_receipt(controls_receipt, arm="A", expected_inputs=expected_controls)
    delivery._validate_control_receipt(
        controls_receipt, arm="A",
        expected_inputs=delivery._expected_control_inputs(plan, evaluation, evaluation_receipt),
    )

    drifted_evaluator = copy.deepcopy(evaluation_receipt)
    drifted_evaluator["metrics_code_sha256"] = "different"
    with pytest.raises(RuntimeError, match="evaluation binding"):
        runner._validate_evaluation_receipt(drifted_evaluator, expected_evaluation)
    with pytest.raises(RuntimeError, match="eval contract drift"):
        delivery._validate_evaluation_receipt(drifted_evaluator, expected_evaluation)

    wrong_evaluation = copy.deepcopy(controls_receipt)
    for item in wrong_evaluation["inputs"]:
        if item["path"] == str((evaluation / "receipt.json").resolve()):
            item.update(path=str(arm_b_receipt.resolve()), sha256=sha256(arm_b_receipt))
            break
    else:
        raise AssertionError("fixture control receipt omitted its evaluation receipt")
    with pytest.raises(RuntimeError, match="input binding"):
        runner._validate_control_receipt(wrong_evaluation, arm="A", expected_inputs=expected_controls)
    with pytest.raises(RuntimeError, match="input binding"):
        delivery._validate_control_receipt(
            wrong_evaluation, arm="A",
            expected_inputs=delivery._expected_control_inputs(plan, evaluation, evaluation_receipt),
        )
