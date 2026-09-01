"""Contract tests for the frozen LONG/SHORT L2 regression comparison."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.retrain_15m_ma_launch_l2_by_side import (
    EXPERIMENT_ID,
    SideSplitError,
    compare_prior_scores,
    empirical_percentile,
    load_preregistration,
    split_dataset_by_side,
    validate_source_dataset,
    verify_outputs,
)
from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"


def rows() -> pd.DataFrame:
    base = {
        "split": "train",
        "dependency_block_id": "b1",
        "dependency_representative": True,
        "label": 1,
        "realized_ret": 0.01,
        "net_ret": 0.008,
    }
    records = []
    for n, (side, split) in enumerate(
        (("long", "train"), ("short", "tune"), ("long", "final_validation"), ("short", "purge"))
    ):
        record = {**base, "episode_id": f"e{n}", "side": side, "split": split}
        record.update({column: 0.01 + n for column in FEATURE_COLUMNS})
        records.append(record)
    return pd.DataFrame(records)


def test_prereg_freezes_regression_contract_without_holdout_or_l1_5_training() -> None:
    prereg = load_preregistration(PREREG)
    assert prereg["single_variable"].startswith("One mixed-side")
    assert prereg["frozen_contract"]["objective"] == "LightGBM regression on gross realized_ret"
    assert prereg["frozen_contract"]["holdout_read"] is False
    assert prereg["l1_5_boundary"]["trained_here"] is False
    assert prereg["safety"]["barriers_or_cost_changed"] is False


def test_side_partition_is_disjoint_and_exact() -> None:
    frame = validate_source_dataset(rows(), expected_rows=4)
    partitions = split_dataset_by_side(frame)
    assert set(partitions) == {"long", "short"}
    assert len(partitions["long"]) == 2
    assert len(partitions["short"]) == 2
    assert set(partitions["long"]["episode_id"]) | set(partitions["short"]["episode_id"]) == set(frame["episode_id"])


def test_dataset_validation_refuses_mixed_unknown_side() -> None:
    frame = rows()
    frame.loc[0, "side"] = "flat"
    with pytest.raises(SideSplitError, match="source sides drifted"):
        validate_source_dataset(frame, expected_rows=4)


def test_dataset_validation_refuses_duplicate_episode() -> None:
    frame = rows()
    frame.loc[1, "episode_id"] = frame.loc[0, "episode_id"]
    with pytest.raises(SideSplitError, match="episode_id is not unique"):
        validate_source_dataset(frame, expected_rows=4)


def test_empirical_percentile_uses_frozen_tune_distribution() -> None:
    observed = empirical_percentile(np.array([1.0, 2.0, 3.0, 4.0]), np.array([0.0, 1.0, 2.5, 4.0, 5.0]))
    np.testing.assert_allclose(observed, [0.0, 0.25, 0.5, 1.0, 1.0])


def test_empirical_percentile_refuses_empty_reference() -> None:
    with pytest.raises(SideSplitError, match="finite, non-empty"):
        empirical_percentile(np.array([]), np.array([1.0]))


def test_prior_score_comparison_requires_exact_episode_identity() -> None:
    prior = pd.DataFrame(
        {
            "episode_id": ["e1", "e2"],
            "dependency_representative": [True, True],
            "l2_score": [0.1, 0.2],
            "l2_keep": [False, True],
        }
    )
    observed = compare_prior_scores(prior.copy(), prior.copy(), 1e-12)
    assert observed["scores_within_tolerance"] is True
    assert observed["keep_decisions_exact"] is True
    assert observed["maximum_absolute_score_delta"] == 0.0


def test_verify_receipt_is_stable_across_repeated_integrity_checks(monkeypatch, tmp_path: Path) -> None:
    import scripts.retrain_15m_ma_launch_l2_by_side as module

    results = tmp_path / "results"
    output = tmp_path / "output"
    models = output / "models"
    results.mkdir()
    models.mkdir(parents=True)

    long_data = output / "l2_dataset_long.csv"
    short_data = output / "l2_dataset_short.csv"
    scored = output / "final_validation_side_split_scored.csv"
    long_model = models / "l2_long_28f.txt"
    short_model = models / "l2_short_28f.txt"
    for path, body in (
        (long_data, "long\n"),
        (short_data, "short\n"),
        (scored, "scores\n"),
        (long_model, "long-model\n"),
        (short_model, "short-model\n"),
    ):
        path.write_text(body, encoding="utf-8")

    def rel(path: Path) -> str:
        return path.relative_to(tmp_path).as_posix()

    receipt = {
        "protocol": "test-protocol",
        "generated_at": "2026-09-01T00:00:00+00:00",
        "source_dataset_sha256": "source-sha",
        "source_rows": 2,
        "partition_rows": {"long": 1, "short": 1},
        "mixed_reproduction": {"scores_within_tolerance": True, "keep_decisions_exact": True},
        "holdout_consumed": False,
        "promoted": False,
        "deployed": False,
        "active_or_frozen_changed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
        "partition_paths": {
            "long": {"path": rel(long_data), "sha256": module.sha256_file(long_data)},
            "short": {"path": rel(short_data), "sha256": module.sha256_file(short_data)},
        },
        "sides": {
            "long": {"model_path": rel(long_model), "model_sha256": module.sha256_file(long_model)},
            "short": {"model_path": rel(short_model), "model_sha256": module.sha256_file(short_model)},
        },
        "scored_validation_path": rel(scored),
        "scored_validation_sha256": module.sha256_file(scored),
    }
    (results / "training_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    prereg = {
        "protocol": "test-protocol",
        "source": {"dataset_sha256": "source-sha"},
    }

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "RESULTS_DIR", results)
    first = verify_outputs(prereg)
    first_bytes = (results / "verify_receipt.json").read_bytes()
    second = verify_outputs(prereg)

    assert first == second
    assert first["generated_at"] == receipt["generated_at"]
    assert first["training_receipt_sha256"] == module.sha256_file(results / "training_receipt.json")
    assert (results / "verify_receipt.json").read_bytes() == first_bytes
