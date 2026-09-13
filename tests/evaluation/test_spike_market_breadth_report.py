"""Synthetic contracts for the SPIKE breadth report integrator."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import yoyo.evaluation.spike_market_breadth_matched_controls as matched_controls
import yoyo.evaluation.spike_market_breadth_study as breadth_study
from yoyo.evaluation.spike_market_breadth_report import build_spike_market_breadth_report, main


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return _sha(path)


def _bundle(tmp_path):
    stage, matched = tmp_path / "stage", tmp_path / "matched"
    stage.mkdir()
    (stage / "candidate_context.csv.gz").write_bytes(b"opaque-candidate-detail; never parse this")
    _csv(stage / "source_manifest.csv", [{"source": "opaque-source-catalog"}])
    outcome = []
    frozen = []
    slices = []
    controls = []
    for variant, minutes in (("v1_common_execution_long", 30), ("v7_bb_long", 60)):
        kept_10r = 0 if variant.startswith("v7") else 1
        frozen_delta = -.1 if variant.startswith("v7") else .2
        outcome.append(dict(variant=variant, timeframe_min=minutes, slice="all", metric="all", candidates=10,
                            closed=10, censored=0, mean_net_r=.1, median_net_r=-.2, win_rate=.4,
                            realized_ge_10r_count=1, realized_ge_10r=.1))
        frozen.append(dict(variant=variant, timeframe_min=minutes, rule="joint_delta_60m > 0",
                           baseline_candidates=10, candidate_retention=.5, baseline_realized_ge_10r_count=1,
                           kept_realized_ge_10r_count=kept_10r, exact_entry_10r_retention=float(kept_10r), candidates=5, closed=5,
                           mean_net_r=.2, median_net_r=.1, win_rate=.6, realized_ge_10r=.2))
        for metric in ("joint_breadth", "launch_density_1h"):
            for slice_name, value in (("bottom_quartile", -.2), ("top_quartile", .3)):
                slices.append(dict(variant=variant, timeframe_min=minutes, threshold=0., slice=slice_name,
                                   metric=metric, candidates=3, closed=3, censored=0, mean_net_r=value,
                                   median_net_r=value, win_rate=.5, realized_ge_10r_count=0, realized_ge_10r=0.,
                                   matched=0, matched_delta_net_r=float("nan"), matched_signflip_p=float("nan")))
                controls.append(dict(variant=variant, timeframe_min=minutes, metric=metric, slice=slice_name,
                                     targets=3, matched=3, match_rate=1., target_mean_net_r=value,
                                     control_mean_net_r=0., paired_delta_mean_net_r=value,
                                     paired_sign_flip_p=.123456789, sign_flip_unit="calendar_month", unmatched_reasons="{}"))
        controls.extend([
            dict(variant=variant, timeframe_min=minutes, metric="baseline", slice="all", targets=10, matched=10,
                 match_rate=1., target_mean_net_r=.1, control_mean_net_r=0., paired_delta_mean_net_r=.1,
                 paired_sign_flip_p=.02, sign_flip_unit="calendar_month", unmatched_reasons="{}"),
            dict(variant=variant, timeframe_min=minutes, metric="joint_delta_60m", slice="positive_rule", targets=5,
                 matched=5, match_rate=1., target_mean_net_r=frozen_delta, control_mean_net_r=0., paired_delta_mean_net_r=frozen_delta,
                 paired_sign_flip_p=.5,
                 sign_flip_unit="calendar_month", unmatched_reasons="{}"),
        ])
    hashes = {
        "candidate_context.csv.gz": _sha(stage / "candidate_context.csv.gz"),
        "source_manifest.csv": _sha(stage / "source_manifest.csv"),
        "outcome_summary.csv": _csv(stage / "outcome_summary.csv", outcome),
        "frozen_candidate_rule.csv": _csv(stage / "frozen_candidate_rule.csv", frozen),
        "single_variable_slices.csv": _csv(stage / "single_variable_slices.csv", slices),
    }
    (stage / "manifest.json").write_text(json.dumps({
        "development_start": "2024-09-10T00:00:00+00:00", "development_end_exclusive": "2025-09-10T00:00:00+00:00",
        "holdout_consumed": True, "frozen_rule": "joint_delta_60m > 0", "study_code_sha256": _sha(Path(breadth_study.__file__)),
        "outputs": hashes,
    }))
    matched.mkdir()
    matched_summary_sha = _csv(matched / "matched_control_summary.csv", controls)
    (matched / "matched_control_pairs.csv.gz").write_bytes(b"opaque matched pairs; never parse this")
    (matched / "control_receipts.csv").write_bytes(b"opaque control receipts; never parse this")
    (matched / "manifest.json").write_text(json.dumps({
        "development_start": "2024-09-10T00:00:00+00:00", "development_end_exclusive": "2025-09-10T00:00:00+00:00",
        "seed": 0, "input_candidate_context_sha256": hashes["candidate_context.csv.gz"],
        "input_source_manifest_sha256": hashes["source_manifest.csv"],
        "outputs": {
            "matched_control_pairs.csv.gz": _sha(matched / "matched_control_pairs.csv.gz"),
            "matched_control_summary.csv": matched_summary_sha,
            "control_receipts.csv": _sha(matched / "control_receipts.csv"),
        },
        "study_code_sha256": _sha(Path(matched_controls.__file__)),
    }))
    return stage, matched


def test_builds_a_pinned_chinese_report_from_summary_tables_only(tmp_path):
    stage, matched = _bundle(tmp_path)
    report = tmp_path / "out" / "report.md"
    result = build_spike_market_breadth_report(stage, matched, report)
    text = report.read_text()
    assert result["cohorts"] == 2
    assert "candidate_context.csv.gz` 仅以字节 SHA-256 核验" in text
    assert "joint_delta_60m > 0" in text
    assert "应拒绝作为统一硬过滤" in text
    assert "匹配随机对照：基线与冻结规则" in text
    assert "其他单变量：matched top/bottom 对比" in text
    assert "没有预注册" in text
    assert "不可上线" in text
    assert "如何优化" in text
    assert "joint_breadth" in text and "launch_density_1h" in text
    assert "配对差值净R" in text
    assert "基线 summary 合计目标 `20`、匹配 `20`，动态匹配率 `100.0%`" in text
    assert "动态给出 `8` 个其余变量×top/bottom×cohort 描述性 paired p" in text
    assert "Bonferroni 上界" in text
    assert "0.123456789" not in text


def test_refuses_a_matched_manifest_pinned_to_another_candidate_file(tmp_path):
    stage, matched = _bundle(tmp_path)
    manifest = json.loads((matched / "manifest.json").read_text())
    manifest["input_candidate_context_sha256"] = "0" * 64
    (matched / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="does not reference stage-one candidate_context.csv.gz"):
        build_spike_market_breadth_report(stage, matched, tmp_path / "report.md")


def test_refuses_a_stage_summary_whose_bytes_no_longer_match_its_manifest(tmp_path):
    stage, matched = _bundle(tmp_path)
    (stage / "outcome_summary.csv").write_text("tampered\n")
    with pytest.raises(ValueError, match="stage-one manifest hash mismatch for outcome_summary.csv"):
        build_spike_market_breadth_report(stage, matched, tmp_path / "report.md")


def test_refuses_a_matched_summary_whose_bytes_no_longer_match_its_manifest(tmp_path):
    stage, matched = _bundle(tmp_path)
    (matched / "matched_control_summary.csv").write_text("tampered\n")
    with pytest.raises(ValueError, match="matched manifest hash mismatch for matched_control_summary.csv"):
        build_spike_market_breadth_report(stage, matched, tmp_path / "report.md")


@pytest.mark.parametrize("name", ["matched_control_pairs.csv.gz", "control_receipts.csv"])
def test_refuses_unparsed_matched_artifacts_whose_bytes_no_longer_match_manifest(tmp_path, name):
    stage, matched = _bundle(tmp_path)
    (matched / name).write_bytes(b"tampered opaque artifact")
    with pytest.raises(ValueError, match=f"matched manifest hash mismatch for {name}"):
        build_spike_market_breadth_report(stage, matched, tmp_path / "report.md")


def test_refuses_matched_manifest_with_tampered_generator_code_identity(tmp_path):
    stage, matched = _bundle(tmp_path)
    manifest = json.loads((matched / "manifest.json").read_text())
    manifest["study_code_sha256"] = "0" * 64
    (matched / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="study_code_sha256 differs"):
        build_spike_market_breadth_report(stage, matched, tmp_path / "report.md")


@pytest.mark.parametrize(
    ("value", "message"),
    (("not-a-sha", "stage-one manifest has invalid study_code_sha256"),
     ("0" * 64, "stage-one manifest study_code_sha256 differs from current generator")),
)
def test_refuses_stage_manifest_without_current_well_formed_study_code_identity(tmp_path, value, message):
    stage, matched = _bundle(tmp_path)
    manifest = json.loads((stage / "manifest.json").read_text())
    manifest["study_code_sha256"] = value
    (stage / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=message):
        build_spike_market_breadth_report(stage, matched, tmp_path / "report.md")


def test_refuses_non_monthly_sign_flip_units_and_non_bijective_frozen_rows(tmp_path):
    stage, matched = _bundle(tmp_path)
    summary = pd.read_csv(matched / "matched_control_summary.csv")
    summary.loc[0, "sign_flip_unit"] = "event"
    summary.to_csv(matched / "matched_control_summary.csv", index=False)
    manifest = json.loads((matched / "manifest.json").read_text())
    manifest["outputs"]["matched_control_summary.csv"] = _sha(matched / "matched_control_summary.csv")
    (matched / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="sign_flip_unit=calendar_month"):
        build_spike_market_breadth_report(stage, matched, tmp_path / "report.md")

    missing = tmp_path / "missing"
    missing.mkdir()
    stage, matched = _bundle(missing)
    frozen = pd.read_csv(stage / "frozen_candidate_rule.csv").iloc[:1]
    frozen.to_csv(stage / "frozen_candidate_rule.csv", index=False)
    stage_manifest = json.loads((stage / "manifest.json").read_text())
    stage_manifest["outputs"]["frozen_candidate_rule.csv"] = _sha(stage / "frozen_candidate_rule.csv")
    (stage / "manifest.json").write_text(json.dumps(stage_manifest))
    with pytest.raises(ValueError, match="does not match baseline variant/timeframe cohorts"):
        build_spike_market_breadth_report(stage, matched, tmp_path / "missing-report.md")


def test_cli_accepts_temporary_result_directories(tmp_path):
    stage, matched = _bundle(tmp_path)
    report = tmp_path / "report.md"
    assert main(["--stage-one", str(stage), "--matched", str(matched), "--report", str(report)]) == 0
    assert report.is_file()
