#!/usr/bin/env python3
"""Fast 10–20 GT overlay sample for pad200 train set (CPU, no YOLO)."""
from __future__ import annotations

import json
import random
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
TIP_X = 0.95


def read_boxes(p: Path) -> list[tuple[float, float, float, float]]:
    out = []
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 5:
            out.append(tuple(map(float, parts[1:5])))
    return out


def draw(img: np.ndarray, boxes) -> np.ndarray:
    vis = img.copy()
    h, w = vis.shape[:2]
    tip = int(TIP_X * w)
    cv2.line(vis, (tip, 0), (tip, h), (0, 220, 220), 1, cv2.LINE_AA)
    for xc, yc, bw, bh in boxes:
        x1 = int((xc - bw / 2) * w)
        y1 = int((yc - bh / 2) * h)
        x2 = int((xc + bw / 2) * w)
        y2 = int((yc + bh / 2) * h)
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 200, 0), 2, cv2.LINE_AA)
    return vis


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=PROJECT / "datasets/dense_owner_v14_pad200")
    ap.add_argument("--out", type=Path, default=PROJECT / "analysis/output/v14_train_sample20")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=14)
    args = ap.parse_args()

    img_dir = args.dataset / "images" / "train"
    lbl_dir = args.dataset / "labels" / "train"
    pads = sorted(img_dir.glob("*_pad200.png"))
    if not pads:
        raise SystemExit(f"no pad200 images in {img_dir}")

    rng = random.Random(args.seed)
    # Prefer okx_ + non-okx mix
    okx = [p for p in pads if p.name.startswith("okx_")]
    other = [p for p in pads if not p.name.startswith("okx_")]
    n_okx = min(len(okx), max(6, args.n // 3))
    pick = rng.sample(okx, n_okx) if okx else []
    remain = args.n - len(pick)
    if remain > 0 and other:
        pick += rng.sample(other, min(remain, len(other)))
    if len(pick) < args.n:
        rest = [p for p in pads if p not in pick]
        pick += rng.sample(rest, min(args.n - len(pick), len(rest)))

    raw_d = args.out / "raw"
    ann_d = args.out / "annotated"
    raw_d.mkdir(parents=True, exist_ok=True)
    ann_d.mkdir(parents=True, exist_ok=True)

    rows = []
    rights = []
    for i, img_p in enumerate(pick, 1):
        stem = img_p.stem
        lbl = lbl_dir / f"{stem}.txt"
        boxes = read_boxes(lbl)
        img = cv2.imread(str(img_p))
        if img is None:
            continue
        right = max((xc + bw / 2) for xc, _, bw, _ in boxes) if boxes else float("nan")
        rights.append(right)
        raw_name = f"{i:02d}_{stem}.png"
        ann_name = f"{i:02d}_{stem}_gt.png"
        cv2.imwrite(str(raw_d / raw_name), img)
        cv2.imwrite(str(ann_d / ann_name), draw(img, boxes))
        rows.append(
            {
                "idx": i,
                "stem": stem,
                "n_boxes": len(boxes),
                "right": None if right != right else round(float(right), 4),
                "raw_rel": f"raw/{raw_name}",
                "annotated_rel": f"annotated/{ann_name}",
            }
        )

    ge = sum(1 for r in rights if r == r and r >= TIP_X)
    manifest = {
        "dataset": str(args.dataset),
        "n": len(rows),
        "right_ge_0.95_in_sample": ge,
        "items": rows,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    cards = "\n".join(
        f'<div class="card"><h3>{r["idx"]}. {r["stem"]}</h3>'
        f'<p>right={r["right"]} boxes={r["n_boxes"]}</p>'
        f'<img src="{r["annotated_rel"]}" loading="lazy"/></div>'
        for r in rows
    )
    (args.out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>v14 pad200 sample</title>"
        "<style>body{font:14px sans-serif;background:#111;color:#eee}"
        ".card{margin:12px 0}img{max-width:100%;height:auto;border:1px solid #333}"
        "h3{margin:0 0 4px}</style>"
        f"<h1>v14 pad200 sample ({len(rows)})</h1>"
        f"<p>黄线 x=0.95；绿框=GT。sample right≥0.95: {ge}/{len(rows)}</p>"
        f"{cards}\n"
    )
    (args.out / "README.md").write_text(
        f"# v14 pad200 抽查 {len(rows)} 张\n\n"
        f"打开 `index.html`。数据集 `{args.dataset}`。\n"
        f"黄线=tip x=0.95；绿框=GT。\n"
    )
    print(json.dumps({"wrote": str(args.out), "n": len(rows), "right_ge_0.95": ge}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
