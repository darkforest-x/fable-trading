"""Build the 5m MA-launch YOLO dataset from the 3,940 gate-passing patterns.

Single variable against the 15m set: the bar interval. The renderer, the box
geometry, the eight render positions and the matched-negative pairing rules are
the 15m contract, reused rather than re-implemented, so a 5m sample differs
from a 15m sample only in what a bar means.

Two places where 15m constants had to be re-derived rather than copied, both
recorded in the preregistration:

  * the chronological purge is 450 bars, not 150. The 15m split purged 150
    bars, which is 37.5 hours; carrying the number instead of the duration
    would have shrunk the isolation gap to 12.5 hours purely because the bar
    got shorter, and train/val leakage is a property of wall-clock distance.

  * negative counts are not targeted. The 15m build aimed at 24,000 negative
    images; here the count is whatever the pairing rules yield, and a shortfall
    is reported instead of being back-filled by loosening them.

Negatives keep the frozen "completed no-launch" definition: same source file,
symbol, half-year, split, core length and render positions as their positive,
never overlapping a protected positive interval, and required to have gone
nowhere by core_end+2/3/5. Empty label, identical render pipeline.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.datasets.fifteen_minute_launch_candidates import (  # noqa: E402
    add_candidate_features, read_preholdout_prefix,
)
from yoyo.datasets.ma_launch_owner_grade_a_negatives import (  # noqa: E402
    PositiveEvent, select_source_negative_events,
)
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas, ALL_MA_COLS  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

PREREG = ROOT / "experiments/active/exp-5m-ma-launch-grade-a-neg-v1/preregistration.json"
MA_COLS = list(ALL_MA_COLS)
PAD_FRACTION = 0.04
CLASS_ID = {"LONG": 0, "SHORT": 1}
# The 15m ledger: eight positions whose pre+post always sums to 14.
VARIANTS = tuple((f"v{i}", i, 5 + i - 1, 9 - i + 1) for i in range(1, 9))


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
            "w": (x1 - x0) / transform.width, "h": (y1 - y0) / transform.height,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit-sources", type=int, default=0)
    args = ap.parse_args()

    prereg = json.loads(PREREG.read_text())
    dataset = ROOT / prereg["outputs"]["dataset_dir"]
    results = ROOT / prereg["outputs"]["results_dir"]
    cutoff = pd.Timestamp(prereg["split"]["cutoff"])
    purge = pd.Timedelta(minutes=5 * int(prereg["split"]["purge_bars_each_side"]))

    rows = [json.loads(l) for l in
            (ROOT / prereg["positive_source"]["candidates_path"]).read_text().splitlines() if l.strip()]
    by_source: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_source[row["source_path"]].append(row)
    sources = sorted(by_source)
    if args.limit_sources:
        sources = sources[: args.limit_sources]
    print(f"patterns {len(rows)}  sources {len(sources)}", flush=True)

    for split in ("train", "val"):
        (dataset / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset / "labels" / split).mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    stats: Counter[str] = Counter()
    manifest: list[dict] = []

    def render_variant(enriched, core_start, core_end, pre, post, split, name, direction):
        ws, we = core_start - pre, core_end + post
        if ws < 200 or we >= len(enriched) - 1:
            return None
        window = enriched.iloc[ws : we + 1]
        if window[MA_COLS].isna().any().any():
            return None
        image, transform = render_chart(window, out_path=None)
        cv2.imwrite(str(dataset / "images" / split / f"{name}.png"), image)
        if direction is None:
            (dataset / "labels" / split / f"{name}.txt").write_text("")
            return {"box": None, "window_start_i": ws, "window_end_i": we}
        box = core_box(transform, window, core_start - ws, core_end - ws)
        (dataset / "labels" / split / f"{name}.txt").write_text(
            f"{CLASS_ID[direction]} {box['cx']:.6f} {box['cy']:.6f} {box['w']:.6f} {box['h']:.6f}\n")
        return {"box": box, "window_start_i": ws, "window_end_i": we}

    for index, source in enumerate(sources, 1):
        path = ROOT / source
        try:
            frame, _ = read_preholdout_prefix(path, end_exclusive=HOLDOUT_START, bar_minutes=5)
            enriched = add_mas(frame)
            featured = add_candidate_features(frame)
        except Exception as exc:  # noqa: BLE001
            stats[f"source unreadable: {type(exc).__name__}"] += 1
            continue

        times = pd.to_datetime(enriched["open_time"], utc=True)
        positives: list[PositiveEvent] = []
        for row in by_source[source]:
            core_start, core_end = int(row["source_core_start_i"]), int(row["source_core_end_i"])
            core_time = times.iloc[core_end]
            if abs(core_time - cutoff) < purge:
                stats["dropped in purge band"] += 1
                continue
            split = "train" if core_time < cutoff else "val"
            positives.append(PositiveEvent(
                event_id=str(row["event_id"]), sample_id=str(row["event_id"]),
                event_order=len(positives) + 1, source_path=source, venue="binance_um",
                symbol=str(row["symbol"]), exchange_symbol=str(row["symbol"]),
                direction=str(row["direction"]), split=split,
                time_block=f"{core_time.year}H{1 if core_time.month <= 6 else 2}",
                core_bars=int(row["core_bars"]), core_start_i=core_start, core_end_i=core_end,
                core_start_time=str(row["core_start_time"]), core_end_time=str(row["core_end_time"]),
                variants=VARIANTS))
        if not positives:
            continue

        for event in positives:
            for variant_id, _, pre, post in event.variants:
                name = f"P_{event.event_id[:16]}_{variant_id}"
                out = render_variant(enriched, event.core_start_i, event.core_end_i,
                                     pre, post, event.split, name, event.direction)
                if out is None:
                    stats["positive variant out of range"] += 1
                    continue
                stats[f"positive {event.split}"] += 1
                manifest.append({"sample_kind": "positive", "name": name, "split": event.split,
                                 "event_id": event.event_id, "symbol": event.symbol,
                                 "direction": event.direction, "timeframe": "5m",
                                 "pre_bars": pre, "post_bars": post, "core_bars": event.core_bars,
                                 "core_start_i": event.core_start_i, "core_end_i": event.core_end_i,
                                 "source_path": source, **{k: v for k, v in out.items() if k != "box"}})

        try:
            negatives, _ = select_source_negative_events(
                featured, source_path=source, positives=positives,
                protected_candidates=by_source[source], prereg=prereg)
        except Exception as exc:  # noqa: BLE001 - a source without partners must not stop the build
            stats[f"negative selection failed: {type(exc).__name__}"] += 1
            negatives = []
        for negative in negatives:
            for variant_id, _, pre, post in negative.variants:
                name = f"N_{negative.negative_event_id[:16]}_{variant_id}"
                out = render_variant(enriched, negative.core_start_i, negative.core_end_i,
                                     pre, post, negative.split, name, None)
                if out is None:
                    stats["negative variant out of range"] += 1
                    continue
                stats[f"negative {negative.split} ({negative.negative_kind})"] += 1
                manifest.append({"sample_kind": "negative", "name": name, "split": negative.split,
                                 "negative_event_id": negative.negative_event_id,
                                 "paired_positive_event_id": negative.paired_positive_event_id,
                                 "negative_kind": negative.negative_kind, "symbol": negative.symbol,
                                 "timeframe": "5m", "pre_bars": pre, "post_bars": post,
                                 "source_path": source, **{k: v for k, v in out.items() if k != "box"}})

        if index % 25 == 0:
            pos = sum(v for k, v in stats.items() if k.startswith("positive "))
            neg = sum(v for k, v in stats.items() if k.startswith("negative ") and "(" in k)
            print(f"  {index}/{len(sources)} sources  pos {pos}  neg {neg}", flush=True)

    (dataset / "manifest.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in manifest))
    (dataset / "data.yaml").write_text(
        f"path: {dataset}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_long\n  1: dense_short\n")
    (results / "build_receipt.json").write_text(json.dumps({
        "preregistration": str(PREREG.relative_to(ROOT)),
        "timeframe": "5m", "stats": dict(stats),
        "split_cutoff": str(cutoff), "purge_bars_each_side": int(prereg["split"]["purge_bars_each_side"]),
        "holdout_read": False, "training_eligible": False, "production_eligible": False,
    }, indent=2, ensure_ascii=False) + "\n")

    print("\n=== build ===")
    for key, value in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {key:44} {value}")
    pos = sum(v for k, v in stats.items() if k.startswith("positive "))
    neg = sum(v for k, v in stats.items() if k.startswith("negative ") and "(" in k)
    print(f"\n  positives {pos}   negatives {neg}   ratio 1:{neg/max(pos,1):.1f}")
    print(f"wrote {dataset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
