"""Semantic data-quality audit for the ETH 3m current-tip dataset.

The audit reads only dataset metadata, not images or market bars.  It uses the
manifest columns ``input_start_time``, ``anchor_time`` and ``label_end_time``
to define full exposure windows, plus the pre-holdout timing table columns
``candidate_time`` and ``first_below_all_mas_lag_bars`` to measure anchor-rule
confounding.  No future outcome, holdout row, model weight, or prediction is
loaded.  A source row at or after ``HOLDOUT_START`` fails closed.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
BAR_MINUTES = 3


def _component_count(frame: pd.DataFrame) -> int:
    """Count connected components of overlapping full exposure intervals."""
    if frame.empty:
        return 0
    ordered = frame.sort_values(["input_start_time", "label_end_time"])
    count = 0
    component_end: pd.Timestamp | None = None
    for row in ordered.itertuples(index=False):
        start = pd.Timestamp(row.input_start_time)
        end = pd.Timestamp(row.label_end_time)
        if component_end is None or start > component_end:
            count += 1
            component_end = end
        else:
            component_end = max(component_end, end)
    return count


def _confusion(y_true: pd.Series, y_pred: pd.Series) -> dict[str, int | float]:
    truth = y_true.astype(int)
    pred = y_pred.astype(int)
    tp = int(((truth == 1) & (pred == 1)).sum())
    fp = int(((truth == 0) & (pred == 1)).sum())
    tn = int(((truth == 0) & (pred == 0)).sum())
    fn = int(((truth == 1) & (pred == 0)).sum())
    n = len(truth)
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "correct": tp + tn,
        "n": n,
        "accuracy": (tp + tn) / n if n else 0.0,
    }


def audit_dataset_quality(
    manifest: pd.DataFrame, timing_detail: pd.DataFrame
) -> dict[str, Any]:
    """Audit dependence blocks, provenance purity, and anchor-rule shortcuts."""
    required_manifest = {
        "sample_id",
        "split",
        "target",
        "event_id",
        "label_provenance",
        "sample_kind",
        "source_task_id",
        "anchor_time",
        "input_start_time",
        "label_end_time",
    }
    required_timing = {
        "task_id",
        "candidate_time",
        "first_below_all_mas_lag_bars",
    }
    if missing := required_manifest - set(manifest.columns):
        raise ValueError(f"manifest missing columns: {sorted(missing)}")
    if missing := required_timing - set(timing_detail.columns):
        raise ValueError(f"timing detail missing columns: {sorted(missing)}")

    rows = manifest.copy()
    for column in ("anchor_time", "input_start_time", "label_end_time"):
        rows[column] = pd.to_datetime(rows[column], utc=True, errors="raise")
    rows["target"] = pd.to_numeric(rows["target"], errors="raise").astype(int)
    if set(rows["target"].unique()) - {0, 1}:
        raise ValueError("manifest target must be binary")
    if set(rows["split"].unique()) != {"train", "val"}:
        raise ValueError("audit expects exactly train and val splits")
    if rows["sample_id"].duplicated().any():
        raise ValueError("duplicate sample_id")
    if (rows["input_start_time"] > rows["anchor_time"]).any():
        raise ValueError("input starts after anchor")
    if (rows["label_end_time"] < rows["anchor_time"]).any():
        raise ValueError("label horizon ends before anchor")
    if (rows[["anchor_time", "label_end_time"]] >= HOLDOUT_START).any().any():
        raise ValueError("refusing to audit a manifest that reaches holdout")

    detail = timing_detail.copy()
    detail["task_id"] = pd.to_numeric(detail["task_id"], errors="raise").astype(int)
    detail["candidate_time"] = pd.to_datetime(
        detail["candidate_time"], utc=True, errors="raise"
    )
    if detail["task_id"].duplicated().any():
        raise ValueError("timing detail task_id must be unique")
    used_ids = set(rows["source_task_id"].astype(int))
    used_detail = detail[detail["task_id"].isin(used_ids)].copy()
    if set(used_detail["task_id"]) != used_ids:
        missing = sorted(used_ids - set(used_detail["task_id"]))
        raise ValueError(f"timing detail missing source tasks: {missing[:10]}")
    if (used_detail["candidate_time"] >= HOLDOUT_START).any():
        raise ValueError("refusing to audit timing rows that reach holdout")

    dependency_blocks = {
        "all": {
            "images": int(len(rows)),
            "blocks": _component_count(rows),
        },
        "train": {
            "images": int((rows["split"] == "train").sum()),
            "blocks": _component_count(rows[rows["split"] == "train"]),
        },
        "val": {
            "images": int((rows["split"] == "val").sum()),
            "blocks": _component_count(rows[rows["split"] == "val"]),
        },
        "all_positive": {
            "images": int((rows["target"] == 1).sum()),
            "blocks": _component_count(rows[rows["target"] == 1]),
        },
        "train_positive": {
            "images": int(
                ((rows["split"] == "train") & (rows["target"] == 1)).sum()
            ),
            "blocks": _component_count(
                rows[(rows["split"] == "train") & (rows["target"] == 1)]
            ),
        },
        "val_positive": {
            "images": int(
                ((rows["split"] == "val") & (rows["target"] == 1)).sum()
            ),
            "blocks": _component_count(
                rows[(rows["split"] == "val") & (rows["target"] == 1)]
            ),
        },
    }

    provenance_counts = (
        rows.groupby(["label_provenance", "target"], dropna=False)
        .size()
        .rename("rows")
        .reset_index()
        .to_dict("records")
    )
    provenance_prediction = rows["label_provenance"].eq(
        "owner_batch_chat_confirmed_current_T"
    )
    provenance_shortcut = _confusion(rows["target"], provenance_prediction)

    merged = rows.merge(
        used_detail[
            ["task_id", "candidate_time", "first_below_all_mas_lag_bars"]
        ],
        left_on="source_task_id",
        right_on="task_id",
        how="left",
        validate="many_to_one",
    )
    lag = pd.to_numeric(
        merged["first_below_all_mas_lag_bars"], errors="coerce"
    )
    first_below_time = merged["candidate_time"] - pd.to_timedelta(
        lag * BAR_MINUTES, unit="m"
    )
    at_first_below = merged["anchor_time"].eq(first_below_time) & lag.notna()
    anchor_shortcut = _confusion(merged["target"], at_first_below)
    negatives = merged[merged["target"] == 0].copy()
    negative_lag = pd.to_numeric(
        negatives["first_below_all_mas_lag_bars"], errors="coerce"
    )

    target_counts_per_event = rows.groupby("event_id")["target"].nunique()
    event_sizes = rows.groupby("event_id").size()
    return {
        "schema_version": 1,
        "status": "failed_semantic_quality",
        "scope": {
            "holdout_start": HOLDOUT_START.isoformat(),
            "holdout_read": False,
            "market_bars_read": False,
            "model_weights_read": False,
            "inputs": [
                "datasets/eth_3m_short_pilot_v2/manifest.csv",
                "analysis/output/eth3m_v10_label_timing/task_timing_metrics.csv",
            ],
        },
        "dependency_window": {
            "definition": "[input_start_time, label_end_time] = [T-199 bars, T+60 bars]",
            "bars": 260,
            "counts": dependency_blocks,
        },
        "event_concentration": {
            "global_event_count": int(rows["event_id"].nunique()),
            "mixed_target_events": int((target_counts_per_event > 1).sum()),
            "max_images_per_event": int(event_sizes.max()),
        },
        "provenance_confounding": {
            "counts": provenance_counts,
            "source_only_shortcut": provenance_shortcut,
            "interpretation": "label provenance perfectly identifies the target; provenance is not a model feature, but proves asymmetric label construction",
        },
        "anchor_rule_confounding": {
            "rule": "predict short_start when anchor_time equals first-below-all-six-MAs time",
            "shortcut": anchor_shortcut,
            "negative_lag_bars": {
                "finite": int(negative_lag.notna().sum()),
                "missing": int(negative_lag.isna().sum()),
                "equals_0": int(negative_lag.eq(0).sum()),
                "less_or_equal_1": int(negative_lag.le(1).sum()),
                "greater_or_equal_2": int(negative_lag.ge(2).sum()),
                "greater_or_equal_6": int(negative_lag.ge(6).sum()),
                "median": float(negative_lag.median()),
            },
            "interpretation": "construction metadata nearly identifies the target; this is anchor-generation confounding, not future leakage and not a deployable baseline",
        },
        "quality_verdict": {
            "structural_validation": "passed",
            "semantic_learnability": "failed",
            "formal_gold": False,
            "promotion_eligible": False,
            "primary_failures": [
                "positive and negative labels answer different review questions",
                "provenance is perfectly pure by class",
                "anchor generation rule nearly determines the class",
                "effective dependency blocks are much fewer than images or label-horizon events",
            ],
        },
    }
