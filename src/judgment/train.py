"""Train and evaluate the judgment-layer LightGBM classifier.

Usage: python3 -m src.judgment.train [--eval-holdout]

Split discipline (strict time-based, no shuffling):
- HOLDOUT_START is frozen: samples with signal_time >= 2026-05-04 00:00 UTC
  are never touched by training or tuning; they are evaluated only when
  --eval-holdout is passed (once, results reported as-is).
- The remaining samples are split by time into train (first 80%) and
  val (last 20%).

Outputs metrics JSON to analysis/output/p2b_metrics.json and feature
importance to analysis/output/p2b_feature_importance.csv.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from src.data.bars import BAR_CHOICES, purge_window
from src.judgment.features import FEATURE_COLUMNS

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATASET_PATH = PROJECT_DIR / "data" / "judgment_dataset.csv"
OUTPUT_DIR = PROJECT_DIR / "analysis" / "output"

HOLDOUT_START = pd.Timestamp("2026-05-04 00:00:00", tz="UTC")  # frozen, do not tune on >= this
TRAIN_FRACTION = 0.8
THRESHOLDS = (0.4, 0.5, 0.6, 0.7)
ROUND_TRIP_COST = 0.002  # 0.2% taker + slippage assumption from P0
SEED = 42

LGB_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 30,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": SEED,
    "verbosity": -1,
}


DEFAULT_HORIZON_BARS = 72
DEFAULT_BAR = "15m"
PURGE_WINDOW = purge_window(DEFAULT_HORIZON_BARS, DEFAULT_BAR)


def load_splits(
    dataset_path: Path = DATASET_PATH, *, horizon_bars: int = DEFAULT_HORIZON_BARS, bar: str = DEFAULT_BAR
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    purge = purge_window(horizon_bars, bar)
    data = pd.read_csv(dataset_path, parse_dates=["signal_time"])
    data = data.sort_values("signal_time").reset_index(drop=True)
    dev = data[data["signal_time"] < HOLDOUT_START - purge].reset_index(drop=True)
    holdout = data[data["signal_time"] >= HOLDOUT_START].reset_index(drop=True)
    split_i = int(len(dev) * TRAIN_FRACTION)
    train, val = dev.iloc[:split_i], dev.iloc[split_i:]
    # purge train samples whose triple-barrier window overlaps the val period
    val_start = val["signal_time"].min()
    train = train[train["signal_time"] < val_start - purge]
    return train, val, holdout


def evaluate(y_true: np.ndarray, y_prob: np.ndarray, returns: np.ndarray) -> dict:
    out = {
        "n": int(len(y_true)),
        "positive_rate": round(float(np.mean(y_true)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, y_prob)), 4),
        "pr_auc": round(float(average_precision_score(y_true, y_prob)), 4),
        "thresholds": {},
    }
    for threshold in THRESHOLDS:
        pred = (y_prob >= threshold).astype(int)
        out["thresholds"][str(threshold)] = {
            "n_signals": int(pred.sum()),
            "precision": round(float(precision_score(y_true, pred, zero_division=0)), 4),
            "recall": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        }
    # top-decile triple-barrier expected return net of round-trip cost
    k = max(1, len(y_prob) // 10)
    top_idx = np.argsort(y_prob)[-k:]
    out["top_decile"] = {
        "n": int(k),
        "mean_realized_ret": round(float(returns[top_idx].mean()), 5),
        "mean_net_ret": round(float(returns[top_idx].mean() - ROUND_TRIP_COST), 5),
        "win_rate": round(float(y_true[top_idx].mean()), 4),
    }
    out["all_mean_net_ret"] = round(float(returns.mean() - ROUND_TRIP_COST), 5)
    return out


def permutation_pvalue(y_true: np.ndarray, y_prob: np.ndarray, *, n_perm: int = 1000) -> float:
    """P(label-permuted AUC >= observed AUC); tests AUC > 0.5 significance."""
    rng = np.random.default_rng(SEED)
    observed = roc_auc_score(y_true, y_prob)
    hits = 0
    for _ in range(n_perm):
        if roc_auc_score(rng.permutation(y_true), y_prob) >= observed:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def train_model(train: pd.DataFrame, val: pd.DataFrame) -> lgb.Booster:
    dtrain = lgb.Dataset(train[FEATURE_COLUMNS], label=train["label"])
    dval = lgb.Dataset(val[FEATURE_COLUMNS], label=val["label"], reference=dtrain)
    return lgb.train(
        LGB_PARAMS,
        dtrain,
        num_boost_round=600,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )


def train_baseline(train: pd.DataFrame) -> tuple[StandardScaler, LogisticRegression]:
    """Naive baseline: logistic regression on ma_spread_pct alone."""
    scaler = StandardScaler()
    x = scaler.fit_transform(train[["ma_spread_pct"]].fillna(0))
    model = LogisticRegression()
    model.fit(x, train["label"])
    return scaler, model


def baseline_prob(scaler: StandardScaler, model: LogisticRegression, frame: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(scaler.transform(frame[["ma_spread_pct"]].fillna(0)))[:, 1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-holdout", action="store_true", help="Evaluate the frozen holdout (run once).")
    parser.add_argument("--data", type=Path, default=DATASET_PATH, help="Dataset CSV from build_dataset.")
    parser.add_argument("--tag", default="p2b", help="Output file prefix, e.g. p2b_v2_strict.")
    parser.add_argument("--bar", choices=BAR_CHOICES, default=DEFAULT_BAR)
    parser.add_argument("--horizon-bars", type=int, default=DEFAULT_HORIZON_BARS)
    args = parser.parse_args()

    train, val, holdout = load_splits(args.data, horizon_bars=args.horizon_bars, bar=args.bar)
    model = train_model(train, val)
    scaler, base = train_baseline(train)

    val_prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    results = {
        "dataset": str(args.data),
        "bar": args.bar,
        "horizon_bars": args.horizon_bars,
        "purge_window": str(purge_window(args.horizon_bars, args.bar)),
        "holdout_start": str(HOLDOUT_START),
        "splits": {
            "train": {"n": len(train), "range": [str(train["signal_time"].min()), str(train["signal_time"].max())]},
            "val": {"n": len(val), "range": [str(val["signal_time"].min()), str(val["signal_time"].max())]},
            "holdout": {"n": len(holdout), "range": [str(holdout["signal_time"].min()), str(holdout["signal_time"].max())]},
        },
        "best_iteration": model.best_iteration,
        "val": evaluate(val["label"].to_numpy(), val_prob, val["realized_ret"].to_numpy()),
        "val_permutation_p": permutation_pvalue(val["label"].to_numpy(), val_prob),
        "val_baseline_ma_spread_logreg": evaluate(
            val["label"].to_numpy(), baseline_prob(scaler, base, val), val["realized_ret"].to_numpy()
        ),
    }

    importance = pd.DataFrame({
        "feature": FEATURE_COLUMNS,
        "gain": model.feature_importance(importance_type="gain"),
        "split": model.feature_importance(importance_type="split"),
    }).sort_values("gain", ascending=False).reset_index(drop=True)
    results["feature_importance_top10"] = importance.head(10)[["feature", "gain"]].to_dict("records")

    if args.eval_holdout:
        hold_prob = model.predict(holdout[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        results["holdout"] = evaluate(holdout["label"].to_numpy(), hold_prob, holdout["realized_ret"].to_numpy())
        results["holdout_permutation_p"] = permutation_pvalue(holdout["label"].to_numpy(), hold_prob)
        results["holdout_baseline_ma_spread_logreg"] = evaluate(
            holdout["label"].to_numpy(), baseline_prob(scaler, base, holdout), holdout["realized_ret"].to_numpy()
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    importance.to_csv(OUTPUT_DIR / f"{args.tag}_feature_importance.csv", index=False)
    (OUTPUT_DIR / f"{args.tag}_metrics.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
