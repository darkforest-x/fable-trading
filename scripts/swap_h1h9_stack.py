"""Stack test on the SWAP mainline: do the two discovery-tier winners (H1
scaled exit, H9 1h-EMA120 trend filter) survive and combine on the swap
universe? Val only; discovery tier.

Cells reported: {tp5, scaled} x {no filter, above_ma filter} -> top-bucket
maker-net, win rate, n. Run offline via offline_queue3.sh.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]

from src.data.loader import iter_series  # noqa: E402
from src.judgment.candidates import add_indicators, scan_candidates  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows  # noqa: E402
from src.judgment.labeling import label_candidate, label_candidate_scaled  # noqa: E402
from src.judgment.train import load_splits, train_model  # noqa: E402
from src.judgment.trend_filter import add_h9_flags  # noqa: E402

OUT = PROJECT_DIR / "analysis" / "output" / "swap_h1h9_stack.json"
SWEEP_DIR = PROJECT_DIR / "data" / "sweep_swap_stack"
MAKER_COST = 0.0016  # keep the conservative spot-maker figure; swap 0.0006 shown too
SWAP_MAKER = 0.0006

CONFIGS = {
    "tp5_sl2": lambda f, i: label_candidate(f, i, tp_mult=5.0, sl_mult=2.0),
    "scaled_25_t3": lambda f, i: label_candidate_scaled(f, i, tp1_mult=2.5, trail_mult=3.0),
}


def bucket(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"n": 0}
    return {"n": int(len(sub)),
            "net_maker016": round(float((sub["realized_ret"] - MAKER_COST).mean()), 5),
            "net_swap006": round(float((sub["realized_ret"] - SWAP_MAKER).mean()), 5),
            "win": round(float((sub["realized_ret"] > 0).mean()), 4)}


def main() -> int:
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    records: dict[str, list[dict]] = {k: [] for k in CONFIGS}
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if not symbol.endswith("_USDT_SWAP"):
            continue
        enriched = add_indicators(frame)
        signal_indices = scan_candidates(enriched, horizon_bars=72, mode="expanded")
        if not signal_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, signal_indices)
        for row_pos, signal_i in enumerate(signal_indices):
            feats = feature_rows.iloc[row_pos].to_dict()
            for name, labeler in CONFIGS.items():
                o = labeler(enriched, signal_i)
                if o is None:
                    continue
                records[name].append({
                    "source": source, "symbol": symbol,
                    "signal_time": enriched["open_time"].iloc[signal_i],
                    "label": o.label, "outcome": o.outcome, "exit_offset": o.exit_offset,
                    "entry_price": o.entry_price, "realized_ret": o.realized_ret, **feats,
                })
    results = {}
    for name, rows in records.items():
        df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
        path = SWEEP_DIR / f"{name}.csv"
        df.to_csv(path, index=False)
        train, val, _ = load_splits(path, horizon_bars=72)  # holdout untouched
        model = train_model(train, val)
        val = val.copy()
        val["score"] = model.predict(val[FEATURE_COLUMNS], num_iteration=model.best_iteration)
        thr = float(np.quantile(val["score"], 0.90))
        val = add_h9_flags(val)
        top = val[(val["score"] >= thr) & val["h1_ok"]]
        results[name] = {
            "n_pool": int(len(df)), "threshold": round(thr, 4),
            "top_all": bucket(top),
            "top_above_ma": bucket(top[top["h1_above_ma"]]),
            "top_against": bucket(top[~top["h1_above_ma"]]),
        }
        print(name, results[name], flush=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
