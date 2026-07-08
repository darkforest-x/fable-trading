"""Exit-structure sweep (v3 exploration, owner-requested 2026-07-08).

One pass over the expanded pool: candidates + features are computed once,
then every candidate is labeled under each exit config; each config gets its
own dataset CSV and a fresh LightGBM train/val evaluation via the unchanged
src.judgment.train pipeline.

Discipline: selection happens on VAL ONLY -- this script never touches the
holdout (train.load_splits drops it, --eval-holdout equivalent is not called).
Horizon stays 72 bars for every config so train.py's PURGE_WINDOW (73 bars)
remains valid; horizon exploration would need the purge parametrized first.

Usage: python3 -m src.judgment.barrier_sweep
Output: data/sweep_v3/*.csv, analysis/output/p2b_v3_sweep.json + stdout table.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.build_dataset import _dedupe_cross_source
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import HORIZON_BARS, label_candidate, label_candidate_trailing
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

PROJECT_DIR = Path(__file__).resolve().parents[2]
SWEEP_DIR = PROJECT_DIR / "data" / "sweep_v3"
OUTPUT_JSON = PROJECT_DIR / "analysis" / "output" / "p2b_v3_sweep.json"
MIN_BARS = 500

CONFIGS: dict[str, dict] = {
    "tp4_sl2": {"tp": 4.0, "sl": 2.0},   # v2 baseline, re-run for identical footing
    "tp5_sl2": {"tp": 5.0, "sl": 2.0},
    "tp6_sl2": {"tp": 6.0, "sl": 2.0},
    "tp6_sl3": {"tp": 6.0, "sl": 3.0},
    "tp8_sl2": {"tp": 8.0, "sl": 2.0},
    "trail2": {"trail": 2.0},            # trend exit: 2xATR trailing stop
    "trail3": {"trail": 3.0},
}


def _label(enriched: pd.DataFrame, signal_i: int, cfg: dict):
    if "trail" in cfg:
        return label_candidate_trailing(enriched, signal_i, trail_mult=cfg["trail"])
    return label_candidate(enriched, signal_i, tp_mult=cfg["tp"], sl_mult=cfg["sl"])


def build_all() -> dict[str, pd.DataFrame]:
    records: dict[str, list[dict]] = {name: [] for name in CONFIGS}
    for source, symbol, frame in iter_series(bar="15m", min_bars=MIN_BARS):
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=HORIZON_BARS, mode="expanded")
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        for row_pos, signal_i in enumerate(signal_indices):
            base = {
                "source": source, "symbol": symbol, "signal_i": signal_i,
                "signal_time": enriched["open_time"].iloc[signal_i],
            }
            feats = feature_rows.iloc[row_pos].to_dict()
            for name, cfg in CONFIGS.items():
                outcome = _label(enriched, signal_i, cfg)
                if outcome is None:
                    continue
                records[name].append({
                    **base, "label": outcome.label, "outcome": outcome.outcome,
                    "exit_offset": outcome.exit_offset, "entry_price": outcome.entry_price,
                    "realized_ret": outcome.realized_ret, **feats,
                })
    out = {}
    for name, rows in records.items():
        df = pd.DataFrame(rows)
        # same okx/gate cross-source dedupe as the official build_dataset path
        df = _dedupe_cross_source(df).sort_values("signal_time").reset_index(drop=True)
        out[name] = df
    return out


def eval_config(name: str, csv_path: Path) -> dict:
    train, val, _holdout = load_splits(csv_path)  # holdout intentionally unused
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y = val["label"].to_numpy()
    rets = val["realized_ret"].to_numpy()
    m = evaluate(y, prob, rets)
    top = m["top_decile"]
    return {
        "config": name,
        "n_train": int(len(train)), "n_val": int(len(val)),
        "positive_rate_val": m["positive_rate"],
        "val_auc": m["roc_auc"],
        "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": top["mean_realized_ret"],
        "top_net_02": round(top["mean_realized_ret"] - 0.002, 5),
        "top_net_03": round(top["mean_realized_ret"] - 0.003, 5),
        "top_win_rate": top["win_rate"],
        "mean_exit_bars": round(float(val["exit_offset"].mean()), 1),
        "timeout_share": round(float((val["outcome"] == "timeout").mean()), 3),
    }


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    print("building all label variants in one scan...", flush=True)
    datasets = build_all()
    results = []
    for name, df in datasets.items():
        path = SWEEP_DIR / f"judgment_v3_{name}.csv"
        df.to_csv(path, index=False)
        print(f"{name}: {len(df)} candidates, pos_rate {df['label'].mean():.3f} -> training", flush=True)
        results.append(eval_config(name, path))
    OUTPUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ["config", "val_auc", "perm_p", "top_gross", "top_net_02", "top_net_03",
            "top_win_rate", "positive_rate_val", "mean_exit_bars", "timeout_share"]
    print(pd.DataFrame(results)[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
