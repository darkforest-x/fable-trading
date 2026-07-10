"""Val-only short-side replication for the OKX perpetual-swap universe.

H10 tests one variable: mirror the validated expanded-pool TP5/SL2 setup from
long to short on *_USDT_SWAP series. Candidate thresholds and costs are held
constant, and holdout remains unused.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_short_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_short_candidate
from src.judgment.train import baseline_prob, evaluate, load_splits, permutation_pvalue, train_baseline, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_DIR / "data" / "short_replication"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "short_replication.json"
COSTS = {"taker_010": 0.0010, "maker_006": 0.0006}
CONFIG = {"name": "swap_short_tp5_sl2", "tp": 5.0, "sl": 2.0}


def _as_short_features(
    feature_rows: pd.DataFrame, featured: pd.DataFrame, signal_indices: list[int]
) -> pd.DataFrame:
    aligned = feature_rows.copy()
    source = featured.iloc[signal_indices].reset_index(drop=True)
    close = source["close"].replace(0, np.nan)
    aligned["ext_up"] = source["ext_down"]
    aligned["close_vs_ema60"] = source["ema60"] / close - 1
    aligned["close_vs_ema120"] = source["ema120"] / close - 1
    aligned["order_score"] = source["down_order_score"]
    aligned["slow_slope_12"] = -source["slow_slope_12"]
    aligned["drawdown24"] = source["runup24"]
    for bars in (4, 12, 24, 48):
        aligned[f"ret_{bars}"] = -source[f"ret_{bars}"]
    return aligned.replace([np.inf, -np.inf], np.nan)


def build() -> pd.DataFrame:
    records: list[dict] = []
    n_series = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        n_series += 1
        enriched = add_indicators(frame)
        signal_indices = scan_short_candidates(enriched, horizon_bars=72, mode="expanded")
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = _as_short_features(extract_feature_rows(featured, signal_indices), featured, signal_indices)
        opens = enriched["open"].to_numpy()
        highs = enriched["high"].to_numpy()
        for row_pos, signal_i in enumerate(signal_indices):
            entry_i = signal_i + 1
            maker_filled = bool(entry_i < len(highs) and highs[entry_i] > opens[entry_i])
            outcome = label_short_candidate(enriched, signal_i, tp_mult=CONFIG["tp"], sl_mult=CONFIG["sl"])
            if outcome is None:
                continue
            records.append({
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
                **feature_rows.iloc[row_pos].to_dict(),
            })
    print(f"swap series scanned: {n_series}")
    return pd.DataFrame(records).sort_values("signal_time").reset_index(drop=True)


def _split_summary(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"n": 0, "range": [None, None], "positive_rate": None}
    return {
        "n": int(len(frame)),
        "range": [str(frame["signal_time"].min()), str(frame["signal_time"].max())],
        "positive_rate": round(float(frame["label"].mean()), 4),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    df = build()
    path = OUT_DIR / f"{CONFIG['name']}.csv"
    df.to_csv(path, index=False)

    train, val, _ = load_splits(path, horizon_bars=72)
    model = train_model(train, val)
    scaler, base = train_baseline(train)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    metrics = evaluate(y, prob, rets)
    baseline_metrics = evaluate(y, baseline_prob(scaler, base, val), rets)
    k = max(1, len(prob) // 10)
    top_idx = np.argsort(prob)[-k:]
    filled = val["maker_filled"].to_numpy()[top_idx]
    top_rets = rets[top_idx]
    importance = pd.DataFrame(
        {
            "feature": FEATURE_COLUMNS,
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False).reset_index(drop=True)

    result = {
        "config": CONFIG,
        "dataset": str(path),
        "n_candidates": int(len(df)),
        "splits": {
            "train": _split_summary(train),
            "val": _split_summary(val),
        },
        "best_iteration": model.best_iteration,
        "val": metrics,
        "val_permutation_p": round(permutation_pvalue(y, prob), 4),
        "val_baseline_ma_spread_logreg": baseline_metrics,
        "maker_fill_rate_top_decile": round(float(filled.mean()), 3),
        "top_net_taker_010": round(float(top_rets.mean()) - COSTS["taker_010"], 5),
        "top_net_maker_006": round(float(top_rets.mean()) - COSTS["maker_006"], 5),
        "baseline_top_net_taker_010": round(
            baseline_metrics["top_decile"]["mean_realized_ret"] - COSTS["taker_010"], 5
        ),
        "baseline_top_net_maker_006": round(
            baseline_metrics["top_decile"]["mean_realized_ret"] - COSTS["maker_006"], 5
        ),
        "feature_importance_top10": importance.head(10)[["feature", "gain"]].to_dict("records"),
    }
    if filled.any():
        result["top_net_maker_filled_only"] = round(float(top_rets[filled].mean()) - COSTS["maker_006"], 5)

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
