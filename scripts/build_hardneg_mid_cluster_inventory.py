#!/usr/bin/env python3
"""H-DET-2 inventory only: mid-window dense boxes with aftermath bars (CPU).

Hard-neg = GT dense boxes whose right edge is NOT tip-anchored, so the window
still contains bars *after* the cluster. These are the shapes the model loves
to fire on live (post-hoc), and the candidates we'd later mark empty / background.

Does NOT train, does NOT run YOLO, does NOT touch MPS/holdout/LIVE/promote.
Protocol for actual training: wait until owner_v13_pad200 finishes, then a
single-variable hard-neg add-on (owner must approve).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_hardneg_mid_cluster_inventory.py
  PYTHONPATH=. .venv/bin/python scripts/build_hardneg_mid_cluster_inventory.py \\
      --preview 10 --out-dir analysis/output/hardneg_mid_cluster
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = PROJECT / "datasets" / "dense_owner_v11"
DEFAULT_OUT = PROJECT / "analysis" / "output" / "hardneg_mid_cluster"
WIN_BARS = 200
# Mid-cluster with aftermath: right edge clearly left of tip band.
RIGHT_LO = 0.30
RIGHT_HI = 0.90  # exclusive of tip-ish (>=0.95 is tip-anchored)
MIN_BARS_AFTER = 8  # ~ (1-0.96)*200; require meaningful aftermath
STEM_RE = re.compile(
    r"^(?:okx_)?(?P<sym>[A-Z0-9]+)_USDT(?:_SWAP)?_(?P<end>\d+)$",
    re.I,
)


def parse_boxes(label_path: Path) -> list[tuple[float, float, float, float]]:
    """Return list of (cx, cy, w, h) YOLO-norm boxes; skip empty / comment lines."""
    text = label_path.read_text(errors="replace").strip()
    if not text:
        return []
    out: list[tuple[float, float, float, float]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            _, cx, cy, w, h = parts[:5]
            out.append((float(cx), float(cy), float(w), float(h)))
        except ValueError:
            continue
    return out


def stem_meta(stem: str) -> dict:
    m = STEM_RE.match(stem)
    if not m:
        return {"symbol_key": "", "window_end_i": None}
    return {
        "symbol_key": m.group("sym").upper(),
        "window_end_i": int(m.group("end")),
    }


def inventory_split(
    src: Path,
    split: str,
    *,
    right_lo: float,
    right_hi: float,
    min_bars_after: int,
    win_bars: int,
) -> list[dict]:
    lbl_dir = src / "labels" / split
    img_dir = src / "images" / split
    rows: list[dict] = []
    if not lbl_dir.is_dir():
        return rows
    for lbl in sorted(lbl_dir.glob("*.txt")):
        boxes = parse_boxes(lbl)
        if not boxes:
            continue
        meta = stem_meta(lbl.stem)
        img = img_dir / f"{lbl.stem}.png"
        for bi, (cx, cy, w, h) in enumerate(boxes):
            left = cx - w / 2
            right = cx + w / 2
            bars_after = max(0.0, (1.0 - right) * win_bars)
            bars_span = w * win_bars
            if not (right_lo <= right < right_hi):
                continue
            if bars_after < min_bars_after:
                continue
            rows.append(
                {
                    "stem": lbl.stem,
                    "split": split,
                    "box_i": bi,
                    "cx": round(cx, 6),
                    "cy": round(cy, 6),
                    "w": round(w, 6),
                    "h": round(h, 6),
                    "left": round(left, 6),
                    "right": round(right, 6),
                    "bars_span": round(bars_span, 2),
                    "bars_after": round(bars_after, 2),
                    "label_rel": str(lbl.relative_to(src)),
                    "image_rel": str(img.relative_to(src)) if img.exists() else "",
                    "image_exists": img.exists(),
                    **meta,
                }
            )
    # Prefer stronger aftermath + mid-er boxes for ranking
    rows.sort(key=lambda r: (-r["bars_after"], abs(r["right"] - 0.55)))
    return rows


def draw_preview(img_path: Path, row: dict, out_path: Path) -> None:
    img = cv2.imread(str(img_path))
    if img is None:
        raise FileNotFoundError(img_path)
    H, W = img.shape[:2]
    x1 = int((row["cx"] - row["w"] / 2) * W)
    x2 = int((row["cx"] + row["w"] / 2) * W)
    y1 = int((row["cy"] - row["h"] / 2) * H)
    y2 = int((row["cy"] + row["h"] / 2) * H)
    # cyan = GT mid cluster (hard-neg candidate); yellow dashed tip band
    cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 0), 2, cv2.LINE_AA)
    tip_x = int(0.95 * W)
    cv2.line(img, (tip_x, 0), (tip_x, H - 1), (0, 220, 255), 1, cv2.LINE_AA)
    caption = (
        f"HARDNEG mid  right={row['right']:.3f}  "
        f"after≈{row['bars_after']:.0f}bars  h={row['h']:.3f}"
    )
    cv2.putText(
        img,
        caption,
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (40, 40, 40),
        2,
        cv2.LINE_AA,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def write_protocol(out_dir: Path) -> None:
    text = """# H-DET-2 hard-neg mid-cluster — inventory only

## What this is
Candidates for **hard negative / empty-label mid windows**: dense GT boxes whose
right edge sits in mid-window (`right ∈ [0.30, 0.90)`), so ≥8 bars of aftermath
remain to the tip. These are the shapes that teach "wait for post-hoc context".

## What this is NOT
- Not a training set yet
- Not empty-label backgrounds copied by pad200 (those have *no* box)
- Not tip-anchored pad200 positives

