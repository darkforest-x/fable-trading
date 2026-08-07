#!/usr/bin/env python3
"""Add hard-negative windows to dense_owner_w20_midbox (same render as pos).

Two sources (both empty YOLO labels):

  1. **dense_ma** — tip windows where full_spread is small (MA bundle tight)
     but far from any gold mid. Looks like "双均线粘合" without a labeled start.

  2. **weak_fire** — re-render tip windows from the 5d gallery weak fires
     (conf in [0.15, hard_conf) ). Model wanted to fire; we force empty so
     the next train learns to reject them. (Heuristic FP; not owner-labeled.)

Usage:
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/add_w20_hardneg_pack.py \\
      --n-dense 1500 --n-weak 800 --preview 4
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
_YOYO = Path.home() / "yoyo-trading"
for p in (PROJECT, _YOYO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.add_w20_midbox_negatives import (  # noqa: E402
    forbidden_intervals,
    overlaps_forbidden,
)
from scripts.build_w20_midbox_dataset import (  # noqa: E402
    WIN_MAX,
    WIN_MIN,
    resolve_series,
    stable_seed,
)
from src.detection.owner_eval import split_of, symbol_of  # noqa: E402

DEFAULT_DS = PROJECT / "datasets" / "dense_owner_w20_midbox"
GALLERY = PROJECT / "analysis" / "output" / "w20_midbox_5d_gallery" / "images"
PAT = re.compile(r"^(?P<sym>.+)_(?P<d>\d{8})_(?P<t>\d{4})_c(?P<c>[\d.]+)\.png$")
MARGIN = 15
# "dense" = full_spread below this (relative to close); tune from data
DENSE_SPREAD_MAX = 0.012
MAX_TRIES = 100


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DS)
    ap.add_argument("--n-dense", type=int, default=1500)
    ap.add_argument("--n-weak", type=int, default=800)
    ap.add_argument("--weak-conf-hi", type=float, default=0.30, help="weak fire conf < this")
    ap.add_argument("--weak-conf-lo", type=float, default=0.15)
    ap.add_argument("--window", type=int, default=24, help="fixed tip window for hardneg")
    ap.add_argument("--seed", type=int, default=20260807)
    ap.add_argument("--preview", type=int, default=0)
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT / "analysis" / "output" / "w20_hardneg_preview",
    )
    args = ap.parse_args()
    ds = args.dataset
    man = json.loads((ds / "w20_manifest.json").read_text())
    mids_by_sym: dict[str, list[int]] = defaultdict(list)
    split_by_sym: dict[str, str] = {}
    for r in man:
        mids_by_sym[r["symbol"]].append(int(r["mid_global"]))
        # use salted split already in dataset via existing images
        split_by_sym[r["symbol"]] = r["split"]

    # map symbol -> train/val from existing w20 files
    def split_of_sym(sym: str) -> str:
        if sym in split_by_sym:
            return split_by_sym[sym]
        # fallback: same salted rule as w20 re-split
        return split_of(f"{sym}_0")

    made = {"dense_train": 0, "dense_val": 0, "weak_train": 0, "weak_val": 0}
    preview_n = 0
    if args.preview:
        args.preview_dir.mkdir(parents=True, exist_ok=True)

    # --- source 1: dense MA ---
    rng = np.random.default_rng(args.seed)
    syms = sorted(mids_by_sym.keys())
    rng.shuffle(syms)
    dense_need = args.n_dense
    si = 0
    fails = 0
    while made["dense_train"] + made["dense_val"] < dense_need and fails < dense_need * 30:
        sym = syms[si % len(syms)]
        si += 1
        df = resolve_series(sym)
        if df is None:
            fails += 1
            continue
        en = add_mas(df)
        if "full_spread" not in en.columns:
            fails += 1
            continue
        n = len(en)
        w = int(args.window)
        forb = forbidden_intervals(mids_by_sym[sym])
        # candidates: bars with tight MA bundle
        spread = en["full_spread"].to_numpy(dtype=float)
        ok = np.where(
            (np.arange(n) >= w)
            & (np.arange(n) < n - 5)
            & np.isfinite(spread)
            & (spread > 0)
            & (spread <= DENSE_SPREAD_MAX)
        )[0]
        if len(ok) < 10:
            fails += 1
            continue
        # sample
        for _ in range(8):
            end_i = int(rng.choice(ok))
            w0 = end_i - w + 1
            if overlaps_forbidden(w0, end_i, forb):
                continue
            win = en.iloc[w0 : end_i + 1].reset_index(drop=True)
            if len(win) != w:
                continue
            split = split_of_sym(sym)
            stem = f"{sym}_{w0:06d}_w{w}_hardneg_dense"
            out_img = ds / "images" / split / f"{stem}.png"
            out_lbl = ds / "labels" / split / f"{stem}.txt"
            if out_img.exists():
                made[f"dense_{split}"] += 1
                break
            img, _ = render_chart(win, out_path=None)
            out_img.parent.mkdir(parents=True, exist_ok=True)
            out_lbl.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out_img), img)
            out_lbl.write_text("")
            made[f"dense_{split}"] += 1
            fails = 0
            if args.preview and preview_n < args.preview:
                vis = img.copy()
                cv2.putText(
                    vis,
                    f"HARD dense {sym} spread={float(spread[end_i]):.4f}",
                    (8, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (20, 20, 20),
                    2,
                    cv2.LINE_AA,
                )
                cv2.imwrite(str(args.preview_dir / f"{stem}.png"), vis)
                preview_n += 1
            break
        else:
            fails += 1
        if (made["dense_train"] + made["dense_val"]) % 100 == 0 and (
            made["dense_train"] + made["dense_val"]
        ) > 0:
            print(
                f"  dense {made['dense_train']+made['dense_val']}/{dense_need}",
                flush=True,
            )

    # --- source 2: weak gallery fires ---
    weak_cards = []
    if GALLERY.is_dir():
        for p in GALLERY.glob("*.png"):
            m = PAT.match(p.name)
            if not m:
                continue
            c = float(m.group("c"))
            if not (args.weak_conf_lo <= c < args.weak_conf_hi):
                continue
            d, t = m.group("d"), m.group("t")
            ts = pd.Timestamp(
                f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:]}:00", tz="UTC"
            )
            weak_cards.append((m.group("sym"), ts, c))
    rng.shuffle(weak_cards)
    weak_need = args.n_weak
    for sym, ts, conf in weak_cards:
        if made["weak_train"] + made["weak_val"] >= weak_need:
            break
        df = resolve_series(sym)
        if df is None:
            # try without _SWAP variants
            continue
        en = add_mas(df)
        times = pd.to_datetime(en["open_time"], utc=True)
        # nearest bar
        i = int(np.searchsorted(times, ts))
        if i >= len(en):
            i = len(en) - 1
        if i > 0 and abs((times.iloc[i] - ts).total_seconds()) > abs(
            (times.iloc[i - 1] - ts).total_seconds()
        ):
            i = i - 1
        w = int(args.window)
        if i < w - 1:
            continue
        w0 = i - w + 1
        forb = forbidden_intervals(mids_by_sym.get(sym, []))
        if overlaps_forbidden(w0, i, forb):
            # still allow — these are model FPs on live; gold may not cover
            pass
        win = en.iloc[w0 : i + 1].reset_index(drop=True)
        if len(win) != w:
            continue
        split = split_of_sym(sym)
        stem = f"{sym}_{w0:06d}_w{w}_hardneg_weak_c{conf:.3f}"
        out_img = ds / "images" / split / f"{stem}.png"
        out_lbl = ds / "labels" / split / f"{stem}.txt"
        if out_img.exists():
            continue
        img, _ = render_chart(win, out_path=None)
        out_img.parent.mkdir(parents=True, exist_ok=True)
        out_lbl.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_img), img)
        out_lbl.write_text("")
        made[f"weak_{split}"] += 1
        if args.preview and preview_n < args.preview + 4:
            vis = img.copy()
            cv2.putText(
                vis,
                f"HARD weak-fire conf={conf:.3f} {sym}",
                (8, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (20, 20, 20),
                2,
                cv2.LINE_AA,
            )
            cv2.imwrite(str(args.preview_dir / f"{stem}.png"), vis)
            preview_n += 1

    def count_split(split: str) -> dict:
        pos = neg = hard = 0
        for f in (ds / "labels" / split).glob("*.txt"):
            empty = not f.read_text().strip()
            if "hardneg" in f.stem:
                hard += 1
            elif empty:
                neg += 1
            else:
                pos += 1
        return {
            "images": pos + neg + hard,
            "pos": pos,
            "empty_bg": neg,
            "hardneg": hard,
        }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": "hardneg_dense_ma + hardneg_weak_fire_from_5d_gallery",
        "dense_spread_max": DENSE_SPREAD_MAX,
        "weak_conf": [args.weak_conf_lo, args.weak_conf_hi],
        "window": args.window,
        "made": made,
        "train": count_split("train"),
        "val": count_split("val"),
        "note": "empty labels; same render as positives; does not retrain automatically",
    }
    (ds / "w20_hardneg_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
