#!/usr/bin/env python3
"""Add empty-label negatives to dense_owner_w20_midbox.

Same render pipeline as positives (full-series MA → slice → render_chart).
Only difference is the empty YOLO label. Avoids the v14 confound where
positives were re-rendered and negatives were old-style PNGs.

Sampling:
  - Same symbols / train-val symbol split as positives (from w20_manifest).
  - Window length W ∈ {20..30} (same as positives).
  - Window must NOT overlap any known positive mid±half (+ margin).
  - Target ratio: ~1.0 empty-bg per positive (override with --ratio).

Usage:
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/add_w20_midbox_negatives.py
  PYTHONPATH=.:../yoyo-trading .venv/bin/python scripts/add_w20_midbox_negatives.py --ratio 1.0 --preview 4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_w20_midbox_dataset import (  # noqa: E402
    WIN_MAX,
    WIN_MIN,
    resolve_series,
    stable_seed,
)

DEFAULT_DS = PROJECT / "datasets" / "dense_owner_w20_midbox"
MARGIN = 15  # bars: keep neg windows away from known small-box cores
MAX_TRIES = 80


def forbidden_intervals(mids: list[int], half_max: int = 3) -> list[tuple[int, int]]:
    """Bars that may not appear inside a negative window."""
    spans = []
    for m in mids:
        spans.append((m - half_max - MARGIN, m + half_max + MARGIN))
    spans.sort()
    if not spans:
        return []
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def overlaps_forbidden(w0: int, w1: int, forb: list[tuple[int, int]]) -> bool:
    for a, b in forb:
        if not (w1 < a or w0 > b):
            return True
    return False


def pick_window(
    n: int,
    forb: list[tuple[int, int]],
    rng: np.random.Generator,
) -> tuple[int, int] | None:
    for _ in range(MAX_TRIES):
        wlen = int(rng.integers(WIN_MIN, WIN_MAX + 1))
        if n < wlen + 50:
            return None
        # leave some headroom for MA warmup context (already on full series)
        w0 = int(rng.integers(0, n - wlen + 1))
        w1 = w0 + wlen - 1
        if not overlaps_forbidden(w0, w1, forb):
            return w0, wlen
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DS)
    ap.add_argument("--ratio", type=float, default=1.0, help="neg / pos per split")
    ap.add_argument("--seed", type=int, default=20260807)
    ap.add_argument("--preview", type=int, default=0)
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=PROJECT / "analysis" / "output" / "w20_midbox_neg_preview",
    )
    args = ap.parse_args()
    ds = args.dataset
    man_path = ds / "w20_manifest.json"
    manifest = json.loads(man_path.read_text())

    by_sym_split: dict[tuple[str, str], list[dict]] = defaultdict(list)
    mids_by_sym: dict[str, list[int]] = defaultdict(list)
    for r in manifest:
        by_sym_split[(r["symbol"], r["split"])].append(r)
        mids_by_sym[r["symbol"]].append(int(r["mid_global"]))

    targets = {"train": 0, "val": 0}
    for split in ("train", "val"):
        n_pos = sum(1 for r in manifest if r["split"] == split)
        targets[split] = int(round(n_pos * args.ratio))

    # existing neg count
    existing = {"train": 0, "val": 0}
    for split in ("train", "val"):
        for p in (ds / "labels" / split).glob("*_neg*.txt"):
            if not p.read_text().strip():
                existing[split] += 1

    need = {s: max(0, targets[s] - existing[s]) for s in targets}
    print(f"pos train={sum(1 for r in manifest if r['split']=='train')} "
          f"val={sum(1 for r in manifest if r['split']=='val')}")
    print(f"neg existing={existing} need={need}")

    if args.preview > 0:
        args.preview_dir.mkdir(parents=True, exist_ok=True)

    made = {"train": 0, "val": 0}
    neg_manifest: list[dict] = []
    # round-robin symbols within each split for diversity
    for split in ("train", "val"):
        if need[split] <= 0:
            continue
        syms = sorted({sym for (sym, sp) in by_sym_split if sp == split})
        if not syms:
            continue
        rng_order = np.random.default_rng(stable_seed(args.seed, "order", split))
        rng_order.shuffle(syms)
        si = 0
        fails = 0
        while made[split] < need[split] and fails < need[split] * 20:
            sym = syms[si % len(syms)]
            si += 1
            df = resolve_series(sym)
            if df is None:
                fails += 1
                continue
            enriched = add_mas(df)
            n = len(enriched)
            forb = forbidden_intervals(mids_by_sym[sym])
            local = np.random.default_rng(
                stable_seed(args.seed, "neg", split, sym, made[split])
            )
            picked = pick_window(n, forb, local)
            if picked is None:
                fails += 1
                continue
            w0, wlen = picked
            win_df = enriched.iloc[w0 : w0 + wlen].reset_index(drop=True)
            stem = f"{sym}_{w0:06d}_w{wlen}_neg"
            out_img = ds / "images" / split / f"{stem}.png"
            out_lbl = ds / "labels" / split / f"{stem}.txt"
            if out_img.exists():
                # already have this exact window; count as done
                if not out_lbl.exists():
                    out_lbl.write_text("")
                made[split] += 1
                continue
            img, _tf = render_chart(win_df, out_path=None)
            out_img.parent.mkdir(parents=True, exist_ok=True)
            out_lbl.parent.mkdir(parents=True, exist_ok=True)
            if args.preview > 0 and made[split] < args.preview and split == "train":
                # draw nothing; just caption
                vis = img.copy()
                cv2.putText(
                    vis,
                    f"NEG {sym} W{wlen} start={w0}",
                    (8, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (20, 20, 20),
                    2,
                    cv2.LINE_AA,
                )
                cv2.imwrite(str(args.preview_dir / f"{stem}.png"), vis)
            cv2.imwrite(str(out_img), img)
            out_lbl.write_text("")  # empty = background
            made[split] += 1
            fails = 0
            neg_manifest.append(
                {
                    "stem": stem,
                    "symbol": sym,
                    "split": split,
                    "win_start": w0,
                    "win_len": wlen,
                    "kind": "empty_bg",
                }
            )
            if made[split] % 100 == 0:
                print(f"  {split} neg {made[split]}/{need[split]}", flush=True)

    # stats
    def _count(split: str) -> dict:
        imgs = list((ds / "images" / split).glob("*.png"))
        n_pos = n_neg = 0
        for im in imgs:
            lbl = ds / "labels" / split / f"{im.stem}.txt"
            if lbl.exists() and lbl.read_text().strip():
                n_pos += 1
            else:
                n_neg += 1
        return {"images": len(imgs), "pos": n_pos, "neg": n_neg}

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": "empty_bg_same_render_pipeline_W20_30",
        "dataset": str(ds),
        "ratio": args.ratio,
        "margin_bars": MARGIN,
        "made": made,
        "need": need,
        "train": _count("train"),
        "val": _count("val"),
        "n_neg_manifest": len(neg_manifest),
    }
    (ds / "w20_neg_summary.json").write_text(json.dumps(summary, indent=2))
    (ds / "w20_neg_manifest.json").write_text(json.dumps(neg_manifest, indent=2))
    # refresh data.yaml path absolute
    (ds / "data.yaml").write_text(
        f"path: {ds.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"names:\n  0: dense_start\n"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
