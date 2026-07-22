"""Round-6 Label Studio pack: 50% SWAP hard + 50% scout / model-disagreement.

Hard half
  - from datasets/dense_swap_v1, not yet in golden_pool
  - prefer model top-conf in [0.15, 0.45] (confusion zone)
  - also prefer stems listed in fiftyone hard TSV if present

Scout / divergence half
  - all current visual-scout gallery PNGs (copied under datasets/round6_scout
    so Label Studio local-files can serve them)
  - remainder filled with dense_swap uncertainty samples (active-learning
    disagreement zone) — scout gallery alone is too small for a full half

Each task carries model prelabels (owner_best) so you correct, not draw cold.
Meta fields: data.bucket = swap_hard | scout_gallery | model_uncertain

Usage:
  PYTHONPATH=. .venv/bin/python scripts/round6_half_half_pack.py \\
      --count 500 --chunks 2
  # append more without reusing prior round6 stems:
  PYTHONPATH=. .venv/bin/python scripts/round6_half_half_pack.py \\
      --count 1500 --chunks 3 --chunk-start 3 \\
      --exclude-glob 'tasks_round6_halfhalf_chunk*.json'
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

SWAP_DS = "dense_swap_v1"
SCOUT_SRC = PROJECT_DIR / "src/webapp/static/scout"
SCOUT_DS = "round6_scout"
HARD_TSVS = [
    PROJECT_DIR / "output/offline_tasks/fiftyone_hard_e21/top50_mistakenness.tsv",
    PROJECT_DIR / "output/offline_tasks/fiftyone_hard/top50_mistakenness.tsv",
]
LS_DIR = PROJECT_DIR / "output/label_studio"


def pick_model() -> Path:
    fixed = PROJECT_DIR / "models/owner_best.pt"
    if fixed.exists():
        return fixed
    runs = sorted(
        PROJECT_DIR.glob("runs/detect/runs/detect/owner_v*/weights/best.pt"),
        key=lambda p: p.stat().st_mtime,
    )
    if not runs:
        raise SystemExit("no owner_best / owner_v* weights")
    return runs[-1]


def load_pool() -> set[str]:
    p = PROJECT_DIR / "data/golden_pool.json"
    if not p.exists():
        return set()
    raw = json.loads(p.read_text())
    # golden_pool is stem -> boxes dict in some versions, list of stems in others
    if isinstance(raw, dict):
        return set(raw.keys())
    return set(raw)


def load_exclude_from_glob(pattern: str) -> set[str]:
    """Stems already packed in prior task JSON files (avoid re-queuing)."""
    out: set[str] = set()
    if not pattern:
        return out
    for path in sorted(LS_DIR.glob(pattern)):
        try:
            tasks = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for t in tasks:
            stem = (t.get("data") or {}).get("stem")
            if stem:
                out.add(stem)
    return out


def load_hard_stems() -> set[str]:
    out: set[str] = set()
    for tsv in HARD_TSVS:
        if not tsv.exists():
            continue
        for line in tsv.read_text().splitlines():
            stem = line.split("\t")[0].split("mistakenness")[0].strip()
            if stem.endswith(".png"):
                stem = stem[:-4]
            if stem:
                out.add(stem)
    return out


def find_swap_image(stem: str) -> Path | None:
    ds = PROJECT_DIR / "datasets" / SWAP_DS
    for split in ("train", "val"):
        p = ds / "images" / split / f"{stem}.png"
        if p.exists():
            return p
    return None


def _iou_xywhn(a, b) -> float:
    ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2
    ax2, ay2 = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2
    bx2, by2 = b[0] + b[2] / 2, b[1] + b[3] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def nms_xywhn(boxes_conf: list[tuple], iou_thr: float = 0.45) -> list[tuple]:
    """Greedy NMS: keep highest conf, drop overlaps. boxes are (cx,cy,w,h,conf)."""
    order = sorted(boxes_conf, key=lambda x: -x[4])
    keep = []
    for b in order:
        if all(_iou_xywhn(b[:4], k[:4]) < iou_thr for k in keep):
            keep.append(b)
    return keep


def score_image(model, img: Path, conf_floor: float = 0.10):
    """Return NMS-deduped boxes (xywhn) and top conf. One box per dense region."""
    res = model.predict(str(img), conf=conf_floor, verbose=False)[0]
    raw = []
    top = 0.0
    if res.boxes is not None and len(res.boxes):
        top = float(res.boxes.conf.max())
        for b, c in zip(res.boxes.xywhn.cpu().numpy(), res.boxes.conf.cpu().numpy()):
            if float(c) >= 0.20:
                raw.append((*map(float, b[:4]), float(c)))
    kept = nms_xywhn(raw, iou_thr=0.45)
    boxes = [k[:4] for k in kept]
    return boxes, top


def task_from(stem: str, image_url: str, boxes, model_name: str, bucket: str, split: str = "train"):
    results = [
        {
            "type": "rectanglelabels",
            "from_name": "label",
            "to_name": "image",
            "original_width": 1280,
            "original_height": 742,
            "value": {
                "x": (cx - w / 2) * 100,
                "y": (cy - h / 2) * 100,
                "width": w * 100,
                "height": h * 100,
                "rectanglelabels": ["dense_cluster"],
            },
        }
        for cx, cy, w, h in boxes
    ]
    return {
        "data": {
            "image": image_url,
            "stem": stem,
            "split": split,
            "bucket": bucket,
        },
        "predictions": [{"model_version": model_name, "result": results}],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=500, help="total tasks across chunks")
    parser.add_argument("--chunks", type=int, default=2)
    parser.add_argument("--chunk-start", type=int, default=1, help="first chunk index in filenames")
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--score-pool", type=int, default=0, help="0 = max(count*3, 2500) candidates")
    parser.add_argument(
        "--exclude-glob",
        default="",
        help="glob under output/label_studio/ of task JSONs whose stems to skip",
    )
    args = parser.parse_args()

    from ultralytics import YOLO

    rng = random.Random(args.seed)
    pool = load_pool()
    already = load_exclude_from_glob(args.exclude_glob)
    blocked = pool | already
    hard_pref = load_hard_stems()
    weights = pick_model()
    model = YOLO(str(weights))
    n_half = args.count // 2
    score_n = args.score_pool or max(args.count * 3, 2500)
    print(f"exclude: golden={len(pool)} prior_tasks={len(already)} blocked={len(blocked)}", flush=True)

    # --- prepare scout gallery under datasets/ for LS local-files ---
    scout_dst = PROJECT_DIR / "datasets" / SCOUT_DS / "images" / "train"
    scout_dst.mkdir(parents=True, exist_ok=True)
    scout_stems = []
    for src in sorted(SCOUT_SRC.glob("*.png")):
        stem = src.stem
        if stem in blocked:
            continue
        shutil.copy2(src, scout_dst / src.name)
        scout_stems.append(stem)

    # --- score dense_swap unlabeled stems ---
    ds = PROJECT_DIR / "datasets" / SWAP_DS
    swap_stems = [
        p.stem
        for split in ("train", "val")
        for p in (ds / "images" / split).glob("*.png")
        if p.stem not in blocked
    ]
    rng.shuffle(swap_stems)
    # boost hardlist stems to front of scoring queue
    hard_first = [s for s in swap_stems if s in hard_pref]
    rest = [s for s in swap_stems if s not in hard_pref]
    candidates = (hard_first + rest)[:score_n]
    print(f"swap candidates available={len(swap_stems)} scoring={len(candidates)}", flush=True)

    print(f"scoring {len(candidates)} swap candidates with {weights.name}…", flush=True)
    scored = []
    for i, stem in enumerate(candidates):
        img = find_swap_image(stem)
        if img is None:
            continue
        boxes, top = score_image(model, img)
        scored.append((stem, img.parent.name, boxes, top))
        if (i + 1) % 100 == 0:
            print(f"  scored {i+1}/{len(candidates)}", flush=True)

    uncertain = [s for s in scored if 0.15 <= s[3] <= 0.45]
    certainish = [s for s in scored if s not in uncertain]
    rng.shuffle(uncertain)
    rng.shuffle(certainish)

    # HARD half: prefer uncertain, then hardlist-scored, then rest
    hard_take = []
    for bucket_list in (uncertain, certainish):
        for row in bucket_list:
            if len(hard_take) >= n_half:
                break
            if row[0] in {h[0] for h in hard_take}:
                continue
            hard_take.append(row)
        if len(hard_take) >= n_half:
            break

    used = {h[0] for h in hard_take}

    # SCOUT half: gallery first, then more uncertain not used in hard
    scout_tasks_meta = []
    print(f"scoring {len(scout_stems)} scout gallery images…", flush=True)
    for stem in scout_stems:
        img = scout_dst / f"{stem}.png"
        boxes, top = score_image(model, img)
        scout_tasks_meta.append((stem, "train", boxes, top, "scout_gallery", SCOUT_DS))

    need_fill = max(0, n_half - len(scout_tasks_meta))
    fill = []
    for row in uncertain + certainish:
        if row[0] in used:
            continue
        if len(fill) >= need_fill:
            break
        fill.append(row)
        used.add(row[0])

    # build task dicts
    tasks_hard = []
    for stem, split, boxes, _ in hard_take[:n_half]:
        url = f"/data/local-files/?d={SWAP_DS}/images/{split}/{stem}.png"
        tasks_hard.append(task_from(stem, url, boxes, weights.name, "swap_hard", split))

    tasks_scout = []
    for stem, split, boxes, _, bucket, ds_name in scout_tasks_meta:
        url = f"/data/local-files/?d={ds_name}/images/{split}/{stem}.png"
        tasks_scout.append(task_from(stem, url, boxes, weights.name, bucket, split))
    for stem, split, boxes, _ in fill:
        url = f"/data/local-files/?d={SWAP_DS}/images/{split}/{stem}.png"
        tasks_scout.append(
            task_from(stem, url, boxes, weights.name, "model_uncertain", split)
        )

    # interleave 50/50 then chunk
    mixed = []
    a, b = list(tasks_hard), list(tasks_scout)
    while a or b:
        if a:
            mixed.append(a.pop(0))
        if b:
            mixed.append(b.pop(0))
    mixed = mixed[: args.count]
    rng.shuffle(mixed)  # avoid long runs of one type in the UI

    out_dir = LS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_size = (len(mixed) + args.chunks - 1) // args.chunks
    paths = []
    for i in range(args.chunks):
        chunk = mixed[i * chunk_size : (i + 1) * chunk_size]
        idx = args.chunk_start + i
        path = out_dir / f"tasks_round6_halfhalf_chunk{idx}.json"
        path.write_text(json.dumps(chunk, ensure_ascii=False), encoding="utf-8")
        paths.append(path)
        buckets = {}
        for t in chunk:
            buckets[t["data"]["bucket"]] = buckets.get(t["data"]["bucket"], 0) + 1
        n_pred = sum(1 for t in chunk if t["predictions"][0]["result"])
        print(f"wrote {path.name}: {len(chunk)} tasks, with_boxes={n_pred}, buckets={buckets}")

    all_r6 = sorted(out_dir.glob("tasks_round6_halfhalf_chunk*.json"))
    total_r6 = sum(len(json.loads(p.read_text())) for p in all_r6)
    readme = out_dir / "README_ROUND6.md"
    import_lines = "\n".join(
        f"PYTHONPATH=. python3 scripts/ls_auto_import.py round6_halfhalf_chunk{args.chunk_start + i} \\\n"
        f"  output/label_studio/{paths[i].name}"
        for i in range(len(paths))
    )
    readme.write_text(
        f"""# Round-6 打标包（一半 SWAP 难例 + 一半 scout/分歧）

**模型预标**：`{weights.name}`（改框 / 删框 / 补框即可）  
**本批新增**：{len(mixed)} 张（chunk {args.chunk_start}–{args.chunk_start + args.chunks - 1}）  
**Round-6 累计**：{total_r6} 张（{len(all_r6)} 个 chunk 文件）

## bucket 含义

| bucket | 含义 |
|---|---|
| `swap_hard` | 合约图库难例（优先模型 conf 0.15–0.45 + FO hardlist） |
| `scout_gallery` | 侦察兵当前 gallery 图 |
| `model_uncertain` | 补齐「分歧半」的模型犹豫区（scout 图不够时） |

## 导入本批

1. Label Studio :8081，label_config 用 `label_config_v2.xml`（含 ⭐ 标杆）
2. 每个 chunk 一个项目：
```bash
{import_lines}
```
3. 标完 export → 合并 golden_pool → build dense_owner_v7 → 再训

## 纪律

- 勿把 `owner_eval_frozen` 符号的新标并进训练集（build 时 `--exclude-eval`）
- 已在 golden_pool / 已有 round6 chunk 的 stem 已排除
""",
        encoding="utf-8",
    )
    print(f"readme → {readme} (round6 total tasks ≈ {total_r6})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
