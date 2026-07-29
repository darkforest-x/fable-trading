from __future__ import annotations

import copy

import pytest

from src.reporting.eth3m_v2_problem_analysis import build_from_files


def _artifact() -> dict:
    return build_from_files(generated_at="2026-07-30T12:00:00Z")


def test_report_preserves_failure_evidence_and_scope() -> None:
    artifact = _artifact()
    datasets = artifact["snapshot"]["datasets"]
    headline = datasets["headline"][0]
    val = next(row for row in datasets["split_results"] if row["split"] == "val")

    assert artifact["surface"] == "report"
    assert headline["independent_positive_events"] == 29
    assert headline["positive_dependency_blocks"] == 23
    assert headline["validation_positive_blocks"] == 5
    assert headline["anchor_shortcut_accuracy"] == 136 / 137
    assert headline["validation_recall"] == 0.0
    assert headline["validation_accuracy"] == headline["majority_accuracy"]
    assert headline["holdout_rows_read"] == 0
    assert (val["tp"], val["fp"], val["tn"], val["fn"]) == (0, 0, 34, 8)


def test_report_keeps_proposals_distinct_from_active_thresholds() -> None:
    artifact = _artifact()
    gates = artifact["snapshot"]["datasets"]["quality_gates"]
    decisions = artifact["snapshot"]["datasets"]["decisions"]

    assert any("建议" in row["pass_rule"] for row in gates)
    assert any("批准" in row["recommendation"] for row in decisions)
    all_text = json_like_text(artifact)
    assert "不读 holdout" in all_text
    assert "不 promote" in all_text


def test_report_rejects_changed_frozen_confusion_matrix() -> None:
    import src.reporting.eth3m_v2_problem_analysis as report

    summary = report._read_json(report.SUMMARY_PATH)
    prereg = report._read_json(report.PREREG_PATH)
    quality = report._read_json(report.QUALITY_AUDIT_PATH)
    changed = copy.deepcopy(summary)
    changed["metrics"]["val"]["tp"] = 1

    with pytest.raises(ValueError, match="evidence contract changed"):
        report.build_artifact(
            changed, prereg, quality, generated_at="2026-07-30T12:00:00Z"
        )


def json_like_text(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(json_like_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(json_like_text(item) for item in value)
    return str(value)
