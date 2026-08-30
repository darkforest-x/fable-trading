"""Build the outcome-labelled 5m dataset: one render per pattern.

Two changes from the frozen recipe, both aimed at the same failure.

Labels come from what the trade did, not from whether it started. The frozen
gate calls a pattern positive when price moved past a floor at core+1/2/3/5,
which the model can partly SEE inside its own window -- so it learns "did the
last two bars move" rather than the compression, scores mAP 0.91 on that
triviality, and then fires on everything at inference. Here a take-profit is
positive and a stop-out is negative, so both classes contain a launch in
progress and that shortcut is worth nothing. Timeouts are dropped: 12 hours
touching neither barrier is neither outcome.

One render per pattern, not eight. The eight (pre, post) positions exist to
stop the detector learning a fixed box location, but rendering every position
for every event multiplies images without adding information: 8,000 images
were only 1,043 patterns, and the model peaked by epoch 6 and decayed for the
remaining 34. Drawing one position at random per event breaks the location
shortcut across the dataset just as well, at an eighth of the images and with
no room to memorise a pattern from its repeats.

This makes the comparison against the eight-render build a clean single
variable: same patterns, same boxes, same split, same labels.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas, ALL_MA_COLS  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

CANDIDATES = ROOT / "analysis/output/ma_launch_5m_candidates_20260830/candidates_5m.jsonl"
OUTCOMES = ROOT / "analysis/output/ma_launch_5m_outcomes_20260830/outcomes_5m.csv"
DST = ROOT / "datasets/ma_launch_5m_outcome_v1"
MA_COLS = list(ALL_MA_COLS)
PAD_FRACTION = 0.04
CLASS_ID = {"LONG": 0, "SHORT": 1}
POSITIONS = tuple((5 + i, 9 - i) for i in range(8))       # the frozen (pre, post) ledger
SPLIT_CUTOFF = pd.Timestamp("2025-12-01T00:00:00Z")
PURGE = pd.Timedelta(minutes=5 * 450)                     # 37.5h, matching the 15m isolation
HORIZON_COL = "outcome_144"                               # 12h: timeouts fall from 358 to 85
SEED = 20260830


def core_box(transform: Any, window: pd.DataFrame, start_local: int, end_local: int) -> dict[str, float]:
    core = window.iloc[start_local : end_local + 1]
    values = np.concatenate((core["high"].to_numpy(float), core["low"].to_numpy(float),
                             core.loc[:, MA_COLS].to_numpy(float).ravel()))
    if not np.isfinite(values).all():
        raise ValueError("non-finite core value")
    high, low = float(values.max()), float(values.min())
    pad = (high - low) * PAD_FRACTION
    x0 = transform.x_at(start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(end_local) + transform.candle_half_w + 2
    y0, y1 = transform.y_at(high + pad), transform.y_at(low - pad)
    x0, x1 = max(0.0, min(x0, x1)), min(float(transform.width), max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(float(transform.height), max(y0, y1))
    return {"cx": (x0 + x1) / 2 / transform.width, "cy": (y0 + y1) / 2 / transform.height,
            "w": (x1 - x0) / transform.width, "h": (y1 - y0) / transform.height}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit-sources", type=int, default=0)
    args = ap.parse_args()

    outcomes = pd.read_csv(OUTCOMES)
    # The outcome table is keyed by symbol + core_end_time, not by event id.
    key = {(str(r.symbol), str(r.core_end_time)): str(getattr(r, HORIZON_COL))
           for r in outcomes.itertuples()}

    patterns = [json.loads(l) for l in CANDIDATES.read_text().splitlines() if l.strip()]
    by_source: dict[str, list[dict]] = defaultdict(list)
    for row in patterns:
        by_source[row["source_path"]].append(row)
    sources = sorted(by_source)
    if args.limit_sources:
        sources = sources[: args.limit_sources]
    print(f"patterns {len(patterns)}  sources {len(sources)}", flush=True)

    for split in ("train", "val"):
        (DST / "images" / split).mkdir(parents=True, exist_ok=True)
        (DST / "labels" / split).mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    stats: Counter[str] = Counter()
    manifest: list[dict] = []

    for index, source in enumerate(sources, 1):
        try:
            frame, _ = read_preholdout_prefix(ROOT / source, end_exclusive=HOLDOUT_START, bar_minutes=5)
            enriched = add_mas(frame)
        except Exception as exc:  # noqa: BLE001
            stats[f"source unreadable: {type(exc).__name__}"] += 1
            continue
        times = pd.to_datetime(enriched["open_time"], utc=True)

        for row in by_source[source]:
            outcome = key.get((str(row["symbol"]), str(row["core_end_time"])))
            if outcome == "tp":
                label_class, kind = row["direction"], "positive"
            elif outcome == "sl":
                label_class, kind = None, "negative"
            else:
                stats[f"dropped ({outcome or 'unsimulated'})"] += 1
                continue

            core_start, core_end = int(row["source_core_start_i"]), int(row["source_core_end_i"])
            core_time = times.iloc[core_end]
            if abs(core_time - SPLIT_CUTOFF) < PURGE:
                stats["dropped in purge band"] += 1
                continue
            split = "train" if core_time < SPLIT_CUTOFF else "val"

            pre, post = POSITIONS[rng.randrange(len(POSITIONS))]
            ws, we = core_start - pre, core_end + post
            if ws < 200 or we >= len(enriched) - 1:
                stats["window out of range"] += 1
                continue
            window = enriched.iloc[ws : we + 1]
            if window[MA_COLS].isna().any().any():
                stats["MA warmup incomplete"] += 1
                continue

            image, transform = render_chart(window, out_path=None)
            name = f"{kind[0].upper()}_{row['symbol']}_{row['event_id'][:12]}"
            cv2.imwrite(str(DST / "images" / split / f"{name}.png"), image)
            if label_class is None:
                (DST / "labels" / split / f"{name}.txt").write_text("")
            else:
                box = core_box(transform, window, core_start - ws, core_end - ws)
                (DST / "labels" / split / f"{name}.txt").write_text(
                    f"{CLASS_ID[label_class]} {box['cx']:.6f} {box['cy']:.6f} "
                    f"{box['w']:.6f} {box['h']:.6f}\n")
            stats[f"{kind} {split}"] += 1
            manifest.append({"sample_kind": kind, "barrier_outcome": outcome, "name": name,
                             "split": split, "symbol": row["symbol"], "direction": row["direction"],
                             "event_id": row["event_id"], "timeframe": "5m",
                             "pre_bars": pre, "post_bars": post, "core_bars": int(row["core_bars"]),
                             "window_start_i": ws, "window_end_i": we, "source_path": source})
        if index % 50 == 0:
            print(f"  {index}/{len(sources)} sources, {len(manifest)} images", flush=True)

    (DST / "manifest.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in manifest))
    (DST / "data.yaml").write_text(
        f"path: {DST}\ntrain: images/train\nval: images/val\n"
        f"names:\n  0: dense_long\n  1: dense_short\n")
    (DST / "build_receipt.json").write_text(json.dumps({
        "rule": "barrier tp -> positive, sl -> negative, timeout -> dropped",
        "horizon_bars": 144, "horizon_hours": 12,
        "renders_per_pattern": 1, "positions_available": list(POSITIONS), "seed": SEED,
        "split_cutoff": str(SPLIT_CUTOFF), "purge_bars_each_side": 450,
        "stats": dict(stats), "holdout_read": False,
        "training_eligible": False, "production_eligible": False,
    }, indent=2, ensure_ascii=False) + "\n")

    print("\n=== build ===")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {k:36} {v}")
    pos = sum(v for k, v in stats.items() if k.startswith("positive "))
    neg = sum(v for k, v in stats.items() if k.startswith("negative "))
    print(f"\n  positives {pos}  negatives {neg}  ratio 1:{neg/max(pos,1):.2f}")
    print(f"wrote {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
