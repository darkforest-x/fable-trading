#!/usr/bin/env python3
"""Explain the frozen BTCUSDT.P 15m LightGBM score with SHAP 0.52.

The model, schema, training medians, and evaluation rows are immutable inputs.
The explained output is the model's raw regression score in predicted net-return
fraction.  A deterministic 100-row sample from the 2023 training population is
the interventional background.  The 2025--2026-02 frozen selected rows are used
for the error-cohort audit; no model fitting or threshold tuning occurs here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/active/exp-btcusdtp-15m-high-recall-l2-trend-runner-preholdout-20260904-v1"
RESULTS = EXPERIMENT / "results"
MODEL_PATH = RESULTS / "l2_huber_model.txt"
CONTRACT_PATH = RESULTS / "model_contract.json"
TRAIN_PATH = RESULTS / "train_scored_events.csv.gz"
EVAL_PATH = RESULTS / "validation_l2_selected.csv.gz"
OUTPUT = RESULTS / "shap_audit"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matrix(frame: pd.DataFrame, features: list[str], medians: dict[str, float]) -> pd.DataFrame:
    return (
        frame[features]
        .replace([np.inf, -np.inf], np.nan)
        .astype(float)
        .fillna({name: float(medians[name]) for name in features})
        .fillna(0.0)
    )


def save_figure(path: Path) -> None:
    figure = plt.gcf()
    figure.tight_layout()
    figure.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    features = list(map(str, contract["feature_names"]))
    train = pd.read_csv(TRAIN_PATH)
    evaluation = pd.read_csv(EVAL_PATH)
    x_train = matrix(train, features, contract["training_medians"])
    x_eval = matrix(evaluation, features, contract["training_medians"])
    background = x_train.sample(n=100, random_state=20260904, replace=False)
    booster = lgb.Booster(model_file=str(MODEL_PATH))
    prediction = booster.predict(x_eval)

    interventional = shap.TreeExplainer(
        booster,
        data=background,
        feature_perturbation="interventional",
        model_output="raw",
        feature_names=features,
    )
    explanation = interventional(x_eval)
    reconstructed = np.asarray(explanation.base_values) + np.asarray(
        explanation.values
    ).sum(axis=1)
    max_error = float(np.max(np.abs(reconstructed - prediction)))
    np.testing.assert_allclose(reconstructed, prediction, rtol=1e-5, atol=1e-8)

    path_explainer = shap.TreeExplainer(
        booster,
        feature_perturbation="tree_path_dependent",
        model_output="raw",
        feature_names=features,
    )
    path_explanation = path_explainer(x_eval)
    path_reconstructed = np.asarray(path_explanation.base_values) + np.asarray(
        path_explanation.values
    ).sum(axis=1)
    path_error = float(np.max(np.abs(path_reconstructed - prediction)))
    np.testing.assert_allclose(path_reconstructed, prediction, rtol=1e-5, atol=1e-8)
    explanation_bp = shap.Explanation(
        values=np.asarray(explanation.values) * 1e4,
        base_values=np.asarray(explanation.base_values) * 1e4,
        data=np.asarray(explanation.data),
        feature_names=features,
    )

    outcome = np.where(
        evaluation["net_return"].to_numpy(dtype=float) > 0.0,
        "net_winner",
        "net_loser",
    )
    importance = pd.DataFrame(
        {
            "feature": features,
            "interventional_mean_abs": np.abs(explanation.values).mean(axis=0),
            "path_dependent_mean_abs": np.abs(path_explanation.values).mean(axis=0),
            "interventional_mean_signed": explanation.values.mean(axis=0),
        }
    ).sort_values("interventional_mean_abs", ascending=False)
    rank_rho = float(
        spearmanr(
            importance["interventional_mean_abs"],
            importance["path_dependent_mean_abs"],
        ).statistic
    )
    importance.to_csv(output / "global_importance.csv", index=False)

    cohort_rows: list[dict[str, object]] = []
    for cohort in ("net_winner", "net_loser"):
        mask = outcome == cohort
        values = explanation.values[mask]
        for index, feature in enumerate(features):
            cohort_rows.append(
                {
                    "cohort": cohort,
                    "events": int(mask.sum()),
                    "feature": feature,
                    "mean_signed_shap": float(values[:, index].mean()),
                    "mean_abs_shap": float(np.abs(values[:, index]).mean()),
                    "median_feature_value": float(x_eval.loc[mask, feature].median()),
                }
            )
    cohorts = pd.DataFrame(cohort_rows)
    cohorts.to_csv(output / "cohort_attributions.csv", index=False)

    values_frame = pd.DataFrame(explanation.values, columns=features)
    values_frame.insert(0, "setup_id", evaluation["setup_id"].astype(str))
    values_frame.insert(1, "entry_time", evaluation["entry_time"].astype(str))
    values_frame.insert(2, "cohort", outcome)
    values_frame.insert(3, "model_score", prediction)
    values_frame.insert(4, "net_return", evaluation["net_return"].to_numpy(dtype=float))
    values_frame.to_csv(
        output / "selected_validation_shap_values.csv.gz",
        index=False,
        compression={"method": "gzip", "mtime": 0},
    )

    plt.figure(figsize=(9.0, 6.0))
    shap.plots.beeswarm(explanation_bp, max_display=15, show=False, plot_size=None)
    plt.xlabel("SHAP contribution to predicted net return (bp)")
    plt.title("Frozen L2: selected validation rows (n=113)")
    save_figure(output / "shap_beeswarm.png")

    top = importance.head(12)["feature"].tolist()
    pivot = cohorts.pivot(index="feature", columns="cohort", values="mean_signed_shap").loc[top]
    delta = (pivot["net_loser"] - pivot["net_winner"]).sort_values()
    colors = ["#17A297" if value < 0 else "#F59E0B" for value in delta]
    fig, ax = plt.subplots(figsize=(9.0, 5.6))
    ax.barh(delta.index, delta.values * 1e4, color=colors)
    ax.axvline(0.0, color="#26323A", linewidth=0.8)
    ax.set_xlabel("Mean SHAP: losers minus winners (predicted bp)")
    ax.set_title("How the frozen model justified selected losers")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(output / "shap_loser_winner_delta.png", dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    loss_indices = np.flatnonzero(evaluation["net_return"].to_numpy(dtype=float) <= 0.0)
    worst_high_score = int(loss_indices[np.argmax(prediction[loss_indices])])
    plt.figure(figsize=(9.0, 6.0))
    shap.plots.waterfall(explanation_bp[worst_high_score], max_display=15, show=False)
    plt.title("Highest-scored losing validation trade")
    save_figure(output / "shap_highest_scored_loss.png")

    package_names = ["shap", "lightgbm", "numpy", "pandas", "matplotlib", "scipy"]
    metadata = {
        "explained_output": "LightGBM raw regression score; predicted net-return fraction",
        "model_sha256": sha256(MODEL_PATH),
        "contract_sha256": sha256(CONTRACT_PATH),
        "training_rows": len(train),
        "evaluation_rows": len(evaluation),
        "background_rows": len(background),
        "background_rule": "2023 training matrix sample(n=100, random_state=20260904)",
        "background_index_sha256": hashlib.sha256(
            ",".join(map(str, background.index.tolist())).encode()
        ).hexdigest(),
        "maskers": [
            "interventional with explicit 100-row training background",
            "tree_path_dependent sensitivity check",
        ],
        "interventional_additivity_max_abs_error": max_error,
        "path_dependent_additivity_max_abs_error": path_error,
        "importance_rank_spearman": rank_rho,
        "highest_scored_loss_setup_id": str(evaluation.iloc[worst_high_score]["setup_id"]),
        "highest_scored_loss_prediction": float(prediction[worst_high_score]),
        "highest_scored_loss_realized_net_return": float(
            evaluation.iloc[worst_high_score]["net_return"]
        ),
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name) for name in package_names
        },
        "limitations": [
            "The fixed model already failed predictive validation; SHAP explains its behavior, not a valid trading edge.",
            "Correlated market features compete for attribution; interventional masks can be off-manifold.",
            "Attributions are descriptive, not causal and not a feature-selection result.",
        ],
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
