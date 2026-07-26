#!/usr/bin/env python3
"""Rebuild the short tip YOLO dataset with a corrected target (owner-approved).

v1 (`build_owner_side_short_yolo_tip.py`) took each owner box's RIGHT EDGE
(cut_global) as the tip. Measured afterwards, that bar is dense only 1.4% of the
time, sits a median 10 bars after the local fast_spread trough, and has the
bundle already expanding 97.6% of the time. The detector trained on it reproduced
exactly that -- firing ~10 bars past the cluster -- and the owner's gold review
scored it 18.3% (51 keep / 228 drop, n=279).

Two corrections, both approved by the owner 2026-07-27:

1. ANCHOR the tip at the local fast_spread trough in [cut-24, cut] instead of at
   cut_global. A human drags a box left-to-right, so its right edge is where the
   hand stopped, not where the pattern peaked.
2. KEEP only boxes that are actually dense at that anchor under the EXPANDED
   preset (fast<=0.0045, full<=0.0088). Splitting the strict gate showed the
   mismatch is entirely full_spread: at the trough, fast<=0.0028 passes 83.5% but
   full<=0.0055 only 39.2% -- the owner's eye accepts a pinched fast bundle while
   the slow MAs are still apart. Expanded is what CLAUDE.md already records as
   the mainline pool; strict was retired for the judgment layer and only the
   detector kept using it.

Expected coverage from the pre-check: 1361 short boxes -> ~60% qualify (~817).
Everything else (window crop, box geometry via segment_to_bbox, time split,
holdout drop) is inherited from v1 unchanged, so this is a single-variable
change to the TARGET only.

v1 is left untouched so the published v1b numbers stay reproducible.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip_v2.py --limit 40
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo_tip_v2.py
"""
from __future__ import annotations

import argparse
import json
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
OUT = PROJECT / "datasets/dense_owner_side_short_tip_v2"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")
VAL_CUT = pd.Timestamp("2026-02-01", tz="UTC")

# Owner-approved 2026-07-27: expanded preset, mirroring src/judgment/candidates.py.
FAST_MAX, FULL_MAX = 0.0045, 0.0088
ANCHOR_LOOKBACK = 24          # search window for the fast_spread trough
WARMUP = 200


