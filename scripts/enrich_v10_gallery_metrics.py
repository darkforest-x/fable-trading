#!/usr/bin/env python3
"""Enrich v10 gallery cards with causal momentum/volume; rebuild index.html.

Metrics (signal bar only, no look-ahead):
  ret_16        = close[i] / close[i-16] - 1     (~4h on 15m)
  volume_ratio  = volume[i] / mean(volume[i-19:i+1])  (candidates_v206)

Writes analysis/output/v10_yolo_5d_gallery/cards_enriched.json and index.html.
Does not modify original manifest.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import scripts.scan_v10_yolo_5d_gallery as g  # noqa: E402
from src.data.loader import list_series, load_series  # noqa: E402

OUT = PROJECT / "analysis" / "output" / "v10_yolo_5d_gallery"
MANIFEST = OUT / "manifest.json"
ENRICHED = OUT / "cards_enriched.json"
IMG = OUT / "images"


def _metrics_at(fr: pd.DataFrame, sig_i: int) -> tuple[float | None, float | None]:
    if sig_i < 0 or sig_i >= len(fr):
        return None, None
    close = fr["close"].astype(float)
    vol = fr["volume"].astype(float)
    ret16 = None
    if sig_i >= 16 and float(close.iloc[sig_i - 16]) != 0:
        ret16 = float(close.iloc[sig_i] / close.iloc[sig_i - 16] - 1.0)
    vol_ratio = None
    lo = max(0, sig_i - 19)
    base = vol.iloc[lo : sig_i + 1]
    m = float(base.mean()) if len(base) else 0.0
    if m > 0 and np.isfinite(m):
        vol_ratio = float(vol.iloc[sig_i] / m)
    return ret16, vol_ratio


def main() -> int:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cards = list(data["cards"])
    keep = {c["rel_img"].split("/")[-1] for c in cards}
    # only cards whose image still exists
    cards = [c for c in cards if (IMG / Path(c["rel_img"]).name).is_file()]
    print(f"enrich n={len(cards)} (manifest images on disk)", flush=True)

    groups = list_series(PROJECT / "data" / "kline_fetched", bar="15m")
    by_sym = {sym: paths for (src, sym), paths in groups.items() if src == "okx"}
    cache: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    miss_sym = 0
    miss_time = 0
    for i, c in enumerate(cards, 1):
        sym = c["symbol"]
        if sym not in cache:
            paths = by_sym.get(sym)
            if not paths:
                miss_sym += 1
                c["ret_16"] = None
                c["volume_ratio"] = None
                continue
            fr = load_series(paths)
            cache[sym] = fr
        fr = cache[sym]
        times = pd.to_datetime(fr["open_time"], utc=True)
        ts = pd.Timestamp(c["signal_time"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        hits = (times == ts).to_numpy().nonzero()[0]
        if len(hits) == 0:
            j = int((times - ts).abs().argmin())
            if (times.iloc[j] - ts).abs() > pd.Timedelta(minutes=20):
                miss_time += 1
                c["ret_16"] = None
                c["volume_ratio"] = None
                continue
            sig_i = j
        else:
            sig_i = int(hits[0])
        c["signal_i"] = sig_i
        ret16, vr = _metrics_at(fr, sig_i)
        c["ret_16"] = ret16
        c["volume_ratio"] = vr
        if i % 100 == 0:
            print(f"  {i}/{len(cards)} el={time.time()-t0:.0f}s", flush=True)

    ok_m = sum(1 for c in cards if c.get("ret_16") is not None)
    ok_v = sum(1 for c in cards if c.get("volume_ratio") is not None)
    print(f"metrics ret16={ok_m}/{len(cards)} vol={ok_v}/{len(cards)} miss_sym={miss_sym} miss_time={miss_time}")

    # band counts for log
    mom_c: dict[str, int] = {}
    vol_c: dict[str, int] = {}
    for c in cards:
        if c.get("ret_16") is not None:
            b = g.mom_band_id(float(c["ret_16"]))
            mom_c[b] = mom_c.get(b, 0) + 1
        if c.get("volume_ratio") is not None:
            b = g.vol_band_id(float(c["volume_ratio"]))
            vol_c[b] = vol_c.get(b, 0) + 1
    print("mom bands", mom_c)
    print("vol bands", vol_c)

    ENRICHED.write_text(
        json.dumps(
            {
                "source_manifest": str(MANIFEST),
                "n": len(cards),
                "ret_16": "close[i]/close[i-16]-1",
                "volume_ratio": "volume[i]/mean(volume[i-19:i+1])",
                "cards": cards,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    days = int(data.get("days", 5))
    conf = float(data.get("conf", 0.3))
    g.write_html(cards, OUT / "index.html", days=days, conf=conf)
    print(f"DONE {OUT / 'index.html'} + {ENRICHED}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
