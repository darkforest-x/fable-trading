#!/usr/bin/env python3
"""Scan last N days with w20 midbox YOLO; write HTML gallery of tip-edge hits.

Uses the same review chart layout as scan_v10_yolo_5d_gallery (signal centered,
±100 bars) but detection window is W=24 (w20 midbox train geometry).

  PYTHONPATH=.:$HOME/yoyo-trading .venv/bin/python \\
    scripts/scan_w20_midbox_5d_gallery.py --days 5 --conf 0.15
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
_YOYO = Path.home() / "yoyo-trading"
if _YOYO.is_dir():
    sys.path.insert(0, str(_YOYO))
os.environ.setdefault("YOYO_DATA_ROOT", str(PROJECT))

from yoyo.data.loader import list_series, load_series  # noqa: E402
from yoyo.layers.l1_detection.candidates import (  # noqa: E402
    load_yolo_model,
    map_box_to_signal,
    right_edge_to_bar,
)
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from src.data.universe import is_stockish  # noqa: E402

# Reuse review drawing / HTML from the v10 gallery module
import scripts.scan_v10_yolo_5d_gallery as g  # noqa: E402

WINDOW = 24
TIP_EDGE = 2
MIN_GAP_BARS = 18
DEFAULT_WEIGHTS = (
    PROJECT
    / "analysis/output/w20_overnight/cycle_0_owner_w20_midbox_cold/weights/best.pt"
)
DEFAULT_OUT = PROJECT / "analysis/output/w20_midbox_5d_gallery"


def scan_symbol_range_w(
    fr: pd.DataFrame,
    model,
    *,
    i_lo: int,
    i_hi: int,
    conf: float,
    device: str,
    tmp_png: Path,
    window: int = WINDOW,
    stride: int = 1,
) -> list[dict]:
    hits: list[dict] = []
    n = len(fr)
    if n < window or i_hi < window - 1:
        return hits
    i_lo = max(i_lo, window - 1)
    i_hi = min(i_hi, n - 1)
    stride = max(1, int(stride))
    for end_i in range(i_lo, i_hi + 1, stride):
        start_i = end_i - window + 1
        win = fr.iloc[start_i : end_i + 1]
        try:
            img, tf = render_chart(win, out_path=None)
            tmp_png.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(tmp_png), img)
            res = model.predict(str(tmp_png), conf=conf, verbose=False, device=device)
        except Exception:
            continue
        r0 = res[0] if res else None
        if r0 is None or r0.boxes is None or len(r0.boxes) == 0:
            continue
        best = None
        for row, cf in zip(r0.boxes.xywhn.cpu().numpy(), r0.boxes.conf.cpu().numpy()):
            cx, cy, w, h = map(float, row)
            m = map_box_to_signal(
                cx=cx,
                w=w,
                tf=tf,
                window_start_i=start_i,
                n_bars=window,
                frame_length=n,
                latest_closed_i=end_i,
                tip_edge_bars=TIP_EDGE,
                apply_tip_edge=True,
                max_global_tip_age_bars=TIP_EDGE,
                allow_pending_entry=True,
            )
            if not m.accepted:
                continue
            b1 = m.bar_in_window
            b0 = right_edge_to_bar(cx - w / 2, 0.0, tf, n_bars=window)
            cand = {
                "signal_i": int(m.mapped_signal_i),
                "window_end_i": int(end_i),
                "window_start_i": int(start_i),
                "bar0": int(b0),
                "bar1": int(b1),
                "conf": float(cf),
                "cx": cx,
                "cy": cy,
                "bw": w,
                "bh": h,
            }
            if best is None or cand["conf"] > best["conf"]:
                best = cand
        if best is not None:
            hits.append(best)
    hits.sort(key=lambda h: -h["conf"])
    kept: list[dict] = []
    used: list[int] = []
    for h in hits:
        si = h["signal_i"]
        if any(abs(si - u) < MIN_GAP_BARS for u in used):
            continue
        used.append(si)
        kept.append(h)
    kept.sort(key=lambda h: h["signal_i"])
    return kept


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--conf", type=float, default=0.15)
    ap.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--window", type=int, default=WINDOW)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-symbols", type=int, default=0, help="0=all")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    if not args.weights.exists():
        raise SystemExit(f"missing weights: {args.weights}")

    # Override gallery module globals so draw_box_chart uses W=24
    g.WINDOW = args.window
    g.TIP_EDGE = TIP_EDGE
    g.MODEL_TAG = "w20_midbox"
    g.WEIGHTS_NAME = str(args.weights)
    g.OUT = args.out

    device = args.device or g._device()
    out = args.out
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    # load series from kline_fetched (+ cache if present)
    groups: dict[tuple[str, str], list[Path]] = {}
    for d in (PROJECT / "data" / "kline_cache", PROJECT / "data" / "kline_fetched"):
        if d.is_dir():
            part = list_series(cache_dir=d, bar="15m")
            for k, paths in part.items():
                groups.setdefault(k, []).extend(paths)

    series_list: list[tuple[str, pd.DataFrame]] = []
    tip_global = None
    for (_src, sym), paths in sorted(groups.items()):
        if not sym.endswith("_USDT_SWAP") or is_stockish(sym):
            continue
        try:
            df = load_series(paths)
        except Exception:
            continue
        if len(df) < args.window + 50:
            continue
        t_last = pd.Timestamp(df["open_time"].iloc[-1])
        if t_last.tzinfo is None:
            t_last = t_last.tz_localize("UTC")
        if tip_global is None or t_last > tip_global:
            tip_global = t_last
        series_list.append((sym, df))

    if not series_list or tip_global is None:
        raise SystemExit("no series loaded")

    t_hi = tip_global
    t_lo = t_hi - pd.Timedelta(days=args.days)
    print(
        f"device={device} weights={args.weights} conf={args.conf} days={args.days} "
        f"window={args.window}\n"
        f"symbols={len(series_list)} tip={t_hi} window UTC {t_lo} .. {t_hi}",
        flush=True,
    )

    model = load_yolo_model(args.weights)
    cards: list[dict] = []
    t0 = time.time()
    tmp = out / "_tmp_detect.png"
    n_sym = len(series_list)
    if args.max_symbols > 0:
        series_list = series_list[: args.max_symbols]
        n_sym = len(series_list)

    for i, (sym, raw) in enumerate(series_list, 1):
        fr = add_mas(raw)
        times = pd.to_datetime(fr["open_time"], utc=True)
        # indices in [t_lo, t_hi]
        mask = (times >= t_lo) & (times <= t_hi)
        idxs = np.where(mask.to_numpy())[0]
        if len(idxs) == 0:
            continue
        i_lo, i_hi = int(idxs[0]), int(idxs[-1])
        hits = scan_symbol_range_w(
            fr,
            model,
            i_lo=i_lo,
            i_hi=i_hi,
            conf=args.conf,
            device=device,
            tmp_png=tmp,
            window=args.window,
            stride=args.stride,
        )
        for h in hits:
            sig_i = int(h["signal_i"])
            st = pd.Timestamp(fr["open_time"].iloc[sig_i])
            if st.tzinfo is None:
                st = st.tz_localize("UTC")
            else:
                st = st.tz_convert("UTC")
            # causal review metrics
            close = fr["close"].to_numpy(dtype=float)
            vol = fr["volume"].to_numpy(dtype=float) if "volume" in fr.columns else None
            ret16 = None
            if sig_i >= 16 and close[sig_i - 16] > 0:
                ret16 = float(close[sig_i] / close[sig_i - 16] - 1.0)
            vol_ratio = None
            if vol is not None and sig_i >= 20:
                base = float(np.mean(vol[sig_i - 20 : sig_i]))
                if base > 0:
                    vol_ratio = float(vol[sig_i] / base)

            stem = f"{sym}_{st.strftime('%Y%m%d_%H%M')}_c{h['conf']:.3f}"
            rel = f"images/{stem}.png"
            out_png = out / rel
            try:
                g.draw_box_chart(fr, h, symbol=sym, out_path=out_png)
            except Exception as e:
                print(f"  draw fail {sym}: {e}", flush=True)
                continue
            cards.append(
                {
                    "symbol": sym,
                    "signal_time": str(st),
                    "signal_i": sig_i,
                    "conf": h["conf"],
                    "rel_img": rel,
                    "ret_16": ret16,
                    "volume_ratio": vol_ratio,
                    "window_end_i": h["window_end_i"],
                    "bar0": h["bar0"],
                    "bar1": h["bar1"],
                }
            )
        if hits or i % 20 == 0:
            print(
                f"[{i}/{n_sym}] {sym} hits={len(hits)} total_cards={len(cards)} "
                f"elapsed={time.time()-t0:.0f}s",
                flush=True,
            )

    # write manifest + html
    man = {
        "weights": str(args.weights.resolve()),
        "model_tag": "w20_midbox",
        "conf": args.conf,
        "window": args.window,
        "days": args.days,
        "tip_edge": TIP_EDGE,
        "time_lo": str(t_lo),
        "time_hi": str(t_hi),
        "n_symbols": n_sym,
        "n_cards": len(cards),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
    }
    (out / "manifest.json").write_text(
        json.dumps({"meta": man, "cards": cards}, indent=2, ensure_ascii=False)
    )
    g.write_html(cards, out / "index.html", days=args.days, conf=args.conf)
    # fix title in html
    html_path = out / "index.html"
    text = html_path.read_text(encoding="utf-8")
    text = text.replace("v10 YOLO", "w20 midbox YOLO")
    html_path.write_text(text, encoding="utf-8")
    print(json.dumps(man, indent=2, ensure_ascii=False))
    print(f"gallery → {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
