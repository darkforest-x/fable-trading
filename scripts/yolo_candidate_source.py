"""YOLO-as-candidate-generator: build a judgment dataset whose candidate
signal bars come from the DETECTOR's boxes instead of the rule scan.

The A/B fairness contract: everything downstream (labeling, features,
train/val split, backtest, costs) is byte-identical to the rule-scan path.
ONLY the candidate source differs. This lets YOLO earn the critical path by
beating the rules head-to-head, without touching the validated rule path.

box right-edge pixel -> bar index (inverse of ChartTransform.x_at) ->
absolute signal_i = window_start + bar_index. Dedupe within MIN_GAP bars.

Usage: PYTHONPATH=. .venv/bin/python scripts/yolo_candidate_source.py \
    --weights models/owner_best.pt --out data/judgment_yolo_swap.csv
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
import sys; sys.path.insert(0, str(PROJECT_DIR))
from src.data.loader import iter_series
from src.data.universe import is_stockish
from src.detection.data import add_mas
from src.detection.render import render_chart, make_chart_transform
from src.judgment.candidates import add_indicators
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.labeling import label_candidate, HORIZON_BARS

WINDOW = 200
STRIDE = 50          # denser than dataset build so live clusters aren't missed
CONF = 0.30
MIN_GAP = 18         # same as rule scan's per-series min gap
TMP = PROJECT_DIR / "data/_yolo_cand_tmp.png"


def right_edge_to_bar(cx: float, w: float, tf) -> int:
    """Normalized box right edge -> bar index within the window."""
    right_px = (cx + w / 2) * tf.width          # xywhn -> pixel
    if tf.plot_w <= 0:
        return WINDOW - 1
    idx = round((right_px - tf.left) / tf.plot_w * (tf.n_bars - 1))
    return int(min(max(idx, 0), tf.n_bars - 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="models/owner_best.pt")
    ap.add_argument("--out", type=Path, default=PROJECT_DIR / "data/judgment_yolo_swap.csv")
    args = ap.parse_args()
    from ultralytics import YOLO
    model = YOLO(args.weights)

    records = []
    n_series = 0
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol):
            continue
        n_series += 1
        enriched_ind = add_indicators(frame)     # judgment-layer indicators (8-55 EMAs, atr...)
        enriched_ma = add_mas(frame)             # detection-layer MAs (20/60/120) for rendering
        featured = add_features(enriched_ind)
        chosen = []
        for start in range(288, len(frame) - WINDOW, STRIDE):
            sub = enriched_ma.iloc[start:start + WINDOW]
            _, tf = render_chart(sub, out_path=TMP)
            res = model.predict(str(TMP), conf=CONF, verbose=False)[0]
            if res.boxes is None:
                continue
            for b in res.boxes.xywhn.cpu().numpy():
                cx, _, w, _ = map(float, b[:4])
                bar_in_win = right_edge_to_bar(cx, w, tf)
                signal_i = start + bar_in_win
                if signal_i < 288 or signal_i >= len(frame) - HORIZON_BARS - 1:
                    continue
                chosen.append(signal_i)
        # dedupe across overlapping windows
        chosen = sorted(set(chosen))
        deduped = []
        for si in chosen:
            if not deduped or si - deduped[-1] >= MIN_GAP:
                deduped.append(si)
        if not deduped:
            continue
        feat_rows = extract_feature_rows(featured, deduped)
        for pos, signal_i in enumerate(deduped):
            o = label_candidate(enriched_ind, signal_i, tp_mult=5.0, sl_mult=2.0)
            if o is None:
                continue
            rec = {"source": source, "symbol": symbol, "signal_i": signal_i,
                   "signal_time": enriched_ind["open_time"].iloc[signal_i],
                   "label": o.label, "outcome": o.outcome, "exit_offset": o.exit_offset,
                   "entry_price": o.entry_price, "realized_ret": o.realized_ret}
            rec.update(feat_rows.iloc[pos].to_dict())
            records.append(rec)
        print(f"  {symbol}: {len(deduped)} YOLO候选", flush=True)
    df = pd.DataFrame(records).sort_values("signal_time").reset_index(drop=True)
    df.to_csv(args.out, index=False)
    print(json.dumps({"series": n_series, "candidates": len(df),
                      "symbols": int(df['symbol'].nunique()) if len(df) else 0,
                      "pos_rate": round(float(df['label'].mean()), 4) if len(df) else None,
                      "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
