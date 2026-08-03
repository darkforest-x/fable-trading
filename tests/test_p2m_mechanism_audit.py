"""Tests for the P2-M read-only mechanism audit."""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from scripts import audit_p2m_mechanism_20260803 as audit
from src.judgment.p1_dataset import load_immutable_dataset
from src.judgment.p2_protocol import HOLDOUT_CUTOFF


@pytest.fixture(scope="module")
def derived_frame() -> pd.DataFrame:
    raw = load_immutable_dataset(audit.P1_MANIFEST)
    data, quality = audit.add_derived_targets(raw)
    assert not any(quality.values())
    return data


@pytest.fixture(scope="module")
def completed_audit() -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    return audit.build_audit()


def test_frozen_inputs_and_only_p1_data_source() -> None:
    actual = audit.assert_frozen_inputs()
    assert actual["data/p1/p1_short_l2_preholdout_aade2a334448d644.csv"] == (
        "aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a"
    )
    prereg = audit._json(audit.PREREG)
    assert prereg["scope"]["allowed_data_source"] == "the exact P1 immutable CSV above"


def test_atr_normalization_recovers_frozen_barrier_units(
    derived_frame: pd.DataFrame,
) -> None:
    result = audit.barrier_scale_diagnostics(derived_frame)
    assert result["accepted"] is True
    assert result["tp"]["median_atr_units"] == pytest.approx(5.0, abs=1e-9)
    assert result["sl"]["median_atr_units"] == pytest.approx(-2.0, abs=1e-9)


def test_five_readonly_folds_and_atr_quintiles(
    derived_frame: pd.DataFrame,
) -> None:
    for column in ("signal_time", "interval_start", "interval_end"):
        derived_frame[column] = pd.to_datetime(derived_frame[column], utc=True)
    folds = audit.reconstruct_readonly_folds(derived_frame)
    assert [len(fold.test) for fold in folds] == [2937, 2918, 2996, 2944, 3000]
    for fold in folds:
        assert audit._atr_buckets(fold.test).nunique() == 5
        assert pd.to_datetime(fold.test["signal_time"], utc=True).max() < HOLDOUT_CUTOFF
        assert pd.to_datetime(fold.test["interval_end"], utc=True).max() < HOLDOUT_CUTOFF


def test_stable_association_rule_is_exact() -> None:
    accepted = audit.stable_association([-0.04, -0.05, -0.03, -0.06, 0.01])
    rejected_sign = audit.stable_association([-0.04, -0.05, 0.03, 0.06, 0.01])
    rejected_size = audit.stable_association([-0.01, -0.02, -0.029, -0.01, -0.02])
    assert accepted["stable"] is True
    assert rejected_sign["stable"] is False
    assert rejected_size["stable"] is False


def test_preregistered_mechanism_counts_and_decision(
    completed_audit: tuple[dict, pd.DataFrame, pd.DataFrame],
) -> None:
    payload, feature_frame, fold_frame = completed_audit
    summary = payload["feature_mechanism"]
    assert summary["frozen_p2r_stable_count"] == 20
    assert summary["mechanical_scale_dominant_count"] == 14
    assert summary["scale_robust_count"] == 8
    assert summary["both_count"] == 3
    assert summary["mixed_count"] == 1
    assert payload["decision"]["global_mechanical_dominance"] is False
    assert payload["decision"]["global_scale_robust_signal"] is True
    assert payload["decision"]["training_allowed"] is False
    assert len(feature_frame) == 28
    assert len(fold_frame) == 5


def test_full_audit_stops_without_runtime_mutation(
    completed_audit: tuple[dict, pd.DataFrame, pd.DataFrame],
) -> None:
    payload, _, _ = completed_audit
    assert payload["verdict"] == "completed_stop"
    assert payload["p2_verdict_unchanged"] == "rejected"
    assert payload["protected_unchanged"] is True
    assert payload["safety"]["data_sources_used"] == [
        "data/p1/p1_short_l2_preholdout_aade2a334448d644.csv"
    ]
    assert payload["safety"]["training_or_fitting_calls"] == 0
    assert payload["safety"]["holdout_read"] is False
    assert payload["safety"]["model_or_feature_selected"] is False


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
