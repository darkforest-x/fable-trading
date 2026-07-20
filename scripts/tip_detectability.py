#!/usr/bin/env python3
"""True tip-detectability metric: re-render window ending at GT box right-edge bar.

For each positive val (or train) image:
  - map GT right edge → signal bar
  - render tip window (last bar = signal)
  - YOLO predict; hit if any box right edge lands in last 8% width (≈ tip)

Also supports proxy mode on existing images.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py \\
      --dataset datasets/dense_owner_v11 --split val --limit 80 \\
      --weights models/owner_best.pt --true-tip

  PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py --write-plan
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

STEM_RE = re.compile(r"^(?:okx_)?(?P<body>.+?)_(?P<idx>\d{4,8})(?:_tip)?$")
WINDOW = 200


def write_plan(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """# H-TIP — tip-firing for live YOLO

## Problem (2026-07-19 live)

Forward log: **0/10** rows with detection lag ≤55m. Mid-image labels teach
"cluster + launch already printed to the right"; live tip has no right context.

## Experiment (single variable)

| Item | Choice |
|------|--------|
| Base | `models/owner_best.pt` (v11) |
| Data | `dense_owner_v12_htip` = v11 ∪ tip clones (train only) |
| Build | `scripts/build_htip_dataset.py` |
| Train | chain finetune 40ep patience 10 AdamW 1e-4 |
| Metric | `tip_detectability.py --true-tip` + frozen owner F1 |
| Success | tip_hit_rate ≫ v11; frozen F1 not collapsed |

## Commands

```bash
PYTHONPATH=. .venv/bin/python scripts/build_htip_dataset.py
PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py \\
  --dataset datasets/dense_owner_v11 --split val --true-tip --limit 100 \\
  --weights models/owner_best.pt --out analysis/output/tip_rate_v11.json
PYTHONPATH=. .venv/bin/python -m src.detection.train \\
  --data datasets/dense_owner_v12_htip/data.yaml \\
  --model models/owner_best.pt --epochs 40 --patience 10 --name owner_v12_htip
PYTHONPATH=. .venv/bin/python scripts/tip_detectability.py \\
  --dataset datasets/dense_owner_v11 --split val --true-tip --limit 100 \\
  --weights runs/detect/runs/detect/owner_v12_htip/weights/best.pt \\
  --out analysis/output/tip_rate_v12.json
```

