"""YOLO-as-candidate-generator: build a judgment dataset whose candidate
signal bars come from the DETECTOR's boxes instead of the rule scan.

The A/B fairness contract: everything downstream (labeling, features,
train/val split, backtest, costs) is byte-identical to the rule-scan path.
ONLY the candidate source differs.

Usage: PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
    --weights models/owner_best.pt --out data/judgment_yolo_swap.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import iter_series
from src.data.universe import is_stockish
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import HORIZON_BARS, label_candidate
from src.judgment.yolo_candidates import DEFAULT_CONF, load_yolo_model, scan_series_with_yolo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="models/owner_best.pt")
    ap.add_argument("--out", type=Path, default=PROJECT_DIR / "data" / "judgment_yolo_swap.csv")
    args = ap.parse_args()
    model = load_yolo_model(args.weights)

    records = []
    n_series = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        n_series += 1
        enriched_ind = add_indicators(frame)
        featured = add_features(enriched_ind)
        deduped = [
            si
            for si in scan_series_with_yolo(frame, model, conf=DEFAULT_CONF)
            if si + 1 + HORIZON_BARS < len(frame)
        ]
        if not deduped:
            continue
        feat_rows = extract_feature_rows(featured, deduped)
        for pos, signal_i in enumerate(deduped):
            o = label_candidate(enriched_ind, signal_i, tp_mult=5.0, sl_mult=2.0)
            if o is None:
                continue
            rec = {
                "source": source,
                "symbol": symbol,
                "signal_i": signal_i,
                "signal_time": enriched_ind["open_time"].iloc[signal_i],
                "label": o.label,
                "outcome": o.outcome,
                "exit_offset": o.exit_offset,
                "entry_price": o.entry_price,
                "realized_ret": o.realized_ret,
            }
            rec.update(feat_rows.iloc[pos].to_dict())
            records.append(rec)
        print(f"  {symbol}: {len(deduped)} YOLO候选", flush=True)
    df = pd.DataFrame(records).sort_values("signal_time").reset_index(drop=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(
        json.dumps(
            {
                "series": n_series,
                "candidates": len(df),
                "symbols": int(df["symbol"].nunique()) if len(df) else 0,
                "pos_rate": round(float(df["label"].mean()), 4) if len(df) else None,
                "out": str(args.out),
                "features": list(FEATURE_COLUMNS),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
