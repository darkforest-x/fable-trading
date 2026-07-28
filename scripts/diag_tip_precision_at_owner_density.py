"""Rewrite of the learnability probe: precision at the owner's own labelling density.

The AUC version of this question cannot be answered, and two runs showed why. With
random negatives it scored 0.989; with mechanically-dense negatives 0.999; with
negatives passing the same break gate as the positives 0.994. Each number is an
artefact of how the negatives were chosen -- dense negatives are tighter than the
owner's own boxes (only 31% of which qualify as mechanically dense), so the model
separates on MA spread in whichever direction the sampling created. Any AUC on a
hand-picked negative set bakes in its answer.

The question has to be posed the way the detector is actually judged: score EVERY
bar, take the top N where N is the owner's own labelling rate, and ask how many of
those are gold. Negatives are then "all other bars", chosen by nobody.

    owner labels 0.18-0.36 signals per symbol-month
    v9 fired 48.8, which is why its charts looked wrong

Two numbers come out, and they mean different things:

  PRECISION@density   of the bars a tip-only model ranks highest, what share are
                      the owner's. This is the ceiling any causal detector can
                      reach with these features.
  RECALL@density      what share of gold that top slice covers.

If precision at owner density is near the base rate, the owner's judgement is not
recoverable from the causal window and no amount of detector work fixes it. If it
is far above, the information is there and v9's failure was engineering.

Split by time (iron rule 2). Read-only, train side only, no holdout, no promote.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_tip_precision_at_owner_density.py
"""
from __future__ import annotations

import json
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
    ANCHOR_LOOKBACK, MAD_MAX, WARMUP, Series,
    archive_index, load_star_boxes, star_side, symbol_of,
)

HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")
BARS_PER_MONTH = 96 * 30.44
OWNER_DENSITY = (0.18, 0.36)      # signals per symbol-month
GOLD_GUARD = 3                    # bars around a gold tip counted as the same event
SEED = 20260729


