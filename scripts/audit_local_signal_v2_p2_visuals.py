#!/usr/bin/env python3
"""Create the pre-training P2 label and hard-negative visual audit package.

Outputs deterministic 200-sample positive, train-hard-negative, and
evaluation-hard-negative montages plus geometry/count distributions required by
the Local Signal V2 handoff. Positive boxes come from YOLO labels; hard-negative
red boxes are the frozen P1 B2 predictions that caused each window to be mined.
No outcome data or project holdout is read.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT / "datasets/local_signal_v2_p2_hardneg_r1"
DEFAULT_OUT = PROJECT / "analysis/output/p2_local_signal_v2_visual_audit_20260811"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
SEED = 20260811
N_MONTAGE = 200


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def xywhn_to_xyxy(box: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    """Convert normalized YOLO center geometry into bounded pixel corners."""
    x, y, w, h = (float(value) for value in box[:4])
    return (
        max(0, min(width - 1, round((x - w / 2) * width))),
        max(0, min(height - 1, round((y - h / 2) * height))),
        max(0, min(width - 1, round((x + w / 2) * width))),
        max(0, min(height - 1, round((y + h / 2) * height))),
    )


def yolo_label_boxes(path: Path) -> list[dict]:
    boxes = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"invalid YOLO label in {path}: {line!r}")
        boxes.append({"confidence": None, "xywhn": [float(value) for value in parts[1:]]})
    return boxes


def deterministic_sample(rows: list[dict], n: int, *, seed: int) -> list[dict]:
    if len(rows) <= n:
        return list(rows)
    rng = np.random.default_rng(seed)
    indexes = sorted(int(value) for value in rng.choice(len(rows), size=n, replace=False))
    return [rows[index] for index in indexes]


def write_montage(
    rows: list[dict],
    out: Path,
    *,
    n: int,
    seed: int,
    prediction_boxes: bool,
) -> dict:
    chosen = deterministic_sample(rows, n, seed=seed)
    cols = 10
    tile_w, image_h, header_h = 240, 140, 20
    tile_h = image_h + header_h
    grid_rows = math.ceil(len(chosen) / cols)
    canvas = Image.new("RGB", (cols * tile_w, grid_rows * tile_h), "white")
    for index, row in enumerate(chosen):
        image_path = PROJECT / row["image_path"]
        image = Image.open(image_path).convert("RGB")
        width, height = image.size
        draw = ImageDraw.Draw(image)
        boxes = row.get("mining_boxes", []) if prediction_boxes else yolo_label_boxes(PROJECT / row["label_path"])
        color = "#ff3030" if prediction_boxes else "#00cc44"
        for box in boxes:
            xyxy = xywhn_to_xyxy(box["xywhn"], width, height)
            draw.rectangle(xyxy, outline=color, width=5)
        image.thumbnail((tile_w, image_h), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (tile_w, tile_h), "white")
        tile.paste(image, ((tile_w - image.width) // 2, header_h))
        label = str(row.get("sample_id") or row.get("stem"))[:30]
        if prediction_boxes and boxes:
            label += f" c={max(float(box['confidence']) for box in boxes):.2f}"
        ImageDraw.Draw(tile).text((3, 3), label, fill="black")
        x = index % cols * tile_w
        y = index // cols * tile_h
        canvas.paste(tile, (x, y))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, optimize=True)
    return {"path": str(out.relative_to(PROJECT)), "available": len(rows), "shown": len(chosen)}


def geometry(rows: list[dict]) -> dict[str, list[float]]:
    result: dict[str, list[float]] = {
        "anchor_x": [],
        "window_len": [],
        "box_width": [],
        "box_height": [],
        "confirm_delay": [],
    }
    for row in rows:
        if row.get("anchor_x_ratio") is not None:
            result["anchor_x"].append(float(row["anchor_x_ratio"]))
        if row.get("window_len") is not None:
            result["window_len"].append(float(row["window_len"]))
        if row.get("confirm_delay") is not None:
            result["confirm_delay"].append(float(row["confirm_delay"]))
        for box in yolo_label_boxes(PROJECT / row["label_path"]):
            result["box_width"].append(float(box["xywhn"][2]))
            result["box_height"].append(float(box["xywhn"][3]))
    return result


def write_distributions(positive_rows: list[dict], all_rows: list[dict], out: Path) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    values = geometry(positive_rows)
    fig, axes = plt.subplots(2, 3, figsize=(16, 9), constrained_layout=True)
    specs = (
        ("anchor_x", "Anchor x ratio", 20),
        ("window_len", "Window length", 11),
        ("box_width", "Box width (normalized)", 20),
        ("box_height", "Box height (normalized)", 20),
        ("confirm_delay", "Confirm delay (bars)", 5),
    )
    for axis, (key, title, bins) in zip(axes.flat[:5], specs):
        axis.hist(values[key], bins=bins, color="#2878b5", edgecolor="white")
        axis.set_title(title)
        axis.set_ylabel("samples")
    symbols = Counter(str(row["symbol"]) for row in all_rows).most_common(20)
    axes.flat[5].barh([name for name, _ in reversed(symbols)], [count for _, count in reversed(symbols)])
    axes.flat[5].set_title("Top-20 symbols (training manifest)")
    axes.flat[5].set_xlabel("samples")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return {
        "path": str(out.relative_to(PROJECT)),
        "anchor_x": {"min": min(values["anchor_x"]), "max": max(values["anchor_x"])},
        "window_len_counts": dict(Counter(values["window_len"])),
        "confirm_delay_counts": dict(Counter(values["confirm_delay"])),
        "box_width": {"min": min(values["box_width"]), "max": max(values["box_width"])},
        "box_height": {"min": min(values["box_height"]), "max": max(values["box_height"])},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--n", type=int, default=N_MONTAGE)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    manifest = read_jsonl(args.dataset / "manifest.jsonl")
    positives = [row for row in manifest if row.get("sample_type") == "positive"]
    train_hard = read_jsonl(args.dataset / "hard_negative_bank.jsonl")
    heldout_hard = read_jsonl(args.dataset / "heldout_hard_negative_bank.jsonl")
    if not positives or len(train_hard) < 100 or len(heldout_hard) < 100:
        raise ValueError("visual audit requires positives and at least 100 hard negatives per bank")
    end_times = [pd.Timestamp(row["end_time"]) for row in train_hard + heldout_hard]
    if max(end_times) >= HOLDOUT_START:
        raise ValueError("visual audit bank touches holdout")

    montages = {
        "positive": write_montage(
            positives,
            args.out_dir / "positive_montage_200.png",
            n=args.n,
            seed=args.seed,
            prediction_boxes=False,
        ),
        "train_hard_negative": write_montage(
            train_hard,
            args.out_dir / "train_hard_negative_montage_200.png",
            n=args.n,
            seed=args.seed + 1,
            prediction_boxes=True,
        ),
        "heldout_hard_negative": write_montage(
            heldout_hard,
            args.out_dir / "heldout_hard_negative_montage_200.png",
            n=args.n,
            seed=args.seed + 2,
            prediction_boxes=True,
        ),
    }
    distributions = write_distributions(
        positives, manifest, args.out_dir / "geometry_and_counts.png"
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset.relative_to(PROJECT)),
        "seed": args.seed,
        "montages": montages,
        "distributions": distributions,
        "counts": {
            "positive": len(positives),
            "train_hard_negative": len(train_hard),
            "heldout_hard_negative": len(heldout_hard),
            "events": len({row["event_id"] for row in positives}),
            "by_split": dict(Counter(str(row["split"]) for row in manifest)),
            "by_timeframe": dict(Counter(str(row["timeframe"]) for row in manifest)),
            "by_symbol": dict(Counter(str(row["symbol"]) for row in manifest)),
        },
        "time_range": {"hard_negative_min": str(min(end_times)), "hard_negative_max": str(max(end_times))},
        "future_outcome_used": False,
        "holdout_read": False,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"montages": montages, "counts": summary["counts"], "time_range": summary["time_range"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
