#!/usr/bin/env python3
"""Prepare a training-ineligible Pine-specific LR/L2 research table.

The table contains V9's continuously replayed 2023--2024 executed candidates,
the project's 28 causal side-aligned judgment features at confirmed signal bar
``t``, entry at ``t+1`` open, and the frozen Pine policy's costed outcome.
Market loading stops at 2025-01-01, before the consumed final-preholdout period.

This script deliberately does *not* fit LogisticRegression, LightGBM, a scaler,
or a threshold.  P0/P1 keeps training_eligible=false, and the existing frozen
YOLO/LightGBM candidate and label semantics are incompatible.  The output is an
interface/lineage audit for a future owner-authorized Pine judgment experiment,
not a reusable training dataset or a model score.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research_pine_eth_15m import (
    Period,
    Variant,
    build_feature_frame,
    load_config,
    simulate_period,
)
from yoyo.layers.l2_judgment.features import (
    FEATURE_COLUMNS,
    extract_feature_rows_for_side,
)
from yoyo.layers.l3_backtest.pine_allin_v7 import load_development_frame


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
ROWS_OUTPUT = RESULTS / "pine_judgment_development_rows.csv"
MANIFEST_OUTPUT = RESULTS / "pine_judgment_development_manifest.json"
DEVELOPMENT_START = pd.Timestamp("2023-01-01T00:00:00Z")
DEVELOPMENT_END = pd.Timestamp("2025-01-01T00:00:00Z")


FOLD_WINDOWS = (
    ("wf_2023h2", "2023-01-01", "2023-07-01", "2023-07-01", "2024-01-01"),
    ("wf_2024h1", "2023-01-01", "2024-01-01", "2024-01-01", "2024-07-01"),
    ("wf_2024h2", "2023-01-01", "2024-07-01", "2024-07-01", "2025-01-01"),
)


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def load_development_features() -> tuple[pd.DataFrame, dict[str, Any]]:
    config = load_config()
    path = PROJECT / config["instrument"]["data_path"]
    holdout_start = _utc(config["time_contract"]["holdout_start"])
    raw = load_development_frame(
        path,
        safe_end=DEVELOPMENT_END,
        holdout_start=holdout_start,
    )
    times = pd.to_datetime(raw["open_time"], utc=True)
    quality = {
        "rows": int(len(raw)),
        "last_bar": times.iloc[-1].isoformat(),
        "development_end_exclusive": DEVELOPMENT_END.isoformat(),
        "consumed_final_rows_read": int(times.ge(DEVELOPMENT_END).sum()),
        "holdout_rows_read": int(times.ge(holdout_start).sum()),
    }
    if quality["consumed_final_rows_read"] or quality["holdout_rows_read"]:
        raise RuntimeError(f"judgment research loader crossed its boundary: {quality}")
    return build_feature_frame(raw), quality


def extract_rows(featured: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Join side-aligned causal features to the continuously replayed ledger."""

    parts = []
    for side in ("long", "short"):
        selected = trades.loc[trades["direction"].eq(side)].copy()
        indices = selected["signal_i"].astype(int).tolist()
        features = extract_feature_rows_for_side(featured, indices, side)
        features.index = selected.index
        parts.append(features)
    causal = pd.concat(parts).sort_index()
    rows = trades[
        [
            "trade_id",
            "direction",
            "signal_i",
            "entry_i",
            "exit_i",
            "signal_time",
            "entry_time",
            "exit_time",
            "holding_bars",
            "exit_reason",
            "project_net_return",
        ]
    ].copy()
    rows = pd.concat([rows, causal], axis=1)
    rows = rows.rename(columns={"direction": "side"})
    rows["feature_semantics"] = "side_aligned_v1"
    rows["candidate_policy"] = "pine_eth_15m_v9_on_policy_executed_v1"
    rows["features_available_at"] = pd.to_datetime(rows["signal_time"], utc=True) + pd.Timedelta(
        minutes=15
    )
    rows["label_end_conservative"] = pd.to_datetime(rows["exit_time"], utc=True) + pd.Timedelta(
        minutes=15
    )
    rows["net_positive"] = rows["project_net_return"].gt(0.0)
    rows["training_eligible"] = False
    rows["counterfactual_gate_safe"] = False
    rows["counterfactual_note"] = (
        "on-policy executed baseline only; a rejecting gate changes later position/cooldown state"
    )
    ordered = [
        "trade_id",
        "candidate_policy",
        "side",
        "signal_i",
        "entry_i",
        "exit_i",
        "signal_time",
        "features_available_at",
        "entry_time",
        "exit_time",
        "label_end_conservative",
        "holding_bars",
        "exit_reason",
        "feature_semantics",
        *FEATURE_COLUMNS,
        "project_net_return",
        "net_positive",
        "training_eligible",
        "counterfactual_gate_safe",
        "counterfactual_note",
    ]
    return rows.loc[:, ordered]