def main() -> int:
    import lightgbm as lgb

    known = {s for (_x, s) in list_series(bar="15m")}
    arch = archive_index()
    ser = Series()

    print("① 定位空头金标 tip…", flush=True)
    gold: dict[str, set[int]] = {}
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
        if cut < WARMUP or cut >= len(framed) - 2 or times.iloc[cut] >= HOLDOUT:
            continue
        lo = max(WARMUP, cut - ANCHOR_LOOKBACK)
        seg = fast[lo:cut + 1]
        if not np.isfinite(seg).any():
            continue
        trough = lo + int(np.nanargmin(seg))
        side, _b = star_side(close, ma_min, ma_max, atrp, trough, len(framed))
        if side < 0:
            gold.setdefault(sym, set()).add(cut)
    n_gold = sum(len(v) for v in gold.values())
    print(f"   {n_gold} 个,覆盖 {len(gold)} 币\n")

    print("② 对这些币的每一根 bar 计算因果特征(负样本 = 其余所有 bar)…", flush=True)
    frames = []
    for k, sym in enumerate(sorted(gold), 1):
        e = ser.get(sym)
        if e is None:
            continue
        framed, fast, full, close, times, ma_min, atrp, ma_max = e
        hi = int((times < HOLDOUT).sum()) - 2
        if hi <= WARMUP + 200:
            continue
        ind = add_features(add_indicators(framed))
        cols = {c: ind[c].to_numpy(dtype=float) for c in FEATURE_COLUMNS if c in ind}
        for n in sorted(FACTORS):
            try:
                cols[f"af_{n}"] = pd.to_numeric(FACTORS[n](add_indicators(framed)),
                                                errors="coerce").to_numpy(dtype=float)
            except Exception:  # noqa: BLE001
                cols[f"af_{n}"] = np.full(len(framed), np.nan)
        idx = np.arange(WARMUP, hi)
        d = pd.DataFrame({c: v[idx] for c, v in cols.items()})
        d["t"] = times.iloc[idx].to_numpy()
        d["sym"] = sym
        g = gold[sym]
        d["y"] = [1 if any(abs(int(i) - x) <= GOLD_GUARD for x in g) else 0 for i in idx]
        frames.append(d)
        if k % 20 == 0:
            print(f"   [{k}/{len(gold)}]", flush=True)
    d = pd.concat(frames, ignore_index=True).sort_values("t").reset_index(drop=True)
    feats = [c for c in d.columns if c not in ("y", "t", "sym")]
    d[feats] = d[feats].replace([np.inf, -np.inf], np.nan)
    base = float(d["y"].mean())
    print(f"\n   总 bar {len(d):,}   金标 {int(d['y'].sum())}   基础率 {base*100:.4f}%")

    tr, te = d[d["t"] < VAL_CUT], d[d["t"] >= VAL_CUT]
    if te["y"].sum() < 10:
        cut = int(len(d) * 0.7)
        tr, te = d.iloc[:cut], d.iloc[cut:]
    sym_months = len(te) / BARS_PER_MONTH
    print(f"   训练 {len(tr):,}(金标 {int(tr['y'].sum())}) → "
          f"测试 {len(te):,}(金标 {int(te['y'].sum())},= {sym_months:.1f} 币·月)\n")

    b = lgb.train({"objective": "binary", "learning_rate": 0.05, "num_leaves": 63,
                   "min_data_in_leaf": 100, "feature_fraction": 0.8,
                   "bagging_fraction": 0.8, "bagging_freq": 1,
                   "scale_pos_weight": float((1-base)/max(base, 1e-9)),
                   "verbose": -1, "seed": SEED},
                  lgb.Dataset(tr[feats].astype(float), label=tr["y"]),
                  num_boost_round=400)
    s = b.predict(te[feats].astype(float))
    y = te["y"].to_numpy()

    print("=== 在不同「开火密度」下的精度 ===")
    print(f"{'密度(条/币·月)':>16} {'取前 N':>9} {'命中金标':>9} {'精度':>9} "
          f"{'召回':>8} {'vs 基础率':>10}")
    rows = []
    for dens in (0.2, 0.3, 0.5, 1.0, 3.0, 10.0, 48.8):
        n = max(1, int(round(dens * sym_months)))
        if n > len(s):
            continue
        top = np.argsort(-s)[:n]
        hit = int(y[top].sum())
        prec = hit / n
        rec = hit / max(int(y.sum()), 1)
        rows.append({"density": dens, "n": n, "hits": hit,
                     "precision": round(prec, 4), "recall": round(rec, 4),
                     "lift_vs_base": round(prec / base, 1) if base else None})
        note = "  ← 你的标注密度" if dens in (0.2, 0.3) else (
               "  ← v9 的密度" if dens == 48.8 else "")
        print(f"{dens:>15.1f} {n:>9,} {hit:>9} {prec*100:>8.2f}% "
              f"{rec*100:>7.2f}% {prec/base if base else 0:>9.1f}x{note}")
    print(f"{'基础率':>15} {'':>9} {'':>9} {base*100:>8.4f}%")

    at_owner = next((r for r in rows if r["density"] == 0.3), None)
    if at_owner is None:
        verdict = "无法在 owner 密度下取样"
    elif at_owner["hits"] == 0:
        verdict = (f"在你的标注密度(0.3 条/币·月,取前 {at_owner['n']} 根)下,"
                   f"模型挑中的金标数为 0 —— 因果特征无法在这个密度上定位你的标注。")
    else:
        verdict = (f"在你的标注密度下精度 {at_owner['precision']*100:.2f}%,"
                   f"是基础率的 {at_owner['lift_vs_base']:.0f} 倍,"
                   f"覆盖 {at_owner['recall']*100:.1f}% 的金标 —— "
                   f"因果窗口里{'有可用信息' if at_owner['lift_vs_base'] and at_owner['lift_vs_base'] > 10 else '信息很弱'}")
    print(f"\n判读: {verdict}")
    print("注:负样本是「其余所有 bar」,不是挑出来的,所以这个数不随采样口径变化。"
          "\n    这测的是「标注能否被因果定位」,不是「定位了能不能赚钱」。")

    (PROJECT / "analysis" / "output" / "diag_tip_precision_at_owner_density.json").write_text(
        json.dumps({"n_gold": n_gold, "n_symbols": len(gold), "n_bars": len(d),
                    "base_rate": base, "test_symbol_months": round(sym_months, 1),
                    "curve": rows, "verdict": verdict},
                   indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
