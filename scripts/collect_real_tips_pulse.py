#!/usr/bin/env python3
"""Per-pulse real-tip collector: the data engine for the v17 detector.

v13..v16 proved that datasets derived from the old mid-window labels cannot
teach tip firing (p_v16_tipuni_train.md: unified rendering still false-fires
on 51.5% of real empty tips). The remedy is a training distribution made of
REAL live tip windows: rule-dense tips (positive candidates for owner review)
plus abundant genuine empty-tip backgrounds. This script runs as a light
side-step of every VPS forward pulse and accumulates exactly that.

Per pulse:
  - every SWAP series: recent tail -> add_mas -> 200-bar tip window
  - rule-dense at tip (find_dense_segments, same rule as the v13 preview
    pack) -> ALWAYS saved (these are rare, ~a few per pulse)
  - non-dense tips -> reservoir-sample K per pulse as background negatives
  - clean render (live pipeline, NO overlays), manifest row per image
  - per-symbol MIN_GAP dedup against the manifest; hard wall-clock budget so
    the 15-min cadence is never at risk (iron rules 7-8)

Output (VPS, single writer): data/real_tip_collect/YYYYMMDD/*.png
                             data/real_tip_collect/manifest.csv

Owner reviews batches later (Label Studio pack builder reads the manifest);
none of this runs YOLO — collection works fine in the detector=none era.
"""
from __future__ import annotations

import csv
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.loader import iter_series  # noqa: E402
from src.data.universe import is_stockish  # noqa: E402
from src.detection.auto_label import find_dense_segments  # noqa: E402
from src.detection.data import add_mas  # noqa: E402
from src.detection.owner_eval import is_eval_symbol  # noqa: E402
from src.detection.render import render_chart  # noqa: E402
from src.judgment.candidates import MIN_GAP_BARS  # noqa: E402

WINDOW = 200
TIP_DENSE_HIT_BARS = 16  # same as v13 preview pack
# Role split (the fix v13..v16 needed): non-dense tips are FREE negatives —
# empty by construction, no owner review, collect them abundantly to drown the
# false-fire shortcut. Rule-dense tips are review-limited: owner triages each
# into real-launch positive vs hard-negative, so cap per pulse to stay sane.
EMPTY_SAMPLES_PER_PULSE = 8
DENSE_CAP_PER_PULSE = 10
BUDGET_SEC = 120.0
TAIL_BARS = 600  # enough MA warm-up for the tip window (EMA120 converged)
OUT_DIR = PROJECT / "data" / "real_tip_collect"
MANIFEST = OUT_DIR / "manifest.csv"
FIELDS = ("symbol", "tip_time", "tip_dense", "mean_full_spread", "saved_at", "png")


def recent_symbol_times() -> dict[str, pd.Timestamp]:
    out: dict[str, pd.Timestamp] = {}
    if not MANIFEST.exists():
        return out
    try:
        df = pd.read_csv(MANIFEST)
        for _, r in df.iterrows():
            t = pd.Timestamp(r["tip_time"])
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            s = str(r["symbol"])
            if s not in out or t > out[s]:
                out[s] = t
    except Exception:
        pass
    return out


def main() -> int:
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)
    day_dir = OUT_DIR / now.strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    write_header = not MANIFEST.exists()
    last_seen = recent_symbol_times()
    gap = pd.Timedelta(minutes=15 * MIN_GAP_BARS)

    saved_dense = saved_empty = scanned = 0
    dense_pool: list[tuple[str, pd.DataFrame, pd.Timestamp, float]] = []
    empty_pool: list[tuple[str, pd.DataFrame, pd.Timestamp, float]] = []
    rng = random.Random()

    with MANIFEST.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if write_header:
            w.writeheader()

        def save(symbol: str, sub: pd.DataFrame, tip_time: pd.Timestamp,
                 dense: bool, spread: float) -> None:
            nonlocal saved_dense, saved_empty
            name = f"{symbol}_{tip_time.strftime('%Y%m%d_%H%M')}.png"
            render_chart(sub, out_path=day_dir / name)
            w.writerow({
                "symbol": symbol,
                "tip_time": str(tip_time),
                "tip_dense": dense,
                "mean_full_spread": round(spread, 6) if spread == spread else "",
                "saved_at": now.isoformat(),
                "png": str((day_dir / name).relative_to(PROJECT)),
            })
            if dense:
                saved_dense += 1
            else:
                saved_empty += 1

        for source, symbol, frame in iter_series(bar="15m", min_bars=TAIL_BARS):
            if time.monotonic() - t0 > BUDGET_SEC:
                print(f"real_tip_collect: budget hit after {scanned} series", flush=True)
                break
            if source != "okx" or not symbol.endswith("_USDT_SWAP"):
                continue
            if is_stockish(symbol) or is_eval_symbol(symbol):
                continue
            scanned += 1
            tail = frame.tail(TAIL_BARS).reset_index(drop=True)
            enriched = add_mas(tail)
            sub = enriched.iloc[-WINDOW:].reset_index(drop=True)
            tip_time = pd.Timestamp(sub["open_time"].iloc[-1])
            tip_time = tip_time.tz_localize("UTC") if tip_time.tzinfo is None else tip_time.tz_convert("UTC")
            prev = last_seen.get(symbol)
            if prev is not None and tip_time - prev < gap:
                continue
            try:
                segs = find_dense_segments(sub)
            except Exception:
                continue
            dense = any(getattr(s, "end", -1) >= WINDOW - TIP_DENSE_HIT_BARS for s in segs)
            spread = float(pd.to_numeric(sub["full_spread"], errors="coerce").iloc[-12:].mean())
            (dense_pool if dense else empty_pool).append((symbol, sub, tip_time, spread))

        for pool, cap, is_dense in (
            (dense_pool, DENSE_CAP_PER_PULSE, True),
            (empty_pool, EMPTY_SAMPLES_PER_PULSE, False),
        ):
            for symbol, sub, tip_time, spread in rng.sample(pool, min(cap, len(pool))):
                save(symbol, sub, tip_time, is_dense, spread)
                last_seen[symbol] = tip_time

    print(
        f"real_tip_collect: scanned={scanned} dense_saved={saved_dense} "
        f"empty_saved={saved_empty} wall={time.monotonic() - t0:.0f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
