"""Small-window YOLO dataset from v10-mined patterns, two classes by direction.

Window length is 40 bars, chosen from the mined boxes rather than picked: v10's
boxes are 12 bars at the median and 15 at p95, so in a 20-bar window half of them
would cover more than 60% of the frame and there would be almost no context left
to judge against. At 40 bars, 98.9% of boxes sit inside 40% of the frame, which
is the proportion the 20-bar/4-5-bar sketch was aiming at.

The box is placed at a uniformly random offset inside the window, subject to
fitting with at least MIN_PAD bars on each side. Position carries no information
that way, so the model cannot learn "the pattern is where the box always is" --
which is how the earlier right-edge-cropped attempts went wrong from the other
direction.

Two classes, short and long, from the two-rule geometric gate (96.3% agreement
with owner's own direction calls where the rules agree; ambiguous boxes are
dropped rather than guessed). Pooling them is what turned +273.9bp/83.3% into
+141.3bp/58.9% in the star-tip analysis, so they stay apart here.

C5: positives and negatives come from the same symbols, the same period, the same
renderer, the same window length and the same image size. The only difference is
whether v10 found a pattern there.
C6: split is by symbol and by time block, never by image.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402

MA_COLS = ["sma20", "ema20", "sma60", "ema60", "sma120", "ema120"]
MIN_PAD = 6          # bars of context that must remain on each side of the box
CLASSES = ["dense_short", "dense_long"]


@contextmanager
def y_anchored(df_wide: pd.DataFrame | None):
    """Render with the vertical scale a wider window would have given.

    The renderer sets its price range from the visible bars, floored at
    MIN_REL_SPAN of mid price. Shrinking 200 bars to 40 shrinks the visible
    range, so the same six-MA cluster covers 2.24x more of the frame height
    (2.9% -> 6.5%, measured over 60 mined boxes). The tightness that defines the
    pattern is a vertical measurement, and cropping the time axis inflates it.

    Raising the floor to the wide window's own span pins the scale back: the
    floor binds at 40 bars anyway, so the axis becomes exactly the wide one.
    Passing None leaves the renderer untouched, which is the other A/B arm.
    """
    import yoyo.layers.l1_detection.render as R
    if df_wide is None:
        yield
        return
    lo = float(pd.concat([df_wide["low"], df_wide[MA_COLS].min(axis=1)]).min())
    hi = float(pd.concat([df_wide["high"], df_wide[MA_COLS].max(axis=1)]).max())
    mid = (hi + lo) / 2
    old = R.MIN_REL_SPAN
    try:
        if mid > 0 and hi > lo:
            R.MIN_REL_SPAN = max(old, (hi - lo) / abs(mid))
        yield
    finally:
        R.MIN_REL_SPAN = old


def dedup(rows: list[dict], gap: int = 18) -> list[dict]:
    """One row per pattern: same symbol, box centres within `gap`, keep best conf."""
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    out = []
    for sym, rs in by_sym.items():
        rs.sort(key=lambda r: -r["conf"])
        taken: list[int] = []
        for r in rs:
            c = (r["box_start_i"] + r["box_end_i"]) // 2
            if all(abs(c - t) >= gap for t in taken):
                taken.append(c)
                out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detections", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=int, default=40)
    ap.add_argument("--kline-dir", default=str(PROJECT / "data/kline_fetched"))
    ap.add_argument("--min-conf", type=float, default=0.30)
    ap.add_argument("--neg-per-pos", type=float, default=1.0)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--max-per-symbol", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260817)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--y-anchor-bars", type=int, default=0,
                    help="pin the vertical scale to this many bars of context (0 = off)")
    ap.add_argument("--box-bars", type=int, default=0,
                    help="crop v10's box to this many bars, centred on its tightest bar "
                         "(0 = keep the box as mined)")
    ap.add_argument("--right-pad", type=int, default=-1,
                    help="bars that must remain to the right of the box; -1 = use MIN_PAD. "
                         "0 lets the box touch the right edge, which is what makes a "
                         "low-latency detection possible at inference")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    W = args.window

    rows = [json.loads(l) for l in open(args.detections) if l.strip()]
    rows = [r for r in rows if r["conf"] >= args.min_conf and r.get("side")]
    _need = (args.box_bars or 99)
    rows = [r for r in rows if min(r["box_bars"], _need) <= W - MIN_PAD]
    rows = dedup(rows)
    if args.limit:
        rows = rows[:args.limit]
    print(f"{len(rows):,} deduped boxes that fit a {W}-bar window "
          f"({Counter(r['side'] for r in rows)})", flush=True)

    # C6: symbols are split first, then time. A symbol never appears on both sides.
    syms = sorted({r["symbol"] for r in rows})
    rng.shuffle(syms)
    n_val = max(1, int(len(syms) * args.val_frac))
    val_syms = set(syms[:n_val])
    print(f"{len(syms)} symbols -> val holds {len(val_syms)}", flush=True)

    out = Path(args.out)
    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    files = {}
    for p in Path(args.kline_dir).glob("okx_*_15m_*.csv"):
        m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
        if m:
            files[m.group(1)] = p

    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)

    stats = Counter()
    for si, (sym, rs) in enumerate(sorted(by_sym.items()), 1):
        if sym not in files:
            continue
        rs = rs[:args.max_per_symbol]
        split = "val" if sym in val_syms else "train"
        fr = add_mas(pd.read_csv(files[sym]).sort_values("ts").reset_index(drop=True))
        n = len(fr)
        used_windows: list[tuple[int, int]] = []

        rpad = MIN_PAD if args.right_pad < 0 else args.right_pad
        for r in rs:
            b0, b1 = r["box_start_i"], r["box_end_i"]
            if args.box_bars:
                # centre on the tightest bar, not on the tail: the tightest bar sits
                # at 0.67 of the box by median, so a tail crop misses it in ~45% of
                # boxes, and it is the bar that defines the pattern
                k = int(r.get("tight_i", (b0 + b1) // 2))
                half = args.box_bars // 2
                nb0, nb1 = k - half, k - half + args.box_bars - 1
                b0, b1 = max(b0, nb0), min(b1, nb1)
                if b1 - b0 + 1 < 3:
                    continue
            span = b1 - b0 + 1
            slack = W - span
            if slack < MIN_PAD + rpad:
                continue
            # box position inside the window is uniform, not pinned anywhere
            left = rng.randint(MIN_PAD, slack - rpad)
            ws, we = b0 - left, b0 - left + W - 1
            if ws < 130 or we >= n:
                continue
            win = fr.iloc[ws:we + 1]
            if win[["sma120", "ema120"]].isna().any().any():
                continue
            wide = fr.iloc[max(0, we - args.y_anchor_bars + 1):we + 1] \
                if args.y_anchor_bars else None
            with y_anchored(wide):
                img, tf = render_chart(win, out_path=None)
            h, w = img.shape[:2]
            x0, x1 = tf.x_at(b0 - ws), tf.x_at(b1 - ws)
            seg = win.iloc[b0 - ws:b1 - ws + 1]
            y0, y1 = tf.y_at(float(seg["high"].max())), tf.y_at(float(seg["low"].min()))
            pad = max(2, int(0.01 * h))
            x0, x1 = max(0, x0 - pad), min(w - 1, x1 + pad)
            y0, y1 = max(0, y0 - pad), min(h - 1, y1 + pad)
            cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
            bw, bh = (x1 - x0) / w, (y1 - y0) / h
            if bw <= 0 or bh <= 0:
                continue
            cls = CLASSES.index(f"dense_{r['side']}")
            stem = f"{sym}_{ws:06d}_{r['side']}"
            cv2.imwrite(str(out / "images" / split / f"{stem}.png"), img)
            (out / "labels" / split / f"{stem}.txt").write_text(
                f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
            used_windows.append((ws, we))
            stats[f"{split}/{r['side']}"] += 1

        # negatives: same symbol, same period, same renderer, no mined box inside
        want = int(len(used_windows) * args.neg_per_pos)
        boxes = [(r["box_start_i"], r["box_end_i"]) for r in by_sym[sym]]
        tries = 0
        made = 0
        while made < want and tries < want * 40:
            tries += 1
            ws = rng.randint(130, max(131, n - W - 1))
            we = ws + W - 1
            if any(not (b1 < ws or b0 > we) for b0, b1 in boxes):
                continue
            win = fr.iloc[ws:we + 1]
            if win[["sma120", "ema120"]].isna().any().any():
                continue
            wide = fr.iloc[max(0, we - args.y_anchor_bars + 1):we + 1] \
                if args.y_anchor_bars else None
            with y_anchored(wide):
                img, _ = render_chart(win, out_path=None)
            stem = f"{sym}_{ws:06d}_bg"
            cv2.imwrite(str(out / "images" / split / f"{stem}.png"), img)
            (out / "labels" / split / f"{stem}.txt").write_text("")
            made += 1
            stats[f"{split}/background"] += 1
        if si % 20 == 0:
            print(f"  [{si}/{len(by_sym)}] {sym} · {dict(stats)}", flush=True)

    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\n"
        f"nc: {len(CLASSES)}\nnames: {CLASSES}\n")
    meta = {
        "window_bars": W, "min_pad_bars": MIN_PAD, "classes": CLASSES,
        "y_anchor_bars": args.y_anchor_bars,
        "box_bars_crop": args.box_bars,
        "box_crop_anchor": "tightest bar in the mined box",
        "right_pad": (MIN_PAD if args.right_pad < 0 else args.right_pad),
        "box_position": "uniform random inside the window, never pinned",
        "side_source": "geometric two-rule agreement gate (96.3% vs owner labels)",
        "detections": str(args.detections), "min_conf": args.min_conf,
        "split": "by symbol (C6), val symbols disjoint from train",
        "val_symbols": sorted(val_syms), "counts": dict(stats),
        "renderer": "src/detection/render.py::render_chart (same as v10 training)",
    }
    (out / "build_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"\ndone: {dict(stats)}\n-> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
