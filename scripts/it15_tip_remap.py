"""IT-15: single-variable tip remap — box right-edge cut vs local density trough.

After tip-mapping audit: box_right_frac≈0.5 is about image crop (unfair as
intent evidence), but at Owner cut only ~1.6% still meet FAST/FULL dense and
spread_chg8>0 on ~98% — mechanical tip gap. Hypothesis: Owner's drawn RIGHT
EDGE is the cluster's visual end / early expansion; the true tip is the local
fast_spread trough a few bars earlier inside/near the box.

Single variable: signal_i definition.
  A) cut_global = box right edge (status quo)
  B) trough in [cut-24, cut] of fast_spread
  C) last dense bar in [cut-24, cut] if any, else trough

Eval: causal TP5/SL2 maker net, long-only and short-only, time-ordered
walk-forward three folds on pre-holdout. Success line same as lab: any side
with all three periods top-decile PF>=1.3 is interesting; otherwise remap
doesn't unlock a tradeable tip.

No holdout. No promote. No YOLO train.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.costs import FORWARD_COST  # noqa: E402
from src.data.loader import iter_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features  # noqa: E402
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS  # noqa: E402
from scripts.it09_both_sides import net_dir  # noqa: E402

HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
WARMUP = 288
FAST_MAX, FULL_MAX = 0.0028, 0.0055
SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
OUT = PROJECT / "analysis" / "output" / "it15_tip_remap.json"
TP, SL = 5.0, 2.0


def pf(x) -> float | None:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return None
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def wf_raw_and_lgbm(nets, times, feats, tag: str) -> list[dict]:
    """Raw all-trade PF + optional LGBM top-decile on same feats (quality filter)."""
    order = np.argsort(times)
    nets, times = nets[order], times[order]
    feats = feats[order]
    n = len(nets)
    rows = []
    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03,
         "min_data_in_leaf": 40, "feature_fraction": 0.8, "verbose": -1}
    for a, b, c in [(0.0, 0.5, 0.65), (0.0, 0.65, 0.8), (0.0, 0.8, 1.0)]:
        tei = slice(int(n * b), int(n * c))
        tri = slice(int(n * a), int(n * b))
        yte = nets[tei]
        raw = pf(yte)
        row = {"n": int(len(yte)), "raw_PF": raw}
        if len(nets[tri]) >= 120 and len(yte) >= 40 and feats.shape[1] > 0:
            # predict net; top-decile by predicted net
            dtr = lgb.Dataset(feats[tri], label=nets[tri])
            bo = lgb.train(P, dtr, num_boost_round=200)
            pred = bo.predict(feats[tei])
            k = max(int(len(yte) * 0.1), 1)
            top = yte[np.argsort(-pred)[:k]]
            row["top_PF"] = pf(top)
            row["top_n"] = k
        rows.append(row)
    print(f"[{tag}] " + " | ".join(
        f"n{r['n']} raw{r.get('raw_PF')} top{r.get('top_PF')}" for r in rows), flush=True)
    return rows


def main() -> int:
    sheet = pd.read_csv(SHEET, dtype=str).fillna("")
    sheet["owner_side"] = sheet["owner_side"].str.strip().str.lower()
    labeled = sheet[sheet["owner_side"].isin(["long", "short"])].copy()
    labeled["cut_global"] = pd.to_numeric(labeled["cut_global"], errors="coerce")

    need = set(labeled["symbol"].unique())
    ind_by: dict[str, pd.DataFrame] = {}
    for _s, sym, frame in iter_series(bar="15m", min_bars=WARMUP + 200):
        if sym in need:
            ind_by[sym] = add_features(add_indicators(frame))
    print(f"loaded indicators {len(ind_by)}/{len(need)}", flush=True)

    # Collect per remap definition
    defs = {"A_cut": [], "B_trough": [], "C_last_dense": []}
    for _, r in labeled.iterrows():
        sym, cut = r["symbol"], r["cut_global"]
        if sym not in ind_by or not np.isfinite(cut):
            continue
        ind = ind_by[sym]
        times = pd.to_datetime(ind["open_time"], utc=True)
        cut_i = int(cut)
        if cut_i < WARMUP + 24 or cut_i >= len(ind) - HORIZON_BARS - 2:
            continue
        if times.iloc[cut_i] >= HOLDOUT_START:
            continue
        fast = ind["fast_spread"].to_numpy(dtype=float)
        full = ind["full_spread"].to_numpy(dtype=float)
        lo = cut_i - 24
        seg_f, seg_u = fast[lo:cut_i + 1], full[lo:cut_i + 1]
        trough_i = lo + int(np.nanargmin(seg_f))
        dense_mask = (seg_f <= FAST_MAX) & (seg_u <= FULL_MAX) & np.isfinite(seg_f) & np.isfinite(seg_u)
        if dense_mask.any():
            last_dense_i = lo + int(np.where(dense_mask)[0][-1])
        else:
            last_dense_i = trough_i

        feat_row = ind.loc[cut_i, FEATURE_COLUMNS].to_numpy(dtype=float)
        feat_row = np.nan_to_num(feat_row, nan=0.0)
        t_ns = times.iloc[cut_i].value
        side = r["owner_side"]
        for name, sig_i in (("A_cut", cut_i), ("B_trough", trough_i), ("C_last_dense", last_dense_i)):
            if sig_i < WARMUP or sig_i >= len(ind) - HORIZON_BARS - 2:
                continue
            if float(ind["atr_pct"].iloc[sig_i]) < ATR_PCT_MIN:
                continue
            ln = net_dir(ind, sig_i, +1)
            sn = net_dir(ind, sig_i, -1)
            if ln is None or sn is None:
                continue
            # features always from original cut (decision-time info Owner had at label)
            # but for B/C trough may be earlier — use feats at sig_i for causal honesty
            fr = ind.loc[sig_i, FEATURE_COLUMNS].to_numpy(dtype=float)
            fr = np.nan_to_num(fr, nan=0.0)
            defs[name].append({
                "side": side, "t": t_ns, "long_net": ln, "short_net": sn,
                "feat": fr, "offset": cut_i - sig_i,
            })

    out = {"n_by_def": {k: len(v) for k, v in defs.items()}, "results": {}, "holdout": "FORBIDDEN"}
    for name, rows in defs.items():
        if not rows:
            continue
        df = pd.DataFrame(rows)
        out["results"][name] = {"offset_from_cut_median": float(np.median([r["offset"] for r in rows]))}
        for side, signed in (("long", "long_net"), ("short", "short_net")):
            sub = df  # evaluate BOTH remaps on all owner boxes, taking that side
            nets = sub[signed].to_numpy(dtype=float)
            times = sub["t"].to_numpy()
            feats = np.vstack(sub["feat"].to_list())
            tag = f"{name}/{side}"
            out["results"][name][side] = wf_raw_and_lgbm(nets, times, feats, tag)

    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
