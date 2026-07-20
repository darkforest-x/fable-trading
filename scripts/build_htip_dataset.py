#!/usr/bin/env python3
"""Build H-TIP tip-truncated training clones from dense_owner_v11 (or any owner set).

For each *positive* train image with owner/GT boxes:
  1. Map box right edge → bar index in the original 200-bar window
  2. Re-render a window whose **last bar is that signal bar** (live tip geometry)
  3. Rewrite YOLO labels so the same bar span sits at the right edge
  4. Write as ``{stem}_tip.png`` into train only (val stays original-only)

Final dataset = original images (train+val) ∪ tip clones (train only).

Red lines: no holdout, no eval-symbol leak (stems already filtered in v11),
no flip/mosaic. Single variable: tip framing only.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_htip_dataset.py \\
      --src datasets/dense_owner_v11 --out datasets/dense_owner_v12_htip \\
      --limit 0
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_stem, symbol_of  # noqa: E402
from src.detection.render import (  # noqa: E402
    IMG_HEIGHT,
    IMG_WIDTH,
    make_chart_transform,
    render_chart,
)

WINDOW = 200
STEM_RE = re.compile(r"^(?:okx_)?(?P<body>.+?)_(?P<idx>\d{4,8})$")


@dataclass
class Stats:
    train_orig: int = 0
    val_orig: int = 0
    tip_ok: int = 0
    tip_skip_no_label: int = 0
    tip_skip_no_series: int = 0
    tip_skip_window: int = 0
    tip_skip_eval: int = 0
    tip_skip_error: int = 0


def parse_stem(stem: str) -> tuple[str, int] | None:
    m = STEM_RE.match(stem)
    if not m:
        return None
    body = m.group("body")
    # body may still be SYMBOL with _USDT_SWAP
    idx = int(m.group("idx"))
    return body, idx


def bar_from_x(tf, x: float) -> int:
    if tf.n_bars <= 1 or tf.plot_w <= 0:
        return 0
    idx = round((float(x) - tf.left) / tf.plot_w * (tf.n_bars - 1))
    return int(min(max(idx, 0), tf.n_bars - 1))


def read_boxes(path: Path) -> list[tuple[float, float, float, float]]:
    boxes = []
    if not path.exists():
        return boxes
    for line in path.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        # cls xc yc w h
        boxes.append(tuple(map(float, parts[1:5])))
    return boxes


def resolve_series(sym_hint: str) -> pd.DataFrame | None:
    """Map stem symbol body to a loaded OHLCV frame."""
    groups = list_series(bar="15m")
    # exact
    for (_src, sym), paths in groups.items():
        if sym == sym_hint or sym == f"{sym_hint}_SWAP" or sym.replace("_SWAP", "") == sym_hint:
            df = load_series(paths)
            if len(df) >= WINDOW + 50:
                return df
    # fuzzy: body contains
    candidates = []
    for (_src, sym), paths in groups.items():
        if sym_hint in sym or sym in sym_hint:
            candidates.append((sym, paths))
    # prefer SWAP
    candidates.sort(key=lambda x: (0 if x[0].endswith("_USDT_SWAP") else 1, len(x[0])))
    for _sym, paths in candidates[:3]:
        df = load_series(paths)
        if len(df) >= WINDOW + 50:
            return df
    return None


def find_window_start(n: int, idx: int) -> int | None:
    """Interpret stem index as window start (common) or end-exclusive."""
    # start index
    if 0 <= idx <= n - WINDOW:
        return idx
    # end index (inclusive last bar)
    start = idx - WINDOW + 1
    if 0 <= start <= n - WINDOW:
        return start
    # end exclusive
    start = idx - WINDOW
    if 0 <= start <= n - WINDOW:
        return start
    return None


def boxes_to_tip(
    boxes: list[tuple[float, float, float, float]],
    tf_old,
    tip_df: pd.DataFrame,
    tip_tf,
    *,
    win_start: int,
    signal_global: int,
) -> list[tuple[float, float, float, float]]:
    """Remap normalized boxes into tip window using bar spans."""
    tip_start = signal_global - WINDOW + 1
    out = []
    for xc, yc, w, h in boxes:
        x1 = (xc - w / 2) * tf_old.width
        x2 = (xc + w / 2) * tf_old.width
        b0 = bar_from_x(tf_old, x1)
        b1 = bar_from_x(tf_old, x2)
        if b1 < b0:
            b0, b1 = b1, b0
        g0, g1 = win_start + b0, win_start + b1
        # clip to tip window
        t0 = max(0, g0 - tip_start)
        t1 = min(WINDOW - 1, g1 - tip_start)
        if t1 < t0 or t1 < WINDOW // 4:
            # force box to end at tip if segment mostly out of view
            t1 = WINDOW - 1
            t0 = max(0, t1 - max(3, b1 - b0))
        # rebuild box with tip transform + MA bundle height of those bars
        region = tip_df.iloc[t0 : t1 + 1]
        from src.detection.data import ALL_MA_COLS

        values: list[float] = []
        for col in ALL_MA_COLS:
            if col in region.columns:
                values.extend(float(v) for v in region[col] if pd.notna(v))
        if not values:
            # fallback: keep relative y from old box, x at tip
            nx1 = tip_tf.x_at(t0) - tip_tf.candle_half_w
            nx2 = tip_tf.x_at(t1) + tip_tf.candle_half_w
            xc2 = ((nx1 + nx2) / 2) / tip_tf.width
            w2 = max((nx2 - nx1) / tip_tf.width, 0.02)
            out.append((float(np.clip(xc2, 0.01, 0.99)), yc, float(np.clip(w2, 0.02, 0.95)), h))
            continue
        hi, lo = max(values), min(values)
        pad = max((hi - lo) * 0.35, (tip_tf.price_max - tip_tf.price_min) * 0.004)
        nx1 = tip_tf.x_at(t0) - tip_tf.candle_half_w - 6
        nx2 = tip_tf.x_at(t1) + tip_tf.candle_half_w + 6
        ny1 = tip_tf.y_at(hi + pad)
        ny2 = tip_tf.y_at(lo - pad)
        nx1 = float(np.clip(nx1, 0, tip_tf.width - 1))
        nx2 = float(np.clip(nx2, 1, tip_tf.width))
        ny1 = float(np.clip(ny1, 0, tip_tf.height - 1))
        ny2 = float(np.clip(ny2, 1, tip_tf.height))
        if nx2 - nx1 < 4 or abs(ny2 - ny1) < 4:
            continue
        xc2 = (nx1 + nx2) / 2 / tip_tf.width
        yc2 = (ny1 + ny2) / 2 / tip_tf.height
        w2 = (nx2 - nx1) / tip_tf.width
        h2 = abs(ny2 - ny1) / tip_tf.height
        out.append((xc2, yc2, w2, h2))
    return out


def process_one(
    stem: str,
    src_img: Path,
    src_lbl: Path,
    out_img: Path,
    out_lbl: Path,
    stats: Stats,
) -> bool:
    if is_eval_stem(stem):
        stats.tip_skip_eval += 1
        return False
    boxes = read_boxes(src_lbl)
    if not boxes:
        stats.tip_skip_no_label += 1
        return False
    parsed = parse_stem(stem)
    if not parsed:
        stats.tip_skip_error += 1
        return False
    body, idx = parsed
    # symbol_of strips trailing digits already handled; body is like BTC_USDT_SWAP or BTC_USDT
    df = resolve_series(body)
    if df is None:
        # try symbol_of(stem) 
        df = resolve_series(symbol_of(stem))
    if df is None:
        stats.tip_skip_no_series += 1
        return False
    n = len(df)
    win_start = find_window_start(n, idx)
    if win_start is None:
        stats.tip_skip_window += 1
        return False
    sub = add_mas(df.iloc[win_start : win_start + WINDOW].reset_index(drop=True))
    if len(sub) < WINDOW:
        stats.tip_skip_window += 1
        return False
    tf_old = make_chart_transform(sub)
    # rightmost box right-edge → signal bar in window
    right_bars = []
    for xc, yc, w, h in boxes:
        x2 = (xc + w / 2) * tf_old.width
        right_bars.append(bar_from_x(tf_old, x2))
    sig_local = int(max(right_bars))
    signal_global = win_start + sig_local
    tip_start = signal_global - WINDOW + 1
    if tip_start < 0 or signal_global >= n:
        stats.tip_skip_window += 1
        return False
    tip_sub = add_mas(df.iloc[tip_start : signal_global + 1].reset_index(drop=True))
    if len(tip_sub) != WINDOW:
        # pad if short (shouldn't)
        stats.tip_skip_window += 1
        return False
    try:
        _, tip_tf = render_chart(tip_sub, out_path=out_img)
        new_boxes = boxes_to_tip(
            boxes, tf_old, tip_sub, tip_tf, win_start=win_start, signal_global=signal_global
        )
        if not new_boxes:
            stats.tip_skip_error += 1
            out_img.unlink(missing_ok=True)
            return False
        lines = "".join(f"0 {a:.6f} {b:.6f} {c:.6f} {d:.6f}\n" for a, b, c, d in new_boxes)
        out_lbl.write_text(lines)
    except Exception:
        stats.tip_skip_error += 1
        out_img.unlink(missing_ok=True)
        return False
    stats.tip_ok += 1
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=PROJECT / "datasets" / "dense_owner_v11")
    ap.add_argument("--out", type=Path, default=PROJECT / "datasets" / "dense_owner_v12_htip")
    ap.add_argument("--limit", type=int, default=0, help="max tip clones (0=all positives)")
    ap.add_argument("--skip-copy", action="store_true", help="only build tips (out must exist)")
    args = ap.parse_args()

    src, dst = args.src, args.out
    if not src.exists():
        print(f"src missing: {src}", file=sys.stderr)
        return 2

    stats = Stats()
    if not args.skip_copy:
        if dst.exists():
            shutil.rmtree(dst)
        for sub in ("images/train", "images/val", "labels/train", "labels/val"):
            (dst / sub).mkdir(parents=True, exist_ok=True)
        # copy originals
        for split in ("train", "val"):
            for img in (src / "images" / split).glob("*.png"):
                shutil.copy2(img, dst / "images" / split / img.name)
                lbl = src / "labels" / split / f"{img.stem}.txt"
                if lbl.exists():
                    shutil.copy2(lbl, dst / "labels" / split / lbl.name)
                if split == "train":
                    stats.train_orig += 1
                else:
                    stats.val_orig += 1
            for img in (src / "images" / split).glob("*.jpg"):
                shutil.copy2(img, dst / "images" / split / img.name)
                lbl = src / "labels" / split / f"{img.stem}.txt"
                if lbl.exists():
                    shutil.copy2(lbl, dst / "labels" / split / lbl.name)
                if split == "train":
                    stats.train_orig += 1
                else:
                    stats.val_orig += 1

    # tip clones from train positives only
    train_imgs = sorted((src / "images" / "train").glob("*.png"))
    train_imgs += sorted((src / "images" / "train").glob("*.jpg"))
    n_tip = 0
    for img in train_imgs:
        stem = img.stem
        lbl = src / "labels" / "train" / f"{stem}.txt"
        if not read_boxes(lbl):
            stats.tip_skip_no_label += 1
            continue
        out_img = dst / "images" / "train" / f"{stem}_tip.png"
        out_lbl = dst / "labels" / "train" / f"{stem}_tip.txt"
        if out_img.exists() and out_lbl.exists():
            stats.tip_ok += 1
            n_tip += 1
            if args.limit and n_tip >= args.limit:
                break
            continue
        ok = process_one(stem, img, lbl, out_img, out_lbl, stats)
        if ok:
            n_tip += 1
            if n_tip % 50 == 0:
                print(f"  tip_ok={stats.tip_ok} skip_series={stats.tip_skip_no_series} …", flush=True)
        if args.limit and n_tip >= args.limit:
            break

    (dst / "data.yaml").write_text(
        f"path: {dst.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n"
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "src": str(src),
        "out": str(dst),
        "window": WINDOW,
        "stats": stats.__dict__,
        "train_images": len(list((dst / "images" / "train").glob("*.*"))),
        "val_images": len(list((dst / "images" / "val").glob("*.*"))),
    }
    (dst / "htip_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
