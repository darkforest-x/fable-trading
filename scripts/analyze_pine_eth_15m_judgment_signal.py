#!/usr/bin/env python3
"""Audit whether Pine-specific judgment features show learnable signal.

The input is the training-ineligible 2023--2024 on-policy V9 lineage table.
All feature values are available at the next-open entry decision.  This audit
fits no LogisticRegression or LightGBM model and selects no executable gate.

Four transparent one-feature priors are scored in expanding time folds.  Their
pooled AUC and static top-decile outcome receive an exact null based on every
combination of within-half-year circular outcome shifts, followed by Holm
correction across the four displayed priors.  The circular shifts preserve the
serial order and marginal outcomes inside each validation half-year while
breaking feature/outcome alignment.  They are a diagnostic null, not a claim
that overlapping trade outcomes are independent.

A deliberately flexible 28-feature selector is also replayed prequentially:
each fold chooses only from purged earlier rows, then scores the next half-year.
Its static returns are diagnostic only.  Rejecting a signal changes later
position and cooldown state, so any future authorized model must be evaluated
inside the dynamic execution replay.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from yoyo.layers.l3_backtest.pine_allin_v7 import auc_from_scores


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
ROWS_PATH = RESULTS / "pine_judgment_development_rows.csv"
MANIFEST_PATH = RESULTS / "pine_judgment_development_manifest.json"
OUTPUT_JSON = RESULTS / "judgment_signal_audit.json"
OUTPUT_CSV = RESULTS / "judgment_feature_associations.csv"

FIXED_PRIORS: tuple[tuple[str, int, str], ...] = (
    ("ma_spread_pct", -1, "original density/LR baseline: tighter is favorable"),
    ("vol_ratio_mean8", 1, "V10 volume-expansion hypothesis: higher is favorable"),
    ("slow_slope_12", 1, "V9 side-aligned trend strength: higher is favorable"),
    ("atr_pct_ratio96", 1, "project volatility-expansion prior: higher is favorable"),
)


def holm_adjust(p_values: Iterable[float]) -> np.ndarray:
    """Return Holm family-wise adjusted p-values in original order."""

    values = np.asarray(list(p_values), dtype=float)
    order = np.argsort(values, kind="stable")
    adjusted = np.empty_like(values)
    running = 0.0
    total = len(values)
    for rank, index in enumerate(order):
        running = max(running, (total - rank) * float(values[index]))
        adjusted[index] = min(1.0, running)
    return adjusted


def empirical_score(train: np.ndarray, validation: np.ndarray, direction: int) -> np.ndarray:
    """Map validation values to the earlier-train empirical CDF."""

    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")
    ordered = np.sort(np.asarray(train, dtype=float))
    if len(ordered) == 0 or not np.isfinite(ordered).all():
        raise ValueError("training feature values must be finite and non-empty")
    raw = np.searchsorted(ordered, np.asarray(validation, dtype=float), side="right") / len(
        ordered
    )
    return raw if direction > 0 else 1.0 - raw


def _fold_masks(
    rows: pd.DataFrame, manifest: dict[str, Any]
) -> list[tuple[dict[str, Any], np.ndarray, np.ndarray]]:
    signal = pd.to_datetime(rows["signal_time"], utc=True)
    label_end = pd.to_datetime(rows["label_end_conservative"], utc=True)
    result = []
    for fold in manifest["folds"]:
        train_start, train_end = map(pd.Timestamp, fold["train_signal_window"])
        validation_start, validation_end = map(
            pd.Timestamp, fold["validation_signal_window"]
        )
        train = (
            signal.ge(train_start)
            & signal.lt(train_end)
            & label_end.lt(validation_start)
        ).to_numpy()
        validation = (
            signal.ge(validation_start)
            & signal.lt(validation_end)
            & label_end.le(validation_end)
        ).to_numpy()
        if int(train.sum()) != int(fold["purged_train_rows"]):
            raise RuntimeError(f"purged train count drifted for {fold['fold']}")
        if int(validation.sum()) != int(fold["validation_rows"]):
            raise RuntimeError(f"validation count drifted for {fold['fold']}")
        result.append((fold, train, validation))
    return result


def exact_shift_null(parts: list[pd.DataFrame]) -> dict[str, Any]:
    """Compute exact pooled AUC/top-decile null over foldwise circular shifts."""

    if len(parts) != 3:
        raise ValueError("the frozen audit expects exactly three validation folds")
    pooled = pd.concat(parts, ignore_index=True)
    scores = pooled["score"].to_numpy(dtype=float)
    returns = pooled["project_net_return"].to_numpy(dtype=float)
    labels = pooled["net_positive"].astype(bool).to_numpy()
    ranks = pd.Series(scores).rank(method="average").to_numpy(dtype=float)
    positive_count = int(labels.sum())
    negative_count = int((~labels).sum())
    observed_auc = auc_from_scores(scores, labels)
    cutoff = float(pd.Series(scores).quantile(0.9))
    selected = scores >= cutoff
    selected_count = int(selected.sum())
    observed_top = float(returns[selected].mean())

    rank_sums: list[np.ndarray] = []
    return_sums: list[np.ndarray] = []
    offset = 0
    for part in parts:
        n = len(part)
        part_labels = part["net_positive"].astype(int).to_numpy()
        part_returns = part["project_net_return"].to_numpy(dtype=float)
        part_ranks = ranks[offset : offset + n]
        part_selected = selected[offset : offset + n].astype(float)
        offset += n
        rank_sums.append(
            np.asarray(
                [float(part_ranks @ np.roll(part_labels, shift)) for shift in range(n)]
            )
        )
        return_sums.append(
            np.asarray(
                [float(part_selected @ np.roll(part_returns, shift)) for shift in range(n)]
            )
        )

    rank_grid = (
        rank_sums[0][:, None, None]
        + rank_sums[1][None, :, None]
        + rank_sums[2][None, None, :]
    )
    auc_null = (
        rank_grid - positive_count * (positive_count + 1) / 2.0
    ) / (positive_count * negative_count)
    return_grid = (
        return_sums[0][:, None, None]
        + return_sums[1][None, :, None]
        + return_sums[2][None, None, :]
    )
    top_null = return_grid / selected_count
    exact_permutations = int(auc_null.size)
    top_returns = np.sort(returns[selected])[::-1]

    return {
        "validation_rows": int(len(pooled)),
        "positive_rows": positive_count,
        "auc": float(observed_auc),
        "auc_exact_circular_shift_p": float(
            np.mean(auc_null >= observed_auc - 1e-15)
        ),
        "auc_null_q05": float(np.quantile(auc_null, 0.05)),
        "auc_null_q95": float(np.quantile(auc_null, 0.95)),
        "top_decile_score_cutoff": cutoff,
        "top_decile_rows": selected_count,
        "top_decile_positive_rows": int(labels[selected].sum()),
        "top_decile_net_bp": observed_top * 10_000.0,
        "top_decile_net_bp_without_top1": (
            float(top_returns[1:].mean() * 10_000.0)
            if len(top_returns) > 1
            else None
        ),
        "top_decile_exact_circular_shift_p": float(
            np.mean(top_null >= observed_top - 1e-15)
        ),
        "top_decile_null_q05_bp": float(np.quantile(top_null, 0.05) * 10_000.0),
        "top_decile_null_q95_bp": float(np.quantile(top_null, 0.95) * 10_000.0),
        "all_validation_net_bp": float(returns.mean() * 10_000.0),
        "exact_shift_combinations": exact_permutations,
        "fold_shift_counts": [int(len(part)) for part in parts],
    }


def _validation_parts(
    rows: pd.DataFrame,
    folds: list[tuple[dict[str, Any], np.ndarray, np.ndarray]],
    feature: str,
    direction: int,
) -> list[pd.DataFrame]:
    parts = []
    for fold, train, validation in folds:
        scores = empirical_score(
            rows.loc[train, feature].to_numpy(dtype=float),
            rows.loc[validation, feature].to_numpy(dtype=float),
            direction,
        )
        part = rows.loc[
            validation, ["signal_time", "project_net_return", "net_positive"]
        ].copy()
        part["score"] = scores
        part["fold"] = fold["fold"]
        parts.append(part.reset_index(drop=True))
    return parts


def prequential_selector(
    rows: pd.DataFrame,
    folds: list[tuple[dict[str, Any], np.ndarray, np.ndarray]],
    features: list[str],
) -> dict[str, Any]:
    """Select the strongest oriented train AUC, then score only the next fold."""

    parts = []
    fold_rows = []
    for fold, train, validation in folds:
        candidates = []
        for feature in features:
            auc = auc_from_scores(rows.loc[train, feature], rows.loc[train, "net_positive"])
            candidates.append((abs(auc - 0.5), feature, auc))
        _strength, selected, train_auc = max(candidates)
        direction = 1 if train_auc >= 0.5 else -1
        part = _validation_parts(rows, [(fold, train, validation)], selected, direction)[0]
        validation_auc = auc_from_scores(part["score"], part["net_positive"])
        fold_rows.append(
            {
                "fold": fold["fold"],
                "selected_feature": selected,
                "favorable_direction": "higher" if direction > 0 else "lower",
                "purged_train_rows": int(train.sum()),
                "validation_rows": int(validation.sum()),
                "train_oriented_auc": float(max(train_auc, 1.0 - train_auc)),
                "validation_auc": float(validation_auc),
            }
        )
        parts.append(part)

    pooled = pd.concat(parts, ignore_index=True)
    cutoff = float(pooled["score"].quantile(0.9))
    top = pooled.loc[pooled["score"].ge(cutoff)].sort_values(
        "project_net_return", ascending=False
    )
    return {
        "folds": fold_rows,
        "pooled_validation_rows": int(len(pooled)),
        "pooled_auc": float(auc_from_scores(pooled["score"], pooled["net_positive"])),
        "pooled_all_net_bp": float(pooled["project_net_return"].mean() * 10_000.0),
        "pooled_top_decile_rows": int(len(top)),
        "pooled_top_decile_positive_rows": int(top["net_positive"].sum()),
        "pooled_top_decile_net_bp": float(top["project_net_return"].mean() * 10_000.0),
        "pooled_top_decile_net_bp_without_top1": (
            float(top.iloc[1:]["project_net_return"].mean() * 10_000.0)
            if len(top) > 1
            else None
        ),
        "passes_directional_sanity": bool(
            auc_from_scores(pooled["score"], pooled["net_positive"]) > 0.5
            and top["project_net_return"].mean() > pooled["project_net_return"].mean()
        ),
    }


def exploratory_associations(rows: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Describe full-development associations; never use this table as a gate."""

    labels = rows["net_positive"].astype(bool).to_numpy()
    returns = rows["project_net_return"].to_numpy(dtype=float)
    output = []
    for feature in features:
        raw_auc = auc_from_scores(rows[feature], labels)
        direction = 1 if raw_auc >= 0.5 else -1
        score = direction * rows[feature].to_numpy(dtype=float)
        cutoff = float(pd.Series(score).quantile(0.9))
        selected = score >= cutoff
        output.append(
            {
                "feature": feature,
                "favorable_direction": "higher" if direction > 0 else "lower",
                "raw_auc": float(raw_auc),
                "oriented_auc": float(max(raw_auc, 1.0 - raw_auc)),
                "top_decile_rows": int(selected.sum()),
                "top_decile_net_bp": float(returns[selected].mean() * 10_000.0),
                "status": "full-development exploratory; in-sample and unadjusted",
            }
        )
    return pd.DataFrame(output).sort_values(
        ["oriented_auc", "feature"], ascending=[False, True]
    )


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    rows = pd.read_csv(ROWS_PATH)
    features = list(manifest["feature_columns"])
    if manifest["training_eligible"] is not False:
        raise RuntimeError("judgment source unexpectedly became training eligible")
    if int(manifest["data_quality"]["consumed_final_rows_read"]) != 0:
        raise RuntimeError("judgment source crossed into the consumed final period")
    if int(manifest["data_quality"]["holdout_rows_read"]) != 0:
        raise RuntimeError("judgment source crossed into holdout")
    if len(rows) != 166 or len(features) != 28:
        raise RuntimeError("judgment audit source contract changed")
    if rows[features].isna().any().any():
        raise RuntimeError("judgment feature matrix contains missing values")

    folds = _fold_masks(rows, manifest)
    fixed = []
    for feature, direction, rationale in FIXED_PRIORS:
        result = exact_shift_null(
            _validation_parts(rows, folds, feature, direction)
        )
        result.update(
            {
                "feature": feature,
                "favorable_direction": "higher" if direction > 0 else "lower",
                "rationale": rationale,
            }
        )
        fixed.append(result)
    auc_adjusted = holm_adjust(row["auc_exact_circular_shift_p"] for row in fixed)
    top_adjusted = holm_adjust(
        row["top_decile_exact_circular_shift_p"] for row in fixed
    )
    for index, row in enumerate(fixed):
        row["auc_holm_p_across_four_displayed_priors"] = float(auc_adjusted[index])
        row["top_decile_holm_p_across_four_displayed_priors"] = float(
            top_adjusted[index]
        )

    prequential = prequential_selector(rows, folds, features)
    associations = exploratory_associations(rows, features)
    associations.to_csv(OUTPUT_CSV, index=False)
    best_fixed = max(fixed, key=lambda row: row["top_decile_net_bp"])
    payload = {
        "audit": "Pine judgment feature signal without model fitting",
        "source_rows": int(len(rows)),
        "source_period": ["2023-01-01T00:00:00Z", "2025-01-01T00:00:00Z"],
        "consumed_final_rows_read": 0,
        "holdout_rows_read": 0,
        "features": len(features),
        "expanding_folds": len(folds),
        "training_or_model_scoring_performed": False,
        "threshold_selected_for_execution": False,
        "dynamic_execution_replay_performed": False,
        "static_economic_metrics_are_strategy_claims": False,
        "fixed_prior_diagnostics": fixed,
        "prequential_28_feature_selector": prequential,
        "full_development_association_table": str(OUTPUT_CSV),
        "null": {
            "method": "exact Cartesian product of circular outcome shifts within each validation half-year",
            "preserves": "outcome order and marginals within each validation half-year",
            "breaks": "feature/outcome alignment",
            "limitations": (
                "diagnostic null only; does not make overlapping on-policy trade outcomes independent"
            ),
        },
        "selection_history_warning": (
            "Holm correction covers only the four displayed priors. vol_ratio_mean8 was found "
            "after a wider development feature search, so its displayed p-values are not "
            "selection-history-adjusted evidence."
        ),
        "best_fixed_prior": best_fixed["feature"],
        "decision": (
            "No displayed prior passes p<0.01, and the flexible prequential selector fails "
            "directional sanity. vol_ratio_mean8 remains the only plausible first-feature "
            "candidate, but it is post-selected, sparse and unproven. Do not fit the 28-feature "
            "LightGBM; if training is later authorized, preregister one regularized LR feature "
            "and evaluate its score inside the stateful replay."
        ),
        "training_eligible": False,
        "production_eligible": False,
    }
    if any(
        row["auc_holm_p_across_four_displayed_priors"] < 0.01
        or row["top_decile_holm_p_across_four_displayed_priors"] < 0.01
        for row in fixed
    ):
        payload["decision"] = (
            "A displayed diagnostic passed the four-prior threshold, but selection-history "
            "and static-state limitations still block training or production."
        )
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
