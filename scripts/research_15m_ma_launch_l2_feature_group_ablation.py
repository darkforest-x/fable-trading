#!/usr/bin/env python3
"""Select causal L2 feature groups by side without opening holdout.

The input is the byte-pinned real-YOLO candidate dataset built by the global
context experiment. Its 28 causal values were sampled at the last bar visible
to L1; this script does not recompute features or read OHLCV. LONG and SHORT
models are trained separately on dependency representatives. Seven fixed
feature subsets are ranked on the chronological tune interval, and only the
frozen winner per side is eligible for the April final-validation decision.

Inputs to every model are the explicitly listed columns in FEATURE_ARMS. They
are all causal values already materialized from bars <= the L1 decision bar.
Only realized_ret and label look forward, and only as training/evaluation
targets. No row dated 2026-05-04 or later is present or read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts.research_15m_ma_launch_l2_global_context import (
    L2_DETERMINISTIC_PARAMS,
    matched_control_metrics,
    outcome_permutation_pvalue,
    safe_metrics,
    selected_metrics,
)
from scripts.retrain_15m_ma_launch_l2_by_side import empirical_percentile
from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS
from yoyo.layers.l2_judgment.train import LGB_PARAMS, train_model


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-feature-group-ablation-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_feature_group_ablation_v1"
REPORT_PATH = ROOT / "analysis" / "p3_15m_ma_launch_l2_feature_group_ablation_20260902.md"
SIDES = ("long", "short")

FEATURE_GROUPS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    [
        ("ma_structure", tuple(FEATURE_COLUMNS[:11])),
        ("price_trend", tuple(FEATURE_COLUMNS[11:16])),
        ("volume", tuple(FEATURE_COLUMNS[16:19])),
        ("volatility", tuple(FEATURE_COLUMNS[19:24])),
        ("momentum", tuple(FEATURE_COLUMNS[24:28])),
    ]
)


def _join_groups(*names: str) -> tuple[str, ...]:
    return tuple(column for name in names for column in FEATURE_GROUPS[name])


FEATURE_ARMS: OrderedDict[str, tuple[str, ...]] = OrderedDict(
    [
        ("ma_spread_only", ("ma_spread_pct",)),
        ("ma_structure", FEATURE_GROUPS["ma_structure"]),
        (
            "context_only",
            _join_groups("price_trend", "volume", "volatility", "momentum"),
        ),
        ("ma_plus_trend", _join_groups("ma_structure", "price_trend")),
        (
            "ma_plus_trend_volume",
            _join_groups("ma_structure", "price_trend", "volume"),
        ),
        (
            "ma_plus_trend_volume_volatility",
            _join_groups("ma_structure", "price_trend", "volume", "volatility"),
        ),
        ("full_28", tuple(FEATURE_COLUMNS)),
    ]
)


class FeatureAblationError(RuntimeError):
    """Raised when experiment lineage or phase separation drifts."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    building = path.with_suffix(path.suffix + ".building")
    building.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    building.replace(path)


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def load_preregistration() -> dict[str, Any]:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise FeatureAblationError("preregistration experiment_id mismatch")
    if prereg["source"].get("holdout_rows") != 0:
        raise FeatureAblationError("source contract is not pre-holdout only")
    if prereg["frozen_contract"].get("holdout_read") is not False:
        raise FeatureAblationError("holdout read must remain disabled")
    prereg_arms: OrderedDict[str, tuple[str, ...]] = OrderedDict()
    for item in prereg["arms_in_fixed_order"]:
        columns = tuple(item.get("columns") or _join_groups(*item["groups"]))
        prereg_arms[item["name"]] = columns
    if prereg_arms != FEATURE_ARMS:
        raise FeatureAblationError("preregistered feature arms drifted from code")
    prereg_groups = OrderedDict(
        (name, tuple(columns)) for name, columns in prereg["feature_groups"].items()
    )
    if prereg_groups != FEATURE_GROUPS:
        raise FeatureAblationError("preregistered feature groups drifted from code")
    return prereg


def verify_pinned_inputs(
    prereg: Mapping[str, Any], *, include_controls: bool
) -> None:
    source = prereg["source"]
    items = [
        ("dataset_path", "dataset_sha256"),
        ("prior_side_split_receipt", "prior_side_split_receipt_sha256"),
        ("prior_side_split_scores", "prior_side_split_scores_sha256"),
    ]
    if include_controls:
        items.append(("matched_controls_path", "matched_controls_sha256"))
    for path_key, sha_key in items:
        path = repo_path(source[path_key])
        if not path.is_file():
            raise FeatureAblationError(f"missing pinned input: {path}")
        actual = sha256_file(path)
        if actual != source[sha_key]:
            raise FeatureAblationError(
                f"pinned input drifted for {path_key}: {actual} != {source[sha_key]}"
            )
    frozen = prereg["frozen_contract"]
    for path_key, sha_key in (
        ("feature_builder", "feature_builder_sha256"),
        ("training_builder", "training_builder_sha256"),
    ):
        path = repo_path(frozen[path_key])
        actual = sha256_file(path)
        if actual != frozen[sha_key]:
            raise FeatureAblationError(f"builder drifted: {path}")


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin(("true", "false")).all():
        raise FeatureAblationError("dependency_representative is not boolean")
    return normalized.map({"true": True, "false": False}).astype(bool)


