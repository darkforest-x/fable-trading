"""Multi-timeframe first pass (H7/H8 discovery): the frozen expanded rules +
TP5/SL2 labels, run per timeframe on the data fetched overnight.

Caveats (documented, accepted for a discovery pass):
- feature windows are BAR-based, so their wall-clock meaning shifts per TF;
- purge width is passed as equivalent-15m bars so the wall-clock purge stays
  correct (load_splits hardcodes 15m minutes).

Val only. Output: analysis/output/mtf_first_pass.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import add_indicators, scan_candidates
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate
from src.judgment.train import evaluate, load_splits, permutation_pvalue, train_model

PROJECT_DIR = Path(__file__).resolve().parents[1]
OUT = PROJECT_DIR / "analysis" / "output" / "mtf_first_pass.json"
SWEEP_DIR = PROJECT_DIR / "data" / "sweep_mtf"
MAKER_COST = 0.0016

# (tag, bar, bar_minutes, label_horizon_bars)
CONFIGS = [
    ("1H_h24", "1H", 60, 24),
    ("1H_h36", "1H", 60, 36),
    ("30m_h48", "30m", 30, 48),
    ("30m_h72", "30m", 30, 72),
    ("5m_h216", "5m", 5, 216),
]


def run_config(tag: str, bar: str, bar_min: int, horizon: int) -> dict | None:
    rows = []
    n_series = 0
    for source, symbol, frame in iter_series(bar=bar, min_bars=500):
        n_series += 1
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=horizon, mode="expanded")
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        for row_pos, signal_i in enumerate(signal_indices):
            o = label_candidate(enriched, signal_i, tp_mult=5.0, sl_mult=2.0, horizon=horizon)
            if o is None:
                continue
            rows.append({
                "source": source, "symbol": symbol,
                "signal_time": enriched["open_time"].iloc[signal_i],
                "label": o.label, "outcome": o.outcome, "exit_offset": o.exit_offset,
                "entry_price": o.entry_price, "realized_ret": o.realized_ret,
                **feature_rows.iloc[row_pos].to_dict(),
            })
    if len(rows) < 400:
        return {"config": tag, "n_series": n_series, "n": len(rows), "note": "pool too small"}
    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    path = SWEEP_DIR / f"{tag}.csv"
    df.to_csv(path, index=False)
    # purge wall-clock = (horizon+1) TF-bars, expressed in equivalent 15m bars
    purge_equiv = math.ceil((horizon + 1) * bar_min / 15)
    train, val, _ = load_splits(path, horizon_bars=purge_equiv)  # holdout untouched
    if len(val) < 100:
        return {"config": tag, "n_series": n_series, "n": len(df), "note": "val too small"}
    model = train_model(train, val)
    prob = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    y, rets = val["label"].to_numpy(), val["realized_ret"].to_numpy()
    m = evaluate(y, prob, rets)
    return {
        "config": tag, "n_series": n_series, "n": int(len(df)), "n_val": int(len(val)),
        "val_auc": m["roc_auc"], "perm_p": round(permutation_pvalue(y, prob), 4),
        "top_gross": m["top_decile"]["mean_realized_ret"],
        "top_net_maker": round(m["top_decile"]["mean_realized_ret"] - MAKER_COST, 5),
        "top_win": m["top_decile"]["win_rate"],
    }


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for cfg in CONFIGS:
        r = run_config(*cfg)
        if r:
            results.append(r)
            print(r, flush=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
