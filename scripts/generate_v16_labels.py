"""Generate 200 v16-detected tip-window charts for owner eyeball review.

Owner wants to see whether v16 detects the dense-cluster pattern correctly.
For a sample of symbols, scan tip windows (right edge = the decision bar, NO
future), run v16, and whenever it fires draw its box on the 200-bar chart.
Collect 200 into an HTML gallery.

Causal: each chart's right edge is the fire bar; nothing after is shown.
"""
from __future__ import annotations

import random
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import WARMUP_BARS  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF, STRIDE, WINDOW, load_yolo_model, right_edge_to_bar,
)

OUT = PROJECT / "analysis" / "output" / "v16_label_review"
WEIGHTS = PROJECT / "models" / "owner_v16_tipuni_cold.pt"
TARGET = 200


def main() -> int:
    import os
    os.environ.setdefault("FABLE_YOLO_DEVICE", "cpu")
    model = load_yolo_model(str(WEIGHTS))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "img").mkdir(exist_ok=True)
    rng = random.Random(20260724)

    jobs = []
    for src, sym, frame in iter_series(bar="15m", min_bars=WINDOW + 400):
        if src != "okx" or not sym.endswith("_USDT_SWAP") or is_stockish(sym) or is_eval_symbol(sym):
            continue
        jobs.append((sym, frame))
    rng.shuffle(jobs)

    cards = []
    tmp = OUT / "_tmp.png"
    for sym, frame in jobs:
        if len(cards) >= TARGET:
            break
        ema = add_mas(frame)
        n = len(frame)
        # sample a handful of tip positions per symbol across history
        tips = sorted(rng.sample(range(WINDOW, n - 1), min(8, n - WINDOW - 1)))
        for tip in tips:
            if len(cards) >= TARGET:
                break
            sub = ema.iloc[tip - WINDOW + 1:tip + 1].reset_index(drop=True)
            try:
                img, tf = render_chart(sub, out_path=tmp)
                res = model.predict(str(tmp), conf=DEFAULT_CONF, verbose=False, device="cpu")
            except Exception:
                continue
            r0 = res[0] if res else None
            if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
                continue
            # keep only fires whose box maps to the right edge (tip/tip-1/tip-2)
            drawn = cv2.imread(str(tmp))
            H, Wd = drawn.shape[:2]
            fired_edge = False
            confs = r0.boxes.conf.cpu().numpy()
            for b, cf in zip(r0.boxes.xywhn.cpu().numpy(), confs):
                cx, cy, w, h = map(float, b[:4])
                bar = right_edge_to_bar(cx, w, tf, n_bars=WINDOW)
                edge = bar >= WINDOW - 3
                col = (0, 200, 0) if edge else (0, 165, 255)
                x1, y1 = int((cx - w / 2) * Wd), int((cy - h / 2) * H)
                x2, y2 = int((cx + w / 2) * Wd), int((cy + h / 2) * H)
                cv2.rectangle(drawn, (x1, y1), (x2, y2), col, 3)
                cv2.putText(drawn, f"{cf:.2f} bar{bar}", (x1, max(y1 - 6, 14)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
                fired_edge = fired_edge or edge
            if not fired_edge:
                continue
            t = pd.Timestamp(sub["open_time"].iloc[-1])
            name = f"{sym}_{t.strftime('%Y%m%d_%H%M')}.png"
            cv2.imwrite(str(OUT / "img" / name), drawn)
            cards.append((name, sym, str(t)))
    tmp.unlink(missing_ok=True)

    html = ["<html><head><meta charset='utf-8'><title>v16 检测复核 200 张</title>",
            "<style>body{font-family:sans-serif;background:#111;color:#eee}",
            "img{max-width:100%;border:1px solid #444}.c{margin:14px 0;border-bottom:1px solid #333;padding-bottom:10px}",
            "h3{color:#7dd3fc;margin:4px 0}</style></head><body>",
            f"<h1>v16 检测复核 · {len(cards)} 张(绿框=贴盘口 tip,橙框=框在中段)</h1>",
            "<p>右缘=当下,后面全遮住。看:v16 画的框,是不是你眼里的均线密集?贴边(绿)对不对?</p>"]
    for name, sym, t in cards:
        html.append(f"<div class='c'><h3>{sym} · {t}</h3><img src='img/{name}'></div>")
    html.append("</body></html>")
    (OUT / "index.html").write_text("\n".join(html), encoding="utf-8")
    print(f"generated {len(cards)} charts -> {OUT / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
