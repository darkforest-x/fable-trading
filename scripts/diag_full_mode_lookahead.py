"""Measure how many FUTURE bars the detector saw when it fired, in mode="full".

Why: data/judgment_yolo_owner_side_short_*.csv was built by
scripts/yolo_candidate_source.py, which calls scan_series_with_yolo WITHOUT
mode=, i.e. the default mode="full". In that mode
`apply_tip_edge = mode in ("live","tip")` is False, so a box may map to ANY bar
inside the 200-bar window (`signal_i = start + bar_in_win`). Every bar after
signal_i inside that window was rendered into the image the detector looked at
-- future information relative to the signal. That is the same class of bug that
inflated the 6.61 backtest, and iron rule 12 forbids such paths.

This does NOT assume; it measures. It replays the exact full-mode schedule for
a few symbols, records bar_in_win per accepted box, and reports the lookahead
distribution: lookahead = (window - 1) - bar_in_win.

  lookahead == 0  -> box on the window's last bar (causal, tip-like)
  lookahead >  0  -> that many future bars were visible when the detector fired

Read-only diagnostic: no dataset is written, nothing is promoted.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/diag_full_mode_lookahead.py --n-symbols 3
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(PROJECT))

from src.data.loader import list_series, load_series  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    STRIDE,
    WARMUP_BARS,
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

WEIGHTS = PROJECT / "runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-symbols", type=int, default=3)
    ap.add_argument("--max-windows", type=int, default=60, help="windows per symbol")
    args = ap.parse_args()

    if not WEIGHTS.exists():
        print(f"weights not found: {WEIGHTS}")
        return 2
    model = load_yolo_model(str(WEIGHTS))

    # use symbols the 100-coin pool actually used
    pool = pd.read_csv(PROJECT / "data" / "judgment_yolo_owner_side_short_100_6m.csv",
                       usecols=["symbol"])
    symbols = list(pd.unique(pool["symbol"]))[: args.n_symbols]
    series = list_series(bar="15m")

    tmp = PROJECT / "analysis" / "output" / "_diag_lookahead"
    tmp.mkdir(parents=True, exist_ok=True)
    all_bar_in_win: list[int] = []
    per_sym = {}

    for sym in symbols:
        key = ("okx", sym)
        if key not in series:
            continue
        frame = add_mas(load_series(series[key]))
        if len(frame) < WARMUP_BARS + WINDOW + 2:
            continue
        last_start = len(frame) - WINDOW
        starts = list(range(WARMUP_BARS, last_start + 1, STRIDE))[: args.max_windows]
        bars_here: list[int] = []
        for start in starts:
            sub = frame.iloc[start : start + WINDOW]
            png = tmp / f"w_{start}.png"
            try:
                _, tf = render_chart(sub, out_path=png)
                res = model.predict([str(png)], conf=DEFAULT_CONF, verbose=False, device="cpu")[0]
            except Exception as exc:  # noqa: BLE001
                print(f"  {sym} start={start} render/predict failed: {exc}")
                continue
            finally:
                png.unlink(missing_ok=True)
            boxes = res.boxes
            if boxes is None or len(boxes) == 0:
                continue
            for b in boxes.xywhn.cpu().numpy():
                cx, _, w, _ = map(float, b[:4])
                bars_here.append(int(right_edge_to_bar(cx, w, tf, n_bars=WINDOW)))
        per_sym[sym] = {"n_windows": len(starts), "n_boxes": len(bars_here)}
        all_bar_in_win.extend(bars_here)
        print(f"  {sym}: windows={len(starts)} boxes={len(bars_here)}", flush=True)

    if not all_bar_in_win:
        print("no boxes detected")
        return 1
    a = np.array(all_bar_in_win)
    look = (WINDOW - 1) - a  # future bars visible at fire time
    out = {
        "weights": str(WEIGHTS),
        "mode": "full (default in yolo_candidate_source.py)",
        "window": WINDOW, "stride": STRIDE,
        "n_boxes": int(len(a)),
        "bar_in_win": {"min": int(a.min()), "p10": int(np.percentile(a, 10)),
                       "p50": int(np.percentile(a, 50)), "p90": int(np.percentile(a, 90)),
                       "max": int(a.max())},
        "lookahead_bars": {"min": int(look.min()), "p10": int(np.percentile(look, 10)),
                           "p50": int(np.percentile(look, 50)), "p90": int(np.percentile(look, 90)),
                           "max": int(look.max()), "mean": round(float(look.mean()), 1)},
        "pct_causal_lookahead_le_2": round(float((look <= 2).mean()) * 100, 1),
        "pct_lookahead_ge_10": round(float((look >= 10).mean()) * 100, 1),
        "per_symbol": per_sym,
    }
    (PROJECT / "analysis" / "output" / "diag_full_mode_lookahead.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