## Train later (after v13 finishes) — single variable
1. Wait for `models/owner_v13_pad200.pt` (do not kill / steal MPS from v13).
2. Owner approves H-DET-2 experiment.
3. Build a small hard-neg add-on from this inventory (empty labels on the *same*
   mid-aftermath windows, or background class) **without** changing pad200
   positives / thresholds / TP-SL.
4. Finetune one short run from v13 (or v12) with only that add-on.
5. Judge on tip-smoke + mid-box rate — not val mAP alone.

## Reproduce inventory (CPU)
```bash
PYTHONPATH=. .venv/bin/python scripts/build_hardneg_mid_cluster_inventory.py
```
"""
    (out_dir / "PROTOCOL_train_after_v13.md").write_text(text)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--split", default="train", choices=("train", "val", "both"))
    ap.add_argument("--right-lo", type=float, default=RIGHT_LO)
    ap.add_argument("--right-hi", type=float, default=RIGHT_HI)
    ap.add_argument("--min-bars-after", type=int, default=MIN_BARS_AFTER)
    ap.add_argument("--preview", type=int, default=10, help="PNG overlays to write")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    splits = ["train", "val"] if args.split == "both" else [args.split]
    all_rows: list[dict] = []
    for sp in splits:
        all_rows.extend(
            inventory_split(
                args.src,
                sp,
                right_lo=args.right_lo,
                right_hi=args.right_hi,
                min_bars_after=args.min_bars_after,
                win_bars=WIN_BARS,
            )
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_protocol(args.out_dir)

    csv_path = args.out_dir / "hardneg_mid_cluster_candidates.csv"
    fields = [
        "stem",
        "split",
        "box_i",
        "symbol_key",
        "window_end_i",
        "cx",
        "cy",
        "w",
        "h",
        "left",
        "right",
        "bars_span",
        "bars_after",
        "label_rel",
        "image_rel",
        "image_exists",
    ]
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    # Diversity sample for previews: top aftermath, then stratified by symbol
    preview_dir = args.out_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    with_img = [r for r in all_rows if r.get("image_exists")]
    by_sym: dict[str, list[dict]] = {}
    for r in with_img:
        by_sym.setdefault(r.get("symbol_key") or "_", []).append(r)
    picks: list[dict] = []
    syms = list(by_sym.keys())
    rng.shuffle(syms)
    # round-robin for diversity, prefer high bars_after within symbol
    for sym in syms:
        by_sym[sym].sort(key=lambda r: -r["bars_after"])
    i = 0
    while len(picks) < args.preview and by_sym:
        sym = syms[i % len(syms)]
        bucket = by_sym.get(sym) or []
        if bucket:
            picks.append(bucket.pop(0))
            if not bucket:
                del by_sym[sym]
                syms = [s for s in syms if s in by_sym]
                if not syms:
                    break
                i = 0
                continue
        i += 1
        if i > 10000:
            break

    preview_manifest = []
    for j, row in enumerate(picks):
        src_img = args.src / row["image_rel"]
        out_img = preview_dir / f"{j:02d}_{row['stem']}_hardneg.png"
        draw_preview(src_img, row, out_img)
        preview_manifest.append(
            {
                "preview": str(out_img.relative_to(PROJECT)),
                "stem": row["stem"],
                "right": row["right"],
                "bars_after": row["bars_after"],
                "h": row["h"],
                "symbol_key": row.get("symbol_key"),
            }
        )

    rights = np.array([r["right"] for r in all_rows], dtype=float) if all_rows else np.array([])
    heights = np.array([r["h"] for r in all_rows], dtype=float) if all_rows else np.array([])
    afters = np.array([r["bars_after"] for r in all_rows], dtype=float) if all_rows else np.array([])
    sym_counts = Counter(r.get("symbol_key") or "?" for r in all_rows)

    def pct(a: np.ndarray, q: float) -> float | None:
        return float(np.percentile(a, q)) if len(a) else None

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hypothesis": "H-DET-2",
        "status": "inventory_only_train_after_v13",
        "src": str(args.src),
        "splits": splits,
        "filters": {
            "right_lo": args.right_lo,
            "right_hi": args.right_hi,
            "min_bars_after": args.min_bars_after,
            "win_bars": WIN_BARS,
            "note": "right in [lo,hi) AND bars_after >= min → mid cluster with aftermath",
        },
        "n_candidates": len(all_rows),
        "n_unique_stems": len({r["stem"] for r in all_rows}),
        "n_symbols": len(sym_counts),
        "top_symbols": sym_counts.most_common(15),
        "right": {"p10": pct(rights, 10), "p50": pct(rights, 50), "p90": pct(rights, 90)},
        "box_h": {"p10": pct(heights, 10), "p50": pct(heights, 50), "p90": pct(heights, 90)},
        "bars_after": {
            "p10": pct(afters, 10),
            "p50": pct(afters, 50),
            "p90": pct(afters, 90),
            "mean": float(afters.mean()) if len(afters) else None,
        },
        "csv": str(csv_path.relative_to(PROJECT)),
        "protocol": str((args.out_dir / "PROTOCOL_train_after_v13.md").relative_to(PROJECT)),
        "previews": preview_manifest,
        "next": "Do NOT train until v13 pad200 finishes + owner approves single-variable hard-neg add-on.",
    }
    summary_path = args.out_dir / "hardneg_mid_cluster_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"candidates={len(all_rows)} stems={summary['n_unique_stems']} syms={summary['n_symbols']}")
    print(f"bars_after p50={summary['bars_after']['p50']}  box_h p50={summary['box_h']['p50']}")
    print(f"wrote {csv_path}")
    print(f"wrote {summary_path}")
    print(f"previews={len(preview_manifest)} -> {preview_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
