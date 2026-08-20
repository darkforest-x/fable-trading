"""Tests for the fail-closed Pine V9 external judgment replay bridge."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.replay_pine_eth_15m_judgment_gate import (
    REQUIRED_SCORE_COLUMNS,
    SCHEMA_VERSION,
    feature_contract_sha256,
    run_self_audit,
    sha256_file,
    validate_gate_manifest,
    validate_scores,
)


def _fixture(tmp_path: Path) -> tuple[pd.DataFrame, dict, dict, pd.DataFrame]:
    surface_path = tmp_path / "surface.csv"
    config_path = tmp_path / "config.json"
    config_path.write_text('{"frozen": true}\n', encoding="utf-8")
    signal_time = pd.to_datetime(
        ["2023-07-01T00:00:00Z", "2023-07-01T00:15:00Z"], utc=True
    )
    surface = pd.DataFrame(
        {
            "candidate_id": [
                f"pine-v9|long|10|{signal_time[0].isoformat()}",
                f"pine-v9|short|11|{signal_time[1].isoformat()}",
            ],
            "side": ["long", "short"],
            "signal_i": [10, 11],
            "signal_time": signal_time,
            "features_available_at": signal_time + pd.Timedelta(minutes=15),
            "earliest_entry_time": signal_time + pd.Timedelta(minutes=15),
            "feature_semantics": "side_aligned_v1",
            "candidate_policy": "pine_eth_15m_v9_raw_guarded_signal_v1",
            "feature_a": [0.1, 0.2],
        }
    )
    surface.to_csv(surface_path, index=False)
    surface_manifest = {
        "rows": 2,
        "candidate_policy": "pine_eth_15m_v9_raw_guarded_signal_v1",
        "feature_semantics": "side_aligned_v1",
        "feature_columns": ["feature_a"],
    }
    feature_hash = feature_contract_sha256(surface_manifest)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": "exp-pine-eth-15m-v1",
        "candidate_policy": surface_manifest["candidate_policy"],
        "feature_semantics": surface_manifest["feature_semantics"],
        "score_scale": "probability_0_1",
        "decision_rule": "score_gte_threshold",
        "threshold": 0.5,
        "threshold_locked": True,
        "threshold_selection_uses_evaluation_outcomes": False,
        "calibration_end_exclusive": "2023-07-01T00:00:00Z",
        "evaluation_start": "2023-07-01T00:00:00Z",
        "evaluation_end_exclusive": "2023-07-02T00:00:00Z",
        "model_sha256": "a" * 64,
        "feature_contract_sha256": feature_hash,
        "candidate_surface_sha256": sha256_file(surface_path),
        "strategy_config_sha256": sha256_file(config_path),
        "bar_minutes": 15,
        "round_trip_cost": 0.002,
        "risk_per_trade_percent": 1.0,
        "owner_approval_reference": "pytest-contract",
        "production_eligible": False,
    }
    scores = pd.DataFrame(
        {
            "candidate_id": surface["candidate_id"],
            "score": [0.8, 0.2],
            "score_available_at": surface["features_available_at"],
            "model_sha256": manifest["model_sha256"],
            "feature_contract_sha256": feature_hash,
        },
        columns=REQUIRED_SCORE_COLUMNS,
    )
    manifest["_surface_path"] = surface_path
    manifest["_config_path"] = config_path
    return surface, surface_manifest, manifest, scores


def test_score_contract_accepts_exact_causal_coverage_and_rejects_mutations(
    tmp_path: Path,
) -> None:
    surface, surface_manifest, manifest, scores = _fixture(tmp_path)
    surface_path = manifest.pop("_surface_path")
    config_path = manifest.pop("_config_path")
    validate_gate_manifest(
        manifest,
        surface_manifest=surface_manifest,
        surface_path=surface_path,
        strategy_config_path=config_path,
    )
    joined = validate_scores(scores, surface, manifest)
    assert joined["gate_pass"].tolist() == [True, False]

    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_scores(scores.iloc[:-1], surface, manifest)
    duplicate = pd.concat([scores, scores.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="one unique row"):
        validate_scores(duplicate, surface, manifest)
    late = scores.copy()
    late.loc[0, "score_available_at"] = pd.Timestamp(late.loc[0, "score_available_at"]) + pd.Timedelta(
        minutes=15
    )
    with pytest.raises(ValueError, match="after the next-open"):
        validate_scores(late, surface, manifest)
    bad = scores.copy()
    bad.loc[0, "score"] = np.inf
    with pytest.raises(ValueError, match="non-finite"):
        validate_scores(bad, surface, manifest)


def test_gate_manifest_rejects_threshold_and_lineage_drift(tmp_path: Path) -> None:
    _, surface_manifest, manifest, _ = _fixture(tmp_path)
    surface_path = manifest.pop("_surface_path")
    config_path = manifest.pop("_config_path")
    null_threshold = dict(manifest, threshold=None)
    with pytest.raises(ValueError, match="finite number"):
        validate_gate_manifest(
            null_threshold,
            surface_manifest=surface_manifest,
            surface_path=surface_path,
            strategy_config_path=config_path,
        )
    wrong_surface = dict(manifest, candidate_surface_sha256="b" * 64)
    with pytest.raises(ValueError, match="surface hash"):
        validate_gate_manifest(
            wrong_surface,
            surface_manifest=surface_manifest,
            surface_path=surface_path,
            strategy_config_path=config_path,
        )
    overlap = dict(manifest, calibration_end_exclusive="2023-07-01T00:15:00Z")
    with pytest.raises(ValueError, match="overlaps"):
        validate_gate_manifest(
            overlap,
            surface_manifest=surface_manifest,
            surface_path=surface_path,
            strategy_config_path=config_path,
        )


def test_self_audit_replays_allow_all_identity_without_a_model() -> None:
    payload = run_self_audit(write=False)
    assert payload["status"] == "pass"
    assert payload["check_count"] == 10
    assert payload["model_trained"] is False
    assert payload["model_loaded"] is False
    assert payload["threshold_selected"] is False
    assert payload["consumed_final_rows_read"] == 0
    assert payload["holdout_rows_read"] == 0
    assert all(row["passed"] for row in payload["reconciliations"])
    assert all(row["status"] == "rejected" for row in payload["fail_closed_mutations"])
