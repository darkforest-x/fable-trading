#!/usr/bin/env python3
"""Short tip dataset v4: built from what the owner actually said, plus their stars.

Three earlier attempts optimised a target nobody had checked against the owner's
own labels. Measured on their 390 verdicts, the mechanical dense definition is
ANTI-correlated with their eye (keeps 31% dense vs drops 47%). Asked directly,
the owner described the pattern as 均线慢慢密集 + K线向下 -- the bundle converges
over the preceding bars, and at the tip price is breaking DOWN.

That description checks out, and it is the strongest signal measured so far:

    ret8   keep -0.69%  drop  0.00%   p=1.8e-10
    ret24  keep -0.88%  drop  0.00%   p=1.2e-07
    ret48  keep -0.77%  drop +0.11%   p=2.1e-07

`ret8 < 0` alone lifts precision on v1b's pool from 18.2% to 27.1%. And the
"converging" half was misread by me at first: at the tip the spread is already
WIDENING (keeps faster than drops, p=1e-5) because the breakdown has started.
Convergence belongs to the bars BEFORE the tip, not at it.

Meanwhile 31.9% of v2's short positives had price RISING at the tip -- a third
of the training set teaching the opposite of a short setup.

So v4 positives require all three:
  1. prior tightness  -- min fast_spread over [anchor-48, anchor] <= FAST_MAX
  2. price falling    -- ret8 < 0 at the anchor
  3. tip at the trough (v2's re-anchor, kept)

Positives come from two sources, reported separately so their value is visible:
  STAR  the 528 stems the owner tagged ⭐标杆 in Label Studio -- their own mark
        for the exemplary ones, found in output/label_studio/*.json
  SIDE  the short boxes from owner_side_review that pass the same three tests

Negatives are v3's, which stay: the owner's 319 drops (hard) plus random
non-dense tip windows (easy), targeting ~60% empty as in v16.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_star_tip_dataset_v4.py --limit 60
  PYTHONPATH=. .venv/bin/python scripts/build_star_tip_dataset_v4.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.auto_label import DenseSegment, segment_to_bbox  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import add_features  # noqa: E402
from scripts.build_htip_dataset import WINDOW, resolve_series  # noqa: E402
from scripts.build_crop_pad200_dataset import boxes_cut_and_spans, resolve_win_start  # noqa: E402

SHEET = PROJECT / "analysis/output/owner_side_review/review_sheet.csv"
GOLD_PACK = PROJECT / "analysis/output/owner_side_short_tip_v1b_detect1000"
LS_GLOB = str(PROJECT / "output/label_studio/*.json")
ARCHIVE_ROOTS = [PROJECT / "datasets/_deprecated_pretip/dense_owner_v11/images",
                 PROJECT / "datasets/_deprecated_pretip/dense_owner_v14_pad200/images"]
OUT = PROJECT / "datasets/dense_owner_short_star_tip_v5"

HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")
FAST_MAX, FULL_MAX = 0.0045, 0.0088
ANCHOR_LOOKBACK, PRIOR_LOOKBACK, WARMUP = 24, 48, 200
RET_BARS = 8                    # 「K线向下」 horizon
DROP_ATR_MIN = 1.0              # the fall must be worth at least this many ATR
NEG_RATIO = 1.5
SEED = 20260727
STAR_TAG = "⭐标杆"


def _git() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_star_boxes() -> dict[str, list[tuple[float, float, float, float]]]:
    """stem -> [(xc, yc, w, h) normalized] for boxes on ⭐标杆-tagged images."""
    out: dict[str, list] = {}
    for f in glob.glob(LS_GLOB):
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, list):
            continue
        for task in data:
            img = (task.get("data", {}) or {}).get("image") or ""
            stem = re.sub(r"\.(png|jpg)$", "", img.split("/")[-1].split("?")[0])
            if not stem:
                continue
            for ann in task.get("annotations", []) or []:
                res = ann.get("result", []) or []
                if not any(STAR_TAG in (r.get("value", {}).get("choices") or []) for r in res):
                    continue
                for r in res:
                    if r.get("type") != "rectanglelabels":
                        continue
                    v = r["value"]
                    # Label Studio stores percentages of the image
                    xc = (v["x"] + v["width"] / 2) / 100.0
                    yc = (v["y"] + v["height"] / 2) / 100.0
                    out.setdefault(stem, []).append(
                        (xc, yc, v["width"] / 100.0, v["height"] / 100.0))
    return out


def archive_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for root in ARCHIVE_ROOTS:
        for p in root.rglob("*.png"):
            idx.setdefault(p.stem, p)
    return idx


class Series:
    def __init__(self) -> None:
        self.c: dict[str, tuple | None] = {}

    def get(self, sym: str):
        if sym in self.c:
            return self.c[sym]
        base = resolve_series(sym)
        if base is None:
            self.c[sym] = None
            return None
        try:
            framed = add_mas(base)
            ind = add_features(add_indicators(base))
            if len(ind) != len(framed):
                self.c[sym] = None
                return None
            from src.detection.data import ALL_MA_COLS
            ma = np.vstack([framed[c].to_numpy(dtype=float)
                            for c in ALL_MA_COLS if c in framed.columns])
            self.c[sym] = (framed,
                           ind["fast_spread"].to_numpy(dtype=float),
                           ind["full_spread"].to_numpy(dtype=float),
                           ind["close"].to_numpy(dtype=float),
                           pd.to_datetime(framed["open_time"], utc=True),
                           np.nanmin(ma, axis=0),
                           ind["atr_pct"].to_numpy(dtype=float))
        except Exception:  # noqa: BLE001
            self.c[sym] = None
        return self.c[sym]


def symbol_of(stem: str, known: set[str]) -> str | None:
    raw = re.sub(r"_\d+$", "", stem)
    for cand in (raw, raw + "_SWAP", "okx_" + raw):
        if cand in known:
            return cand
    return None


def passes(fast, full, close, anchor: int, ma_min=None, atr=None) -> tuple[bool, dict]:
    """The owner's stated pattern, at the strictness their eye actually wants.

    v4 used `ret8 < 0`, i.e. merely lower than two hours ago. Shown the samples
    the owner said the boxes land 有点早, and measuring against their 390
    verdicts agrees -- later and more decisive is better:

        ret8 < 0                          218 hits, 27.1%  (1.49x base)
        close below all six MAs           163 hits, 33.1%  (1.82x)
        below all MAs AND fall > 1 ATR     96 hits, 42.7%  (2.35x)
        fall > 2 ATR                       47 hits, 51.1%  (2.80x)

    Taking below-all-MAs AND fall>1*ATR: both halves are things the owner
    described (the break is confirmed, and it is a real move rather than drift),
    and it keeps ~2x the sample of the 2-ATR cut, which would also push the tip
    so late that the entry is largely gone.
    """
    lo = max(WARMUP, anchor - PRIOR_LOOKBACK)
    prior = fast[lo:anchor + 1]
    prior_min = float(np.nanmin(prior)) if np.isfinite(prior).any() else np.inf
    ret = close[anchor] / close[anchor - RET_BARS] - 1 if anchor >= RET_BARS else np.nan
    below = bool(ma_min is not None and np.isfinite(ma_min) and close[anchor] < ma_min)
    a = float(atr) if atr is not None and np.isfinite(atr) and atr > 0 else np.nan
    drop_atr = ret / a if np.isfinite(ret) and np.isfinite(a) else np.nan
    ok = bool(prior_min <= FAST_MAX and below
              and np.isfinite(drop_atr) and drop_atr < -DROP_ATR_MIN)
    return ok, {"prior_min_fast": prior_min, "below_all_ma": below,
                "drop_atr": float(drop_atr) if np.isfinite(drop_atr) else None}


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

    ser = Series()
    from src.data.loader import list_series
    known = {s for (_src, s) in list_series(bar="15m")}
    arch = archive_index()
    stars = load_star_boxes()
    print(f"⭐标杆 stems: {len(stars)}  存档图可用: {sum(1 for s in stars if s in arch)}")

    kept = {"train": 0, "val": 0}
    negs = {"train": 0, "val": 0}
    src_count = {"star": 0, "side": 0}
    skips = {"no_symbol": 0, "no_series": 0, "no_window": 0, "oob": 0,
             "holdout": 0, "not_pattern": 0, "box": 0, "error": 0, "dup": 0}
    seen: dict[str, int] = {}
    used: set[str] = set()

    def emit_positive(sym: str, cut: int, width: int, tag: str) -> bool:
        e = ser.get(sym)
        if e is None:
            skips["no_series"] += 1
            return False
        framed, fast, full, close, times, ma_min, atrp = e
        if cut < WARMUP or cut >= len(framed):
            skips["oob"] += 1
            return False
        lo = max(WARMUP, cut - ANCHOR_LOOKBACK)
        seg = fast[lo:cut + 1]
        if not np.isfinite(seg).any():
            skips["oob"] += 1
            return False
        anchor = lo + int(np.nanargmin(seg))
        if times.iloc[anchor] >= HOLDOUT:
            skips["holdout"] += 1
            return False
        ok, _ = passes(fast, full, close, anchor, ma_min[anchor], atrp[anchor])
        if not ok:
            skips["not_pattern"] += 1
            return False
        start = anchor - WINDOW + 1
        if start < 0:
            skips["no_window"] += 1
            return False
        sub = framed.iloc[start:anchor + 1].reset_index(drop=True)
        if len(sub) != WINDOW:
            skips["no_window"] += 1
            return False
        stem = f"{sym}_{anchor:06d}"
        sp = "train" if times.iloc[anchor] < VAL_CUT else "val"
        if stem in seen:
            skips["dup"] += 1
            if width <= seen[stem]:
                return False
            kept[sp] -= 1
        img_p = out / "images" / sp / f"{stem}.png"
        try:
            _, tf = render_chart(sub, out_path=img_p)
            t1 = WINDOW - 1
            box = segment_to_bbox(sub, DenseSegment(start=max(0, t1 - width), end=t1), tf)
            if box is None:
                skips["box"] += 1
                img_p.unlink(missing_ok=True)
                return False
            xc, yc, w, h = box
            (out / "labels" / sp / f"{stem}.txt").write_text(
                f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
        except Exception:  # noqa: BLE001
            skips["error"] += 1
            img_p.unlink(missing_ok=True)
            return False
        seen[stem] = width
        used.add(stem)
        kept[sp] += 1
        src_count[tag] += 1
        return True

    # ---------- positives A: the owner's ⭐标杆 ----------
    items = list(stars.items())
    if args.limit:
        items = items[: args.limit]
    for stem, boxes in items:
        sym = symbol_of(stem, known)
        if sym is None:
            skips["no_symbol"] += 1
            continue
        e = ser.get(sym)
        if e is None:
            skips["no_series"] += 1
            continue
        framed = e[0]
        m = re.search(r"_(\d+)$", stem)
        if not m:
            skips["no_window"] += 1
            continue
        stored = None
        p = arch.get(stem)
        if p is not None:
            stored = cv2.imread(str(p))
        r = resolve_win_start(len(framed), int(m.group(1)), enriched=framed, stored_img=stored)
        if r is None:
            skips["no_window"] += 1
            continue
        _mode, win_start, _mad = r
        sub_old = framed.iloc[win_start:win_start + WINDOW].reset_index(drop=True)
        if len(sub_old) != WINDOW:
            skips["no_window"] += 1
            continue
        tf_old = make_chart_transform(sub_old)
        _cut_local, spans = boxes_cut_and_spans(boxes, tf_old)
        for b0, b1, *_ in spans:
            emit_positive(sym, win_start + b1, max(1, b1 - b0), "star")

    print(f"positives from ⭐标杆: {src_count['star']}")

    # ---------- positives B: side-review shorts passing the same three tests ----
    if SHEET.exists() and not args.limit:
        sh = pd.read_csv(SHEET)
        sh = sh[sh["owner_side"].astype(str).str.strip() == "short"]
        sh["cut_global"] = pd.to_numeric(sh["cut_global"], errors="coerce")
        for _, r in sh.iterrows():
            if not np.isfinite(r["cut_global"]):
                continue
            sym = symbol_of(str(r["stem"]), known) or str(r["symbol"])
            emit_positive(sym, int(r["cut_global"]),
                          max(1, int(r["bar_b1"]) - int(r["bar_b0"])), "side")
    n_pos = kept["train"] + kept["val"]
    print(f"positives from side-review: {src_count['side']}   合计 {n_pos}")

    # ---------- negatives (same recipe as v3) ----------
    def emit_negative(sym: str, tip: int, ts, tag: str) -> bool:
        e = ser.get(sym)
        if e is None:
            return False
        framed = e[0]
        stem = f"{tag}_{sym}_{tip:06d}"
        if stem in used:
            return False
        start = tip - WINDOW + 1
        if start < 0:
            return False
        sub = framed.iloc[start:tip + 1].reset_index(drop=True)
        if len(sub) != WINDOW:
            return False
        sp = "train" if ts < VAL_CUT else "val"
        img_p = out / "images" / sp / f"{stem}.png"
        try:
            render_chart(sub, out_path=img_p)
        except Exception:  # noqa: BLE001
            img_p.unlink(missing_ok=True)
            return False
        (out / "labels" / sp / f"{stem}.txt").write_text("")
        used.add(stem)
        negs[sp] += 1
        return True

    n_hard = 0
    gp = GOLD_PACK / "review_sheet.csv"
    if gp.exists() and not args.limit:
        g = pd.read_csv(gp)
        for _, r in g[g["owner_keep"].astype(str).str.strip() == "drop"].iterrows():
            sym = str(r["symbol"])
            e = ser.get(sym)
            if e is None:
                continue
            times = e[4]
            ts = pd.Timestamp(r["tip_time"])
            if ts >= HOLDOUT:
                continue
            tip = int(times.searchsorted(ts))
            if tip < WARMUP or tip >= len(e[0]):
                continue
            if emit_negative(sym, tip, times.iloc[tip], "neghard"):
                n_hard += 1

    want = max(0, int(n_pos * NEG_RATIO) - n_hard)
    pool = [s for s in ser.c if ser.c[s] is not None]
    n_easy, guard = 0, 0
    while n_easy < want and guard < want * 60 and pool:
        guard += 1
        sym = rng.choice(pool)
        e = ser.get(sym)
        if e is None:
            continue
        framed, fast, full, close, times, ma_min, atrp = e
        hi = len(framed) - 1
        if hi <= WARMUP + WINDOW:
            continue
        tip = rng.randint(WARMUP + WINDOW, hi)
        if times.iloc[tip] >= HOLDOUT:
            continue
        if not np.isfinite(fast[tip]) or not np.isfinite(full[tip]):
            continue
        ok, _ = passes(fast, full, close, tip, ma_min[tip], atrp[tip])
        if ok:
            continue                       # this matches the pattern — not a negative
        if emit_negative(sym, tip, times.iloc[tip], "negrand"):
            n_easy += 1

    n_neg = negs["train"] + negs["val"]
    total = n_pos + n_neg
    (out / "data.yaml").write_text(
        "# v4: owner-stated pattern (converged before + price falling at tip),\n"
        "# positives seeded from the owner's ⭐标杆 tags. Tip = right edge.\n"
        f"path: {out}\ntrain: images/train\nval: images/val\n"
        "names:\n  0: dense_cluster\nnc: 1\n", encoding="utf-8")
    meta = {"generated_at": pd.Timestamp.utcnow().isoformat(), "git": _git(),
            "script": "scripts/build_star_tip_dataset_v4.py",
            "owner_pattern": "均线慢慢密集(prior fast_spread<=FAST_MAX) + 跌破全部6均线 + 8根跌幅>1×ATR",
            "positives": kept, "by_source": src_count, "negatives": negs,
            "n_pos": n_pos, "n_neg": n_neg, "n_total": total,
            "neg_share": round(n_neg / max(total, 1), 3),
            "hard_negatives": n_hard, "easy_negatives": n_easy,
            "skips": skips, "seed": SEED,
            "thresholds": {"FAST_MAX": FAST_MAX, "FULL_MAX": FULL_MAX,
                           "RET_BARS": RET_BARS, "PRIOR_LOOKBACK": PRIOR_LOOKBACK,
                           "DROP_ATR_MIN": DROP_ATR_MIN}}
    (out / "build_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(f"\n输出 {out}")
    print(f"  正 {n_pos} (⭐{src_count['star']} + side {src_count['side']})  "
          f"负 {n_neg}  合计 {total}  空图 {meta['neg_share']*100:.1f}%")
    print(f"  skips: {skips}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
