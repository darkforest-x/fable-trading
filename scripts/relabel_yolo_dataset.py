"""Rewrite YOLO label txts for an existing rendered dataset (images unchanged).

P2-11 E1 use case: after changing only `auto_label.X_PAD_PX`, recompute boxes
for every image already in the dataset without a full rebuild/re-render.

Image names must match build_dataset: `{symbol}_{start:06d}.png`.
Window length is read from dataset_summary.json (default 200).

Usage:
  PYTHONPATH=. python3 scripts/relabel_yolo_dataset.py \
      --dataset datasets/dense_15m_full --dry-run
  PYTHONPATH=. python3 scripts/relabel_yolo_dataset.py \
      --dataset datasets/dense_15m_full
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from src.detection.auto_label import X_PAD_PX, Y_PAD_FRAC, label_window, to_yolo_lines
from src.detection.data import add_mas, list_cache_files, load_ohlcv_csv
from src.detection.render import make_chart_transform

STEM_RE = re.compile(r"^(?P<symbol>.+)_(?P<start>\d{6})$")


def _index_cache_by_symbol(min_rows: int = 10000) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in list_cache_files(min_rows=min_rows):
        # e.g. PAXG_USDT_15m_17700.csv -> PAXG_USDT
        symbol = path.stem.rsplit("_", 2)[0]
        out[symbol] = path
    return out


def _iter_images(dataset: Path) -> list[tuple[str, Path, Path]]:
    """Return (split, image_path, label_path) for every png."""
    rows: list[tuple[str, Path, Path]] = []
    for split in ("train", "val"):
        img_dir = dataset / "images" / split
        lbl_dir = dataset / "labels" / split
        if not img_dir.is_dir():
            continue
        for img in sorted(img_dir.glob("*.png")):
            rows.append((split, img, lbl_dir / f"{img.stem}.txt"))
    return rows


def relabel(
    dataset: Path,
    *,
    window: int,
    dry_run: bool = False,
) -> dict:
    cache_by_symbol = _index_cache_by_symbol()
    items = _iter_images(dataset)
    stats: Counter = Counter()
    box_dims: list[tuple[float, float]] = []
    missing_symbol: Counter = Counter()
    frame_cache: dict[str, object] = {}

    for split, img_path, lbl_path in items:
        matched = STEM_RE.match(img_path.stem)
        if matched is None:
            stats["bad_stem"] += 1
            continue
        symbol = matched.group("symbol")
        start = int(matched.group("start"))
        cache_path = cache_by_symbol.get(symbol)
        if cache_path is None:
            missing_symbol[symbol] += 1
            stats["missing_cache"] += 1
            continue
        if symbol not in frame_cache:
            frame_cache[symbol] = add_mas(load_ohlcv_csv(cache_path))
        df = frame_cache[symbol]
        end = start + window
        if end > len(df) or start < 0:
            stats["bad_window"] += 1
            continue
        sub = df.iloc[start:end].reset_index(drop=True)
        # Transform-only: images stay on disk; boxes follow current auto_label pads.
        tf = make_chart_transform(sub)
        boxes = label_window(sub, tf)
        text = to_yolo_lines(boxes)
        if not dry_run:
            lbl_path.parent.mkdir(parents=True, exist_ok=True)
            lbl_path.write_text(text)
        stats[f"{split}_images"] += 1
        stats[f"{split}_boxes"] += len(boxes)
        if not boxes:
            stats[f"{split}_background"] += 1
        box_dims.extend((w, h) for _, _, w, h in boxes)

    summary = {
        "dataset": str(dataset.resolve()),
        "window": window,
        "x_pad_px": X_PAD_PX,
        "y_pad_frac": Y_PAD_FRAC,
        "dry_run": dry_run,
        "images_total": len(items),
        "missing_symbols": dict(missing_symbol),
        **{k: int(v) for k, v in sorted(stats.items())},
        "box_w_mean": round(sum(w for w, _ in box_dims) / max(len(box_dims), 1), 4),
        "box_h_mean": round(sum(h for _, h in box_dims) / max(len(box_dims), 1), 4),
        "n_boxes": len(box_dims),
    }
    if not dry_run:
        summary_path = dataset / "dataset_summary.json"
        prev = {}
        if summary_path.exists():
            try:
                prev = json.loads(summary_path.read_text())
            except json.JSONDecodeError:
                prev = {}
        prev.update(
            {
                "x_pad_px": X_PAD_PX,
                "y_pad_frac": Y_PAD_FRAC,
                "relabel_note": "labels rewritten in place; images unchanged",
                "box_w_mean": summary["box_w_mean"],
                "box_h_mean": summary["box_h_mean"],
                "train_boxes": summary.get("train_boxes", prev.get("train_boxes")),
                "val_boxes": summary.get("val_boxes", prev.get("val_boxes")),
            }
        )
        summary_path.write_text(json.dumps(prev, indent=2) + "\n")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="datasets/dense_15m_full")
    parser.add_argument("--window", type=int, default=0, help="0 = read dataset_summary.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dataset = Path(args.dataset)
    window = args.window
    if window <= 0:
        summary_path = dataset / "dataset_summary.json"
        if summary_path.exists():
            window = int(json.loads(summary_path.read_text()).get("window", 200))
        else:
            window = 200
    result = relabel(dataset, window=window, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    if result.get("missing_cache"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