No holdout. Promote only after owner OK.
""",
        encoding="utf-8",
    )
    print(f"wrote {path}")


def parse_stem(stem: str):
    m = STEM_RE.match(stem.replace("_tip", ""))
    if not m:
        return None
    return m.group("body"), int(m.group("idx"))


def bar_from_x(tf, x: float) -> int:
    if tf.n_bars <= 1 or tf.plot_w <= 0:
        return 0
    idx = round((float(x) - tf.left) / tf.plot_w * (tf.n_bars - 1))
    return int(min(max(idx, 0), tf.n_bars - 1))


def resolve_series(sym_hint: str):
    from src.data.loader import list_series, load_series

    groups = list_series(bar="15m")
    for (_src, sym), paths in groups.items():
        if sym == sym_hint or sym.replace("_SWAP", "") == sym_hint.replace("_SWAP", ""):
            df = load_series(paths)
            if len(df) >= WINDOW + 50:
                return df
    for (_src, sym), paths in groups.items():
        if sym_hint in sym or sym in sym_hint:
            df = load_series(paths)
            if len(df) >= WINDOW + 50:
                return df
    return None


def find_window_start(n: int, idx: int) -> int | None:
    if 0 <= idx <= n - WINDOW:
        return idx
    start = idx - WINDOW + 1
    if 0 <= start <= n - WINDOW:
        return start
    start = idx - WINDOW
    if 0 <= start <= n - WINDOW:
        return start
    return None


def read_boxes(path: Path):
    boxes = []
    if not path.exists():
        return boxes
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 5:
            boxes.append(tuple(map(float, p[1:5])))
    return boxes


def true_tip_metric(dataset: Path, split: str, weights: Path, conf: float, limit: int) -> dict:
    from src.detection.data import add_mas
    from src.detection.render import make_chart_transform, render_chart
    from src.judgment.yolo_candidates import load_yolo_model

    img_dir = dataset / "images" / split
    lbl_dir = dataset / "labels" / split
    stems = []
    for p in sorted(img_dir.glob("*.png")) + sorted(img_dir.glob("*.jpg")):
        if p.stem.endswith("_tip"):
            continue
        if read_boxes(lbl_dir / f"{p.stem}.txt"):
            stems.append(p.stem)
    if limit > 0:
        stems = stems[:limit]

    model = load_yolo_model(weights)
    tmp = PROJECT / "data" / f"_tip_metric_{split}.png"
    hits = total = skip = 0
    details = []
    for stem in stems:
        boxes = read_boxes(lbl_dir / f"{stem}.txt")
        parsed = parse_stem(stem)
        if not parsed or not boxes:
            skip += 1
            continue
        body, idx = parsed
        df = resolve_series(body)
        if df is None:
            skip += 1
            continue
        n = len(df)
        win_start = find_window_start(n, idx)
        if win_start is None:
            skip += 1
            continue
        sub = add_mas(df.iloc[win_start : win_start + WINDOW].reset_index(drop=True))
        tf_old = make_chart_transform(sub)
        rights = []
        for xc, yc, w, h in boxes:
            x2 = (xc + w / 2) * tf_old.width
            rights.append(bar_from_x(tf_old, x2))
        sig_local = int(max(rights))
        signal_global = win_start + sig_local
        tip_start = signal_global - WINDOW + 1
        if tip_start < 0 or signal_global >= n:
            skip += 1
            continue
        tip_sub = add_mas(df.iloc[tip_start : signal_global + 1].reset_index(drop=True))
        if len(tip_sub) != WINDOW:
            skip += 1
            continue
        try:
            render_chart(tip_sub, out_path=tmp)
            res = model.predict(str(tmp), conf=conf, verbose=False)[0]
        except Exception as exc:
            details.append({"stem": stem, "ok": False, "err": str(exc)})
            skip += 1
            continue
        total += 1
        tip_hit = False
        max_conf = 0.0
        n_boxes = 0
        if res.boxes is not None and len(res.boxes):
            n_boxes = len(res.boxes)
            xywhn = res.boxes.xywhn.cpu().numpy()
            confs = res.boxes.conf.cpu().numpy()
            for (cx, _, w, _), c in zip(xywhn, confs):
                max_conf = max(max_conf, float(c))
                if cx + w / 2 >= 0.92:
                    tip_hit = True
        if tip_hit:
            hits += 1
        details.append(
            {
                "stem": stem,
                "ok": True,
                "tip_hit": tip_hit,
                "max_conf": round(max_conf, 4),
                "n_boxes": n_boxes,
            }
        )

    rate = hits / total if total else 0.0
    return {
        "method": "true_tip_rerender",
        "dataset": str(dataset),
        "split": split,
        "weights": str(weights),
        "conf": conf,
        "n": total,
        "skipped": skip,
        "tip_hits": hits,
        "tip_hit_rate": round(rate, 4),
        "details_head": details[:30],
    }


def proxy_metric(dataset: Path, split: str, weights: Path, conf: float, limit: int) -> dict:
    from src.judgment.yolo_candidates import load_yolo_model

    img_dir = dataset / "images" / split
    stems = [p.stem for p in sorted(img_dir.glob("*.png"))]
    if limit > 0:
        stems = stems[:limit]
    model = load_yolo_model(weights)
    hits = total = 0
    for stem in stems:
        img = img_dir / f"{stem}.png"
        if not img.exists():
            continue
        total += 1
        res = model.predict(str(img), conf=conf, verbose=False)[0]
        tip_hit = False
        if res.boxes is not None:
            for b in res.boxes.xywhn.cpu().numpy():
                cx, _, w, _ = b[:4]
                if cx + w / 2 >= 0.92:
                    tip_hit = True
        if tip_hit:
            hits += 1
    return {
        "method": "existing_image_right_edge_proxy",
        "n": total,
        "tip_hits": hits,
        "tip_hit_rate": round(hits / total, 4) if total else 0.0,
        "weights": str(weights),
        "dataset": str(dataset),
        "split": split,
        "conf": conf,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-plan", action="store_true")
    ap.add_argument("--plan-path", type=Path, default=PROJECT / "analysis" / "h_tip_plan.md")
    ap.add_argument("--dataset", type=Path, default=PROJECT / "datasets" / "dense_owner_v11")
    ap.add_argument("--split", default="val")
    ap.add_argument("--weights", type=Path, default=PROJECT / "models" / "owner_best.pt")
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--true-tip", action="store_true")
    ap.add_argument("--out", type=Path, default=PROJECT / "analysis" / "output" / "tip_detectability.json")
    args = ap.parse_args()

    if args.write_plan:
        write_plan(args.plan_path)
        return 0

    if not args.weights.exists():
        print(f"weights missing: {args.weights}", file=sys.stderr)
        return 2

    if args.true_tip:
        summary = true_tip_metric(args.dataset, args.split, args.weights, args.conf, args.limit)
    else:
        summary = proxy_metric(args.dataset, args.split, args.weights, args.conf, args.limit)
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(
        f"[{summary.get('method')}] tip_hit_rate={summary['tip_hit_rate']} "
        f"({summary.get('tip_hits')}/{summary.get('n')}) → {args.out}"
    )
    write_plan(args.plan_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
