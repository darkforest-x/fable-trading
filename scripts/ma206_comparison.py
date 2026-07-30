"""Rebuild and validate the canonical SMA/EMA 20/60/120 SWAP candidate pool."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates_v206 import (
    add_features,
    add_indicators,
    extract_feature_rows,
    scan_candidates,
)
from src.judgment.features import FEATURE_COLUMNS
from src.judgment.labeling import HORIZON_BARS, label_candidate
from src.judgment.train import (
    evaluate,
    load_splits,
    permutation_pvalue,
    train_baseline,
    train_model,
    baseline_prob,
)

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT_DATASET = PROJECT_DIR / "data" / "ma206" / "swap_tp5_sl2_ma206.csv"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "p2b_ma206_validation.json"
MAKER_COST = 0.0006
MIN_BARS = 500


def build_v206_dataset() -> pd.DataFrame:
    records: list[dict] = []
    series_count = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=MIN_BARS):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        series_count += 1
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=HORIZON_BARS)
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        opens = enriched["open"].to_numpy()
        lows = enriched["low"].to_numpy()
        for row_pos, signal_i in enumerate(signal_indices):
            outcome = label_candidate(enriched, signal_i, tp_mult=5.0, sl_mult=2.0)
            if outcome is None:
                continue
            entry_i = signal_i + 1
            maker_filled = bool(entry_i < len(lows) and lows[entry_i] < opens[entry_i])
            record = {
                "source": source,
                "symbol": symbol,
                "signal_i": signal_i,
                "signal_time": enriched["open_time"].iloc[signal_i],
                "maker_filled": maker_filled,
                "label": outcome.label,
                "outcome": outcome.outcome,
                "exit_offset": outcome.exit_offset,
                "entry_price": outcome.entry_price,
                "realized_ret": outcome.realized_ret,
            }
            record.update(feature_rows.iloc[row_pos].to_dict())
            records.append(record)
    dataset = pd.DataFrame(records)
    if not dataset.empty:
        dataset = dataset.sort_values("signal_time").reset_index(drop=True)
    dataset.attrs["series_count"] = series_count
    return dataset


def _top_maker_net(val: pd.DataFrame, prob: np.ndarray) -> tuple[float, float]:
    k = max(1, len(prob) // 10)
    top_idx = np.argsort(prob)[-k:]
    filled = val["maker_filled"].to_numpy()[top_idx]
    returns = val["realized_ret"].to_numpy()[top_idx]
    if not filled.any():
        return (float("nan"), 0.0)
    return (round(float(returns[filled].mean() - MAKER_COST), 5), round(float(filled.mean()), 3))


def evaluate_dataset(name: str, dataset_path: Path) -> dict:
    train, val, holdout = load_splits(dataset_path, horizon_bars=HORIZON_BARS)
    model = train_model(train, val)
    scaler, base = train_baseline(train)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    base_prob = baseline_prob(scaler, base, val)
    y = val["label"].to_numpy()
    returns = val["realized_ret"].to_numpy()
    metrics = evaluate(y, prob, returns)
    baseline = evaluate(y, base_prob, returns)
    maker_net, maker_fill = _top_maker_net(val, prob)
    return {
        "name": name,
        "dataset": str(dataset_path),
        "n_candidates": int(len(pd.read_csv(dataset_path, usecols=["signal_time"]))),
        "splits": {
            "train": {"n": int(len(train)), "range": [str(train["signal_time"].min()), str(train["signal_time"].max())]},
            "val": {"n": int(len(val)), "range": [str(val["signal_time"].min()), str(val["signal_time"].max())]},
            "holdout": {"n": int(len(holdout)), "range": [str(holdout["signal_time"].min()), str(holdout["signal_time"].max())]},
        },
        "positive_rate": round(float(pd.read_csv(dataset_path, usecols=["label"])["label"].mean()), 4),
        "val_auc": metrics["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": metrics["top_decile"]["mean_realized_ret"],
        "top_net_020": metrics["top_decile"]["mean_net_ret"],
        "top_net_maker_006": maker_net,
        "maker_fill_rate": maker_fill,
        "top_win_rate": metrics["top_decile"]["win_rate"],
        "baseline_ma_spread_auc": baseline["roc_auc"],
        "baseline_ma_spread_net_020": baseline["top_decile"]["mean_net_ret"],
        "feature_importance_top10": pd.DataFrame({
            "feature": FEATURE_COLUMNS,
            "gain": model.feature_importance(importance_type="gain"),
        }).sort_values("gain", ascending=False).head(10).to_dict("records"),
    }


def main() -> int:
    OUT_DATASET.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    v206 = build_v206_dataset()
    v206.to_csv(OUT_DATASET, index=False)
    results = [evaluate_dataset("sma_ema_20_60_120_swap_tp5_sl2", OUT_DATASET)]
    payload = {
        "discipline": "val-only; holdout loaded for counts only and not evaluated",
        "label": "TP5/SL2 h72",
        "universe": "OKX USDT_SWAP 15m",
        "v206_series_scanned": v206.attrs.get("series_count"),
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
