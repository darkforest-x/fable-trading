"""Tests for the P2-R read-only root-cause audit."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import audit_p2r_root_causes_20260803 as audit
from src.judgment.p1_dataset import load_immutable_dataset
from src.judgment.p2_protocol import HOLDOUT_CUTOFF


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return load_immutable_dataset(audit.P1_MANIFEST)


@pytest.fixture(scope="module")
def folds(frame: pd.DataFrame) -> list[audit.ReadOnlyFold]:
    return audit.reconstruct_readonly_folds(frame)


def test_all_frozen_input_hashes_match() -> None:
    actual = audit.assert_frozen_inputs()
    assert actual["data/p1/p1_short_l2_preholdout_aade2a334448d644.csv"] == (
        "aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a"
    )


def test_readonly_folds_reproduce_frozen_p2_rows(
    folds: list[audit.ReadOnlyFold],
) -> None:
    assert [len(fold.test) for fold in folds] == [2937, 2918, 2996, 2944, 3000]
    assert len(folds) == 5
    for fold in folds:
        assert pd.to_datetime(fold.test["signal_time"], utc=True).max() < HOLDOUT_CUTOFF
        assert pd.to_datetime(fold.test["interval_end"], utc=True).max() < HOLDOUT_CUTOFF


def test_feature_ic_rule_is_pre_registered_and_complete(
    folds: list[audit.ReadOnlyFold],
) -> None:
    diagnostics = audit.feature_ic_diagnostics(folds)
    assert len(diagnostics) == 28
    assert int(diagnostics["stable_by_preregistered_rule"].sum()) == 20
    assert int(diagnostics["missing_total"].sum()) == 0
    assert int(diagnostics["nonfinite_total"].sum()) == 0
    atr = diagnostics.loc[diagnostics["feature"] == "atr_pct"].iloc[0]
    assert atr["same_sign_test_folds"] == 5
    assert atr["test_median_spearman"] == pytest.approx(-0.3001850715423181)


def test_exact_week_signflip_recomputes_frozen_matched_control() -> None:
    pairs = pd.read_csv(audit.P2_PAIRS)
    result = audit.exact_week_signflip(pairs)
    assert result["n_pairs"] == 1051
    assert result["n_blocks"] == 12
    assert result["permutations"] == 4096
    assert result["observed_lift"] == pytest.approx(0.00007410623660668925)
    assert result["p_value"] == pytest.approx(0.483642578125)


def test_exact_week_signflip_detects_uniform_positive_blocks() -> None:
    pairs = pd.DataFrame(
        {
            "utc_week": [f"2026-W{week:02d}" for week in range(1, 9)],
            "selected_pressure_net": np.repeat(0.01, 8),
            "control_pressure_net": np.zeros(8),
        }
    )
    result = audit.exact_week_signflip(pairs)
    assert result["permutations"] == 256
    assert result["p_value"] < 0.01


def test_full_audit_reaches_stop_without_runtime_mutation() -> None:
    payload, feature_ic, fold_frame = audit.build_audit()
    assert payload["verdict"] == "completed_stop"
    assert payload["p2_verdict_unchanged"] == "rejected"
    assert payload["protected_unchanged"] is True
    assert payload["safety"]["training_or_fitting_calls"] == 0
    assert payload["safety"]["holdout_read"] is False
    assert payload["next_step_gate"]["threshold_only_fix_supported"] is False
    assert len(feature_ic) == 28
    assert len(fold_frame) == 5


def test_audit_source_contains_no_estimator_fit_or_training_call() -> None:
    source_path = Path(audit.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_names = {"train_regressor", "fit_single_feature_baseline", "train"}
    calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in forbidden_names:
            calls.append(node.func.id)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"fit", "fit_predict"}:
            calls.append(node.func.attr)
    assert calls == []
