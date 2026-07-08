"""H1/H2 exit-variant sweep (RESEARCH_AGENDA): scaled TP and breakeven shift
vs the TP5/SL2 baseline, one scan of the expanded 15m pool, val-only.

Run offline via scripts/offline_queue2.sh. Discovery tier.
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
from src.judgment.labeling import (
    label_candidate, label_candidate_breakeven, label_candidate_scaled,
)
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
SWEEP_DIR = PROJECT_DIR / "data" / "sweep_exits"
OUT_JSON = PROJECT_DIR / "analysis" / "output" / "exit_variants.json"
MAKER_COST = 0.0016

CONFIGS = {
    "tp5_sl2_base": lambda f, i: label_candidate(f, i, tp_mult=5.0, sl_mult=2.0),
    "scaled_25_t3": lambda f, i: label_candidate_scaled(f, i, tp1_mult=2.5, trail_mult=3.0),
    "breakeven_15": lambda f, i: label_candidate_breakeven(f, i, tp_mult=5.0, be_trigger=1.5),
}


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    records: dict[str, list[dict]] = {k: [] for k in CONFIGS}
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        opens = enriched["open"].to_numpy()
        lows = enriched["low"].to_numpy()
        for row_pos, signal_i in enumerate(signal_indices):
            entry_i = signal_i + 1
            maker_filled = bool(entry_i < len(lows) and lows[entry_i] < opens[entry_i])
            feats = feature_rows.iloc[row_pos].to_dict()
            for name, labeler in CONFIGS.items():
                outcome = labeler(enriched, signal_i)
                if outcome is None:
                    continue
                records[name].append({
                    "source": source, "symbol": symbol, "signal_i": signal_i,
                    "signal_time": enriched["open_time"].iloc[signal_i],
                    "maker_filled": maker_filled,
                    "label": outcome.label, "outcome": outcome.outcome,
                    "exit_offset": outcome.exit_offset, "entry_price": outcome.entry_price,
                    "realized_ret": outcome.realized_ret, **feats,
                })
    results = []
    for name, rows in records.items():
        df = _dedupe_cross_source(pd.DataFrame(rows)).sort_values("signal_time").reset_index(drop=True)
        path = SWEEP_DIR / f"{name}.csv"
        df.to_csv(path, index=False)
        train, val, _ = load_splits(path, horizon_bars=72)  # holdout unused
        model = train_model(train, val)
        prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        y, rets = val["label"].to_numpy(), val["realized_ret"].to_numpy()
        m = evaluate(y, prob, rets)
        results.append({
            "config": name, "n": int(len(df)),
            "val_auc": m["roc_auc"], "perm_p": round(permutation_pvalue(y, prob), 4),
            "top_gross": m["top_decile"]["mean_realized_ret"],
            "top_net_maker": round(m["top_decile"]["mean_realized_ret"] - MAKER_COST, 5),
            "top_win_rate": m["top_decile"]["win_rate"],
            "mean_exit_bars": round(float(val["exit_offset"].mean()), 1),
            "outcomes": val["outcome"].value_counts().to_dict(),
        })
        print(results[-1], flush=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
