#!/usr/bin/env python3
"""Build YOLO dataset for Owner short-side boxes only (dual-pipeline step 1).

Reads analysis/output/owner_side_review/review_sheet.csv, keeps owner_side=short,
writes datasets/dense_owner_side_short/{images,labels}/{train,val} + data.yaml.
Single class dense_cluster=0. Holdout (>=2026-05-04) rows are dropped (sheet has none).

Usage:
  PYTHONPATH=. .venv/bin/python scripts/build_owner_side_short_yolo.py
"""
from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
SHEET = PROJECT / "analysis/output/owner_side_review/review_sheet.csv"
OUT = PROJECT / "datasets/dense_owner_side_short"
HOLDOUT = pd.Timestamp("2026-05-04", tz="UTC")


def main() -> int:
    df = pd.read_csv(SHEET)
    df = df[df["owner_side"].astype(str).str.lower() == "short"].copy()
    df["ct"] = pd.to_datetime(df["cut_time"], utc=True)
    df = df[df["ct"] < HOLDOUT]
    if df.empty:
        raise SystemExit("no short rows")

    # one label file per image; resolve split conflicts by majority then train
    by_img: dict[str, list[pd.Series]] = defaultdict(list)
    for _, r in df.iterrows():
        by_img[str(r["image_path"])].append(r)

    if OUT.exists():
        shutil.rmtree(OUT)
    for sp in ("train", "val"):
        (OUT / "images" / sp).mkdir(parents=True)
        (OUT / "labels" / sp).mkdir(parents=True)

    n_img = {"train": 0, "val": 0}
    n_box = {"train": 0, "val": 0}
    for rel, rows in by_img.items():
        src = PROJECT / rel
        if not src.is_file():
            print(f"missing image: {rel}")
            continue
        splits = [str(r["split"]) for r in rows]
        sp = "train" if splits.count("train") >= splits.count("val") else "val"
        if sp not in ("train", "val"):
            sp = "train"
        stem = Path(rel).stem
        dst_img = OUT / "images" / sp / f"{stem}.png"
        dst_lbl = OUT / "labels" / sp / f"{stem}.txt"
        if not dst_img.exists():
            dst_img.symlink_to(src.resolve())
        lines = []
        for r in rows:
            lines.append(
                f"0 {float(r['yolo_xc']):.6f} {float(r['yolo_yc']):.6f} "
                f"{float(r['yolo_w']):.6f} {float(r['yolo_h']):.6f}"
            )
        dst_lbl.write_text("\n".join(lines) + "\n")
        n_img[sp] += 1
        n_box[sp] += len(lines)

    yaml = f"""# Owner short-side tip boxes (from review_sheet). Single class.
path: {OUT}
train: images/train
val: images/val
names:
  0: dense_cluster
nc: 1
"""
    (OUT / "data.yaml").write_text(yaml)
    meta = {
        "source_sheet": str(SHEET),
        "n_short_rows": int(len(df)),
        "n_images": n_img,
        "n_boxes": n_box,
        "holdout_cut": str(HOLDOUT),
    }
    import json

    (OUT / "build_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    print(f"wrote {OUT}/data.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
