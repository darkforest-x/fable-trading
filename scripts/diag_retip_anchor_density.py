"""Would re-anchoring the tip to the local density trough fix the short dataset?

Owner reviewed 280 of v1b's boxes and got 18.3% precision (51 keep / 228 drop).
Root cause candidate, already measured in p_tip_mapping_owner_intent.md: the
training set treats the owner box's RIGHT EDGE (cut_global) as the tip, but at
that bar dense_at_cut is only 1.55%, spread_chg8>0 is 97.6%, and it sits a
median 10 bars AFTER the local fast_spread trough. The detector faithfully
learned to fire ~10 bars past the cluster, where the MA bundle has already
opened -- which is exactly what the owner is rejecting.

Before spending a rebuild plus a 3060 run on the obvious fix (move the tip back
to the trough), check the fix can actually work. Three questions, in order of
how much they matter:

  1. Re-anchoring to the trough in [cut-W, cut]: what is dense_at_anchor then?
  2. UPPER BOUND: what share of owner boxes contain ANY mechanically dense bar
     in that window at all? Re-anchoring can never beat this. The audit already
     hints it is low (dense_in_prior_8 = 32%), and if it stays low at W=48 then
     no amount of re-anchoring rescues the target.
  3. If the bound is low, is the mismatch the THRESHOLD rather than the anchor?
     Report the distribution of fast/full at each box's trough -- that is the
     owner's implicit density bar, and it says what threshold would capture
     their eye.

FAST_MAX/FULL_MAX are owner decisions (CLAUDE.md); this only measures against
them and against alternatives, it never adopts a new value.

Read-only, train window only, no holdout, no training, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_retip_anchor_density.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import add_features  # noqa: E402  (spread_chg8 lives here)

SHEET = PROJECT / "analysis" / "output" / "owner_side_review" / "review_sheet.csv"
FAST_MAX, FULL_MAX = 0.0028, 0.0055   # owner-set; measured against, never changed
HOLDOUT_START = pd.Timestamp("2026-05-04", tz="UTC")
WARMUP = 200
WINDOWS = (8, 16, 24, 48)


def main() -> int:
    sheet = pd.read_csv(SHEET)
    short = sheet[sheet["owner_side"].astype(str).str.strip() == "short"].copy()
    short["cut_global"] = pd.to_numeric(short["cut_global"], errors="coerce")
    print(f"owner short boxes: {len(short)}")

    series = list_series(bar="15m")
    ind_by: dict[str, pd.DataFrame] = {}
    rows = []
    skips = {"no_series": 0, "oob": 0, "holdout": 0, "bad_ind": 0}

    for sym, grp in short.groupby("symbol"):
        if sym not in ind_by:
            key = ("okx", sym)
            if key not in series:
                skips["no_series"] += len(grp)
                continue
            try:
                ind_by[sym] = add_features(add_indicators(load_series(series[key])))
            except Exception:
                skips["no_series"] += len(grp)
                continue
        ind = ind_by[sym]
        times = pd.to_datetime(ind["open_time"], utc=True)
        fast = ind["fast_spread"].to_numpy(dtype=float)
        full = ind["full_spread"].to_numpy(dtype=float)
        for _, r in grp.iterrows():
            cut = r["cut_global"]
            if not np.isfinite(cut):
                skips["oob"] += 1
                continue
            ci = int(cut)
            if ci < WARMUP or ci >= len(ind) - 8:
                skips["oob"] += 1
                continue
            if times.iloc[ci] >= HOLDOUT_START:
                skips["holdout"] += 1
                continue
            if not np.isfinite(fast[ci]) or not np.isfinite(full[ci]):
                skips["bad_ind"] += 1
                continue
            rec = {"symbol": sym, "cut": ci,
                   "dense_at_cut": bool(fast[ci] <= FAST_MAX and full[ci] <= FULL_MAX)}
            for w in WINDOWS:
                lo = max(WARMUP, ci - w)
                seg_f, seg_u = fast[lo:ci + 1], full[lo:ci + 1]
                if not np.isfinite(seg_f).any():
                    rec[f"anchor_dense_w{w}"] = False
                    rec[f"any_dense_w{w}"] = False
                    continue
                j = lo + int(np.nanargmin(seg_f))          # the re-anchored tip
                rec[f"shift_w{w}"] = ci - j
                rec[f"anchor_dense_w{w}"] = bool(fast[j] <= FAST_MAX and full[j] <= FULL_MAX)
                rec[f"any_dense_w{w}"] = bool(((seg_f <= FAST_MAX) & (seg_u <= FULL_MAX)).any())
                if w == 24:
                    rec["trough_fast"] = float(fast[j])
                    rec["trough_full"] = float(full[j])
            rows.append(rec)

    d = pd.DataFrame(rows)
    if d.empty:
        print("no usable rows", flush=True)
        return 1
    n = len(d)
    print(f"usable {n}  skips={skips}\n")

    base = float(d["dense_at_cut"].mean())
    print(f"基线 dense_at_cut(现训练集的 tip) = {base*100:.2f}%   ← 现状")
    print("\n重新锚到 fast_spread 谷底后:")
    print(f"{'窗口':>6} {'锚点 dense':>12} {'窗内有 dense(上界)':>20} {'中位后移':>10}")
    out_w = {}
    for w in WINDOWS:
        a = float(d[f"anchor_dense_w{w}"].mean())
        u = float(d[f"any_dense_w{w}"].mean())
        s = float(d[f"shift_w{w}"].median()) if f"shift_w{w}" in d else float("nan")
        out_w[w] = {"anchor_dense": round(a, 4), "any_dense_upper_bound": round(u, 4),
                    "median_shift_bars": s}
        print(f"{w:>6} {a*100:>11.2f}% {u*100:>19.2f}% {s:>10.0f}")

    tf = d["trough_fast"].dropna().to_numpy()
    tu = d["trough_full"].dropna().to_numpy()
    print(f"\n谷底处的实际 spread(owner 眼里的'密集'到底多密,n={len(tf)}):")
    qs = [50, 60, 70, 80, 90]
    print(f"  fast: " + "  ".join(f"p{q}={np.percentile(tf,q):.4f}" for q in qs)
          + f"   (门限 {FAST_MAX})")
    print(f"  full: " + "  ".join(f"p{q}={np.percentile(tu,q):.4f}" for q in qs)
          + f"   (门限 {FULL_MAX})")
    cover = {q: {"fast": round(float(np.percentile(tf, q)), 5),
                 "full": round(float(np.percentile(tu, q)), 5)} for q in qs}

    best = max(out_w.values(), key=lambda v: v["anchor_dense"])["anchor_dense"]
    if best >= 0.80:
        verdict = "重新锚定可行:锚点 dense 率高,重建数据集值得做"
    elif max(v["any_dense_upper_bound"] for v in out_w.values()) < 0.5:
        verdict = ("重新锚定救不了:多数 owner 框附近根本没有机械意义上的密集 → "
                   "问题不在锚点,而在阈值/密集定义与 owner 的眼不一致(见分位表)")
    else:
        verdict = "部分可行:锚点有改善但未过 80%,需先对齐阈值再重建"
    print(f"\n判读: {verdict}")

    (PROJECT / "analysis" / "output" / "diag_retip_anchor_density.json").write_text(
        json.dumps({
            "n": n, "skips": skips,
            "thresholds": {"FAST_MAX": FAST_MAX, "FULL_MAX": FULL_MAX,
                           "note": "owner decisions — measured against, not changed"},
            "dense_at_cut_baseline": round(base, 4),
            "by_window": out_w,
            "trough_spread_percentiles": cover,
            "verdict": verdict,
        }, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
