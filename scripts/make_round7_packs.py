"""Round-7 packs: 3000 unlabeled images prelabeled by the current best detector
(v7_chain, frozen-F1 0.625) for the owner to correct -> v8.

Two exclusions keep the measurement honest, and both are load-bearing:
  - frozen-eval symbols (sha1%7==0): if v8 trains on them, its frozen-F1 becomes
    memorization, which is exactly how v5's 0.663 turned out to be a leak.
  - anything already in golden_pool: re-labelling what we have buys nothing.

Half the sample is drawn from the model's uncertainty zone (top conf 0.15-0.45).
Those are the images where the model is wrong most often, so the owner's correction
carries the most information per minute spent labelling.

Run this ONLY when no training holds the GPU. nice(1) does not isolate the MPS
queue -- see docs/learnings/nice-does-not-isolate-gpu-contention.md. Use
--device cpu if something else must have the GPU.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/make_round7_packs.py
  PYTHONPATH=. .venv/bin/python scripts/make_round7_packs.py --device cpu --limit 1000
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.owner_eval import is_eval_stem  # noqa: E402  (needs sys.path above)

ROUND = 7
N_TOTAL = 3000
N_CHUNKS = 6
UNCERTAIN_LO, UNCERTAIN_HI = 0.15, 0.45


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default=None, help="cpu | mps | 0 (default: auto)")
    ap.add_argument("--limit", type=int, default=3600, help="candidates to score")
    ap.add_argument("--model", default=str(PROJECT / "models/owner_best.pt"))
    args = ap.parse_args()

    from ultralytics import YOLO

    rng = random.Random(20260716)
    pool = set(json.loads((PROJECT / "data/golden_pool.json").read_text()))
    print(f"golden_pool: {len(pool)} 张已标")

    cands = []
    for ds in ("dense_swap_v1", "dense_15m_full"):
        for sp in ("train", "val"):
            d = PROJECT / "datasets" / ds / "images" / sp
            if not d.exists():
                continue
            for p in d.glob("*.png"):
                if p.stem in pool or is_eval_stem(p.stem):
                    continue
                cands.append((p, ds, sp, p.stem))
    rng.shuffle(cands)
    print(f"可选(排除已标 + 冻结eval币种): {len(cands)}")
    if len(cands) < N_TOTAL:
        print(f"⚠️  可选只有 {len(cands)} < {N_TOTAL},本轮会短一些")

    model = YOLO(args.model)
    predict_kw = {"conf": 0.10, "verbose": False}
    if args.device:
        predict_kw["device"] = args.device

    scored = []
    todo = cands[: args.limit]
    t0 = time.time()
    for i, (p, ds, sp, stem) in enumerate(todo, 1):
        r = model.predict(str(p), **predict_kw)[0]
        if r.boxes is not None and len(r.boxes):
            confs = r.boxes.conf.cpu().numpy()
            xywhn = r.boxes.xywhn.cpu().numpy()
            boxes = [tuple(map(float, b[:4])) for b, c in zip(xywhn, confs) if float(c) >= 0.20]
            top = float(confs.max())
        else:
            boxes, top = [], 0.0
        scored.append((stem, ds, sp, boxes, top))
        if i % 250 == 0:
            rate = i / (time.time() - t0)
            eta = (len(todo) - i) / rate / 60
            print(f"  打分 {i}/{len(todo)}  {rate:.1f} 张/秒  剩 {eta:.0f} 分钟", flush=True)

    # Partition by index, not by value: `s not in unc` compared tuples holding
    # lists of floats -- O(n*m) and fragile. Indices are exact and cheap.
    unc_idx = {i for i, s in enumerate(scored) if UNCERTAIN_LO <= s[4] <= UNCERTAIN_HI}
    unc = [s for i, s in enumerate(scored) if i in unc_idx]
    rest = [s for i, s in enumerate(scored) if i not in unc_idx]
    n_unc = min(len(unc), N_TOTAL // 2)
    take = unc[:n_unc] + rest[: N_TOTAL - n_unc]
    rng.shuffle(take)
    print(f"\n不确定区({UNCERTAIN_LO}-{UNCERTAIN_HI}): {len(unc)} 张,取 {n_unc}")
    print(f"其余: {len(rest)} 张,取 {len(take) - n_unc}")
    print(f"合计: {len(take)} 张\n")

    per = -(-len(take) // N_CHUNKS)  # ceil
    for i in range(N_CHUNKS):
        chunk = take[i * per : (i + 1) * per]
        if not chunk:
            continue
        tasks = [
            {
                "data": {
                    "image": f"/data/local-files/?d={ds}/images/{sp}/{stem}.png",
                    "stem": stem,
                    "split": sp,
                },
                "predictions": [
                    {
                        "model_version": "owner_v7_chain",
                        "result": [
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
                            for cx, cy, w, h in bx
                        ],
                    }
                ],
            }
            for stem, ds, sp, bx, _ in chunk
        ]
        out = PROJECT / f"output/label_studio/tasks_round{ROUND}_chunk{i+1}.json"
        out.write_text(json.dumps(tasks, ensure_ascii=False))
        n_pre = sum(1 for t in tasks if t["predictions"][0]["result"])
        print(f"  {out.name}: {len(tasks)} 任务 ({n_pre} 张有预标)")

    print(f"\n✅ round{ROUND} 生成完毕,耗时 {(time.time()-t0)/60:.0f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