def fold_manifest(rows: pd.DataFrame) -> list[dict[str, Any]]:
    """Describe expanding time folds with conservative label-end purging."""

    signal = pd.to_datetime(rows["signal_time"], utc=True)
    label_end = pd.to_datetime(rows["label_end_conservative"], utc=True)
    folds = []
    for name, train_start, train_end, val_start, val_end in FOLD_WINDOWS:
        train_start_ts = _utc(train_start)
        train_end_ts = _utc(train_end)
        val_start_ts = _utc(val_start)
        val_end_ts = _utc(val_end)
        raw_train = signal.ge(train_start_ts) & signal.lt(train_end_ts)
        purged_train = raw_train & label_end.lt(val_start_ts)
        validation = signal.ge(val_start_ts) & signal.lt(val_end_ts) & label_end.le(val_end_ts)
        train_rows = rows.loc[purged_train]
        val_rows = rows.loc[validation]
        folds.append(
            {
                "fold": name,
                "train_signal_window": [train_start_ts.isoformat(), train_end_ts.isoformat()],
                "validation_signal_window": [val_start_ts.isoformat(), val_end_ts.isoformat()],
                "raw_train_rows": int(raw_train.sum()),
                "purged_train_rows": int(purged_train.sum()),
                "purged_for_label_overlap": int(raw_train.sum() - purged_train.sum()),
                "validation_rows": int(validation.sum()),
                "train_positive_rate": float(train_rows["net_positive"].mean()),
                "validation_positive_rate": float(val_rows["net_positive"].mean()),
                "train_latest_label_end": (
                    None
                    if train_rows.empty
                    else pd.to_datetime(
                        train_rows["label_end_conservative"], utc=True
                    ).max().isoformat()
                ),
                "validation_earliest_feature_time": (
                    None
                    if val_rows.empty
                    else pd.to_datetime(
                        val_rows["features_available_at"], utc=True
                    ).min().isoformat()
                ),
            }
        )
    return folds


def main() -> None:
    featured, quality = load_development_features()
    period = Period("development_continuous_2023_2024", DEVELOPMENT_START, DEVELOPMENT_END)
    spec = Variant("v9_judgment_lineage", "v9_long", "v9_short")
    trades, _ = simulate_period(featured, spec, period)
    rows = extract_rows(featured, trades)
    rows.to_csv(ROWS_OUTPUT, index=False)
    features_available = pd.to_datetime(rows["features_available_at"], utc=True)
    entry = pd.to_datetime(rows["entry_time"], utc=True)
    label_end = pd.to_datetime(rows["label_end_conservative"], utc=True)
    missing = rows[FEATURE_COLUMNS].isna().sum()
    manifest = {
        "artifact": "Pine-specific judgment research lineage table",
        "status": "training-ineligible interface audit",
        "data_quality": quality,
        "rows": int(len(rows)),
        "long_rows": int(rows["side"].eq("long").sum()),
        "short_rows": int(rows["side"].eq("short").sum()),
        "net_positive_rows": int(rows["net_positive"].sum()),
        "net_positive_rate": float(rows["net_positive"].mean()),
        "feature_columns": FEATURE_COLUMNS,
        "feature_count": len(FEATURE_COLUMNS),
        "feature_semantics": "side_aligned_v1",
        "missing_feature_cells": int(missing.sum()),
        "features_available_exactly_at_entry_open": bool(features_available.eq(entry).all()),
        "labels_end_after_entry": bool(label_end.gt(entry).all()),
        "folds": fold_manifest(rows),
        "training_eligible": False,
        "existing_frozen_model_scored": False,
        "lr_fitted": False,
        "lightgbm_fitted": False,
        "threshold_selected": False,
        "why_old_model_is_invalid": (
            "Existing frozen model is short-side YOLO-v10 with 72-bar barrier and legacy "
            "candidate/feature semantics; this table is bidirectional Pine V9 with stateful "
            "reverse/stop/break-even outcomes."
        ),
        "counterfactual_limit": (
            "Rows are baseline on-policy executions. A judgment gate changes subsequent "
            "position and cooldown state, so future authorized evaluation must score signals "
            "inside the dynamic replay; static top-decile filtering is insufficient."
        ),
        "future_authorized_protocol": {
            "models": ["ma_spread_pct LogisticRegression baseline", "28-feature LightGBM"],
            "selection": "calibration-only q90; no validation/holdout threshold tuning",
            "evaluation": [
                "dynamic stateful replay",
                "top-decile net after 20 bp",
                "week-cluster p<0.01",
                "64-seed matched-control sensitivity",
                "leave-top-winner-out",
            ],
        },
    }
    if len(rows) == 0 or not np.isfinite(rows["project_net_return"]).all():
        raise RuntimeError("judgment lineage rows are empty or non-finite")
    if manifest["missing_feature_cells"] != 0:
        raise RuntimeError(f"judgment lineage has missing causal features: {missing[missing > 0]}")
    if not manifest["features_available_exactly_at_entry_open"]:
        raise RuntimeError("feature availability no longer matches t+1 entry open")
    MANIFEST_OUTPUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
