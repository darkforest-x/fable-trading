"""Build Label Studio import JSON from YOLO dense_15m_full split.

Each task = one image + GT boxes as pre-annotations (so you can accept/fix).
Does not start the server — use docker compose on port 8081.

Usage:
  python3 scripts/label_studio_prepare_import.py --split val --limit 80 --seed 20260709
  # then in Label Studio: Import → upload output/label_studio/tasks_val.json
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def yolo_to_pct_box(cx: float, cy: float, w: float, h: float) -> dict:
    """YOLO cxcywh normalized → Label Studio rectanglepercent top-left."""
    x = (cx - w / 2) * 100
    y = (cy - h / 2) * 100
    return {
        "x": max(0.0, x),
        "y": max(0.0, y),
        "width": w * 100,
        "height": h * 100,
        "rotation": 0,
    }


def load_boxes(txt: Path) -> list[tuple[float, float, float, float]]:
    if not txt.exists():
        return []
    boxes = []
    for line in txt.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 5:
            _, cx, cy, w, h = map(float, parts[:5])
            boxes.append((cx, cy, w, h))
    return boxes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="datasets/dense_15m_full")
    parser.add_argument("--split", default="val", choices=("val", "train"))
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260709)
    parser.add_argument(
        "--out",
        default="",
        help="default output/label_studio/tasks_<split>.json",
    )
    parser.add_argument(
        "--stratify",
        action="store_true",
        help="half with boxes, half background (like label_audit)",
    )
    args = parser.parse_args()

    root = Path(args.dataset)
    img_dir = root / "images" / args.split
    lbl_dir = root / "labels" / args.split
    images = sorted(img_dir.glob("*.png"))
    rng = random.Random(args.seed)

    if args.stratify:
        with_box, bg = [], []
        for p in images:
            boxes = load_boxes(lbl_dir / f"{p.stem}.txt")
            (with_box if boxes else bg).append(p)
        n_pos = min(len(with_box), args.limit * 2 // 3)
        n_bg = min(len(bg), args.limit - n_pos)
        sample = rng.sample(with_box, n_pos) + rng.sample(bg, n_bg)
        rng.shuffle(sample)
    else:
        sample = rng.sample(images, min(args.limit, len(images)))

    tasks = []
    for p in sample:
        boxes = load_boxes(lbl_dir / f"{p.stem}.txt")
        # Local files storage path as configured in compose
        image_url = f"/data/local-files/?d={Path(args.dataset).name}/images/{args.split}/{p.name}"
        results = []
        for i, (cx, cy, w, h) in enumerate(boxes):
            results.append(
                {
                    "id": f"gt_{p.stem}_{i}",
                    "type": "rectanglelabels",
                    "from_name": "label",
                    "to_name": "image",
                    "original_width": 1280,
                    "original_height": 742,
                    "image_rotation": 0,
                    "value": {
                        **yolo_to_pct_box(cx, cy, w, h),
                        "rectanglelabels": ["dense_cluster"],
                    },
                }
            )
        task = {
            "data": {
                "image": image_url,
                "stem": p.stem,
                "split": args.split,
            },
            "predictions": [
                {
                    "model_version": "auto_label_gt_E2",
                    "score": 1.0,
                    "result": results,
                }
            ]
            if results
            else [],
        }
        # Put GT only in predictions (pre-annotations). LS 1.23+ rejects
        # annotations.completed_by=0 ("not a valid annotator's email or ID").
        # Predictions show as reviewable boxes without requiring a real user id.
        tasks.append(task)

    out = Path(args.out) if args.out else Path(f"output/label_studio/tasks_{args.split}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tasks, indent=2), encoding="utf-8")

    labeling_config = """
<View>
  <Image name="image" value="$image" zoom="true" zoomControl="true"/>
  <RectangleLabels name="label" toName="image" strokeWidth="2">
    <Label value="dense_cluster" background="#3cc878" hotkey="1"/>
  </RectangleLabels>
  <Header value="stem=$stem split=$split — 拖动/删除/新增框；通过=接受预标"/>
</View>
""".strip()
    cfg_path = out.parent / "label_config.xml"
    if not cfg_path.exists():  # never clobber a curated config (two agents already collided here)
        cfg_path.write_text(labeling_config + "\n", encoding="utf-8")
    (out.parent / "README_IMPORT.txt").write_text(
        f"""Label Studio import pack
========================
1. docker compose -f scripts/label_studio_compose.yml up -d
2. Open http://127.0.0.1:8081  create local account
3. Create project → Settings → Labeling Interface → paste label_config.xml
4. Settings → Cloud Storage (optional) OR use local files already mounted
5. Import → {out.name}
6. Review: green boxes = current auto_label (E2). Fix wrong ones, export YOLO later.

Tasks: {len(tasks)}  split={args.split}  seed={args.seed}
""",
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(tasks)} tasks)")
    print(f"wrote {out.parent / 'label_config.xml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
