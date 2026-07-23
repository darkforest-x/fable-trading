#!/usr/bin/env python3
"""Dump v16 detector candidates with judgment features + forward outcome.

Same-source judgment rebuild (owner 2026-07-23): the judgment layer must be
trained on the SAME detector's candidate distribution it will score at
inference. v11 judgment was trained on v11-hindsight candidates -> anti-selects
v16's tip candidates. Fix = retrain judgment on v16's OWN candidates.

This runs v16 detection bar-by-bar (causal, tip view) over history, and for
each fire records the 28 judgment features (add_features) + the TP5/SL2/72bar
forward net return (maker cost) = the training label. Output CSV feeds
train_samesource_judgment.py. <2026-05-04 only (holdout untouched).

Usage (on the RTX 3060, CUDA):
  set PYTHONPATH=C:\\fable
  python scripts/dump_v16_candidates.py --weights models/owner_v16_tipuni_cold.pt \\
      --n-symbols 30 --end 2026-05-03 --device 0 --out data/v16_candidates.csv
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]

from src.costs import FORWARD_COST  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS, add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, TIP_EDGE_BARS, WINDOW, load_yolo_model, right_edge_to_bar,
)

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT = 5.0, 2.0
PREDICT_BATCH = 16


def forward_net(enriched, i):
    entry_i = i + 1
    if entry_i >= len(enriched):
        return None
    atr = float(enriched["atr14"].iloc[i]); atr_pct = float(enriched["atr_pct"].iloc[i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
        return None
    last_i = min(entry_i + HORIZON_BARS - 1, len(enriched) - 1)
    if last_i < entry_i:
        return None
    highs = enriched["high"].to_numpy()[entry_i:last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i:last_i + 1]
    upper, lower = entry + TP_MULT * atr, entry - SL_MULT * atr
    up = int(np.argmax(highs >= upper)) if (highs >= upper).any() else len(highs)
    dn = int(np.argmax(lows <= lower)) if (lows <= lower).any() else len(highs)
    if up < dn:
        gross = upper / entry - 1
    elif dn < up:
        gross = lower / entry - 1
    elif (lows <= lower).any():
        gross = lower / entry - 1
    elif last_i - entry_i + 1 >= HORIZON_BARS:
        gross = float(enriched["close"].iloc[last_i]) / entry - 1
    else:
        return None
    return gross - FORWARD_COST


def v16_fire_bars(enriched_ma, model, device, lo, hi):
    """Bars where v16 fires (A' edge) scanning tip windows lo..hi, MIN_GAP-deduped."""
    fires = []
    tmp = PROJECT / "data" / "_dumpcand_tmp.png"
    last_sig = -10**9
    for t in range(lo, hi):
        sub = enriched_ma.iloc[t - WINDOW + 1:t + 1].reset_index(drop=True)
        try:
            _, tf = render_chart(sub, out_path=tmp)
            res = model.predict(str(tmp), conf=DEFAULT_CONF, verbose=False, device=device)
        except Exception:
            continue
        r0 = res[0] if res else None
        if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
            continue
        fired = False
        for box in r0.boxes.xywhn.cpu().numpy():
            cx, _, w, _ = map(float, box[:4])
            if right_edge_to_bar(cx, w, tf, n_bars=WINDOW) >= WINDOW - TIP_EDGE_BARS:
                fired = True
                break
        if fired and t - last_sig >= MIN_GAP_BARS:
            fires.append(t)
            last_sig = t
    return fires


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--n-symbols", type=int, default=30)
    ap.add_argument("--end", default="2026-05-03")
    ap.add_argument("--device", default=None)
    ap.add_argument("--out", default="data/v16_candidates.csv")
    args = ap.parse_args()
    end = min(pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1), HOLDOUT_START)

    model = load_yolo_model(args.weights)
    device = args.device
    if device is None:
        import torch
        device = "0" if torch.cuda.is_available() else "cpu"

    pool = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=WINDOW + 400):
        if source != "okx" or not symbol.endswith("_USDT_SWAP") or is_stockish(symbol) or is_eval_symbol(symbol):
            continue
        pool.append((symbol, frame))
    random.seed(20260723)
    chosen = random.sample(pool, min(args.n_symbols, len(pool)))

    rows = []
    for k, (symbol, frame) in enumerate(chosen, 1):
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < end].reset_index(drop=True)
        if len(frame) < WINDOW + 200:
            continue
        enriched = add_indicators(add_mas(frame))
        featured = add_features(enriched)
        ema = add_mas(frame)
        fires = v16_fire_bars(ema, model, device, WINDOW, len(frame) - HORIZON_BARS - 2)
        if not fires:
            print(f"[{k}/{len(chosen)}] {symbol}: fires=0", flush=True)
            continue
        feats = extract_feature_rows(featured, fires)
        n_ok = 0
        for pos, i in enumerate(fires):
            net = forward_net(enriched, i)
            if net is None:
                continue
            row = {c: float(feats.iloc[pos][c]) for c in FEATURE_COLUMNS}
            row["symbol"] = symbol
            row["signal_time"] = str(pd.to_datetime(enriched["open_time"], utc=True).iloc[i])
            row["net"] = round(float(net), 6)
            rows.append(row)
            n_ok += 1
        print(f"[{k}/{len(chosen)}] {symbol}: fires={len(fires)} rows={n_ok}", flush=True)

    df = pd.DataFrame(rows)
    out = PROJECT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nwrote {len(df)} candidate rows -> {out}")
    if len(df):
        print(f"base rate of v16 candidates: win={float((df['net']>0).mean()):.4f} mean_net={float(df['net'].mean()):.5f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
