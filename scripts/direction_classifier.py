"""Layer-2 as DIRECTION prediction: up-break vs down-break of the dense spring.

Owner insight (2026-07-23): layer 1 only screens (compression); the alpha is in
direction (oracle PF 2.68 if you pick the side). Prior "judgment" predicted
realized_ret (magnitude, long-baked-in). This instead predicts the SIDE:

  candidate : dense-rule tips (causal), <2026-05-04
  label     : 1 if LONG TP3/SL1 net > SHORT TP3/SL1 net (up was the right side)
  features  : 28 judgment + broad + BTC-direction + relative strength
              (DIRECTIONAL features -- momentum/trend/BTC, not just spread)
  model     : LightGBM binary -> P(long is the better side)
  trade     : p>=0.5+m -> long ; p<=0.5-m -> short ; else skip. TP3/SL1.
  judge     : WALK-FORWARD PF of the direction-picked trades vs always-long
              (0.77) / always-short (0.98) / oracle ceiling (2.68).

If walk-forward PF clears 1.3 robustly -> direction IS predictable, the two-
layer works with layer2=direction. If ~1.0 -> direction ~ random, oracle
unreachable, thin persists.
"""
from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.costs import FORWARD_COST  # noqa: E402
from src.data.loader import iter_series, list_series, load_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from scripts.broad_features import add_broad_features  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
FAST_MAX, FULL_MAX, MIN_DENSE, MIN_GAP = 0.0028, 0.0055, 5, 18
HORIZ, TP_A, SL_B = 72, 3.0, 1.0


def _tpsl(entry, atr, hi, lo, cl, direction):
    if direction > 0:
        up, dn = entry + TP_A * atr, entry - SL_B * atr
        ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
        dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
        g = (TP_A * atr / entry) if ut <= dt and ut < 10**9 else \
            (-SL_B * atr / entry) if dt < 10**9 else (cl[-1] / entry - 1)
    else:
        dn, up = entry - TP_A * atr, entry + SL_B * atr
        dt = np.argmax(lo <= dn) if (lo <= dn).any() else 10**9
        ut = np.argmax(hi >= up) if (hi >= up).any() else 10**9
        g = (TP_A * atr / entry) if dt <= ut and dt < 10**9 else \
            (-SL_B * atr / entry) if ut < 10**9 else (entry / cl[-1] - 1)
    return g - FORWARD_COST


def build_btc():
    try:
        btc = load_series(list_series(bar="15m")[("okx", "BTC_USDT_SWAP")])
    except Exception:
        return {}
    c = btc["close"].astype(float)
    d = {"btc_ret12": c.pct_change(12), "btc_ret48": c.pct_change(48),
         "btc_ret96": c.pct_change(96), "btc_above": (c > c.ewm(span=200, adjust=False).mean()).astype(float)}
    t = pd.to_datetime(btc["open_time"], utc=True).astype(str)
    out = {}
    for i, tt in enumerate(t):
        out[tt] = {k: (float(v.iloc[i]) if np.isfinite(v.iloc[i]) else 0.0) for k, v in d.items()}
    return out


