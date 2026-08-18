"""Mine historical pattern candidates with owner_v10_chain, full context.

This is the Pattern Teacher use of v10, not the trigger use. The distinction is
the whole point: at the tip, with nothing to its right, v10 reproduces 9-10% of
its own boxes; given the full window it reproduces 62-72%. The 2026-08-05
five-day scan ran in tip mode and yielded 159 candidates from 344 symbols, which
is why Pattern Library v1 is 2207 owner boxes and only 159 teacher ones.

So: same weights, same renderer, same conf/iou, and no tip-edge filter. A box is
accepted wherever it lands in the window.

Direction is assigned geometrically, not by asking. Against owner's own 619
direction calls, "close below the six-MA centre" agrees 93.5% of the time, and
where that rule and "mean MA slope is down" agree -- 92.6% of boxes -- agreement
rises to 96.3%. The 7.4% where they disagree is 58.7%, i.e. a coin flip, so
those are marked ambiguous and left for a human rather than guessed.

Renders on this machine on purpose. v10 was trained on src/detection/render.py
output; the 3060 has the weights and the CSVs but not that module, and a second
rendering path is the exact shape of the v15 failure (pos and neg came from two
pipelines and the model learned the pipeline). The 3060 gets the training job,
where the renderer is whatever we build the dataset with.

Writes one JSONL line per accepted detection, flushed per symbol, so a partial
run is a usable run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
_YOYO = Path.home() / "yoyo-trading"
if _YOYO.is_dir():
    sys.path.insert(0, str(_YOYO))

from src.detection.data import add_mas  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from yoyo.layers.l1_detection.candidates import (  # noqa: E402
    WINDOW,
    load_yolo_model,
    right_edge_to_bar,
)

HOLDOUT = pd.Timestamp("2026-05-04T00:00:00Z")
MA_COLS = ["sma20", "ema20", "sma60", "ema60", "sma120", "ema120"]


def geometry(fr: pd.DataFrame, b0: int, b1: int) -> dict:
    """Causal features at the box, and the two direction rules."""
    seg = fr.iloc[b0:b1 + 1]
    if seg.empty:
        return {}
    mas = seg[MA_COLS].to_numpy()
    if not np.isfinite(mas).all():
        return {}
    hi, lo = mas.max(1), mas.min(1)
    width = hi - lo
    atr = float(seg["atr14"].iloc[-1]) if "atr14" in seg else np.nan
    if not np.isfinite(atr) or atr <= 0:
        return {}
    k = int(np.argmin(width))               # tightest bar in the box
    row = seg.iloc[k]
    centre = float(mas[k].mean())
    close = float(row["close"])
    prev = fr.iloc[max(0, b0 + k - 5)]
    slope = float(np.mean([(mas[k][j] - prev[MA_COLS[j]]) / (5 * atr) for j in range(6)]))
    by_price = "short" if close < centre else "long"
    by_slope = "short" if slope < 0 else "long"
    return {
        "tight_i": int(b0 + k),
        "cluster_width_atr": round(float(width[k] / atr), 5),
        "price_to_centre_atr": round((close - centre) / atr, 5),
        "slope_mean_atr": round(slope, 6),
        "atr_pct": round(atr / close, 6),
        "side_by_price": by_price,
        "side_by_slope": by_slope,
        "side": by_price if by_price == by_slope else None,
        "side_agree": by_price == by_slope,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(PROJECT / "models/owner_v10_chain.pt"))
    ap.add_argument("--kline-dir", default=str(PROJECT / "data/kline_fetched"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--stride", type=int, default=20)
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--iou", type=float, default=0.70)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--limit-symbols", type=int, default=0)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():                                  # resume
        for line in out.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["symbol"])
        print(f"resuming: {len(done)} symbols already in {out}", flush=True)

    files = {}
    for p in Path(args.kline_dir).glob("okx_*_15m_*.csv"):
        m = re.match(r"okx_(.+)_15m_\d+\.csv", p.name)
        if m:
            files[m.group(1)] = p
    syms = sorted(files)
    if args.limit_symbols:
        syms = syms[:args.limit_symbols]
    print(f"{len(syms)} symbols · stride {args.stride} · conf {args.conf} · "
          f"iou {args.iou} · device {args.device} · pre-holdout only", flush=True)

    model = load_yolo_model(args.weights)
    t_start = time.time()
    n_win = n_det = 0

    for si, sym in enumerate(syms, 1):
        if sym in done:
            continue
        try:
            fr = pd.read_csv(files[sym]).sort_values("ts").reset_index(drop=True)
            fr = add_mas(fr)
            t = pd.to_datetime(fr["open_time"], utc=True)
            tr = pd.concat([fr["high"] - fr["low"],
                            (fr["high"] - fr["close"].shift()).abs(),
                            (fr["low"] - fr["close"].shift()).abs()], axis=1).max(axis=1)
            fr["atr14"] = tr.rolling(14).mean()
        except Exception as e:                        # noqa: BLE001
            print(f"  [{si}/{len(syms)}] {sym}: unreadable ({e})", flush=True)
            continue

        last = int((t < HOLDOUT).sum()) - 1           # pre-holdout gate
        if last < WINDOW:
            continue
        ends = list(range(WINDOW - 1, last + 1, args.stride))
        rows: list[dict] = []
        for i in range(0, len(ends), args.batch):
            chunk = ends[i:i + args.batch]
            imgs, tfs = [], []
            for e in chunk:
                img, tf = render_chart(fr.iloc[e - WINDOW + 1:e + 1], out_path=None)
                imgs.append(img); tfs.append(tf)
            res = model.predict(imgs, conf=args.conf, iou=args.iou,
                                verbose=False, device=args.device)
            n_win += len(chunk)
            for e, tf, r in zip(chunk, tfs, res):
                if r.boxes is None or len(r.boxes) == 0:
                    continue
                start_i = e - WINDOW + 1
                for row, cf in zip(r.boxes.xywhn.cpu().numpy(),
                                   r.boxes.conf.cpu().numpy()):
                    cx, cy, w, h = map(float, row)
                    b1 = right_edge_to_bar(cx + w / 2, 0.0, tf, n_bars=WINDOW)
                    b0 = right_edge_to_bar(cx - w / 2, 0.0, tf, n_bars=WINDOW)
                    ab0, ab1 = start_i + max(0, b0), start_i + min(WINDOW - 1, b1)
                    if ab1 <= ab0:
                        continue
                    g = geometry(fr, ab0, ab1)
                    if not g:
                        continue
                    rows.append({
                        "symbol": sym, "window_start_i": start_i, "window_end_i": e,
                        "box_start_i": ab0, "box_end_i": ab1,
                        "box_bars": ab1 - ab0 + 1,
                        "box_pos_in_window": round((ab1 - start_i) / (WINDOW - 1), 4),
                        "conf": round(float(cf), 4),
                        "xywhn": [round(v, 6) for v in (cx, cy, w, h)],
                        "box_end_time": str(t.iloc[ab1]),
                        "teacher": "owner_v10_chain", **g,
                    })
        with out.open("a") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        n_det += len(rows)
        el = time.time() - t_start
        rate = n_win / el if el else 0
        eta = (len(syms) - si) * (n_win / si) / rate / 3600 if rate and si else 0
        print(f"  [{si}/{len(syms)}] {sym}: {len(rows)} boxes from {len(ends)} windows "
              f"| total {n_det:,} boxes / {n_win:,} windows "
              f"| {rate:.1f} win/s | eta {eta:.1f}h", flush=True)

    print(f"\ndone: {n_det:,} detections from {n_win:,} windows in "
          f"{(time.time()-t_start)/3600:.2f}h -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
