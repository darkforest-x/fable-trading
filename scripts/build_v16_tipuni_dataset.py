#!/usr/bin/env python3
"""v16 unified-pipeline dataset: every image is a FRESH render from one code path.

Why (analysis/p_v15_dataset_confound.md): in v14/v15 the positives were fresh
`_pad200` re-renders while the negatives kept archive PNGs from months-old
renderers/data — a label-correlated style cue (the pad200 builder itself guards
archive!=kline drift with a MAD gate). A detector can separate fresh-vs-archive
pixels without ever learning dense semantics, which matches v15's signature:
val mAP 0.72, yet 58% false fire on real empty tips and 0/6 on real dense tips.

Recipe (owner approved 2026-07-23):
  POS  = v14 pad200 positives, copied as-is (already fresh renders from the
         thrice-debugged builder: wrong-window MAD gate + box-OHLC checks).
  NEG  = every v14 no-box stem RE-RENDERED from current klines with the current
         renderer over the SAME window (geometry identity; pixels same-era).
         Window convention (start vs end-incl stem index) is disambiguated per
         stem by MAD against the stored archive PNG; ambiguous stems are
         dropped and logged, never guessed.
  NEG+ = live-collected real tip empties (v13_real_tip_preview provisional
         tip-empty-ok; pre-owner-review, train split only, tagged `_livetip`).

Val keeps the v14 split; promotion is NEVER judged on this val (real-tip
golden set + tip-smoke only — see p_v15_dataset_confound.md gate spec).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_v16_tipuni_dataset.py --limit 40   # smoke
  PYTHONPATH=. .venv/bin/python scripts/build_v16_tipuni_dataset.py             # full
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_stem  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from scripts.build_htip_dataset import WINDOW, parse_stem, resolve_series  # noqa: E402

SRC = PROJECT / "datasets" / "dense_owner_v14_pad200"
OUT = PROJECT / "datasets" / "dense_owner_v16_tipuni"
LIVE_EMPTY_DIR = PROJECT / "analysis" / "output" / "v13_real_tip_preview"

# Wrong window renders a different price series entirely -> MAD explodes (40+).
# Archive drift on the RIGHT window stays modest; 12 accepts drift, rejects
# wrong-window (pad200 builder used 5.0 against its own fresh-era archives).
MAX_NEG_STORED_MAD = 12.0
# Single-entry cache: stems process in alphabetical (per-symbol) order, and a
# full 300-symbol MA-frame cache would blow past 16GB on the build Mac.
_ma_cache: dict[str, "object"] = {}


def _enriched(symbol: str):
    if symbol not in _ma_cache:
        _ma_cache.clear()
        df = resolve_series(symbol)
        _ma_cache[symbol] = None if df is None or len(df) < WINDOW + 50 else add_mas(df)
    return _ma_cache[symbol]


def _render_window(enriched, start: int) -> np.ndarray | None:
    if start < 0 or start + WINDOW > len(enriched):
        return None
    sub = enriched.iloc[start : start + WINDOW].reset_index(drop=True)
    img, _ = render_chart(sub, out_path=None)
    return img


def rebuild_negative(stem: str, stored_png: Path) -> tuple[np.ndarray, str, float] | tuple[None, str, float]:
    """Fresh render of a no-box stem's window; convention picked by archive MAD."""
    parsed = parse_stem(stem)
    if parsed is None:
        return None, "unparsable_stem", -1.0
    symbol, idx = parsed
    enriched = _enriched(symbol)
    if enriched is None:
        return None, "no_series", -1.0
    stored = cv2.imread(str(stored_png))
    if stored is None:
        return None, "no_stored_png", -1.0
    best: tuple[np.ndarray, str, float] | None = None
    for conv, start in (("start", idx), ("end_incl", idx - WINDOW + 1)):
        img = _render_window(enriched, start)
        if img is None or img.shape != stored.shape:
            continue
        mad = float(np.abs(img.astype(np.int16) - stored.astype(np.int16)).mean())
        if best is None or mad < best[2]:
            best = (img, conv, mad)
    if best is None:
        return None, "window_out_of_range", -1.0
    if best[2] > MAX_NEG_STORED_MAD:
        return None, f"mad_too_high_{best[1]}", best[2]
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="negatives per split (0 = all); smoke runs")
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    src, out = Path(args.src), Path(args.out)
    if not src.exists():
        raise SystemExit(f"missing source dataset: {src}")
    if out.exists():
        shutil.rmtree(out)
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    stats: dict[str, int] = {}
    skips: list[dict] = []
    mads: list[float] = []

    def bump(key: str) -> None:
        stats[key] = stats.get(key, 0) + 1

    for split in ("train", "val"):
        n_neg = 0
        for lbl in sorted((src / "labels" / split).glob("*.txt")):
            stem = lbl.stem
            if is_eval_stem(stem):
                bump(f"{split}_eval_excluded")
                continue
            png = src / "images" / split / f"{stem}.png"
            if not png.exists():
                bump(f"{split}_no_png")
                continue
            if lbl.read_text().strip():
                # v14's VAL positives were never tip-aligned (that was v15's
                # whole experiment) -- owner caught a mid-window val box in the
                # sample gallery on 2026-07-23. Val positives come from v15
                # tipval instead (below); only TRAIN positives copy from v14.
                if split == "val":
                    bump("val_pos_skipped_untipped")
                    continue
                shutil.copy2(png, out / "images" / split / png.name)
                shutil.copy2(lbl, out / "labels" / split / lbl.name)
                bump(f"{split}_pos")
                continue
            if args.limit and n_neg >= args.limit:
                continue
            img, conv, mad = rebuild_negative(stem, png)
            if img is None:
                bump(f"{split}_neg_skipped")
                skips.append({"stem": stem, "split": split, "reason": conv, "mad": mad})
                continue
            cv2.imwrite(str(out / "images" / split / f"{stem}.png"), img)
            (out / "labels" / split / f"{stem}.txt").write_text("")
            mads.append(mad)
            bump(f"{split}_neg_rerendered")
            n_neg += 1

    # Val positives: v15 tipval's tip-aligned pad200 renders (803).
    v15_val = PROJECT / "datasets" / "dense_owner_v15_tipval"
    for lbl in sorted((v15_val / "labels" / "val").glob("*.txt")):
        if not lbl.read_text().strip():
            continue
        png = v15_val / "images" / "val" / f"{lbl.stem}.png"
        if not png.exists() or is_eval_stem(lbl.stem):
            continue
        shutil.copy2(png, out / "images" / "val" / png.name)
        shutil.copy2(lbl, out / "labels" / "val" / lbl.name)
        bump("val_pos")

    # Live-collected real tip empties -> train negatives. The preview PNGs
    # carry review overlays (tip line / rule / YOLO boxes) and must NEVER be
    # trained on -- re-render each window CLEAN from klines via the same
    # pipeline. owner_class wins over provisional when the sheet is filled.
    live_added = 0
    sheet = LIVE_EMPTY_DIR / "review_sheet.csv"
    if sheet.exists():
        import csv

        import pandas as pd

        with sheet.open() as fh:
            for row in csv.DictReader(fh):
                cls = (row.get("owner_class") or "").strip() or row.get("provisional_class", "")
                if cls != "tip-empty-ok":
                    continue
                symbol = row["symbol"]
                enriched = _enriched(symbol)
                if enriched is None:
                    skips.append({"stem": symbol, "split": "train", "reason": "livetip_no_series", "mad": -1.0})
                    continue
                ts = pd.Timestamp(row["signal_time"])
                times = pd.to_datetime(enriched["open_time"], utc=True)
                hits = np.flatnonzero(times == (ts.tz_localize("UTC") if ts.tzinfo is None else ts))
                if len(hits) == 0:
                    skips.append({"stem": symbol, "split": "train", "reason": "livetip_bar_missing", "mad": -1.0})
                    continue
                img = _render_window(enriched, int(hits[0]) - WINDOW + 1)
                if img is None:
                    skips.append({"stem": symbol, "split": "train", "reason": "livetip_window_short", "mad": -1.0})
                    continue
                name = f"{symbol}_{ts.strftime('%Y%m%d_%H%M')}_livetip"
                cv2.imwrite(str(out / "images" / "train" / f"{name}.png"), img)
                (out / "labels" / "train" / f"{name}.txt").write_text("")
                live_added += 1
    stats["train_neg_livetip"] = live_added

    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n"
    )
    meta = {
        "recipe": "v16 tipuni: fresh-render everything through one pipeline",
        "src": str(src),
        "stats": stats,
        "neg_archive_mad": {
            "n": len(mads),
            "mean": round(float(np.mean(mads)), 3) if mads else None,
            "p95": round(float(np.percentile(mads, 95)), 3) if mads else None,
        },
        "n_skipped": len(skips),
        "confound_doc": "analysis/p_v15_dataset_confound.md",
    }
    (out / "v16_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    (out / "v16_skips.json").write_text(json.dumps(skips, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