def load_source(prereg: Mapping[str, Any]) -> pd.DataFrame:
    data = pd.read_csv(repo_path(prereg["source"]["dataset_path"]))
    if len(data) != int(prereg["source"]["dataset_rows"]):
        raise FeatureAblationError("source row count drifted")
    required = {
        "episode_id",
        "symbol",
        "side",
        "split",
        "available_at",
        "dependency_representative",
        "label",
        "realized_ret",
        "net_ret",
        *FEATURE_COLUMNS,
    }
    missing = sorted(required - set(data.columns))
    if missing:
        raise FeatureAblationError(f"source columns missing: {missing}")
    if data["episode_id"].duplicated().any():
        raise FeatureAblationError("episode_id is not unique")
    if set(data["side"].astype(str)) != set(SIDES):
        raise FeatureAblationError("side domain drifted")
    if set(data["split"].astype(str)) != {
        "train",
        "purge",
        "tune",
        "final_validation",
    }:
        raise FeatureAblationError("split domain drifted")
    data = data.copy()
    data["dependency_representative"] = bool_series(
        data["dependency_representative"]
    )
    learning = data["split"].isin(("train", "tune", "final_validation"))
    values = data.loc[learning, FEATURE_COLUMNS].apply(
        pd.to_numeric, errors="coerce"
    )
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise FeatureAblationError("learning rows contain non-finite features")
    available = pd.to_datetime(data["available_at"], utc=True)
    if (available >= pd.Timestamp("2026-05-04", tz="UTC")).any():
        raise FeatureAblationError("source unexpectedly contains holdout rows")
    return data


def learning_rows(data: pd.DataFrame, side: str, split: str) -> pd.DataFrame:
    return data[
        (data["side"].astype(str) == side)
        & (data["split"].astype(str) == split)
        & data["dependency_representative"]
    ].copy()


def fractional_top_decile_metrics(
    scores: Sequence[float],
    returns: Sequence[float],
    labels: Sequence[int],
    cost: float,
) -> dict[str, Any]:
    """Return a tie-invariant exact 10% tail using fractional boundary weights."""

    score = np.asarray(scores, dtype=float)
    ret = np.asarray(returns, dtype=float)
    label = np.asarray(labels, dtype=float)
    if not (len(score) == len(ret) == len(label)) or len(score) == 0:
        raise FeatureAblationError(
            "fractional top-decile arrays must be non-empty and aligned"
        )
    target = max(1.0, len(score) * 0.1)
    order = np.argsort(-score, kind="mergesort")
    sorted_score = score[order]
    weights = np.zeros(len(score), dtype=float)
    remaining = target
    start = 0
    while start < len(score) and remaining > 1e-12:
        end = start + 1
        while end < len(score) and sorted_score[end] == sorted_score[start]:
            end += 1
        group_size = end - start
        weight = min(1.0, remaining / group_size)
        weights[order[start:end]] = weight
        remaining -= weight * group_size
        start = end
    gross = float(np.average(ret, weights=weights))
    return {
        "effective_n": float(weights.sum()),
        "boundary_score": float(score[weights > 0].min()),
        "boundary_fraction": float(weights[weights > 0].min()),
        "gross_mean": gross,
        "net_mean": gross - float(cost),
        "win_rate": float(np.average(label, weights=weights)),
    }


def score_diagnostics(
    frame: pd.DataFrame, scores: np.ndarray, threshold: float, cost: float
) -> dict[str, Any]:
    from scipy.stats import spearmanr

    returns = frame["realized_ret"].to_numpy(dtype=float)
    labels = frame["label"].to_numpy(dtype=int)
    rho = spearmanr(scores, returns).statistic if len(scores) > 1 else np.nan
    keep = scores >= threshold
    return {
        "n": len(frame),
        "distinct_scores": int(np.unique(scores).size),
        "spearman_score_vs_return": (
            None if not np.isfinite(rho) else float(rho)
        ),
        "fractional_top_decile": fractional_top_decile_metrics(
            scores, returns, labels, cost
        ),
        "q90": selected_metrics(frame, keep, cost),
    }


def runtime_versions() -> dict[str, Any]:
    import scipy
    import sklearn

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            "lightgbm": lgb.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit-learn": sklearn.__version__,
            "scipy": scipy.__version__,
        },
    }


