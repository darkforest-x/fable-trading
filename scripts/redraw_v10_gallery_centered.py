#!/usr/bin/env python3
"""Re-draw v10_yolo_5d_gallery images with signal/box in the horizontal center.

Does not re-run YOLO. Uses filename time → signal bar, box ≈ tip-edge span.
Shows ~lookback before + ~lookahead after so the signal sits mid-chart.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import scripts.scan_v10_yolo_5d_gallery as g
from src.data.loader import list_series, load_series
from src.detection.data import add_mas

OUT = PROJECT / "analysis/output/v10_yolo_5d_gallery"
IMG = OUT / "images"
LOOKBACK = 100
LOOKAHEAD = 100


def parse_name(name: str) -> tuple[str, pd.Timestamp, float] | None:
    m = re.search(r"_(\d{8})_(\d{4})_c([0-9.]+)\.png$", name)
    if not m:
        return None
    ymd, hm, conf = m.group(1), m.group(2), float(m.group(3))
    base = name[: m.start()]
    # base is symbol (may contain _)
    ts = pd.Timestamp(f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]} {hm[:2]}:{hm[2:]}:00", tz="UTC")
    return base, ts, conf


def main() -> int:
    kline = PROJECT / "data/kline_fetched"
    groups = list_series(kline, bar="15m")
    sym_paths = {(src, sym): paths for (src, sym), paths in groups.items() if src == "okx"}
    # also key by sym only
    by_sym = {sym: paths for (src, sym), paths in sym_paths.items()}

    paths = sorted(IMG.glob("*.png"))
    print(f"redraw n={len(paths)} center={LOOKBACK}+1+{LOOKAHEAD}", flush=True)
    cards = []
    cache: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    for i, p in enumerate(paths, 1):
        parsed = parse_name(p.name)
        if not parsed:
            print("skip", p.name)
            continue
        sym, ts, conf = parsed
        if sym not in cache:
            paths_k = by_sym.get(sym)
            if not paths_k:
                print("no kline", sym)
                continue
            fr = load_series(paths_k)
            if fr.empty:
                continue
            cache[sym] = add_mas(fr)
        fr = cache[sym]
        times = pd.to_datetime(fr["open_time"], utc=True)
        hits = (times == ts).to_numpy().nonzero()[0]
        if len(hits) == 0:
            j = int((times - ts).abs().argmin())
            if (times.iloc[j] - ts).abs() > pd.Timedelta(minutes=20):
                print("time miss", sym, ts)
                continue
            sig_i = j
        else:
            sig_i = int(hits[0])
        # tip-window geometry for box (right edge ~ signal)
        win_end = min(len(fr) - 1, max(sig_i, g.WINDOW - 1))
        win_start = win_end - g.WINDOW + 1
        # bar index of signal inside that window
        bar1 = sig_i - win_start
        bar0 = max(0, bar1 - 4)
        hit = {
            "signal_i": sig_i,
            "window_end_i": win_end,
            "window_start_i": win_start,
            "bar0": bar0,
            "bar1": min(bar1, g.WINDOW - 1),
            "conf": conf,
            "cx": 0.9,
            "cy": 0.45,
            "bw": 0.1,
            "bh": 0.3,
        }
        g.draw_box_chart(
            fr, hit, symbol=sym, out_path=p, lookback=LOOKBACK, lookahead=LOOKAHEAD
        )
        cards.append(
            {
                "symbol": sym,
                "signal_time": str(pd.Timestamp(times.iloc[sig_i])),
                "signal_i": sig_i,
                "conf": conf,
                "rel_img": f"images/{p.name}",
            }
        )
        if i % 50 == 0:
            print(f"{i}/{len(paths)} el={time.time()-t0:.0f}s", flush=True)
            g.write_html(
                sorted(cards, key=lambda c: c["signal_time"], reverse=True),
                OUT / "index.html",
                days=5,
                conf=0.30,
            )

    cards = sorted(cards, key=lambda c: c["signal_time"], reverse=True)
    g.write_html(cards, OUT / "index.html", days=5, conf=0.30)
    print(f"DONE centered redraw {len(cards)} -> {OUT / 'index.html'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
