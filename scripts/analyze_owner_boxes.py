#!/usr/bin/env python3
"""Do the owner's 5550 manual boxes carry alpha the crude rule misses?

The central tension (2026-07-23): three audits say the signal is thin (rule
base rate 0.87), but the owner is convinced their manual "perfect dense" picks
make money. This resolves it using the owner's EXISTING labels -- no new
labeling.

Method (with the honesty trap built in):
  1. Extract each owner box's cut_global bar (box right edge, MAD-disambiguated
     window) from dense_owner_v11.
  2. Causal features at cut_global (only that bar and before) + box geometry
     + TP5/SL2/72bar forward net (maker cost).
  3. Negatives: random bars from the same symbols/era.
  4. LightGBM classifier (owner-box=1 vs random=0) on CAUSAL features -> what
     the owner's eye keys on (feature gain).
  5. DECISIVE (causal, survivorship-proof): on a held-out TEST period, score
     the RANDOM background bars, take the top decile the classifier calls
     "most owner-like", and measure THEIR forward-return base rate. If that
     is >> 0.87 -> the owner's learnable criteria causally select better
     clusters = real deployable alpha. If ~0.87 -> the owner's eye = the crude
     rule, the box-return edge was survivorship (the future they saw when
     labeling), not deployable.

Honesty trap: the classifier's AUC (box vs random) is hindsight-inflated
because the boxes were placed knowing the future -- DO NOT read AUC as alpha.
The causal base rate of classifier-selected background bars is the only judge.

<2026-05-04 only (holdout untouched).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

import lightgbm as lgb  # noqa: E402
from src.costs import FORWARD_COST  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_stem  # noqa: E402
from src.detection.render import make_chart_transform  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS  # noqa: E402
from scripts.build_crop_pad200_dataset import (  # noqa: E402
    WINDOW, boxes_cut_and_spans, parse_stem, read_boxes, resolve_series, resolve_win_start,
)
from scripts.broad_features import add_broad_features  # noqa: E402

V11 = PROJECT / "datasets" / "_deprecated_pretip" / "dense_owner_v11"
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
TP_MULT, SL_MULT = 3.0, 1.0  # tight long exit (was 5,2)
BOX_GEOM = ["box_w", "box_h", "box_cy"]
FEATS = list(FEATURE_COLUMNS) + BOX_GEOM


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
        g = upper / entry - 1
    elif dn < up:
        g = lower / entry - 1
    elif (lows <= lower).any():
        g = lower / entry - 1
    elif last_i - entry_i + 1 >= HORIZON_BARS:
        g = float(enriched["close"].iloc[last_i]) / entry - 1
    else:
        return None
    return round(g - FORWARD_COST, 6)


BROAD_COLS: list[str] = []


def feat_row(featured, broad, i):
    r = featured.iloc[i]
    row = {c: float(r[c]) for c in FEATURE_COLUMNS}  # keep the 28 too
    br = broad.iloc[i]
    for c in BROAD_COLS:
        row[c] = float(br[c]) if np.isfinite(br[c]) else 0.0
    return row


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def stats(net):
    net = np.asarray(net)
    if not len(net):
        return {"n": 0}
    return {"n": int(len(net)), "win": round(float((net > 0).mean()), 4),
            "PF": pf(net), "mean_net": round(float(net.mean()), 5)}


def main() -> int:
    rng = random.Random(20260723)
    pos, neg = [], []
    resolved_cache: dict[str, pd.DataFrame | None] = {}
    enr_cache: dict[str, tuple] = {}

    def get_frames(body):
        if body not in resolved_cache:
            df = resolve_series(body)
            resolved_cache[body] = df
        return resolved_cache[body]

    label_files = list((V11 / "labels" / "train").glob("*.txt")) + \
                  list((V11 / "labels" / "val").glob("*.txt"))
    print(f"scanning {len(label_files)} label files ...", flush=True)
    n_box = n_skip = 0
    for k, lbl in enumerate(label_files):
        stem = lbl.stem
        if is_eval_stem(stem):
            continue
        boxes = read_boxes(lbl)
        if not boxes:
            continue
        parsed = parse_stem(stem)
        if not parsed:
            continue
        body, idx = parsed
        df = get_frames(body)
        if df is None:
            n_skip += 1
            continue
        # cache enriched per symbol
        if body not in enr_cache:
            ema = add_mas(df)
            ind = add_indicators(df)
            feat = add_features(ind)
            broad = add_broad_features(df)
            global BROAD_COLS
            if not BROAD_COLS:
                BROAD_COLS = list(broad.columns)
            times = pd.to_datetime(df["open_time"], utc=True)
            enr_cache[body] = (ema, ind, feat, broad, times)
        ema, ind, feat, broad, times = enr_cache[body]
        n = len(df)
        png = V11 / "images" / ("train" if (V11 / "images/train" / f"{stem}.png").exists() else "val") / f"{stem}.png"
        stored = cv2.imread(str(png)) if png.exists() else None
        res = resolve_win_start(n, idx, enriched=ema, stored_img=stored)
        if res is None:
            n_skip += 1
            continue
        _mode, win_start, _mad = res
        if win_start < 0 or win_start + WINDOW > n:
            n_skip += 1
            continue
        sub = ema.iloc[win_start:win_start + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            n_skip += 1
            continue
        try:
            cut_local, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub))
        except Exception:
            n_skip += 1
            continue
        cut_global = win_start + cut_local
        if cut_global < 210 or cut_global >= n - 1 or times.iloc[cut_global] >= HOLDOUT_START:
            continue
        net = forward_net(ind, cut_global)
        if net is None:
            continue
        row = feat_row(feat, broad, cut_global)
        # box geometry of the rightmost span
        b0, b1, _, _ = spans[0]
        xc, yc, w, h = boxes[0]
        row.update({"box_w": float(w), "box_h": float(h), "box_cy": float(yc)})
        row.update({"net": net, "signal_time": str(times.iloc[cut_global]), "symbol": body})
        pos.append(row)
        n_box += 1
        # negatives: 2 random bars in same symbol, pre-holdout, not near cut
        valid = [j for j in range(210, min(n - HORIZON_BARS - 2, int((times < HOLDOUT_START).sum())))
                 if abs(j - cut_global) > 100]
        for j in rng.sample(valid, min(2, len(valid))):
            nn = forward_net(ind, j)
            if nn is None:
                continue
            r2 = feat_row(feat, broad, j)
            r2.update({"box_w": 0.0, "box_h": 0.0, "box_cy": 0.0})
            r2.update({"net": nn, "signal_time": str(times.iloc[j]), "symbol": body})
            neg.append(r2)
        if k % 500 == 0:
            print(f"  {k}/{len(label_files)}  box={n_box} neg={len(neg)} skip={n_skip}", flush=True)

    dp = pd.DataFrame(pos); dn = pd.DataFrame(neg)
    print(f"\nowner boxes={len(dp)}  random negs={len(dn)}  skipped={n_skip}")
    if len(dp) < 100:
        print("too few boxes resolved -- aborting"); return 1

    # R_owner: direct forward return of owner boxes (SURVIVORSHIP-inflated reference)
    print(f"\n[reference, survivorship-inflated] R_owner box base rate: {stats(dp['net'])}")
    print(f"[reference] random bars base rate:                        {stats(dn['net'])}")

    # NO box geometry in the classifier: box_w>0 vs 0 trivially separates and
    # leaks (AUC 1.0, learns nothing). Force CAUSAL MARKET features only, so the
    # ranking of random bars is meaningful.
    MFEAT = BROAD_COLS  # free-discovered broad factor bank, NOT the 28
    both = pd.concat([dp.assign(y=1), dn.assign(y=0)]).sort_values("signal_time").reset_index(drop=True)
    for c in MFEAT:
        both[c] = pd.to_numeric(both[c], errors="coerce").fillna(0.0)
    both.to_csv(PROJECT / "data" / "owner_box_dataset_broad.csv", index=False)
    from sklearn.metrics import roc_auc_score

    def train_eval(tr, te):
        clf = lgb.train({"objective": "binary", "metric": "auc", "num_leaves": 31,
                         "learning_rate": 0.03, "feature_fraction": 0.8, "bagging_fraction": 0.8,
                         "bagging_freq": 5, "min_data_in_leaf": 50, "verbose": -1},
                        lgb.Dataset(tr[MFEAT], label=tr["y"]), num_boost_round=300)
        te = te.copy(); te["p"] = clf.predict(te[MFEAT])
        auc = float(roc_auc_score(te["y"], te["p"])) if te["y"].nunique() > 1 else float("nan")
        te_neg = te[te["y"] == 0].sort_values("p", ascending=False)
        k = max(int(len(te_neg) * .1), 1)
        return clf, round(auc, 4), stats(te_neg["net"].to_numpy()[:k]), stats(te_neg["net"])

    # single split (reference)
    cut = int(len(both) * 0.7)
    clf, auc, top10, allr = train_eval(both.iloc[:cut], both.iloc[cut:])
    imp = sorted(zip(MFEAT, clf.feature_importance("gain")), key=lambda x: -x[1])[:12]

    # WALK-FORWARD (the judge that killed prior 'positives' -- regime luck check)
    wf = []
    n = len(both)
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tr = both.iloc[int(n * a):int(n * b)]; te = both.iloc[int(n * b):int(n * c)]
        _clf, a_, t_, all_ = train_eval(tr, te)
        wf.append({"test_start": te["signal_time"].iloc[0][:10], "AUC_market_only": a_,
                   "owner_like_top10": t_, "all_test_random": all_})
    out = {
        "owner_boxes": len(dp), "random_negs": len(dn),
        "R_owner_direct_SURVIVORSHIP_INFLATED": stats(dp["net"]),
        "random_baseline": stats(dn["net"]),
        "note": "box geometry EXCLUDED from classifier (leak). Market features only.",
        "single_split": {"AUC_market_only": auc, "owner_like_random_top10": top10,
                         "all_test_random": allr},
        "top_market_features_by_gain": [{"f": f, "gain": round(float(g), 1)} for f, g in imp],
        "WALK_FORWARD_decisive": wf,
        "crude_rule_base_rate_reference": 0.874,
    }
    (PROJECT / "analysis" / "output" / "owner_box_dir_tp3sl1.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