def train_one_arm(
    train: pd.DataFrame,
    tune: pd.DataFrame,
    *,
    side: str,
    arm: str,
    columns: Sequence[str],
    cost: float,
) -> dict[str, Any]:
    model = train_model(
        train,
        tune,
        feature_columns=columns,
        objective="regression",
        params_override=L2_DETERMINISTIC_PARAMS,
    )
    scores = model.predict(
        tune[list(columns)], num_iteration=model.best_iteration
    )
    threshold = float(np.quantile(scores, 0.9))
    diagnostics = score_diagnostics(tune, scores, threshold, cost)
    health = {
        "best_iteration_at_least_2": int(model.best_iteration) >= 2,
        "at_least_10_distinct_tune_scores": (
            diagnostics["distinct_scores"] >= 10
        ),
        "tune_q90_pass_rate_at_most_25pct": (
            diagnostics["q90"]["pass_rate"] <= 0.25
        ),
    }
    health["passed"] = all(health.values())
    model_dir = OUTPUT_DIR / "models"
    score_dir = OUTPUT_DIR / "tune_scores"
    importance_dir = OUTPUT_DIR / "feature_importance"
    for directory in (model_dir, score_dir, importance_dir):
        directory.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"{side}_{arm}.txt"
    model.save_model(str(model_path))
    score_path = score_dir / f"{side}_{arm}.csv"
    pd.DataFrame(
        {
            "episode_id": tune["episode_id"].astype(str).to_numpy(),
            "score": scores,
            "label": tune["label"].to_numpy(dtype=int),
            "realized_ret": tune["realized_ret"].to_numpy(dtype=float),
        }
    ).to_csv(score_path, index=False)
    importance = pd.DataFrame(
        {
            "feature": list(columns),
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values(["gain", "feature"], ascending=[False, True])
    importance_path = importance_dir / f"{side}_{arm}.csv"
    importance.to_csv(importance_path, index=False)
    return {
        "side": side,
        "arm": arm,
        "feature_columns": list(columns),
        "feature_count": len(columns),
        "train_rows": len(train),
        "tune_rows": len(tune),
        "best_iteration": int(model.best_iteration),
        "tune_q90_threshold": threshold,
        "diagnostics": diagnostics,
        "health": health,
        "model_path": repo_relative(model_path),
        "model_sha256": sha256_file(model_path),
        "tune_scores_path": repo_relative(score_path),
        "tune_scores_sha256": sha256_file(score_path),
        "feature_importance_path": repo_relative(importance_path),
        "feature_importance_sha256": sha256_file(importance_path),
        "feature_importance_top10": importance.head(10).to_dict("records"),
    }


def select_best_arm(
    records: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    healthy = [record for record in records if record["health"]["passed"]]
    if not healthy:
        return next(
            record for record in records if record["arm"] == "full_28"
        )
    order = {arm: index for index, arm in enumerate(FEATURE_ARMS)}

    def rank_key(
        record: Mapping[str, Any]
    ) -> tuple[float, float, int, int]:
        diagnostics = record["diagnostics"]
        rho = diagnostics["spearman_score_vs_return"]
        return (
            -float(
                diagnostics["fractional_top_decile"]["net_mean"]
            ),
            -float(rho if rho is not None else -np.inf),
            int(record["feature_count"]),
            order[str(record["arm"])],
        )

    return sorted(healthy, key=rank_key)[0]


def run_selection(prereg: Mapping[str, Any]) -> dict[str, Any]:
    verify_pinned_inputs(prereg, include_controls=False)
    data = load_source(prereg)
    cost = float(
        prereg["frozen_contract"]["round_trip_cost_fraction"]
    )
    selections: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for side in SIDES:
        train = learning_rows(data, side, "train")
        tune = learning_rows(data, side, "tune")
        if min(len(train), len(tune)) == 0:
            raise FeatureAblationError(
                f"empty development split for {side}"
            )
        records = [
            train_one_arm(
                train,
                tune,
                side=side,
                arm=arm,
                columns=columns,
                cost=cost,
            )
            for arm, columns in FEATURE_ARMS.items()
        ]
        chosen = select_best_arm(records)
        selections[side] = {
            "selected_arm": chosen["arm"],
            "feature_columns": chosen["feature_columns"],
            "selection_reason": (
                "highest healthy tune fractional tie-aware top-decile net; "
                "preregistered tie-breakers applied"
            ),
            "arms": records,
        }
        for record in records:
            rows.append(
                {
                    "side": side,
                    "arm": record["arm"],
                    "feature_count": record["feature_count"],
                    "best_iteration": record["best_iteration"],
                    "distinct_tune_scores": record["diagnostics"][
                        "distinct_scores"
                    ],
                    "fractional_top_decile_net": record["diagnostics"][
                        "fractional_top_decile"
                    ]["net_mean"],
                    "q90_n": record["diagnostics"]["q90"]["n"],
                    "q90_pass_rate": record["diagnostics"]["q90"][
                        "pass_rate"
                    ],
                    "q90_net": record["diagnostics"]["q90"]["net_mean"],
                    "spearman": record["diagnostics"][
                        "spearman_score_vs_return"
                    ],
                    "healthy": record["health"]["passed"],
                    "selected": record["arm"] == chosen["arm"],
                }
            )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ranking_path = OUTPUT_DIR / "tune_arm_ranking.csv"
    pd.DataFrame(rows).to_csv(ranking_path, index=False)
    receipt = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "phase": "tune_selection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "source_dataset_sha256": prereg["source"]["dataset_sha256"],
        "feature_arms": {
            name: list(columns)
            for name, columns in FEATURE_ARMS.items()
        },
        "training_params": {
            **LGB_PARAMS,
            **L2_DETERMINISTIC_PARAMS,
            "objective": "regression",
        },
        "runtime": runtime_versions(),
        "selections": selections,
        "ranking_path": repo_relative(ranking_path),
        "ranking_sha256": sha256_file(ranking_path),
        "final_validation_rows_used_for_selection": 0,
        "holdout_rows_read": 0,
        "promoted": False,
        "deployed": False,
        "production_eligible": False,
    }
    write_json(RESULTS_DIR / "selection_receipt.json", receipt)
    return receipt


def load_selection(prereg: Mapping[str, Any]) -> dict[str, Any]:
    path = RESULTS_DIR / "selection_receipt.json"
    if not path.is_file():
        raise FeatureAblationError(
            "selection receipt is required before final evaluation"
        )
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("phase") != "tune_selection":
        raise FeatureAblationError("selection phase receipt drifted")
    if (
        receipt.get("source_dataset_sha256")
        != prereg["source"]["dataset_sha256"]
    ):
        raise FeatureAblationError("selection source drifted")
    if receipt.get("final_validation_rows_used_for_selection") != 0:
        raise FeatureAblationError(
            "selection improperly used final validation"
        )
    for side in SIDES:
        arm = receipt["selections"][side]["selected_arm"]
        if arm not in FEATURE_ARMS:
            raise FeatureAblationError(
                f"unknown selected arm for {side}: {arm}"
            )
    return receipt


def load_model_record(
    selection: Mapping[str, Any], side: str, arm: str
) -> Mapping[str, Any]:
    records = selection["selections"][side]["arms"]
    record = next(
        (item for item in records if item["arm"] == arm), None
    )
    if record is None:
        raise FeatureAblationError(
            f"missing trained {side}/{arm} record"
        )
    for path_key, sha_key in (
        ("model_path", "model_sha256"),
        ("tune_scores_path", "tune_scores_sha256"),
        ("feature_importance_path", "feature_importance_sha256"),
    ):
        path = repo_path(record[path_key])
        if not path.is_file() or sha256_file(path) != record[sha_key]:
            raise FeatureAblationError(
                f"selection artifact drifted: {path}"
            )
    return record


def strict_control_metrics(
    validation: pd.DataFrame,
    controls: pd.DataFrame,
    selected_ids: set[str],
) -> dict[str, Any]:
    metrics = matched_control_metrics(
        validation,
        controls,
        selected_ids,
        required_assignments=8,
    )
    selected = set(map(str, selected_ids))
    assignment_sets = (
        controls[
            controls["episode_id"].astype(str).isin(selected)
        ]
        .groupby("episode_id")["assignment"]
        .agg(lambda values: frozenset(int(value) for value in values))
    )
    required = frozenset(range(8))
    covered = set(
        assignment_sets[assignment_sets == required].index.astype(str)
    )
    complete = covered == selected
    metrics.update(
        {
            "selected_event_count": len(selected),
            "selected_event_complete_control_count": len(covered),
            "selected_event_complete_coverage": complete,
            "selected_event_missing_controls": sorted(selected - covered),
        }
    )
    metrics["all_assignments_positive"] = bool(
        metrics["all_assignments_positive"] and complete
    )
    return metrics


def score_final_arm(
    data: pd.DataFrame,
    selection: Mapping[str, Any],
    *,
    side: str,
    arm: str,
) -> tuple[pd.DataFrame, np.ndarray]:
    record = load_model_record(selection, side, arm)
    columns = list(FEATURE_ARMS[arm])
    model = lgb.Booster(
        model_file=str(repo_path(record["model_path"]))
    )
    # Score plateaus are common in small LightGBM models. The default pandas
    # parser can move a serialized float by one ULP, turning an equality at a
    # plateau into < or > and shifting the empirical CDF by one full rank.
    tune_scores = pd.read_csv(
        repo_path(record["tune_scores_path"]),
        float_precision="round_trip",
    )["score"].to_numpy(dtype=float)
    final_events = data[
        (data["side"].astype(str) == side)
        & (data["split"].astype(str) == "final_validation")
    ].copy()
    scores = model.predict(
        final_events[columns],
        num_iteration=int(record["best_iteration"]),
    )
    threshold = float(record["tune_q90_threshold"])
    prefix = f"{arm}_{side}"
    final_events[f"{prefix}_score"] = scores
    final_events[f"{prefix}_percentile"] = empirical_percentile(
        tune_scores, scores
    )
    final_events[f"{prefix}_threshold"] = threshold
    final_events[f"{prefix}_keep"] = scores >= threshold
    return final_events, tune_scores


def metric_bundle(
    scored_by_side: Mapping[str, pd.DataFrame],
    controls: pd.DataFrame,
    *,
    arms_by_side: Mapping[str, str],
    cost: float,
) -> dict[str, Any]:
    pieces: list[pd.DataFrame] = []
    by_side: dict[str, Any] = {}
    for side in SIDES:
        arm = arms_by_side[side]
        prefix = f"{arm}_{side}"
        events = scored_by_side[side]
        representatives = events[
            events["dependency_representative"]
        ].copy()
        score = representatives[f"{prefix}_score"].to_numpy(
            dtype=float
        )
        percentile = representatives[
            f"{prefix}_percentile"
        ].to_numpy(dtype=float)
        keep = representatives[f"{prefix}_keep"].to_numpy(dtype=bool)
        returns = representatives["realized_ret"].to_numpy(dtype=float)
        labels = representatives["label"].to_numpy(dtype=int)
        by_side[side] = {
            "arm": arm,
            "feature_count": len(FEATURE_ARMS[arm]),
            "final_validation": safe_metrics(
                labels, score, returns, cost
            ),
            "fractional_top_decile": fractional_top_decile_metrics(
                score, returns, labels, cost
            ),
            "frozen_q90": selected_metrics(
                representatives, keep, cost
            ),
            "outcome_permutation_p": outcome_permutation_pvalue(
                score, returns
            ),
        }
        renamed = representatives.copy()
        renamed["evaluation_score"] = percentile
        renamed["evaluation_keep"] = keep
        pieces.append(renamed)
    combined = pd.concat(pieces, ignore_index=True)
    score = combined["evaluation_score"].to_numpy(dtype=float)
    keep = combined["evaluation_keep"].to_numpy(dtype=bool)
    returns = combined["realized_ret"].to_numpy(dtype=float)
    labels = combined["label"].to_numpy(dtype=int)
    selected_ids = set(
        combined.loc[keep, "episode_id"].astype(str)
    )
    return {
        "arms_by_side": dict(arms_by_side),
        "final_validation": safe_metrics(
            labels, score, returns, cost
        ),
        "fractional_top_decile": fractional_top_decile_metrics(
            score, returns, labels, cost
        ),
        "frozen_q90": selected_metrics(combined, keep, cost),
        "outcome_permutation_p": outcome_permutation_pvalue(
            score, returns
        ),
        "matched_control": strict_control_metrics(
            combined, controls, selected_ids
        ),
        "by_side": by_side,
        "representative_rows": len(combined),
    }


def baseline_reproduction(
    scored: Mapping[str, pd.DataFrame],
    prereg: Mapping[str, Any],
) -> dict[str, Any]:
    previous = pd.read_csv(
        repo_path(prereg["source"]["prior_side_split_scores"])
    )
    deltas: list[float] = []
    decisions: list[bool] = []
    threshold_deltas: list[float] = []
    percentile_deltas: list[float] = []
    rows = 0
    for side in SIDES:
        current = scored[side][
            [
                "episode_id",
                f"full_28_{side}_score",
                f"full_28_{side}_percentile",
                f"full_28_{side}_threshold",
                f"full_28_{side}_keep",
            ]
        ].copy()
        prior = previous[
            previous["side"].astype(str) == side
        ][
            [
                "episode_id",
                "l2_score",
                "side_percentile_score",
                "l2_threshold",
                "l2_keep",
            ]
        ].copy()
        merged = current.merge(
            prior, on="episode_id", validate="one_to_one"
        )
        if len(merged) != len(current) or len(merged) != len(prior):
            raise FeatureAblationError(
                f"baseline row identity drifted for {side}"
            )
        deltas.extend(
            np.abs(
                merged[f"full_28_{side}_score"]
                - merged["l2_score"]
            ).tolist()
        )
        threshold_deltas.extend(
            np.abs(
                merged[f"full_28_{side}_threshold"]
                - merged["l2_threshold"]
            ).tolist()
        )
        percentile_deltas.extend(
            np.abs(
                merged[f"full_28_{side}_percentile"]
                - merged["side_percentile_score"]
            ).tolist()
        )
        decisions.extend(
            (
                merged[f"full_28_{side}_keep"].astype(bool)
                == merged["l2_keep"].astype(bool)
            ).tolist()
        )
        rows += len(merged)
    maximum_score_delta = max(deltas, default=0.0)
    maximum_threshold_delta = max(
        threshold_deltas, default=0.0
    )
    maximum_percentile_delta = max(
        percentile_deltas, default=0.0
    )
    passed = (
        maximum_score_delta <= 1e-12
        and maximum_threshold_delta <= 1e-12
        and maximum_percentile_delta <= 1e-12
        and all(decisions)
    )
    return {
        "rows": rows,
        "maximum_absolute_score_delta": maximum_score_delta,
        "maximum_absolute_threshold_delta": maximum_threshold_delta,
        "maximum_absolute_percentile_delta": maximum_percentile_delta,
        "score_tolerance": 1e-12,
        "keep_decisions_exact": all(decisions),
        "passed": passed,
    }


def run_final_evaluation(
    prereg: Mapping[str, Any]
) -> dict[str, Any]:
    verify_pinned_inputs(prereg, include_controls=True)
    selection = load_selection(prereg)
    data = load_source(prereg)
    controls = pd.read_csv(
        repo_path(prereg["source"]["matched_controls_path"])
    )
    cost = float(
        prereg["frozen_contract"]["round_trip_cost_fraction"]
    )
    selected_arms = {
        side: selection["selections"][side]["selected_arm"]
        for side in SIDES
    }
    configurations = {
        "selected": selected_arms,
        "full_28": {side: "full_28" for side in SIDES},
        "ma_spread_only": {
            side: "ma_spread_only" for side in SIDES
        },
    }
    scored_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for arms in configurations.values():
        for side, arm in arms.items():
            key = (side, arm)
            if key not in scored_cache:
                scored_cache[key], _ = score_final_arm(
                    data, selection, side=side, arm=arm
                )
    metrics: dict[str, Any] = {}
    for name, arms in configurations.items():
        metrics[name] = metric_bundle(
            {
                side: scored_cache[(side, arms[side])]
                for side in SIDES
            },
            controls,
            arms_by_side=arms,
            cost=cost,
        )
    reproduction = baseline_reproduction(
        {
            side: scored_cache[(side, "full_28")]
            for side in SIDES
        },
        prereg,
    )
    selected = metrics["selected"]
    baseline = metrics["full_28"]
    by_side = selected["by_side"]
    gate = {
        "baseline_reproduction_required": reproduction["passed"],
        "aggregate_selected_q90_net_strictly_better_than_full_28": (
            selected["frozen_q90"]["net_mean"] is not None
            and baseline["frozen_q90"]["net_mean"] is not None
            and selected["frozen_q90"]["net_mean"]
            > baseline["frozen_q90"]["net_mean"]
        ),
        "aggregate_selected_top_decile_net_positive": (
            selected["fractional_top_decile"]["net_mean"] > 0
        ),
        "aggregate_outcome_permutation_p_lt_0_01": (
            selected["outcome_permutation_p"] < 0.01
        ),
        "aggregate_minimum_30_selected_dependency_blocks": (
            selected["frozen_q90"]["n"] >= 30
        ),
        "aggregate_beats_matched_controls_every_assignment": (
            selected["matched_control"]["all_assignments_positive"]
        ),
        "each_side_minimum_10_selected_dependency_blocks": all(
            by_side[side]["frozen_q90"]["n"] >= 10
            for side in SIDES
        ),
        "neither_side_selected_q90_net_negative": all(
            by_side[side]["frozen_q90"]["net_mean"] is not None
            and by_side[side]["frozen_q90"]["net_mean"] >= 0
            for side in SIDES
        ),
    }
    gate["passed"] = all(gate.values())
    scored_columns = [
        "episode_id",
        "symbol",
        "side",
        "available_at",
        "label",
        "realized_ret",
        "net_ret",
        "dependency_block_id",
        "dependency_representative",
    ]
    scored_frames: list[pd.DataFrame] = []
    for side in SIDES:
        base = scored_cache[
            (side, selected_arms[side])
        ][scored_columns].copy()
        for config, arms in configurations.items():
            arm = arms[side]
            prefix = f"{arm}_{side}"
            source = scored_cache[(side, arm)].set_index(
                "episode_id"
            )
            base[f"{config}_arm"] = arm
            for suffix in (
                "score",
                "percentile",
                "threshold",
                "keep",
            ):
                base[f"{config}_{suffix}"] = base[
                    "episode_id"
                ].map(source[f"{prefix}_{suffix}"])
        scored_frames.append(base)
    scored_path = (
        OUTPUT_DIR
        / "final_validation_feature_ablation_scored.csv"
    )
    pd.concat(scored_frames, ignore_index=True).to_csv(
        scored_path, index=False
    )
    receipt = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "phase": "final_validation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": git_head(),
        "selection_receipt_path": repo_relative(
            RESULTS_DIR / "selection_receipt.json"
        ),
        "selection_receipt_sha256": sha256_file(
            RESULTS_DIR / "selection_receipt.json"
        ),
        "selected_arms": selected_arms,
        "baseline_reproduction": reproduction,
        "metrics": metrics,
        "primary_gate": gate,
        "decision": "accept" if gate["passed"] else "reject",
        "scored_path": repo_relative(scored_path),
        "scored_sha256": sha256_file(scored_path),
        "final_validation_rows_used_for_selection": 0,
        "holdout_rows_read": 0,
        "promoted": False,
        "deployed": False,
        "active_or_frozen_changed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
    }
    write_json(RESULTS_DIR / "training_receipt.json", receipt)
    return receipt


def render_diagnostics(prereg: Mapping[str, Any]) -> Path:
    import matplotlib.pyplot as plt

    selection = load_selection(prereg)
    training_path = RESULTS_DIR / "training_receipt.json"
    if not training_path.is_file():
        raise FeatureAblationError("training receipt missing")
    training = json.loads(
        training_path.read_text(encoding="utf-8")
    )
    ranking = pd.read_csv(repo_path(selection["ranking_path"]))
    fig, axes = plt.subplots(
        2, 2, figsize=(16, 10), constrained_layout=True
    )
    for axis, side in zip(axes[0], SIDES):
        arm_rows = ranking[ranking["side"] == side]
        colors = [
            "#d62728" if bool(value) else "#4c78a8"
            for value in arm_rows["selected"]
        ]
        axis.barh(
            arm_rows["arm"],
            arm_rows["fractional_top_decile_net"] * 100,
            color=colors,
        )
        axis.axvline(0, color="black", linewidth=0.8)
        axis.set_title(
            f"{side.upper()} tune: exact top-10% net return"
        )
        axis.set_xlabel("%")
        axis.invert_yaxis()
    names = ["ma_spread_only", "full_28", "selected"]
    labels = ["1 feature", "full 28", "selected"]
    metrics = training["metrics"]
    top_values = [
        metrics[name]["fractional_top_decile"]["net_mean"] * 100
        for name in names
    ]
    q90_values = [
        metrics[name]["frozen_q90"]["net_mean"] * 100
        for name in names
    ]
    axes[1, 0].bar(
        labels,
        top_values,
        color=["#9ecae9", "#6baed6", "#d62728"],
    )
    axes[1, 0].axhline(0, color="black", linewidth=0.8)
    axes[1, 0].set_title(
        "April final: exact top-10% net return"
    )
    axes[1, 0].set_ylabel("%")
    axes[1, 1].bar(
        labels,
        q90_values,
        color=["#9ecae9", "#6baed6", "#d62728"],
    )
    axes[1, 1].axhline(0, color="black", linewidth=0.8)
    axes[1, 1].set_title(
        "April final: frozen tune-q90 net return"
    )
    axes[1, 1].set_ylabel("%")
    fig.suptitle(
        "15m L2 feature-group ablation "
        "(LONG / SHORT selected independently)"
    )
    path = OUTPUT_DIR / "feature_group_ablation_diagnostics.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def pct(value: Any) -> str:
    return "—" if value is None else f"{float(value):+.3%}"


def build_report(prereg: Mapping[str, Any]) -> Path:
    selection = load_selection(prereg)
    training = json.loads(
        (RESULTS_DIR / "training_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    ranking = pd.read_csv(repo_path(selection["ranking_path"]))
    source = load_source(prereg)
    reps = source[source["dependency_representative"]]
    selected = training["metrics"]["selected"]
    baseline = training["metrics"]["full_28"]
    one = training["metrics"]["ma_spread_only"]
    lines = [
        "# 15m L2 28 特征分组消融（2026-09-02）",
        "",
        "## 结论先行",
        "",
        (
            f"本轮最终裁决：**{training['decision'].upper()}**。"
            f"LONG 在 tune 期选中 **{training['selected_arms']['long']}**，"
            f"SHORT 选中 **{training['selected_arms']['short']}**。"
            "28 个特征不是先验真理，而是旧基线；当前 YOLO 候选上是否应该保留，"
            "必须由时间外经济结果决定。"
        ),
        "",
        "![特征组消融诊断](output/ma_launch_l2_feature_group_ablation_v1/feature_group_ablation_diagnostics.png)",
        "",
        "## 数据统计",
        "",
        "| split | LONG 独立事件 | SHORT 独立事件 | 合计 |",
        "|---|---:|---:|---:|",
    ]
    for split in ("train", "tune", "final_validation"):
        long_n = len(
            reps[
                (reps["split"] == split)
                & (reps["side"] == "long")
            ]
        )
        short_n = len(
            reps[
                (reps["split"] == split)
                & (reps["side"] == "short")
            ]
        )
        lines.append(
            f"| {split} | {long_n} | {short_n} | "
            f"{long_n + short_n} |"
        )
    start = pd.to_datetime(source["available_at"], utc=True).min()
    end = pd.to_datetime(source["available_at"], utc=True).max()
    lines.extend(
        [
            "",
            (
                f"数据范围：{start:%Y-%m-%d} 至 {end:%Y-%m-%d}；"
                "holdout 读取：0。每项指标只用 dependency representative，"
                "避免同一行情的重叠检测框重复计票。"
            ),
            "",
            "## tune 期预注册选择",
            "",
            "| 方向 | 方案 | 特征数 | iter | 精确 top-10% 净收益 | q90 n | q90 净收益 | Spearman | 健康 | 入选 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for _, row in ranking.iterrows():
        lines.append(
            f"| {str(row['side']).upper()} | {row['arm']} | "
            f"{int(row['feature_count'])} | "
            f"{int(row['best_iteration'])} | "
            f"{pct(row['fractional_top_decile_net'])} | "
            f"{int(row['q90_n'])} | {pct(row['q90_net'])} | "
            f"{row['spearman']:.4f} | {bool(row['healthy'])} | "
            f"{bool(row['selected'])} |"
        )
    lines.extend(
        [
            "",
            (
                "选择只看 3 月 tune；4 月 final 没有参与选方案。"
                "LightGBM early stopping 也使用同一个 tune，因此仍有"
                "“既早停又选组”的乐观偏差，不能把 tune 最优当成生产结论。"
            ),
            "",
            "## April final 同表对照",
            "",
            "| 配置 | LONG/SHORT 特征 | top-10% 净收益 | q90 n | q90 净收益 | 胜率 | 置换 p |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, key in (
        ("单特征基线", "ma_spread_only"),
        ("旧 28 特征", "full_28"),
        ("tune 入选组合", "selected"),
    ):
        item = training["metrics"][key]
        arms = item["arms_by_side"]
        lines.append(
            f"| {label} | {arms['long']} / {arms['short']} | "
            f"{pct(item['fractional_top_decile']['net_mean'])} | "
            f"{item['frozen_q90']['n']} | "
            f"{pct(item['frozen_q90']['net_mean'])} | "
            f"{item['frozen_q90']['win_rate']:.2%} | "
            f"{item['outcome_permutation_p']:.6f} |"
        )
    lines.extend(
        [
            "",
            (
                "AUC 只作诊断：入选组合 "
                f"AUC={selected['final_validation']['roc_auc']:.4f}，"
                f"PR-AUC={selected['final_validation']['pr_auc']:.4f}，"
                f"Spearman="
                f"{selected['final_validation']['spearman_score_vs_return']:.4f}。"
                "裁决仍以扣成本收益、置换检验、样本量和匹配对照为准。"
            ),
            "",
            "## LONG / SHORT final",
            "",
            "| 方向 | 入选方案 | final n | q90 n | q90 净收益 | 胜率 | p |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for side in SIDES:
        item = selected["by_side"][side]
        lines.append(
            f"| {side.upper()} | {item['arm']} | "
            f"{item['final_validation']['n']} | "
            f"{item['frozen_q90']['n']} | "
            f"{pct(item['frozen_q90']['net_mean'])} | "
            f"{item['frozen_q90']['win_rate']:.2%} | "
            f"{item['outcome_permutation_p']:.6f} |"
        )
    matched = selected["matched_control"]
    gate = training["primary_gate"]
    lines.extend(
        [
            "",
            "## 匹配随机对照与裁决门",
            "",
            (
                "匹配条件保持同币、同月、同 UTC 8 小时时段、同 ATR 桶、"
                "同方向、同 TP/SL/horizon/cost。完整 assignment："
                f"{matched['usable_assignment_count']}/"
                f"{matched['required_assignment_count']}；"
                f"入选事件完整覆盖："
                f"{matched['selected_event_complete_control_count']}/"
                f"{matched['selected_event_count']}；"
                f"全部 assignment 跑赢："
                f"{matched['all_assignments_positive']}；"
                f"平均事件减对照："
                f"{pct(matched['mean_event_minus_control'])}。"
            ),
            "",
            "| 预注册门 | 通过 |",
            "|---|---|",
        ]
    )
    for name, passed in gate.items():
        if name != "passed":
            lines.append(f"| {name} | {passed} |")
    lines.extend(
        [
            f"| 全部门 | **{gate['passed']}** |",
            "",
            "## 为什么是这些 28 个，以及本轮回答了什么",
            "",
            (
                "28 个是 2026-07-07 为当时 strict-rule 候选人工设计的旧基线："
                "11 个均线密集/持续性、5 个价格与趋势、3 个成交量、"
                "5 个波动率、4 个动量。它们覆盖了合理的市场维度，"
                "但不是自动从当前 YOLO 候选上筛出来的，也不保证全部有用。"
            ),
            "",
            (
                "本轮把“特征是否有用”改成可证伪问题：MA 单特征、"
                "MA 结构、纯上下文，以及逐组加入趋势/量能/波动/动量，"
                "固定模型与时间切分逐一比较。"
                "若 tune 入选组合在 final 失败，说明当前 229 个 tune 事件"
                "不足以稳定选择，不能继续凭感觉删特征。"
            ),
            "",
            "## 基线复现与无前视",
            "",
            (
                "旧 28 特征 LONG/SHORT 的全部 final 分数、阈值和 KEEP "
                f"决策复现通过：{training['baseline_reproduction']['passed']}；"
                "最大分数差 "
                f"{training['baseline_reproduction']['maximum_absolute_score_delta']:.3e}。"
                "特征来自已冻结的 L1 决策 bar 及以前，标签才看未来。"
            ),
            "",
            "## 风险与诚实声明",
            "",
            "- 训练 417、tune 229、final 242 个独立事件，按方向再拆后仍偏小；特征选择方差可能很大。",
            "- 七个方案在同一 tune 上比较，存在多重比较；未用 final 反向重选。",
            "- 当前 YOLO 是 completed-history 检测器，消费过 core 后 2–9 根 K；本实验不能冒充 tip 实盘信号。",
            "- 未读取 2026-05-04 后 holdout，未 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。",
            "",
            "## 复现命令",
            "",
            "    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_group_ablation --select",
            "    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_group_ablation --evaluate-final",
            "    PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l2_feature_group_ablation --render --verify --report",
            "    python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_feature_group_ablation_20260902.md --out-dir analysis/html",
            "",
            "## 下一步",
            "",
            (
                "只有全部预注册门通过，才值得在新的、未见时间段复验。"
                "本报告不授权读取 holdout、promote 或部署。"
            ),
            "",
        ]
    )
    REPORT_PATH.write_text(
        "\n".join(lines), encoding="utf-8"
    )
    return REPORT_PATH


def verify_outputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    verify_pinned_inputs(prereg, include_controls=True)
    selection = load_selection(prereg)
    training_path = RESULTS_DIR / "training_receipt.json"
    if not training_path.is_file():
        raise FeatureAblationError("training receipt missing")
    training = json.loads(
        training_path.read_text(encoding="utf-8")
    )
    if (
        training["selection_receipt_sha256"]
        != sha256_file(RESULTS_DIR / "selection_receipt.json")
    ):
        raise FeatureAblationError(
            "training receipt is not bound to selection receipt"
        )
    if training["holdout_rows_read"] != 0:
        raise FeatureAblationError("holdout read drifted")
    if training["final_validation_rows_used_for_selection"] != 0:
        raise FeatureAblationError(
            "final validation leaked into selection"
        )
    scored_path = repo_path(training["scored_path"])
    if sha256_file(scored_path) != training["scored_sha256"]:
        raise FeatureAblationError("scored final artifact drifted")
    for side in SIDES:
        for arm in FEATURE_ARMS:
            load_model_record(selection, side, arm)
    diagnostic = OUTPUT_DIR / "feature_group_ablation_diagnostics.png"
    checks = {
        "baseline_reproduction": training[
            "baseline_reproduction"
        ]["passed"],
        "selection_final_rows_zero": (
            selection["final_validation_rows_used_for_selection"] == 0
        ),
        "holdout_rows_zero": (
            selection["holdout_rows_read"] == 0
            and training["holdout_rows_read"] == 0
        ),
        "all_14_models_verified": True,
        "scored_hash_verified": True,
        "diagnostic_exists": diagnostic.is_file(),
        "not_promoted_or_deployed": (
            not training["promoted"]
            and not training["deployed"]
            and not training["active_or_frozen_changed"]
        ),
    }
    checks["passed"] = all(checks.values())
    receipt = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "selection_receipt_sha256": sha256_file(
            RESULTS_DIR / "selection_receipt.json"
        ),
        "training_receipt_sha256": sha256_file(training_path),
        "diagnostic_path": repo_relative(diagnostic),
        "diagnostic_sha256": (
            sha256_file(diagnostic) if diagnostic.is_file() else None
        ),
    }
    write_json(RESULTS_DIR / "verify_receipt.json", receipt)
    if not checks["passed"]:
        raise FeatureAblationError(
            f"verification failed: {checks}"
        )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--evaluate-final", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not any(
        (
            args.select,
            args.evaluate_final,
            args.render,
            args.verify,
            args.report,
        )
    ):
        parser.error("select at least one action")
    prereg = load_preregistration()
    outputs: dict[str, Any] = {}
    if args.select:
        outputs["selection"] = run_selection(prereg)
    if args.evaluate_final:
        outputs["final"] = run_final_evaluation(prereg)
    if args.render:
        outputs["render"] = repo_relative(
            render_diagnostics(prereg)
        )
    if args.verify:
        outputs["verify"] = verify_outputs(prereg)
    if args.report:
        outputs["report"] = repo_relative(
            build_report(prereg)
        )
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
