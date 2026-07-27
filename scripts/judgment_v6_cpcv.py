"""Re-judge the v6 judgment layer under CPCV, with dispersion instead of a number.

The single-path walk-forward on this pool gave gross PF 3.272 / 0.399 / 0.732 /
0.955 across four folds and I reported it as "the layer selects worse than raw".
That reading may be right, but one path cannot say whether a spread that wide is
signal or sampling -- and this session has already shown what happens when I
answer that question by eye (a "collapse" read off 16 trades whose interval
spanned the breakeven).

CPCV re-tests the same pool over C(6,2)=15 purged, embargoed splits that
recombine into 5 full-length paths, so the output is a DISTRIBUTION of
top-decile performance. The question stops being "is the number above 1?" and
becomes "what fraction of paths clear the raw pool, and how wide is the spread?".

Purging uses each sample's label end (signal + 72 bars), not its signal time.
Passing signal time would disable the guard while looking like it works.

Reported against the raw pool on the identical test blocks, because a selector
can only be credited with what it adds over taking everything, and at gross PF
as well as net, because net gains from diluting a fixed cost are not skill.

No holdout (pool ends 2026-05-03). No promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/judgment_v6_cpcv.py
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

from src.backtest.cpcv import CPCV  # noqa: E402
from src.costs import SWAP_TAKER  # noqa: E402

POOL = PROJECT / "data" / "judgment_yolo_short_v6.csv"
HORIZON_BARS, BAR_MIN = 72, 15
TOP_FRAC = 0.20
NON_FEAT = {"source", "symbol", "side", "signal_i", "signal_time", "label",
            "outcome", "exit_offset", "entry_price", "realized_ret", "t", "label_end"}


def pf(x):
    x = np.asarray(x)
    w, l = x[x > 0].sum(), x[x < 0].sum()
    return round(float(w / -l), 3) if l < 0 else None


def main() -> int:
    d = pd.read_csv(POOL)
    d["t"] = pd.to_datetime(d["signal_time"], utc=True)
    d = d.sort_values("t").reset_index(drop=True)
    d["label_end"] = d["t"] + pd.Timedelta(minutes=BAR_MIN * HORIZON_BARS)
    feats = [c for c in d.columns if c not in NON_FEAT and pd.api.types.is_numeric_dtype(d[c])]
    d[feats] = d[feats].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    print(f"候选 {len(d)}  特征 {len(feats)}")

    cv = CPCV(n_groups=6, n_test_groups=2, embargo_frac=0.01)
    print(f"CPCV: {cv.n_splits} 个切分 → {cv.n_paths} 条完整路径  "
          f"(purge 用 label_end = signal + {HORIZON_BARS} bar)")

    P = {"objective": "regression", "num_leaves": 31, "learning_rate": 0.03,
         "min_data_in_leaf": 30, "feature_fraction": 0.8, "bagging_fraction": 0.8,
         "bagging_freq": 5, "verbose": -1}

    rows = []
    for sp in cv.split(d["t"], d["label_end"]):
        tr, te = d.iloc[sp.train_idx], d.iloc[sp.test_idx]
        if len(tr) < 200 or len(te) < 60:
            continue
        cut = int(len(tr) * 0.85)
        bo = lgb.train(P, lgb.Dataset(tr[feats].iloc[:cut], label=tr["realized_ret"].iloc[:cut]),
                       num_boost_round=400,
                       valid_sets=[lgb.Dataset(tr[feats].iloc[cut:],
                                               label=tr["realized_ret"].iloc[cut:])],
                       callbacks=[lgb.early_stopping(30, verbose=False)])
        s = bo.predict(te[feats])
        k = max(int(len(te) * TOP_FRAC), 1)
        top = te["realized_ret"].to_numpy()[np.argsort(-s)][:k]
        raw = te["realized_ret"].to_numpy()
        rows.append({"combo": sp.combo, "n_train": len(tr), "n_test": len(te),
                     "purged": sp.n_purged, "embargoed": sp.n_embargoed,
                     "top_gross_PF": pf(top), "raw_gross_PF": pf(raw),
                     "top_net": round(float(top.mean() - SWAP_TAKER), 5),
                     "raw_net": round(float(raw.mean() - SWAP_TAKER), 5),
                     "beats_raw": bool(top.mean() > raw.mean())})

    if not rows:
        print("无有效切分")
        return 1
    tp = np.array([r["top_net"] for r in rows])
    rw = np.array([r["raw_net"] for r in rows])
    beat = sum(r["beats_raw"] for r in rows)
    purged = sum(r["purged"] for r in rows)
    embar = sum(r["embargoed"] for r in rows)

    print(f"\n有效切分 {len(rows)}  累计 purge {purged} 条 / embargo {embar} 条")
    print(f"\n{'切分':<10} {'top毛PF':>9} {'裸池毛PF':>10} {'top净':>10} {'裸池净':>10} {'胜过裸池':>9}")
    for r in rows:
        print(f"{str(r['combo']):<10} {str(r['top_gross_PF']):>9} {str(r['raw_gross_PF']):>10} "
              f"{r['top_net']:>+10.5f} {r['raw_net']:>+10.5f} {'是' if r['beats_raw'] else '否':>9}")

    print(f"\n=== 分布(CPCV 的重点) ===")
    print(f"  top20 净收益: p10={np.percentile(tp,10):+.5f}  中位={np.median(tp):+.5f}  "
          f"p90={np.percentile(tp,90):+.5f}")
    print(f"  裸池 净收益 : p10={np.percentile(rw,10):+.5f}  中位={np.median(rw):+.5f}  "
          f"p90={np.percentile(rw,90):+.5f}")
    print(f"  判断层胜过裸池的切分: {beat}/{len(rows)} = {100*beat/len(rows):.0f}%")
    print(f"  top20 为正的切分    : {int((tp>0).sum())}/{len(rows)}")

    verdict = ("判断层无效:多数切分不如裸池,且分布跨零"
               if beat <= len(rows) / 2 or np.median(tp) <= 0 else
               f"判断层有效:{beat}/{len(rows)} 切分胜过裸池,中位净 {np.median(tp):+.5f}")
    print(f"\n判读: {verdict}")
    print("对照:单路径 walk-forward 只给 4 个数,看不出这个分布有多宽。")

    (PROJECT / "analysis" / "output" / "judgment_v6_cpcv.json").write_text(
        json.dumps({"pool": POOL.name, "n": len(d), "n_splits": len(rows),
                    "n_paths": cv.n_paths, "purged": purged, "embargoed": embar,
                    "top_net_p10_p50_p90": [round(float(np.percentile(tp, q)), 5)
                                            for q in (10, 50, 90)],
                    "raw_net_p10_p50_p90": [round(float(np.percentile(rw, q)), 5)
                                            for q in (10, 50, 90)],
                    "beats_raw": f"{beat}/{len(rows)}", "splits": rows,
                    "verdict": verdict}, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
