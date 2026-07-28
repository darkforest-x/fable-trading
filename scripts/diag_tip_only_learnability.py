"""Is the owner's label learnable from the tip alone? 21 days of detection assume it is.

Thirteen YOLO runs have chased one target: reproduce the owner's hand-drawn boxes.
Nobody has asked whether that target contains information available at the tip.
It matters because of what the labelling looked like -- of 499 stars only 2 were
drawn at the tip, the median had 97 bars of future on screen, and 67.3% had the
entire 72-bar horizon already visible. If what the owner recognised lives in those
bars, no causal detector can reproduce it, and every "the model has not learned it
yet" is really "the information is not in the window".

This answers it without asking the owner to label anything, by dropping the pixels
and asking the same question of numbers. Take the gold tips as the positive class,
compute the 28 production features plus the causal alpha library AT THE TIP, and
see whether a model can separate them from bars that are not gold.

Two negative sets, because they ask different questions:

  RANDOM   any bar from the same symbols. Easy, and reported only as a floor.
  DENSE    bars passing the mechanical dense test. Also easy, and the first run of
           this script scored AUC 0.9992 on it -- which was a bug, not a finding:
           the gold tips are SELECTED by star_side (close below the bundle plus a
           >=1 ATR eight-bar fall) while these negatives were filtered on MA
           spread alone, and the feature set contains exactly those quantities.
           The model was recovering my own selection rule, tautologically.
  BREAK    bars passing the SAME mechanical gate the positives passed -- dense,
           below the bundle, >=1 ATR fall -- and simply not chosen by the owner.
           This is the only version that asks the real question: among bars where
           the pattern mechanically occurred, can causal information tell which
           ones the owner picked? A coin flip here means the owner's judgement is
           not in the causal window.

Split by time (iron rule 2), reported with an AUC confidence interval, since a
result near 0.5 is the whole point and needs its uncertainty stated.

Read-only, train side only (<2026-05-04), no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_tip_only_learnability.py
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series  # noqa: E402
from src.detection.render import make_chart_transform  # noqa: E402
from src.factors.library import FACTORS  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import FEATURE_COLUMNS, add_features  # noqa: E402
from src.judgment.yolo_candidates import WINDOW  # noqa: E402
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402
from scripts.build_star_tip_dataset_v10 import (  # noqa: E402
    ANCHOR_LOOKBACK, DROP_ATR_MIN, FAST_MAX, FULL_MAX, MAD_MAX, RET_BARS, WARMUP, Series,
    archive_index, load_star_boxes, star_side, symbol_of,
)

HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")
NEG_PER_POS = 4
SEED = 20260729


def auc_ci(y: np.ndarray, s: np.ndarray) -> tuple[float, float, float]:
    """AUC with a Hanley-McNeil standard error, so 'near 0.5' can be judged."""
    from sklearn.metrics import roc_auc_score
    a = float(roc_auc_score(y, s))
    n1, n2 = int(y.sum()), int((1 - y).sum())
    if n1 == 0 or n2 == 0:
        return a, float("nan"), float("nan")
    q1 = a / (2 - a)
    q2 = 2 * a * a / (1 + a)
    se = math.sqrt((a*(1-a) + (n1-1)*(q1-a*a) + (n2-1)*(q2-a*a)) / (n1*n2))
    return a, a - 1.96*se, a + 1.96*se


def main() -> int:
    import lightgbm as lgb

    known = {s for (_x, s) in list_series(bar="15m")}
    arch = archive_index()
    ser = Series()
    rng = np.random.default_rng(SEED)

    print("① 定位 owner 空头金标 tip(与 v10 同一套口径:MAD 达标 + 方向取较大位移)…",
          flush=True)
    tips: list[tuple[str, int]] = []
    for stem, boxes in load_star_boxes().items():
        if stem not in arch:
            continue
        sym = symbol_of(stem, known)
        if sym is None:
            continue
        e = ser.get(sym)
        if e is None:
            continue
        framed, fast, full, close, times, ma_min, atrp, ma_max = e
        m = re.search(r"_(\d+)$", stem)
        if not m:
            continue
        r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed,
                              stored_img=cv2.imread(str(arch[stem])))
        if r is None:
            continue
        _mo, ws, mad = r
        if not (np.isfinite(mad) and mad < MAD_MAX):
            continue
        sub = framed.iloc[ws:ws + WINDOW].reset_index(drop=True)
        if len(sub) != WINDOW:
            continue
        _c, spans = boxes_cut_and_spans(boxes, make_chart_transform(sub))
        if not spans:
            continue
        cut = ws + spans[0][1]
        if cut < WARMUP or cut >= len(framed) - 2:
            continue
        if times.iloc[cut] >= HOLDOUT:
            continue
        lo = max(WARMUP, cut - ANCHOR_LOOKBACK)
        seg = fast[lo:cut + 1]
        if not np.isfinite(seg).any():
            continue
        trough = lo + int(np.nanargmin(seg))
        side, _b = star_side(close, ma_min, ma_max, atrp, trough, len(framed))
        if side >= 0:
            continue
        tips.append((sym, cut))
    print(f"   空头金标 tip {len(tips)} 个,覆盖 {len({s for s,_ in tips})} 币\n")

    print("② 采负样本(随机 / 机械密集)…", flush=True)
    gold_by_sym: dict[str, set[int]] = {}
    for s, b in tips:
        gold_by_sym.setdefault(s, set()).add(b)
    neg_rand: list[tuple[str, int]] = []
    neg_dense: list[tuple[str, int]] = []
    neg_break: list[tuple[str, int]] = []
    for sym in sorted(gold_by_sym):
        e = ser.get(sym)
        if e is None:
            continue
        framed, fast, full, close, times, ma_min, atrp, ma_max = e
        hi = int((times < min(HOLDOUT, times.iloc[-1])).sum()) - 2
        if hi <= WARMUP + 10:
            continue
        g = gold_by_sym[sym]
        want = NEG_PER_POS * len(g)
        dense_bars, break_bars = [], []
        for i in range(WARMUP, hi):
            if not (np.isfinite(fast[i]) and np.isfinite(full[i])):
                continue
            if not (fast[i] <= FAST_MAX and full[i] <= FULL_MAX):
                continue
            if not all(abs(i - x) > 30 for x in g):
                continue
            dense_bars.append(i)
            # the same gate star_side applies to the positives, so the classifier
            # cannot win by rediscovering the selection rule
            if i < RET_BARS or not np.isfinite(atrp[i]) or atrp[i] <= 0:
                continue
            mv = (close[i] / close[i - RET_BARS] - 1) / atrp[i]
            if np.isfinite(ma_min[i]) and close[i] < ma_min[i] and mv < -DROP_ATR_MIN:
                break_bars.append(i)
        for bucket, dst in ((dense_bars, neg_dense), (break_bars, neg_break)):
            if bucket:
                k = min(want, len(bucket))
                dst += [(sym, int(b)) for b in
                        rng.choice(bucket, size=k, replace=False)]
        pool = [i for i in range(WARMUP, hi) if all(abs(i - x) > 30 for x in g)]
        if pool:
            k = min(want, len(pool))
            neg_rand += [(sym, int(b)) for b in
                         rng.choice(pool, size=k, replace=False)]
    print(f"   随机负 {len(neg_rand)} · 密集负 {len(neg_dense)} · 同门负(密集+跌破) {len(neg_break)}\n")

    print("③ 在 tip 上计算因果特征…", flush=True)
    feat_cache: dict[str, tuple] = {}

    def featurize(items: list[tuple[str, int]], label: int) -> list[dict]:
        rows = []
        for sym, bar in items:
            if sym not in feat_cache:
                e = ser.get(sym)
                if e is None:
                    feat_cache[sym] = None
                else:
                    fr = e[0]
                    ind = add_features(add_indicators(fr))
                    af = {}
                    for n in sorted(FACTORS):
                        try:
                            af[n] = pd.to_numeric(FACTORS[n](add_indicators(fr)),
                                                  errors="coerce").to_numpy(dtype=float)
                        except Exception:  # noqa: BLE001
                            af[n] = np.full(len(fr), np.nan)
                    feat_cache[sym] = (ind, af, e[4])
            c = feat_cache.get(sym)
            if c is None:
                continue
            ind, af, times = c
            if bar >= len(ind):
                continue
            row = {k: float(ind[k].iloc[bar]) for k in FEATURE_COLUMNS if k in ind}
            row.update({f"af_{k}": float(v[bar]) for k, v in af.items()})
            row["y"] = label
            row["t"] = times.iloc[bar]
            rows.append(row)
        return rows

    pos_rows = featurize(tips, 1)
    rand_rows = featurize(neg_rand, 0)
    dense_rows = featurize(neg_dense, 0)
    break_rows = featurize(neg_break, 0)
    print(f"   正 {len(pos_rows)} · 随机负 {len(rand_rows)} · 密集负 {len(dense_rows)} · 同门负 {len(break_rows)}\n")

    results = []
    for name, negs in (("下限对照:金标 vs 随机 bar", rand_rows),
                       ("中间对照:金标 vs 机械密集 bar", dense_rows),
                       ("真问题:金标 vs 同门 bar(密集+跌破)", break_rows)):
        d = pd.DataFrame(pos_rows + negs)
        if d.empty or d["y"].nunique() < 2:
            continue
        d = d.sort_values("t").reset_index(drop=True)
        feats = [c for c in d.columns if c not in ("y", "t")]
        d[feats] = d[feats].replace([np.inf, -np.inf], np.nan)
        tr, te = d[d["t"] < VAL_CUT], d[d["t"] >= VAL_CUT]
        if len(te) < 60 or te["y"].nunique() < 2:
            tr, te = d.iloc[:int(len(d)*0.7)], d.iloc[int(len(d)*0.7):]
        b = lgb.train({"objective": "binary", "learning_rate": 0.05,
                       "num_leaves": 31, "min_data_in_leaf": 40,
                       "feature_fraction": 0.8, "bagging_fraction": 0.8,
                       "bagging_freq": 1, "verbose": -1, "seed": SEED},
                      lgb.Dataset(tr[feats].astype(float), label=tr["y"]),
                      num_boost_round=300)
        s = b.predict(te[feats].astype(float))
        a, lo_, hi_ = auc_ci(te["y"].to_numpy(), s)
        imp = pd.Series(b.feature_importance("gain"), index=feats).sort_values(ascending=False)
        results.append({"variant": name, "n_train": len(tr), "n_test": len(te),
                        "pos_rate_test": round(float(te["y"].mean()), 4),
                        "auc": round(a, 4), "ci": [round(lo_, 4), round(hi_, 4)],
                        "top_features": [str(k) for k in imp.head(6).index]})
        print(f"=== {name} ===")
        print(f"  训练 {len(tr)} → 测试 {len(te)}(正类率 {te['y'].mean():.3f})")
        print(f"  AUC {a:.4f}   95%CI [{lo_:.4f}, {hi_:.4f}]")
        print(f"  最重要特征: {', '.join(str(k) for k in imp.head(5).index)}\n")

    hard = next((r for r in results if r["variant"].startswith("真问题")), None)
    if hard is None:
        verdict = "密集负样本不足,无法判定"
    elif hard["ci"][0] <= 0.5:
        verdict = (f"AUC {hard['auc']:.3f},95%CI 下沿 {hard['ci'][0]:.3f} 未超过 0.5 —— "
                   f"在机械密集的 bar 里,因果特征分不出 owner 会挑哪一个。"
                   f"这与「标注时中位可见 97 根未来」一致:owner 的判断可能主要来自"
                   f"框右边的走势,而检测器在盘口看不到。整条检测线的前提未被证实。")
    elif hard["auc"] < 0.6:
        verdict = (f"AUC {hard['auc']:.3f},CI [{hard['ci'][0]:.3f}, {hard['ci'][1]:.3f}] "
                   f"显著高于 0.5 但很弱 —— 因果窗口里有信息,但很少,"
                   f"检测器做到 100% 复现人眼不现实")
    else:
        verdict = (f"AUC {hard['auc']:.3f},CI [{hard['ci'][0]:.3f}, {hard['ci'][1]:.3f}] "
                   f"—— 因果窗口里确实有可学信息,继续修检测器是对的方向")
    print(f"判读: {verdict}")
    print("注:训练池内时间切分,未碰 holdout;这测的是「标注能否被因果学习」,"
          "不是「学了能不能赚钱」。")

    (PROJECT / "analysis" / "output" / "diag_tip_only_learnability.json").write_text(
        json.dumps({"n_gold": len(pos_rows), "n_rand": len(rand_rows),
                    "n_dense": len(dense_rows), "n_break": len(break_rows), "results": results,
                    "verdict": verdict}, indent=2, ensure_ascii=False, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
