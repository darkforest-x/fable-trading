#!/usr/bin/env python3
"""Discovery-size Label Studio import pack from hardneg mid-cluster CSV.

Does NOT start LS, does NOT train, does NOT touch MPS/holdout/LIVE.
Pre-annotations mark dense_cluster boxes that are hardneg candidates so the
reviewer can accept / delete / convert.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/hardneg_to_labelstudio.py --limit 24
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = PROJECT / "analysis/output/hardneg_mid_cluster/hardneg_mid_cluster_candidates.csv"
DEFAULT_SRC = PROJECT / "datasets/dense_owner_v11"
DEFAULT_OUT = PROJECT / "output/label_studio"


def yolo_to_pct_box(cx: float, cy: float, w: float, h: float) -> dict:
    x = (cx - w / 2) * 100
    y = (cy - h / 2) * 100
    return {
        "x": max(0.0, x),
        "y": max(0.0, y),
        "width": w * 100,
        "height": h * 100,
        "rotation": 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20260722)
    ap.add_argument("--out-prefix", default="tasks_hardneg_discovery")
    args = ap.parse_args()

    rows = list(csv.DictReader(args.candidates.open()))
    # one task per stem (first hardneg box); diversify by symbol
    by_sym: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("image_exists") not in ("True", "true", True):
            continue
        by_sym.setdefault(r.get("symbol_key") or "_", []).append(r)
    rng = random.Random(args.seed)
    syms = list(by_sym.keys())
    rng.shuffle(syms)
    for s in syms:
        by_sym[s].sort(key=lambda r: -float(r["bars_after"]))

    picks: list[dict] = []
    i = 0
    while len(picks) < args.limit and by_sym:
        sym = syms[i % len(syms)]
        bucket = by_sym.get(sym) or []
        if bucket:
            picks.append(bucket.pop(0))
            if not bucket:
                del by_sym[sym]
                syms = [x for x in syms if x in by_sym]
                if not syms:
                    break
                i = 0
                continue
        i += 1
        if i > 20000:
            break

    tasks = []
    for r in picks:
        stem = r["stem"]
        split = r["split"]
        img_name = Path(r["image_rel"]).name
        image_url = f"/data/local-files/?d={args.src.name}/images/{split}/{img_name}"
        cx, cy, w, h = map(float, (r["cx"], r["cy"], r["w"], r["h"]))
        result = {
            "id": f"hardneg_{stem}_{r['box_i']}",
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
        tasks.append(
            {
                "data": {
                    "image": image_url,
                    "stem": stem,
                    "split": split,
                    "hardneg": True,
                    "bars_after": float(r["bars_after"]),
                    "right": float(r["right"]),
                    "note": "H-DET-2 candidate — mid cluster with aftermath; review before any train add-on",
                },
                "predictions": [
                    {
                        "model_version": "hardneg_inventory_gt",
                        "score": 1.0,
                        "result": [result],
                    }
                ],
            }
        )

    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    out_json = DEFAULT_OUT / f"{args.out_prefix}.json"
    out_json.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n")

    readme = DEFAULT_OUT / f"{args.out_prefix}_README.md"
    readme.write_text(
        f"""# Hardneg discovery LS pack

- **File**: `{out_json.relative_to(PROJECT)}`
- **n_tasks**: {len(tasks)}
- **Source CSV**: `{args.candidates.relative_to(PROJECT)}`
- **Dataset mount**: `{args.src.name}` (same local-files layout as other LS packs)

## Import
1. `docker compose -f scripts/label_studio_compose.yml up -d`
2. Project labeling interface: paste `output/label_studio/label_config.xml` (dense_cluster)
3. Import → `{out_json.name}`
4. Review: these boxes are **hardneg mid-cluster candidates** (aftermath remains). Do **not**
   treat as tip gold. Training add-on waits for v13 + owner approve (H-DET-2).

## Constraints
CPU/offline only. No promote. No holdout.
""",
        encoding="utf-8",
    )
    print(f"wrote {out_json} n={len(tasks)}")
    print(f"wrote {readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
