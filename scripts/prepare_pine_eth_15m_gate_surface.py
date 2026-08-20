#!/usr/bin/env python3
"""Prepare the complete training-ineligible Pine V9 raw-candidate surface.

Unlike the baseline executed-trade lineage, this feature-only table contains
every V9 long/short raw signal that passes the causal calendar and volatility
guards in 2023--2024, including signals skipped because a position/cooldown was
active.  A future authorized judgment model must score this complete surface
before a dynamic replay; statically filtering the 166 executed baseline trades
cannot represent the counterfactual state machine.

Features use the confirmed signal bar and earlier.  The file has no outcome
label, model score or threshold, reads no consumed-final/holdout row, and is
explicitly ineligible for training, forward execution or production.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.prepare_pine_eth_15m_judgment_research import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    MANIFEST_OUTPUT as EXECUTED_MANIFEST,
    ROWS_OUTPUT as EXECUTED_ROWS,
    load_development_features,
)
from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS, extract_feature_rows_for_side


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
OUTPUT = RESULTS / "judgment_gate_candidate_features.csv"
MANIFEST = RESULTS / "judgment_gate_surface_manifest.json"


def build_candidate_surface(featured: pd.DataFrame) -> pd.DataFrame:
    """Extract all guarded V9 raw signals with side-aligned causal features."""

    times = pd.to_datetime(featured["open_time"], utc=True)
    in_development = times.ge(DEVELOPMENT_START) & times.lt(DEVELOPMENT_END)
    allowed = featured["entry_allowed"].fillna(False).astype(bool)
    parts = []
    for side, signal_column in (("long", "v9_long"), ("short", "v9_short")):
        mask = in_development & allowed & featured[signal_column].fillna(False).astype(bool)
        indices = np.flatnonzero(mask.to_numpy()).astype(int).tolist()
        causal = extract_feature_rows_for_side(featured, indices, side)
        selected_times = times.iloc[indices].reset_index(drop=True)
        entry_times = times.iloc[[index + 1 for index in indices]].reset_index(drop=True)
        prefix = pd.DataFrame(
            {
                "candidate_id": [
                    f"pine-v9|{side}|{index}|{selected_times.iloc[offset].isoformat()}"
                    for offset, index in enumerate(indices)
                ],
                "side": side,
                "signal_i": indices,
                "signal_time": selected_times,
                "features_available_at": selected_times + pd.Timedelta(minutes=15),
                "earliest_entry_time": entry_times,
                "feature_semantics": "side_aligned_v1",
                "candidate_policy": "pine_eth_15m_v9_raw_guarded_signal_v1",
            }
        )
        parts.append(pd.concat([prefix, causal], axis=1))
    rows = pd.concat(parts, ignore_index=True).sort_values(
        ["signal_i", "side"], kind="stable"
    )
    rows = rows.reset_index(drop=True)
    if rows["candidate_id"].duplicated().any():
        raise RuntimeError("raw candidate ids are not unique")
    if not pd.to_datetime(rows["features_available_at"], utc=True).eq(
        pd.to_datetime(rows["earliest_entry_time"], utc=True)
    ).all():
        raise RuntimeError("feature availability is later than the next-open decision")
    if rows[FEATURE_COLUMNS].isna().any().any():
        raise RuntimeError("raw candidate surface contains missing causal features")
    return rows


def compare_executed_coverage(surface: pd.DataFrame, executed: pd.DataFrame) -> dict[str, Any]:
    """Show why baseline executed rows are insufficient for a dynamic gate."""

    surface_keys = set(zip(surface["side"], surface["signal_i"].astype(int)))
    executed_keys = set(zip(executed["side"], executed["signal_i"].astype(int)))
    missing = sorted(executed_keys - surface_keys)
    if missing:
        raise RuntimeError(f"executed baseline contains non-surface signals: {missing[:3]}")
    return {
        "raw_guarded_candidates": len(surface_keys),
        "baseline_executed_candidates": len(executed_keys),
        "raw_candidates_not_in_baseline_ledger": len(surface_keys - executed_keys),
        "baseline_coverage_of_raw_surface": float(len(executed_keys) / len(surface_keys)),
        "why_extra_rows_matter": (
            "Rejecting an earlier entry changes position and cooldown state, so a later raw "
            "signal absent from the baseline ledger can become executable."
        ),
    }


def main() -> None:
    featured, quality = load_development_features()
    if quality["consumed_final_rows_read"] or quality["holdout_rows_read"]:
        raise RuntimeError("gate surface loader crossed a protected boundary")
    rows = build_candidate_surface(featured)
    executed = pd.read_csv(EXECUTED_ROWS)
    executed_manifest = json.loads(EXECUTED_MANIFEST.read_text(encoding="utf-8"))
    coverage = compare_executed_coverage(rows, executed)
    if executed_manifest["training_eligible"] is not False:
        raise RuntimeError("executed lineage unexpectedly became training eligible")

    payload = {
        "artifact": "complete Pine V9 raw-candidate judgment feature surface",
        "status": "feature-only interface; no labels, scores, thresholds or model",
        "data_quality": quality,
        "rows": int(len(rows)),
        "long_rows": int(rows["side"].eq("long").sum()),
        "short_rows": int(rows["side"].eq("short").sum()),
        "feature_columns": FEATURE_COLUMNS,
        "feature_count": len(FEATURE_COLUMNS),
        "missing_feature_cells": int(rows[FEATURE_COLUMNS].isna().sum().sum()),
        "feature_semantics": "side_aligned_v1",
        "candidate_policy": "pine_eth_15m_v9_raw_guarded_signal_v1",
        "features_available_exactly_at_earliest_entry": bool(
            pd.to_datetime(rows["features_available_at"], utc=True).eq(
                pd.to_datetime(rows["earliest_entry_time"], utc=True)
            ).all()
        ),
        "executed_coverage": coverage,
        "required_external_score_columns": [
            "candidate_id",
            "score",
            "score_available_at",
            "model_sha256",
            "feature_contract_sha256",
        ],
        "future_dynamic_gate_contract": {
            "score_coverage": "exactly one finite score for every raw candidate in the scored period",
            "availability": "score_available_at <= earliest_entry_time",
            "threshold": "pre-registered on an earlier calibration period; never selected on validation/final/holdout",
            "replay": "AND the pass decision into v9_long/v9_short before simulate_symbol state transitions",
            "missing_or_late_score": "fail closed / reject candidate",
            "comparison": "same frozen stop, break-even, cooldown, reversal and 20 bp cost",
        },
        "labels_present": False,
        "scores_present": False,
        "threshold_selected": False,
        "model_trained_or_scored": False,
        "consumed_final_rows_read": 0,
        "holdout_rows_read": 0,
        "training_eligible": False,
        "forward_eligible": False,
        "production_eligible": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(OUTPUT, index=False)
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