def _git_desc() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def build_symbol_cache(sym: str, cache: dict) -> tuple | None:
    """Return (frame_with_mas, fast, full, times) or None."""
    if sym in cache:
        return cache[sym]
    base = resolve_series(sym)
    if base is None:
        cache[sym] = None
        return None
    try:
        framed = add_mas(base)
        ind = add_features(add_indicators(base))
        if len(ind) != len(framed):
            cache[sym] = None
            return None
        entry = (framed,
                 ind["fast_spread"].to_numpy(dtype=float),
                 ind["full_spread"].to_numpy(dtype=float),
                 pd.to_datetime(framed["open_time"], utc=True))
    except Exception:  # noqa: BLE001
        cache[sym] = None
        return None
    cache[sym] = entry
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="cap rows (smoke test)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    sheet = pd.read_csv(SHEET)
    rows = sheet[sheet["owner_side"].astype(str).str.strip() == "short"].copy()
    rows["cut_global"] = pd.to_numeric(rows["cut_global"], errors="coerce")
    if args.limit:
        rows = rows.head(args.limit)
    print(f"owner short rows: {len(rows)}")

    out = args.out
    if out.exists():
        shutil.rmtree(out)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    cache: dict = {}
    kept = {"train": 0, "val": 0}
    skips = {"no_series": 0, "oob": 0, "holdout": 0, "not_dense": 0,
             "window": 0, "box": 0, "error": 0, "dup_anchor": 0}
    shifts, right_fracs, anchor_fast, anchor_full = [], [], [], []
    # Two owner boxes that sit close together re-anchor to the SAME trough --
    # they describe one dense event, not two. Keep the widest span rather than
    # letting whichever row came last silently overwrite the file.
    seen_width: dict[str, int] = {}

    for _, r in rows.iterrows():
        sym = str(r["symbol"])
        entry = build_symbol_cache(sym, cache)
        if entry is None:
            skips["no_series"] += 1
            continue
        framed, fast, full, times = entry
        cut = r["cut_global"]
        if not np.isfinite(cut):
            skips["oob"] += 1
            continue
        ci = int(cut)
        if ci < WARMUP or ci >= len(framed):
            skips["oob"] += 1
            continue

        # --- correction 1: anchor at the fast_spread trough, not the box edge
        lo = max(WARMUP, ci - ANCHOR_LOOKBACK)
        seg = fast[lo:ci + 1]
        if not np.isfinite(seg).any():
            skips["oob"] += 1
            continue
        anchor = lo + int(np.nanargmin(seg))

        if times.iloc[anchor] >= HOLDOUT:
            skips["holdout"] += 1
            continue
        # --- correction 2: must be dense AT the anchor under the expanded preset
        if not (fast[anchor] <= FAST_MAX and full[anchor] <= FULL_MAX):
            skips["not_dense"] += 1
            continue

        tip_start = anchor - WINDOW + 1
        if tip_start < 0:
            skips["window"] += 1
            continue
        tip_sub = framed.iloc[tip_start:anchor + 1].reset_index(drop=True)
        if len(tip_sub) != WINDOW:
            skips["window"] += 1
            continue

        split = "train" if times.iloc[anchor] < VAL_CUT else "val"
        stem = f"{sym}_{anchor:06d}"
        width = max(1, int(r["bar_b1"]) - int(r["bar_b0"]))
        if stem in seen_width:
            skips["dup_anchor"] += 1
            if width <= seen_width[stem]:
                continue          # already have an equal-or-wider span
            kept[split] -= 1      # this row supersedes the stored one
        img_p = out / "images" / split / f"{stem}.png"
        lbl_p = out / "labels" / split / f"{stem}.txt"
        try:
            _, tip_tf = render_chart(tip_sub, out_path=img_p)
            t1 = WINDOW - 1
            box = segment_to_bbox(tip_sub, DenseSegment(start=max(0, t1 - width), end=t1), tip_tf)
            if box is None:
                skips["box"] += 1
                img_p.unlink(missing_ok=True)
                continue
            xc, yc, w, h = box
            lbl_p.write_text(f"0 {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
        except Exception:  # noqa: BLE001
            skips["error"] += 1
            img_p.unlink(missing_ok=True)
            continue

        kept[split] += 1
        seen_width[stem] = width
        shifts.append(ci - anchor)
        right_fracs.append(float(xc + w / 2))
        anchor_fast.append(float(fast[anchor]))
        anchor_full.append(float(full[anchor]))

    (out / "data.yaml").write_text(
        "# Owner short-side boxes, tip re-anchored to the fast_spread trough and\n"
        f"# filtered to the EXPANDED preset (fast<={FAST_MAX}, full<={FULL_MAX}).\n"
        "# Tip = image right edge. Time split at 2026-02-01. Holdout dropped.\n"
        f"path: {out}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: dense_cluster\n"
        "nc: 1\n", encoding="utf-8")

    total = kept["train"] + kept["val"]
    meta = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "script": "scripts/build_owner_side_short_yolo_tip_v2.py",
        "git": _git_desc(),
        "source_sheet": str(SHEET.relative_to(PROJECT)),
        "corrections_vs_v1": [
            "tip anchored at local fast_spread trough in [cut-24, cut], not cut_global",
            f"kept only boxes dense at the anchor under expanded preset "
            f"(fast<={FAST_MAX}, full<={FULL_MAX})",
        ],
        "owner_approval": "2026-07-27 (全做)",
        "window": WINDOW, "val_cut": str(VAL_CUT), "holdout_cut": str(HOLDOUT),
        "n_short_rows_raw": int(len(rows)),
        "n_images": kept, "n_total": total,
        "dedup_note": "rows whose anchor collided with an existing tip are counted "
                      "in skips.dup_anchor; the widest span wins",
        "coverage_pct": round(total / max(len(rows), 1) * 100, 1),
        "skips": skips,
        "anchor_shift_bars": {
            "p50": float(np.median(shifts)) if shifts else None,
            "p90": float(np.percentile(shifts, 90)) if shifts else None},
        "anchor_spread": {
            "fast_p50": float(np.median(anchor_fast)) if anchor_fast else None,
            "fast_max": float(np.max(anchor_fast)) if anchor_fast else None,
            "full_p50": float(np.median(anchor_full)) if anchor_full else None,
            "full_max": float(np.max(anchor_full)) if anchor_full else None},
        "box_right_frac": {
            "p50": float(np.median(right_fracs)) if right_fracs else None,
            "min": float(np.min(right_fracs)) if right_fracs else None},
    }
    (out / "build_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    print(f"\n输出: {out}")
    print(f"  train {kept['train']}  val {kept['val']}  合计 {total} "
          f"({meta['coverage_pct']}% of {len(rows)})")
    print(f"  skips: {skips}")
    print(f"  锚点后移: p50={meta['anchor_shift_bars']['p50']} 根")
    print(f"  锚点处 spread: fast p50={meta['anchor_spread']['fast_p50']:.5f} "
          f"(max {meta['anchor_spread']['fast_max']:.5f} <= {FAST_MAX}) / "
          f"full p50={meta['anchor_spread']['full_p50']:.5f} "
          f"(max {meta['anchor_spread']['full_max']:.5f} <= {FULL_MAX})")
    print(f"  框右缘: p50={meta['box_right_frac']['p50']:.4f} "
          f"min={meta['box_right_frac']['min']:.4f}  (应贴近 1.0)")
    print("\n按构造,本数据集每个 tip 都在 expanded 预设下算密集 —— "
          "这是 v1 的 1.4% 对照的那个数。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
