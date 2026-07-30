"""H9 feature-version retrain: add completed-1h above-EMA120 flag on val only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.backtest.maker_val_sim import maker_cost_for_dataset
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model
from src.judgment.trend_filter import add_h9_flags

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT = PROJECT_DIR / "analysis" / "output" / "h9_feature_retrain.json"
H9_FEATURE = "h1_above_ma"


def _with_h9_feature(frame: pd.DataFrame) -> pd.DataFrame:
    out = add_h9_flags(frame)
    out[H9_FEATURE] = out[H9_FEATURE].astype(int)
    return out


def _model_metrics(name: str, model, val: pd.DataFrame, feature_columns: list[str], maker_cost: float) -> dict:
    prob = model.predict(val[feature_columns], num_iteration=model.best_iteration)
    metrics = evaluate(val["label"].to_numpy(), prob, val["realized_ret"].to_numpy())
    top = metrics["top_decile"]
    return {
        "name": name,
        "best_iteration": int(model.best_iteration),
        "val_auc": metrics["roc_auc"],
        "perm_p": round(permutation_pvalue(val["label"].to_numpy(), prob), 4),
        "top_gross": top["mean_realized_ret"],
        "top_net_maker": round(float(top["mean_realized_ret"] - maker_cost), 5),
        "top_win_rate": top["win_rate"],
    }


def _importance_frame(model, feature_columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "feature": feature_columns,
        "gain": model.feature_importance(importance_type="gain"),
        "split": model.feature_importance(importance_type="split"),
    }).sort_values("gain", ascending=False).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_FROZEN_CONFIG.dataset_path)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--horizon-bars", type=int, default=72)
    args = parser.parse_args()

    train, val, _ = load_splits(args.data, horizon_bars=args.horizon_bars)
    base_model = train_model(train, val)
    train_h9 = _with_h9_feature(train)
    val_h9 = _with_h9_feature(val)
    feature_columns = [*FEATURE_COLUMNS, H9_FEATURE]
    h9_model = train_model(train_h9, val_h9, feature_columns=feature_columns)
    maker_cost = maker_cost_for_dataset(args.data)
    importance = _importance_frame(h9_model, feature_columns)
    h9_importance = importance[importance["feature"] == H9_FEATURE].iloc[0]

    results = {
        "dataset": str(args.data),
        "horizon_bars": args.horizon_bars,
        "maker_cost": maker_cost,
        "h9_feature": H9_FEATURE,
        "feature_coverage": round(float(val_h9["h1_ok"].mean()), 4),
        "feature_pass_rate": round(float(val_h9[H9_FEATURE].mean()), 4),
        "baseline": _model_metrics("baseline", base_model, val, FEATURE_COLUMNS, maker_cost),
        "h9_feature_model": _model_metrics("h9_feature_model", h9_model, val_h9, feature_columns, maker_cost),
        "h9_feature_importance": {
            "rank_by_gain": int(h9_importance.name) + 1,
            "gain": float(h9_importance["gain"]),
            "split": int(h9_importance["split"]),
        },
        "h9_feature_importance_top12": importance.head(12)[["feature", "gain", "split"]].to_dict("records"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
