"""Pine judgment lineage may be prepared, but must remain untrainable."""
import json

import pandas as pd

from scripts.prepare_pine_eth_15m_judgment_research import (
    FEATURE_COLUMNS,
    MANIFEST_OUTPUT,
    ROWS_OUTPUT,
)


def test_judgment_research_rows_are_causal_complete_and_blocked() -> None:
    manifest = json.loads(MANIFEST_OUTPUT.read_text(encoding="utf-8"))
    rows = pd.read_csv(
        ROWS_OUTPUT,
        parse_dates=[
            "signal_time",
            "features_available_at",
            "entry_time",
            "exit_time",
            "label_end_conservative",
        ],
    )
    assert len(rows) == manifest["rows"] == 166
    assert len(FEATURE_COLUMNS) == manifest["feature_count"] == 28
    assert not rows[FEATURE_COLUMNS].isna().any().any()
    assert rows["features_available_at"].equals(rows["entry_time"])
    assert (rows["label_end_conservative"] > rows["entry_time"]).all()
    assert not rows["training_eligible"].astype(bool).any()
    assert not rows["counterfactual_gate_safe"].astype(bool).any()


def test_walkforward_manifest_purges_label_overlap_and_fits_nothing() -> None:
    manifest = json.loads(MANIFEST_OUTPUT.read_text(encoding="utf-8"))
    assert manifest["data_quality"]["consumed_final_rows_read"] == 0
    assert manifest["data_quality"]["holdout_rows_read"] == 0
    assert manifest["training_eligible"] is False
    assert manifest["existing_frozen_model_scored"] is False
    assert manifest["lr_fitted"] is False
    assert manifest["lightgbm_fitted"] is False
    assert manifest["threshold_selected"] is False
    assert len(manifest["folds"]) == 3
    assert sum(fold["purged_for_label_overlap"] for fold in manifest["folds"]) == 2
    for fold in manifest["folds"]:
        assert fold["train_latest_label_end"] < fold["validation_earliest_feature_time"]
