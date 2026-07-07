"""Build the YOLO dataset for dense MA-cluster detection from cached 15m candles.

Sliding-window rendering over multi-symbol 15m caches from the old project
(read-only). Train/val split is strictly by time per symbol: windows whose
last bar falls in the earliest 80% of that symbol's history go to train,
windows fully inside the latest 20% go to val, and windows straddling the
cutoff are dropped (no temporal leakage).

Usage:
  python -m src.detection.build_dataset --out datasets/dense_15m \
      --window 200 --stride 200 --max-images 3200
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .auto_label import CLASS_NAME, find_dense_segments, label_window, to_yolo_lines
from .data import add_mas, list_cache_files, load_ohlcv_csv
from .render import render_chart

# EMA(120) needs extra warmup beyond SMA's 120 bars for values to stabilize.
WINDOW_START_MIN = 360


@dataclass
class WindowSpec:
    symbol: str
    cache_path: Path
    start: int  # window start index into the enriched frame
    n_boxes: int
    split: str  # "train" | "val"


def _scan_symbol(path: Path, *, window: int, stride: int, train_frac: float) -> list[WindowSpec]:
    symbol = path.stem.rsplit("_", 2)[0]
    df = add_mas(load_ohlcv_csv(path))
    n = len(df)
    if n < WINDOW_START_MIN + window:
        return []
    cutoff = int(n * train_frac)
    specs: list[WindowSpec] = []
    for start in range(WINDOW_START_MIN, n - window + 1, stride):
        end = start + window  # exclusive
        if end <= cutoff:
            split = "train"
        elif start >= cutoff:
            split = "val"
        else:
            continue  # straddles the time cutoff -> drop
        sub = df.iloc[start:end]
        segs = find_dense_segments(sub.reset_index(drop=True))
        specs.append(WindowSpec(symbol, path, start, len(segs), split))
    return specs


def _balance(specs: list[WindowSpec], *, target_bg_frac: float, max_images: int, rng: random.Random) -> list[WindowSpec]:
    """Cap total images and keep background share near target by downsampling positives."""
    result: list[WindowSpec] = []
    for split in ("train", "val"):
        pool = [s for s in specs if s.split == split]
        bg = [s for s in pool if s.n_boxes == 0]
        pos = [s for s in pool if s.n_boxes > 0]
        split_cap = int(max_images * (0.8 if split == "train" else 0.2))
        # keep all backgrounds up to the target share; fill the rest with positives
        n_bg = min(len(bg), int(split_cap * target_bg_frac))
        n_pos = min(len(pos), split_cap - n_bg)
        rng.shuffle(bg)
        rng.shuffle(pos)
        result.extend(bg[:n_bg])
        result.extend(pos[:n_pos])
    return result


def build(out_dir: Path, *, window: int, stride: int, train_frac: float,
          target_bg_frac: float, max_images: int, seed: int) -> dict:
    rng = random.Random(seed)
    cache_files = list_cache_files(min_rows=10000)
    all_specs: list[WindowSpec] = []
    for path in cache_files:
        all_specs.extend(_scan_symbol(path, window=window, stride=stride, train_frac=train_frac))
    chosen = _balance(all_specs, target_bg_frac=target_bg_frac, max_images=max_images, rng=rng)

    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    # render chosen windows grouped by cache file to avoid reloading CSVs
    stats = Counter()
    box_counts: list[int] = []
    box_dims: list[tuple[float, float]] = []
    by_file: dict[Path, list[WindowSpec]] = {}
    for spec in chosen:
        by_file.setdefault(spec.cache_path, []).append(spec)
    for path, specs in by_file.items():
        df = add_mas(load_ohlcv_csv(path))
        for spec in specs:
            sub = df.iloc[spec.start : spec.start + window].reset_index(drop=True)
            name = f"{spec.symbol}_{spec.start:06d}"
            img_path = out_dir / "images" / spec.split / f"{name}.png"
            _, tf = render_chart(sub, out_path=img_path)
            boxes = label_window(sub, tf)
            (out_dir / "labels" / spec.split / f"{name}.txt").write_text(to_yolo_lines(boxes))
            stats[f"{spec.split}_images"] += 1
            stats[f"{spec.split}_boxes"] += len(boxes)
            if len(boxes) == 0:
                stats[f"{spec.split}_background"] += 1
            box_counts.append(len(boxes))
            box_dims.extend((w, h) for _, _, w, h in boxes)

    (out_dir / "data.yaml").write_text(
        f"path: {out_dir.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        f"  0: {CLASS_NAME}\n"
    )
    summary = {
        "symbols": len(by_file),
        "window": window,
        "stride": stride,
        "train_frac": train_frac,
        **{k: int(v) for k, v in sorted(stats.items())},
        "boxes_per_image_mean": round(sum(box_counts) / max(len(box_counts), 1), 3),
        "box_w_mean": round(sum(w for w, _ in box_dims) / max(len(box_dims), 1), 4),
        "box_h_mean": round(sum(h for _, h in box_dims) / max(len(box_dims), 1), 4),
    }
    (out_dir / "dataset_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="datasets/dense_15m")
    parser.add_argument("--window", type=int, default=200)
    parser.add_argument("--stride", type=int, default=200)
    parser.add_argument("--train-frac", type=float, default=0.8)
    parser.add_argument("--target-bg-frac", type=float, default=0.35)
    parser.add_argument("--max-images", type=int, default=3200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    summary = build(
        Path(args.out), window=args.window, stride=args.stride,
        train_frac=args.train_frac, target_bg_frac=args.target_bg_frac,
        max_images=args.max_images, seed=args.seed,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
