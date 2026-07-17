"""Round-9 packs: 2025-H2 windows -- a time range no prior round has labelled.

The owner asked for 3000 tasks from a completely different time period than
before, with no repeats. Prior coverage: rounds 1-7 sampled 2025-06..2026-03 but
with stride-50 overlap (96% of that pool shares bars); round8 rendered
2026-01..05-03. This round takes 2025-06-01..2025-12-01 -- disjoint from round8,
and safely before the 2026-05-04 holdout line so detector training never sees
holdout-era bars (the standing H-TS concern).

Three guards enforce "nothing repeated":
  1. windows are non-overlapping within round9 (stride == WINDOW);
  2. any stem already in golden_pool is skipped;
  3. any window whose bar index lands within WINDOW of an already-labelled window
     of the same symbol is skipped -- this is what kills the "似曾相识" feeling,
     not just exact-stem dedup.

Prelabels from models/owner_best.pt (v10_chain, F1 0.645) with IoU>=0.30
duplicate-box suppression. Eval symbols and tokenized equities excluded.
Renders persist under datasets/dense_2025h2/ as a future reservoir; the next
training pipeline must add that dir to its SRC_DIRS.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/make_round9_packs.py
  PYTHONPATH=. .venv/bin/python scripts/make_round9_packs.py --limit 3   # smoke
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.data.universe import is_stockish                     # noqa: E402
from src.detection.data import add_mas                        # noqa: E402
from src.detection.owner_eval import is_eval_symbol           # noqa: E402
from src.detection.render import render_chart                 # noqa: E402

ROUND = 9
WINDOW = 200
START = pd.Timestamp("2025-06-01", tz="UTC")
END = pd.Timestamp("2025-12-01", tz="UTC")
N_TOTAL = 3000
N_CHUNKS = 6
UNCERTAIN_LO, UNCERTAIN_HI = 0.15, 0.45
PRELABEL_CONF = 0.20
IOU_DEDUP = 0.30
OUT_DIR = PROJECT / "datasets/dense_2025h2/images/train"


def labelled_index() -> dict[str, list[int]]:
    """symbol -> sorted bar-indices already in golden_pool, for overlap checks."""
    pool = json.loads((PROJECT / "data/golden_pool.json").read_text())
    by = defaultdict(list)
    for stem in pool:
        m = re.match(r"^(.*)_(\d+)$", stem)
        if m:
            by[m.group(1)].append(int(m.group(2)))
    for sym in by:
        by[sym].sort()
    return by


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0]-a[2]/2, a[1]-a[3]/2, a[0]+a[2]/2, a[1]+a[3]/2
    bx1, by1, bx2, by2 = b[0]-b[2]/2, b[1]-b[3]/2, b[0]+b[2]/2, b[1]+b[3]/2
    iw = max(0.0, min(ax2, bx2)-max(ax1, bx1)); ih = max(0.0, min(ay2, by2)-max(ay1, by1))
    inter = iw*ih
    u = a[2]*a[3] + b[2]*b[3] - inter
    return inter/u if u > 0 else 0.0


def render_symbol(csv_path: Path, labelled: dict[str, list[int]]) -> list[str]:
    m = re.match(r"okx_(.+)_15m_\d+\.csv$", csv_path.name)
    if not m:
        return []
    sym = m.group(1)
    if is_eval_symbol(sym) or is_stockish(sym):
        return []
    df = pd.read_csv(csv_path)
    if len(df) < WINDOW + 150:
        return []
    df["open_time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    enriched = add_mas(df)
    ts = enriched["open_time"]
    seen = labelled.get(sym, [])
    stems = []
    i = max(WINDOW + 130, int(ts.searchsorted(START)))
    while i < len(enriched):
        t = ts.iloc[i]
        if t >= END:
            break
        if t >= START:
            # guard 3: skip if within one window of an already-labelled index
            if not any(abs(i - j) < WINDOW for j in seen):
                stem = f"{sym}_{i:06d}"
                out = OUT_DIR / f"{stem}.png"
                if not out.exists():
                    sub = enriched.iloc[i-WINDOW+1: i+1].reset_index(drop=True)
                    render_chart(sub, out_path=out)
                stems.append(stem)
        i += WINDOW
    return stems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260718)
    labelled = labelled_index()
    pool = set(json.loads((PROJECT / "data/golden_pool.json").read_text()))

    t0 = time.time()
    csvs = sorted(glob.glob(str(PROJECT / "data/kline_fetched/okx_*_USDT_SWAP_15m_*.csv")))
    if args.limit:
        csvs = csvs[: args.limit]
    all_stems: list[str] = []
    for k, f in enumerate(csvs, 1):
        for s in render_symbol(Path(f), labelled):
            if s not in pool:                       # guard 2: exact-stem dedup
                all_stems.append(s)
        if k % 40 == 0:
            print(f"  渲染 {k}/{len(csvs)} 币种, 累计 {len(all_stems)} 窗, "
                  f"{(time.time()-t0)/60:.0f} 分钟", flush=True)
    print(f"渲染完成: {len(all_stems)} 个 2025H2 不重叠新窗口 "
          f"({(time.time()-t0)/60:.0f} 分钟)", flush=True)
    if not all_stems:
        raise SystemExit("没有可用窗口")

    from ultralytics import YOLO
    model = YOLO(str(PROJECT / "models/owner_best.pt"))
    scored = []
    t1 = time.time()
    for k, stem in enumerate(all_stems, 1):
        r = model.predict(str(OUT_DIR / f"{stem}.png"), conf=0.10, verbose=False)[0]
        cand = []
        if r.boxes is not None and len(r.boxes):
            for b, c in zip(r.boxes.xywhn.cpu().numpy(), r.boxes.conf.cpu().numpy()):
                cand.append((float(c), tuple(map(float, b[:4]))))
        cand.sort(reverse=True)
        keep = []
        for c, box in cand:
            if c >= PRELABEL_CONF and not any(iou(box, kb) >= IOU_DEDUP for kb in keep):
                keep.append(box)
        top = cand[0][0] if cand else 0.0
        scored.append((stem, keep, top))
        if k % 1000 == 0:
            print(f"  打分 {k}/{len(all_stems)}  {k/(time.time()-t1):.1f} 张/秒", flush=True)

    unc_idx = {i for i, s in enumerate(scored) if UNCERTAIN_LO <= s[2] <= UNCERTAIN_HI}
    unc = [s for i, s in enumerate(scored) if i in unc_idx]
    rest = [s for i, s in enumerate(scored) if i not in unc_idx]
    rng.shuffle(unc); rng.shuffle(rest)
    n_unc = min(len(unc), N_TOTAL // 2)
    take = unc[:n_unc] + rest[: N_TOTAL - n_unc]
    rng.shuffle(take)
    print(f"不确定区: {len(unc)},取 {n_unc};其余取 {len(take)-n_unc};合计 {len(take)}", flush=True)

    per = -(-len(take) // N_CHUNKS)
    for i in range(N_CHUNKS):
        chunk = take[i*per: (i+1)*per]
        if not chunk:
            continue
        tasks = [{
            "data": {"image": f"/data/local-files/?d=dense_2025h2/images/train/{stem}.png",
                     "stem": stem, "split": "train"},
            "predictions": [{
                "model_version": "owner_v10_chain_dedup",
                "result": [{
                    "type": "rectanglelabels", "from_name": "label", "to_name": "image",
                    "original_width": 1280, "original_height": 742,
                    "value": {"x": (cx-w/2)*100, "y": (cy-h/2)*100,
                              "width": w*100, "height": h*100,
                              "rectanglelabels": ["dense_cluster"]},
                } for cx, cy, w, h in boxes],
            }],
        } for stem, boxes, _ in chunk]
        out = PROJECT / f"output/label_studio/tasks_round{ROUND}_chunk{i+1}.json"
        out.write_text(json.dumps(tasks, ensure_ascii=False))
        n_pre = sum(1 for t in tasks if t["predictions"][0]["result"])
        print(f"  {out.name}: {len(tasks)} 任务 ({n_pre} 张有预标)", flush=True)

    print(f"ROUND9_DONE in {(time.time()-t0)/60:.0f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
