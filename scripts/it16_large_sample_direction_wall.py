#!/usr/bin/env python3
"""IT-16: large-sample direction wall on expanded v16 fires (owner §7-2).

Hypothesis (low prior): the IT-00~13 verdict \"no tradeable direction at decision
time\" is a small-sample artifact of the 4014-fire / 79-symbol pool. Re-dump v16
on (near-)full universe (<2026-05-04), then re-measure:

  - always_long / always_short / oracle PF (TP3/SL1 maker)
  - LightGBM P(long better) AUC + traded-by-pred PF
  - 3-period walk-forward top conviction

Success (pre-registered): direction AUC > 0.55 AND any period traded PF@maker
>= 1.3 with the other periods not collapsing below 0.9. Else: wall confirmed,
not a small-n mirage.

Train only; no holdout; no promote. Candidate CSV usually produced on 3060:
  data/v16_candidates_large.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

PROJECT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT))

from src.costs import FORWARD_COST  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN  # noqa: E402
from scripts.broad_features import add_broad_features  # noqa: E402
from scripts.v16_judgment_v2 import geom_features, tight_net  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
PERIODS = [
    ("p1", "2025-06-01", "2025-11-01"),
    ("p2", "2025-11-01", "2026-02-01"),
    ("p3", "2026-02-01", "2026-05-04"),
]


def pf(nets: np.ndarray) -> float:
    nets = np.asarray(nets, dtype=float)
    nets = nets[np.isfinite(nets)]
    if len(nets) == 0:
        return float("nan")
    pos, neg = nets[nets > 0].sum(), -nets[nets < 0].sum()
    return float(pos / neg) if neg > 0 else (float("inf") if pos > 0 else float("nan"))


def build_rows(cand: pd.DataFrame) -> pd.DataFrame:
    """Attach geom+broad features and long/short tight nets at each fire."""
    rows = []
    for sym, g in cand.groupby("symbol"):
        try:
            frame = load_series(list_series(bar="15m")[("okx", sym)])
        except Exception:
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        frame = frame[times < HOLDOUT_START].reset_index(drop=True)
        if len(frame) < 400:
            continue
        ind = add_indicators(add_mas(frame))
        geom = geom_features(frame)
        broad = add_broad_features(frame)
        feat = pd.concat([geom, broad], axis=1)
        tmap = {pd.Timestamp(t): i for i, t in enumerate(pd.to_datetime(frame["open_time"], utc=True))}
        for _, r in g.iterrows():
            t = pd.Timestamp(r["signal_time"])
            if t.tzinfo is None:
                t = t.tz_localize("UTC")
            i = tmap.get(t)
            if i is None:
                continue
            nl = tight_net(ind, i, +1)
            ns = tight_net(ind, i, -1)
            if nl is None or ns is None:
                continue
            row = feat.iloc[i].to_dict()
            row.update(
                {
                    "symbol": sym,
                    "signal_time": str(t),
                    "net_long": float(nl),
                    "net_short": float(ns),
                    "y_long_better": int(nl > ns),
                }
            )
            # keep dump CSV tabular feats if present
            for c in cand.columns:
                if c in ("symbol", "signal_time", "net"):
                    continue
                if c not in row:
                    try:
                        row[c] = float(r[c])
                    except Exception:
                        pass
            rows.append(row)
    return pd.DataFrame(rows)


def walk_forward(df: pd.DataFrame, feat_cols: list[str]) -> dict:
    out = {}
    for name, a, b in PERIODS:
        ta, tb = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
        tr = df[pd.to_datetime(df["signal_time"], utc=True) < ta]
        te = df[
            (pd.to_datetime(df["signal_time"], utc=True) >= ta)
            & (pd.to_datetime(df["signal_time"], utc=True) < tb)
        ]
        if len(tr) < 200 or len(te) < 50:
            out[name] = {"skip": True, "n_train": len(tr), "n_test": len(te)}
            continue
        Xtr = tr[feat_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        ytr = tr["y_long_better"].astype(int)
        Xte = te[feat_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        dtr = lgb.Dataset(Xtr, label=ytr)
        booster = lgb.train(
            {
                "objective": "binary",
                "metric": "auc",
                "learning_rate": 0.05,
                "num_leaves": 31,
                "min_data_in_leaf": 40,
                "verbosity": -1,
                "seed": 42,
            },
            dtr,
            num_boost_round=200,
        )
        p = booster.predict(Xte)
        auc = float(roc_auc_score(te["y_long_better"], p)) if te["y_long_better"].nunique() > 1 else float("nan")
        side = np.where(p >= 0.5, te["net_long"].to_numpy(), te["net_short"].to_numpy())
        # top conviction: |p-0.5| top 20%
        conv = np.abs(p - 0.5)
        thr = np.quantile(conv, 0.8)
        mask = conv >= thr
        out[name] = {
            "n_train": int(len(tr)),
            "n_test": int(len(te)),
            "auc": auc,
            "pf_pred_side": pf(side),
            "pf_top_conviction": pf(side[mask]) if mask.any() else float("nan"),
            "pf_always_long": pf(te["net_long"].to_numpy()),
            "pf_always_short": pf(te["net_short"].to_numpy()),
            "pf_oracle": pf(np.maximum(te["net_long"].to_numpy(), te["net_short"].to_numpy())),
            "n_top": int(mask.sum()),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="data/v16_candidates_large.csv")
    ap.add_argument("--baseline", default="data/v16_candidates_100.csv")
    ap.add_argument("--tag", default="it16_large_sample_direction_wall")
    ap.add_argument("--max-rows", type=int, default=0, help="optional subsample for smoke")
    args = ap.parse_args()

    path = PROJECT / args.data
    if not path.exists():
        raise SystemExit(f"missing {path} — finish 3060 dump first")

    cand = pd.read_csv(path)
    if args.max_rows > 0:
        cand = cand.sample(n=min(args.max_rows, len(cand)), random_state=42)
    base = None
    bp = PROJECT / args.baseline
    if bp.exists():
        base = pd.read_csv(bp)

    print(f"large n={len(cand)} syms={cand['symbol'].nunique()}", flush=True)
    df = build_rows(cand)
    print(f"labeled rows={len(df)}", flush=True)
    feat_cols = [
        c
        for c in df.columns
        if c
        not in (
            "symbol",
            "signal_time",
            "net_long",
            "net_short",
            "y_long_better",
            "net",
        )
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    wf = walk_forward(df, feat_cols)
    summary = {
        "tag": args.tag,
        "data": str(path),
        "n_fires_csv": int(len(cand)),
        "n_symbols_csv": int(cand["symbol"].nunique()),
        "n_labeled": int(len(df)),
        "n_feat": len(feat_cols),
        "overall": {
            "pf_always_long": pf(df["net_long"].to_numpy()),
            "pf_always_short": pf(df["net_short"].to_numpy()),
            "pf_oracle": pf(np.maximum(df["net_long"].to_numpy(), df["net_short"].to_numpy())),
            "long_win_rate": float((df["net_long"] > 0).mean()),
            "short_win_rate": float((df["net_short"] > 0).mean()),
            "frac_long_better": float(df["y_long_better"].mean()),
        },
        "walk_forward": wf,
        "baseline_100": None
        if base is None
        else {
            "n_fires": int(len(base)),
            "n_symbols": int(base["symbol"].nunique()),
        },
        "gates": {
            "auc_gt_0_55": any(
                (wf.get(k) or {}).get("auc", 0) > 0.55 for k in ("p1", "p2", "p3")
            ),
            "any_period_pf_ge_1_3": any(
                (wf.get(k) or {}).get("pf_top_conviction", 0) >= 1.3
                or (wf.get(k) or {}).get("pf_pred_side", 0) >= 1.3
                for k in ("p1", "p2", "p3")
            ),
        },
        "verdict": None,
    }
    # gate: need AUC>0.55 somewhere AND a period PF>=1.3 without others <0.9
    ok_auc = summary["gates"]["auc_gt_0_55"]
    pfs = [
        (wf.get(k) or {}).get("pf_top_conviction")
        or (wf.get(k) or {}).get("pf_pred_side")
        for k in ("p1", "p2", "p3")
        if not (wf.get(k) or {}).get("skip")
    ]
    ok_pf = any(p is not None and p >= 1.3 for p in pfs)
    no_collapse = all(p is None or p >= 0.9 for p in pfs) if pfs else False
    passed = bool(ok_auc and ok_pf and no_collapse)
    summary["verdict"] = "PASS_break_wall" if passed else "FAIL_wall_confirmed"
    summary["gates"]["passed"] = passed

    out_json = PROJECT / "analysis" / "output" / f"{args.tag}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    print(f"wrote {out_json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
