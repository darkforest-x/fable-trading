"""Re-box the Grade-A positives onto the densest MA-crossing block, per image.

Owner observation (2026-08-30), confirmed on all 1,043 events: the frozen box
ends AT the point where the six moving averages converge, instead of centring
on it. It captures a
median of 3 pairwise crossings where the best equal-length block in the same
window holds 6, and only 9.5% of events are already on that best block. The
owner's spec is that the crossing cluster should sit in the MIDDLE of the box,
not at its right edge.

Two things this deliberately does NOT do:

  * No global offset. The required shift ranges from -4 to +5 bars and 15.6% of
    events need to move LEFT, so one uniform delta would push those further
    from the truth while merely swapping one position shortcut for another --
    the failure recorded in
    docs/learnings/per-image-reboxing-needs-indexed-boundaries-not-global-offsets.md.
    Every event is re-anchored to its own argmax block.

  * No window lengthening. When the corrected box would run past the frame the
    whole 18-bar window SLIDES right instead of growing, so window_bars stays
    the contract it has always been and the frame keeps a uniform bar pitch.
    Sliding costs pre-context (median 2 bars, floor enforced at MIN_PRE_BARS)
    and buys post-context, which is the honest price of looking at a cluster
    that finishes later than the old box claimed.

Dropping the samples that cannot slide is the third option and it is rejected:
it would truncate the post_bars distribution, and that distribution is the
anti-shortcut device stopping YOLO learning "the box is always here".

Images are re-rendered ONLY where the window slid. Everywhere else the source
PNG is copied byte-for-byte, and the builder asserts its own renderer
reproduces an untouched image exactly before it writes anything -- otherwise
slid and copied images would differ in style and the model would have a new
shortcut to find.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.layers.l1_detection.data import add_mas, ALL_MA_COLS  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

SRC = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
DST = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_crossbox_v1"
MA_COLS = list(ALL_MA_COLS)
MA_PAIRS = list(itertools.combinations(MA_COLS, 2))
PAD_FRACTION = 0.04
MIN_PRE_BARS = 3
SEARCH_PRE, SEARCH_POST = 6, 12
CLASS_ID = {"LONG": 0, "SHORT": 1}


class CrossBoxError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def crossing_counts(enriched: pd.DataFrame, lo: int, hi: int) -> np.ndarray:
    """Pairwise MA crossings on each bar of [lo, hi].

    A crossing is a sign flip of (ma_a - ma_b) between consecutive bars, so bar
    lo-1 must exist to judge bar lo.
    """
    seg = enriched[MA_COLS].iloc[lo - 1 : hi + 1]
    counts = np.zeros(hi - lo + 1, dtype=int)
    for a, b in MA_PAIRS:
        sign = np.sign((seg[a] - seg[b]).to_numpy(dtype=float))
        counts += (sign[1:] * sign[:-1] < 0).astype(int)
    return counts


def densest_block_shift(enriched: pd.DataFrame, core_start: int, core_end: int) -> int:
    """Bars to move the core so the six MAs' convergence point sits in its middle.

    Convergence is measured as MINIMUM six-MA bandwidth, not maximum pairwise
    crossing count. Crossing count was tried first and is wrong: it is sparse
    (0-3 per bar) and, worse, it keeps firing while the bundle FANS OUT, because
    a fast MA sweeping down through the slow ones scores crossings all the way
    through the launch. On NOT_USDT_SWAP the crossing argmax landed on bar 10
    with bandwidth already expanding 74 -> 89 -> 103 bps, dragging the box
    entirely into the launch, while the actual convergence sat one bar earlier
    at 65.9 bps. Bandwidth is the smooth, monotone-into-the-launch measure of
    "all six lines passing through one point", which is what the eye reads as
    the densest crossing.

    Ties keep the earliest bar, biasing towards less future.
    """
    lo = core_start - SEARCH_PRE
    hi = min(core_end + SEARCH_POST, len(enriched) - 2)
    if lo < 1 or hi <= lo:
        raise CrossBoxError("convergence search range does not fit in the source series")
    seg = enriched[MA_COLS].iloc[lo : hi + 1]
    close = enriched["close"].iloc[lo : hi + 1]
    bandwidth = ((seg.max(axis=1) - seg.min(axis=1)) / close).to_numpy(dtype=float)
    if not np.isfinite(bandwidth).all():
        raise CrossBoxError("non-finite bandwidth in convergence search range")
    tightest = lo + int(np.argmin(bandwidth))
    return int(round(tightest - (core_start + core_end) / 2))


def core_box(transform: Any, window: pd.DataFrame, start_local: int, end_local: int) -> dict[str, Any]:
    """Same geometry as yoyo.datasets.ma_launch_density_core_box_review, minus
    that module's hard-coded five-bar core: this dataset carries 4 and 5."""
    core = window.iloc[start_local : end_local + 1]
    values = np.concatenate((
        core["high"].to_numpy(dtype=float),
        core["low"].to_numpy(dtype=float),
        core.loc[:, MA_COLS].to_numpy(dtype=float).ravel(),
    ))
    if not np.isfinite(values).all():
        raise CrossBoxError("non-finite core OHLC/MA value")
    raw_high, raw_low = float(values.max()), float(values.min())
    if raw_high <= raw_low:
        raise CrossBoxError("core price extent is empty")
    pad = (raw_high - raw_low) * PAD_FRACTION
    x0 = transform.x_at(start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(end_local) + transform.candle_half_w + 2
    y0, y1 = transform.y_at(raw_high + pad), transform.y_at(raw_low - pad)
    x0, x1 = max(0.0, min(x0, x1)), min(float(transform.width), max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(float(transform.height), max(y0, y1))
    return {
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "cx_norm": (x0 + x1) / 2.0 / transform.width,
        "cy_norm": (y0 + y1) / 2.0 / transform.height,
        "w_norm": (x1 - x0) / transform.width,
        "h_norm": (y1 - y0) / transform.height,
    }


def self_check(records: list[dict[str, Any]], enriched_of) -> None:
    """Refuse to build unless renderer AND box formula reproduce the source.

    Running successfully is not evidence the geometry is right; matching the
    frozen bytes and the frozen label to 1e-9 is.
    """
    checked = 0
    for rec in records:
        if rec.get("sample_kind") != "positive":
            continue
        enriched = enriched_of(rec["source_path"])
        ws, we = int(rec["window_start_i"]), int(rec["window_end_i"])
        window = enriched.iloc[ws : we + 1]
        image, transform = render_chart(window, out_path=None)
        on_disk = cv2.imread(str(SRC / rec["image_path"]))
        if not np.array_equal(image, on_disk):
            raise CrossBoxError(f"renderer differs from frozen image: {rec['image_path']}")
        box = core_box(transform, window,
                       int(rec["source_core_start_i"]) - ws, int(rec["source_core_end_i"]) - ws)
        for key in ("cx_norm", "cy_norm", "w_norm", "h_norm"):
            if abs(box[key] - float(rec["box"][key])) > 1e-9:
                raise CrossBoxError(f"box formula differs on {key}: {rec['image_path']}")
        checked += 1
        if checked >= 25:
            return
    raise CrossBoxError("no positive samples available for self-check")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="debug: cap positives processed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    records = [json.loads(l) for l in (SRC / "manifest.jsonl").read_text().splitlines() if l.strip()]
    print(f"source manifest rows: {len(records)}")

    cache: dict[str, pd.DataFrame] = {}
    def enriched_of(path: str) -> pd.DataFrame:
        if path not in cache:
            cache[path] = add_mas(pd.read_csv(ROOT / path if not Path(path).is_absolute() else path))
            if len(cache) > 60:
                cache.pop(next(iter(cache)))
        return cache[path]

    print("self-check: renderer and box formula vs frozen dataset ...", flush=True)
    self_check(records, enriched_of)
    print("  OK - reproduces frozen images and labels exactly\n")

    shifts: dict[str, int] = {}
    out_manifest: list[dict[str, Any]] = []
    stats = Counter()
    if not args.dry_run:
        for split in ("train", "val"):
            (DST / "images" / split).mkdir(parents=True, exist_ok=True)
            (DST / "labels" / split).mkdir(parents=True, exist_ok=True)

    done = 0
    for rec in records:
        split = rec["split"]
        img_rel, lab_rel = rec["image_path"], rec["label_path"]

        if rec.get("sample_kind") != "positive":
            stats["negative copied"] += 1
            if not args.dry_run:
                shutil.copyfile(SRC / img_rel, DST / img_rel)
                (DST / lab_rel).write_text("")
            out_manifest.append({**rec, "crossbox_shift": None, "crossbox_window_slide": 0})
            continue

        if args.limit and done >= args.limit:
            break
        enriched = enriched_of(rec["source_path"])
        cs, ce = int(rec["source_core_start_i"]), int(rec["source_core_end_i"])
        eid = rec["event_id"]
        if eid not in shifts:
            shifts[eid] = densest_block_shift(enriched, cs, ce)
        shift = shifts[eid]

        ws, we = int(rec["window_start_i"]), int(rec["window_end_i"])
        new_cs, new_ce = cs + shift, ce + shift
        slide = max(0, new_ce - we + 1)                       # keep one bar of room
        if slide and (cs - ws) - slide < MIN_PRE_BARS:
            stats["dropped: not enough pre-context to slide"] += 1
            continue
        ws, we = ws + slide, we + slide
        if we >= len(enriched) - 1 or new_cs < ws:
            stats["dropped: window leaves the source series"] += 1
            continue

        window = enriched.iloc[ws : we + 1]
        if window[MA_COLS].isna().any().any():
            stats["dropped: MA warmup incomplete"] += 1
            continue
        image, transform = render_chart(window, out_path=None)
        box = core_box(transform, window, new_cs - ws, new_ce - ws)
        cls = CLASS_ID[rec["direction"]]
        label = f"{cls} {box['cx_norm']:.6f} {box['cy_norm']:.6f} {box['w_norm']:.6f} {box['h_norm']:.6f}\n"

        if not args.dry_run:
            cv2.imwrite(str(DST / img_rel), image)
            (DST / lab_rel).write_text(label)

        stats["positive re-boxed"] += 1
        stats["  window slid" if slide else "  image unchanged"] += 1
        out_manifest.append({
            **rec,
            "crossbox_shift": shift,
            "crossbox_window_slide": slide,
            "crossbox_core_start_i": new_cs,
            "crossbox_core_end_i": new_ce,
            "window_start_i": ws,
            "window_end_i": we,
            "pre_bars": new_cs - ws,
            "post_bars": we - new_ce,
            "box": {**rec["box"], **box},
            "image_sha256": sha256_bytes(cv2.imencode(".png", image)[1].tobytes()),
            "label_sha256": sha256_bytes(label.encode()),
        })
        done += 1
        if done % 500 == 0:
            print(f"  {done} positives ...", flush=True)

    print("\n=== build stats ===")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {k:44} {v}")
    dist = Counter(shifts.values())
    print(f"\n  events re-anchored: {len(shifts)}")
    print(f"  shift distribution: {dict(sorted(dist.items()))}")

    if not args.dry_run:
        (DST / "manifest.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out_manifest))
        shutil.copyfile(SRC / "data.yaml", DST / "data.yaml")
        yaml = (DST / "data.yaml").read_text().replace(SRC.name, DST.name)
        (DST / "data.yaml").write_text(yaml)
        (DST / "build_receipt.json").write_text(json.dumps({
            "source_dataset": SRC.name,
            "output_dataset": DST.name,
            "definition": "box centred on the bar of minimum six-MA bandwidth (the convergence point)",
            "ma_columns": MA_COLS,
            "ma_pairs": len(MA_PAIRS),
            "search_range_bars": [-SEARCH_PRE, SEARCH_POST],
            "tie_break": "earliest block (biases towards less future)",
            "overflow_policy": "slide the whole window right; window_bars unchanged",
            "min_pre_bars": MIN_PRE_BARS,
            "stats": dict(stats),
            "shift_distribution": {str(k): v for k, v in sorted(dist.items())},
            "images_re_rendered_only_when_window_slid": True,
            "holdout_read": False,
            "training_eligible": False,
            "production_eligible": False,
        }, indent=2, ensure_ascii=False) + "\n")
        print(f"\nwrote {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