def main() -> int:
    btc = build_btc()
    rows = []
    for src, sym, frame in iter_series(bar="15m", min_bars=500):
        if src != "okx" or not sym.endswith("_USDT_SWAP") or is_stockish(sym) or is_eval_symbol(sym):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < 500:
            continue
        ema = add_mas(frame); ind = add_indicators(frame); feat = add_features(ind)
        broad = add_broad_features(frame)
        fast = pd.to_numeric(ema["fast_spread"], errors="coerce").to_numpy()
        full = pd.to_numeric(ema["full_spread"], errors="coerce").to_numpy()
        dense = (fast <= FAST_MAX) & (full <= FULL_MAX)
        t = pd.to_datetime(ind["open_time"], utc=True)
        run = 0; last_sig = -10**9; tips = []
        for i in range(210, len(frame) - HORIZ - 2):
            run = run + 1 if dense[i] else 0
            if run == MIN_DENSE and i - last_sig >= MIN_GAP:
                tips.append(i); last_sig = i
        if not tips:
            continue
        fr = extract_feature_rows(feat, tips)
        for pos, i in enumerate(tips):
            entry_i = i + 1
            atr = float(ind["atr14"].iloc[i]); atr_pct = float(ind["atr_pct"].iloc[i])
            if not np.isfinite(atr) or atr <= 0 or not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
                continue
            entry = float(ind["open"].iloc[entry_i])
            if entry <= 0:
                continue
            last = min(entry_i + HORIZ - 1, len(ind) - 1)
            hi = ind["high"].to_numpy()[entry_i:last + 1]
            lo = ind["low"].to_numpy()[entry_i:last + 1]
            cl = ind["close"].to_numpy()[entry_i:last + 1]
            if len(cl) < 8:
                continue
            Lnet = _tpsl(entry, atr, hi, lo, cl, +1)
            Snet = _tpsl(entry, atr, hi, lo, cl, -1)
            row = {c: float(fr.iloc[pos][c]) for c in FEATURE_COLUMNS}
            br = broad.iloc[i]
            for c in broad.columns:
                row[c] = float(br[c]) if np.isfinite(br[c]) else 0.0
            row.update(btc.get(str(t.iloc[i]), {"btc_ret12": 0, "btc_ret48": 0, "btc_ret96": 0, "btc_above": 0}))
            row["rel12"] = row.get("ret_12", 0.0) - row.get("btc_ret12", 0.0)
            row["long_net"] = Lnet; row["short_net"] = Snet
            row["y_long"] = int(Lnet > Snet)
            row["signal_time"] = str(t.iloc[i])
            rows.append(row)

    df = pd.DataFrame(rows).sort_values("signal_time").reset_index(drop=True)
    FEAT = [c for c in df.columns if c not in
            {"long_net", "short_net", "y_long", "signal_time"} and pd.api.types.is_numeric_dtype(df[c])]
    for c in FEAT:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    print(f"signals={len(df)} features={len(FEAT)} up-better rate={df['y_long'].mean():.3f}")

    def pf(x):
        x = np.asarray(x); w, l = x[x > 0].sum(), x[x < 0].sum()
        return round(float(w / -l), 3) if l < 0 else None

    def traded(te, m=0.0):
        p = te["p"].to_numpy()
        net = np.where(p >= 0.5, te["long_net"].to_numpy(), te["short_net"].to_numpy())
        mask = np.abs(p - 0.5) >= m
        net = net[mask]
        return {"n": int(mask.sum()), "PF": pf(net),
                "mean_bps": round(float(net.mean()) * 1e4, 1) if len(net) else None,
                "win": round(float((net > 0).mean()), 3) if len(net) else None}

    P = {"objective": "binary", "num_leaves": 31, "learning_rate": 0.03,
         "min_data_in_leaf": 50, "feature_fraction": 0.8, "bagging_fraction": 0.8,
         "bagging_freq": 5, "verbose": -1}
    n = len(df); wf = []
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tr, te = df.iloc[int(n * a):int(n * b)].copy(), df.iloc[int(n * b):int(n * c)].copy()
        bo = lgb.train(P, lgb.Dataset(tr[FEAT], label=tr["y_long"]), num_boost_round=300)
        te["p"] = bo.predict(te[FEAT])
        from sklearn.metrics import roc_auc_score
        auc = float(roc_auc_score(te["y_long"], te["p"])) if te["y_long"].nunique() > 1 else float("nan")
        wf.append({"test_start": te["signal_time"].iloc[0][:10], "dir_AUC": round(auc, 4),
                   "trade_all_dir": traded(te, 0.0), "trade_conviction_0.1": traded(te, 0.1),
                   "always_long_PF": pf(te["long_net"]), "always_short_PF": pf(te["short_net"])})
    out = {"exit": "TP3/SL1", "signals": len(df), "walk_forward": wf}
    (PROJECT / "analysis" / "output" / "direction_classifier.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
