#!/usr/bin/env python3
"""Short tip dataset v3: same positives as v2, plus the NEGATIVES v1/v2 never had.

The actual cause of v1b's 18% gold precision, found after v2 failed to move it:

    dense_owner_side_short_tip      1361 images, 1361 with a box, 0 empty
    dense_owner_side_short_tip_v2    765 images,  765 with a box, 0 empty
    dense_owner_v16_tipuni          9244 images, 3438 with a box, 5806 empty (63%)

Every training image had a cluster at the right edge, so "always draw a box at
the right edge" is an optimal policy that scores well on val and learns nothing
about density. That is exactly the observed behaviour: v1b fires on 100% of the
owner's review pack and v2 on 96.9%, including the boxes the owner rejected.
v2's re-anchoring fixed a real defect (the tip sat ~10 bars into the expansion)
but it was the secondary one -- with no negatives there was nothing to fix.

v3 keeps v2's positives unchanged and adds two kinds of negative, both rendered
through the same tip-window path so pixels stay same-era (the confound that
sank v15 -- see p_v15_dataset_confound.md):

  HARD  the owner's own rejections. Reviewing v1b they marked boxes drop, i.e.
        "the detector fired here and it is not a cluster". Those are exactly the
        mistakes to train against, and they are already labelled.
  EASY  random tip windows that FAIL the expanded preset at the right edge.
        These teach the base rate: most moments are not dense.

Target mix follows v16, the one detector dataset in this repo built with
negatives: roughly 60% empty.

Positives, window crop, box geometry, split and holdout drop are inherited from
v2 unchanged, so negatives are the single new variable.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip_v3.py --limit 40
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip_v3.py
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.auto_label import DenseSegment, segment_to_bbox  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import add_features  # noqa: E402
from scripts.build_htip_dataset import WINDOW, resolve_series  # noqa: E402

SHEET = PROJECT / "analysis/output/owner_side_review/review_sheet.csv"
GOLD_PACK = PROJECT / "analysis/output/owner_side_short_tip_v1b_detect1000"
OUT = PROJECT / "datasets/dense_owner_side_short_tip_v3"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")

FAST_MAX, FULL_MAX = 0.0045, 0.0088     # expanded preset (owner-approved)
ANCHOR_LOOKBACK, WARMUP = 24, 200
NEG_RATIO = 1.5                          # negatives per positive -> ~60% empty
SEED = 20260727


def _git() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


class Sym:
    """Cached per-symbol frames and spread arrays."""

    def __init__(self) -> None:
        self.cache: dict[str, tuple | None] = {}

    def get(self, sym: str):
        if sym in self.cache:
            return self.cache[sym]
        base = resolve_series(sym)
        if base is None:
            self.cache[sym] = None
            return None
        try:
            framed = add_mas(base)
            ind = add_features(add_indicators(base))
            if len(ind) != len(framed):
                self.cache[sym] = None
                return None
            self.cache[sym] = (framed,
                               ind["fast_spread"].to_numpy(dtype=float),
                               ind["full_spread"].to_numpy(dtype=float),
                               pd.to_datetime(framed["open_time"], utc=True))
        except Exception:  # noqa: BLE001
            self.cache[sym] = None
        return self.cache[sym]


def split_of(ts) -> str:
    return "train" if ts < VAL_CUT else "val"


def render_window(framed, tip: int, out_img: Path):
    start = tip - WINDOW + 1
    if start < 0:
        return None
    sub = framed.iloc[start:tip + 1].reset_index(drop=True)
    if len(sub) != WINDOW:
        return None
    _, tf = render_chart(sub, out_path=out_img)
    return sub, tf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    rng = random.Random(SEED)

    out = args.out
    if out.exists():
        shutil.rmtree(out)
    for s in ("train", "val"):
        (out / "images" / s).mkdir(parents=True, exist_ok=True)
        (out / "labels" / s).mkdir(parents=True, exist_ok=True)

    syms = Sym()
    kept = {"train": 0, "val": 0}
    negs = {"train": 0, "val": 0}
    skips = {"no_series": 0, "oob": 0, "holdout": 0, "not_dense": 0,
             "window": 0, "box": 0, "error": 0, "dup": 0}
    seen: dict[str, int] = {}
    used_tips: set[str] = set()

    # ---------- positives (identical rule to v2) ----------
    sheet = pd.read_csv(SHEET)
    rows = sheet[sheet["owner_side"].astype(str).str.strip() == "short"].copy()
    rows["cut_global"] = pd.to_numeric(rows["cut_global"], errors="coerce")
    if args.limit:
        rows = rows.head(args.limit)
    print(f"owner short rows: {len(rows)}")

    for _, r in rows.iterrows():
        sym = str(r["symbol"])
        e = syms.get(sym)
        if e is None:
            skips["no_series"] += 1
            continue
        framed, fast, full, times = e
        cut = r["cut_global"]
        if not np.isfinite(cut):
            skips["oob"] += 1
            continue
        ci = int(cut)
        if ci < WARMUP or ci >= len(framed):
            skips["oob"] += 1
            continue
        lo = max(WARMUP, ci - ANCHOR_LOOKBACK)
        seg = fast[lo:ci + 1]
        if not np.isfinite(seg).any():
            skips["oob"] += 1
            continue
        anchor = lo + int(np.nanargmin(seg))
        if times.iloc[anchor] >= HOLDOUT:
            skips["holdout"] += 1
            continue
        if not (fast[anchor] <= FAST_MAX and full[anchor] <= FULL_MAX):
            skips["not_dense"] += 1
            continue
        stem = f"{sym}_{anchor:06d}"
        width = max(1, int(r["bar_b1"]) - int(r["bar_b0"]))
        sp = split_of(times.iloc[anchor])
        if stem in seen:
            skips["dup"] += 1
            if width <= seen[stem]:
                continue
            kept[sp] -= 1
        img_p = out / "images" / sp / f"{stem}.png"
        try:
            got = render_window(framed, anchor, img_p)
            if got is None:
                skips["window"] += 1
                continue
            sub, tf = got
            t1 = WINDOW - 1
            box = segment_to_bbox(sub, DenseSegment(start=max(0, t1 - width), end=t1), tf)
            if box is None:
                skips["box"] += 1
                img_p.unlink(missing_ok=True)
                continue
            xc, yc, w, h = box
            (out / "labels" / sp / f"{stem}.txt").write_text(
                f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
        except Exception:  # noqa: BLE001
            skips["error"] += 1
            img_p.unlink(missing_ok=True)
            continue
        seen[stem] = width
        used_tips.add(stem)
        kept[sp] += 1

    n_pos = kept["train"] + kept["val"]
    print(f"positives: train {kept['train']}  val {kept['val']}  合计 {n_pos}")

    def write_negative(sym: str, framed, tip: int, ts, tag: str) -> bool:
        stem = f"{tag}_{sym}_{tip:06d}"
        if stem in used_tips:
            return False
        sp = split_of(ts)
        img_p = out / "images" / sp / f"{stem}.png"
        try:
            if render_window(framed, tip, img_p) is None:
                return False
        except Exception:  # noqa: BLE001
            img_p.unlink(missing_ok=True)
            return False
        (out / "labels" / sp / f"{stem}.txt").write_text("")   # empty = negative
        used_tips.add(stem)
        negs[sp] += 1
        return True

    # ---------- hard negatives: the owner's own rejections ----------
    n_hard = 0
    gp = GOLD_PACK / "review_sheet.csv"
    if gp.exists():
        g = pd.read_csv(gp)
        drops = g[g["owner_keep"].astype(str).str.strip() == "drop"]
        print(f"owner drop 记录: {len(drops)}")
        for _, r in drops.iterrows():
            sym = str(r["symbol"])
            e = syms.get(sym)
            if e is None:
                continue
            framed, fast, full, times = e
            ts = pd.Timestamp(r["tip_time"])
            if ts >= HOLDOUT:
                continue
            tip = int(times.searchsorted(ts))
            if tip < WARMUP or tip >= len(framed):
                continue
            if write_negative(sym, framed, tip, times.iloc[tip], "neghard"):
                n_hard += 1
    print(f"hard negatives (owner drop): {n_hard}")

    # ---------- easy negatives: random NON-dense tip windows ----------
    want = int(n_pos * NEG_RATIO) - n_hard
    pool = [s for s in syms.cache if syms.cache[s] is not None]
    n_easy, guard = 0, 0
    while n_easy < want and guard < want * 60 and pool:
        guard += 1
        sym = rng.choice(pool)
        e = syms.get(sym)
        if e is None:
            continue
        framed, fast, full, times = e
        hi = len(framed) - 1
        if hi <= WARMUP + WINDOW:
            continue
        tip = rng.randint(WARMUP + WINDOW, hi)
        if times.iloc[tip] >= HOLDOUT:
            continue
        if not np.isfinite(fast[tip]) or not np.isfinite(full[tip]):
            continue
        if fast[tip] <= FAST_MAX and full[tip] <= FULL_MAX:
            continue                      # this IS dense — not a negative
        if write_negative(sym, framed, tip, times.iloc[tip], "negrand"):
            n_easy += 1
    print(f"easy negatives (random non-dense): {n_easy}")

    n_neg = negs["train"] + negs["val"]
    total = n_pos + n_neg
    (out / "data.yaml").write_text(
        "# Short tip v3 = v2 positives + negatives (hard: owner drops; easy: random non-dense).\n"
        f"# Expanded preset fast<={FAST_MAX} full<={FULL_MAX}. Tip = right edge.\n"
        f"path: {out}\n"
        "train: images/train\nval: images/val\n"
        "names:\n  0: dense_cluster\nnc: 1\n", encoding="utf-8")
    meta = {
        "generated_at": pd.Timestamp.utcnow().isoformat(), "git": _git(),
        "script": "scripts/build_owner_side_short_yolo_tip_v3.py",
        "why": "v1/v2 had 0 negatives -> detector learned 'always box the right edge'",
        "positives": kept, "negatives": negs,
        "n_pos": n_pos, "n_neg": n_neg, "n_total": total,
        "neg_share": round(n_neg / max(total, 1), 3),
        "hard_negatives_owner_drop": n_hard, "easy_negatives_random": n_easy,
        "skips": skips, "seed": SEED,
        "thresholds": {"FAST_MAX": FAST_MAX, "FULL_MAX": FULL_MAX},
    }
    (out / "build_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    print(f"\n输出 {out}")
    print(f"  正样本 {n_pos}  负样本 {n_neg}  合计 {total}  空图占比 {meta['neg_share']*100:.1f}%")
    print(f"  (v16 参照 62.8%;v1/v2 是 0%)")
    print(f"  train {kept['train']+negs['train']}  val {kept['val']+negs['val']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
