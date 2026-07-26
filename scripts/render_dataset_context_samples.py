"""Render dataset positives as REVIEW charts: box in place, plus what came after.

Training images are tip-only by iron rule 12, and the owner has now twice said
the same thing about them: a box jammed against the right edge with nothing
after it cannot be judged. So sampling a dataset for human eyes has to
re-render, not just copy the training PNG.

Each sample shows the box where the label puts it, `back` bars of lead-in, `fwd`
bars of what followed, and a grey line at the tip marking the boundary the
detector would have seen. The box is mapped by rebuilding the tip window's
transform, converting the normalised label back to bar indices and a price
range, then projecting into the wider window -- so it lands where the label
really is, not an approximation.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/render_dataset_context_samples.py \
      --dataset datasets/dense_owner_short_star_tip_v4 --n 6
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.data import add_mas  # noqa: E402
from src.detection.render import make_chart_transform, render_chart  # noqa: E402
from src.judgment.yolo_candidates import right_edge_to_bar  # noqa: E402
from scripts.build_htip_dataset import WINDOW, resolve_series  # noqa: E402

OUT = PROJECT / "analysis" / "output" / "dataset_context_samples"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--back", type=int, default=140)
    ap.add_argument("--fwd", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    labels = sorted(p for p in (args.dataset / "labels").rglob("*.txt")
                    if p.stat().st_size > 0)
    if not labels:
        print(f"no positive labels under {args.dataset}")
        return 1
    random.Random(args.seed).shuffle(labels)

    out = args.out
    if out.exists():
        for p in out.glob("*.png"):
            p.unlink()
    out.mkdir(parents=True, exist_ok=True)

    cache: dict[str, pd.DataFrame | None] = {}
    made = 0
    for lp in labels:
        if made >= args.n:
            break
        stem = lp.stem
        m = re.match(r"(.+)_(\d{6})$", stem)
        if not m:
            continue
        sym, tip = m.group(1), int(m.group(2))
        if sym not in cache:
            base = resolve_series(sym)
            cache[sym] = add_mas(base) if base is not None else None
        framed = cache[sym]
        if framed is None or tip >= len(framed) or tip < WINDOW:
            continue

        parts = lp.read_text().split()
        if len(parts) < 5:
            continue
        cx, yc, bw, bh = (float(v) for v in parts[1:5])

        # label -> absolute bars + price range, via the tip window's transform
        w0 = tip - WINDOW + 1
        tf_tip = make_chart_transform(framed.iloc[w0:tip + 1])
        r_bar = right_edge_to_bar(cx + bw / 2, 0.0, tf_tip, n_bars=WINDOW)
        l_bar = right_edge_to_bar(cx - bw / 2, 0.0, tf_tip, n_bars=WINDOW)
        span = max(tf_tip.price_max - tf_tip.price_min, 1e-12)

        def price_at(y_norm: float) -> float:
            y = y_norm * tf_tip.height
            return tf_tip.price_max - (y - tf_tip.top) / max(tf_tip.plot_h, 1) * span

        p_hi, p_lo = price_at(yc - bh / 2), price_at(yc + bh / 2)
        abs_l, abs_r = w0 + l_bar, w0 + r_bar

        lo = max(0, tip - args.back + 1)
        hi = min(len(framed) - 1, tip + args.fwd)
        img, tf = render_chart(framed.iloc[lo:hi + 1], out_path=None)
        img = img.copy()
        x0, x1 = tf.x_at(abs_l - lo), tf.x_at(abs_r - lo)
        y0, y1 = tf.y_at(p_hi), tf.y_at(p_lo)
        # render_chart's array is already in cv2's channel order — no swap.
        cv2.rectangle(img, (x0 - tf.candle_half_w, y0),
                      (x1 + tf.candle_half_w, y1), (60, 60, 255), 3)
        xt = tf.x_at(tip - lo)
        cv2.line(img, (xt, tf.top), (xt, tf.top + tf.plot_h), (150, 150, 150), 1)
        cv2.putText(img, "tip (detector saw up to here)", (xt + 6, tf.top + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 120, 120), 1, cv2.LINE_AA)
        dst = out / f"ctx_{made}_{stem}.png"
        cv2.imwrite(str(dst), img)
        print(f"  {dst.name}")
        made += 1

    print(f"\n{made} 张 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
